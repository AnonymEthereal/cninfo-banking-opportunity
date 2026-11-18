import json
import traceback
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from time import monotonic

from pydantic import BaseModel

from .deadline import deadline, remaining
from .dedup import dedupe_events, dedupe_opportunities, link_related_disclosures
from .errors import RuntimeLeaseLostError
from .evidence import preview
from .models import Announcement, EventExtractionResult, OpportunityDraft, MarketingAnalysis, Opportunity, ScanError
from .storage import atomic_write_json


def as_model(model, value):
    """Normalize an agent result into the currently imported model class.

    Mock agents return plain dicts; live agents return instances. A deploy can
    reload ``core.models`` while an already-submitted task keeps running, so a
    live agent may hand back an instance of the previous class. Pydantic rejects
    that with ``Input should be a valid dictionary or instance of ...`` even
    though the fields are identical, so round-trip through ``model_dump``.
    """
    if isinstance(value, model):
        return value
    if isinstance(value, BaseModel):
        value = value.model_dump()
    return model.model_validate(value)


def _analysis_action(code):
    if code in ('EVIDENCE_MISMATCH', 'AMOUNT_EVIDENCE_MISMATCH'):
        return '证据引用未通过逐字校验；系统已自动重试一次。仍失败可重试该公告，或检查公告正文是否完整。'
    if code in ('AMOUNT_VALUE_MISMATCH', 'AMOUNT_CURRENCY_MISMATCH'):
        return '金额数值或币种与原文无法对应；系统已自动重试一次。仍无法确认时请人工核对原文后再处理。'
    if code in ('SCHEMA_VALIDATION_FAILED', 'LLM_INVALID_JSON'):
        return '模型返回的结构不符合要求；系统已自动重试一次。仍失败可重试该公告。'
    if code in ('AGENT_TIMEOUT', 'TASK_TIMEOUT', 'LLM_TIMEOUT'):
        return '超时可增加预算后重试；其他错误请检查模型配置、服务额度或结构化输出。'
    return '其他错误请检查模型配置、服务额度或结构化输出。'

def analyze_one(announcement, agents, folder, seconds, runner=None, lease_guard=None):
    token = deadline.set(monotonic() + seconds)
    stage = 'read_document'
    text = ''
    try:
        if runner is not None:
            runner._assert_lease(lease_guard)
        text = __import__('pathlib').Path(announcement.text_path).read_text(encoding='utf-8')
        stage = 'agent_1'
        if runner is not None:
            runner._assert_lease(lease_guard)
        events = as_model(EventExtractionResult, agents[0].analyze(announcement, text))
        if runner is not None:
            runner._assert_lease(lease_guard)
        events.events = dedupe_events(events.events)
        remaining()
        (runner._write_json if runner is not None else atomic_write_json)(
            folder / 'agent1.json', events, lease_guard=lease_guard
        )
        items, drafts, marketing = [], [], []
        for event in events.events:
            if not event.worth_analyzing:
                continue
            remaining()
            stage = 'agent_2'
            if runner is not None:
                runner._assert_lease(lease_guard)
            draft = as_model(OpportunityDraft, agents[1].analyze(event, text))
            if runner is not None:
                runner._assert_lease(lease_guard)
            drafts.append(draft.model_dump())
            (runner._write_json if runner is not None else atomic_write_json)(
                folder / 'agent2.json', {'items': drafts}, lease_guard=lease_guard
            )
            remaining()
            stage = 'agent_3'
            if runner is not None:
                runner._assert_lease(lease_guard)
            advice = as_model(MarketingAnalysis, agents[2].analyze(draft, text))
            if runner is not None:
                runner._assert_lease(lease_guard)
            if advice.analysis_outcome is None:
                advice.analysis_outcome = (
                    "opportunity_identified"
                    if advice.bank_products
                    or advice.marketing_direction
                    or advice.suggested_action
                    else "no_clear_entry_point"
                )
            if (
                advice.analysis_outcome == "opportunity_identified"
                and not (
                    advice.bank_products
                    or advice.marketing_direction
                    or advice.suggested_action
                )
            ):
                advice.analysis_outcome = "no_clear_entry_point"
            if advice.analysis_outcome == "no_clear_entry_point":
                advice.bank_products = []
                advice.marketing_direction = None
                advice.suggested_action = None
            if (
                advice.analysis_outcome == "no_clear_entry_point"
                and not advice.analysis_summary
            ):
                advice.analysis_summary = "基于目前披露，暂未识别出明确的银行业务切入点。"
            remaining()
            marketing.append(advice.model_dump())
            (runner._write_json if runner is not None else atomic_write_json)(
                folder / 'agent3.json', {'items': marketing}, lease_guard=lease_guard
            )
            fields = draft.model_dump(exclude={'event_id'})
            fields.update(advice.model_dump())
            item = Opportunity(**fields, opportunity_id=f'{announcement.announcement_id}_{event.event_id}',
                announcement_id=announcement.announcement_id, stock_code=announcement.stock_code,
                company_name=announcement.company_name, publish_date=announcement.publish_date,
                announcement_title=announcement.title, source_url=announcement.source_url).model_dump()
            item["_source_event_id"] = event.event_id
            items.append(item)
        deduplicated = dedupe_opportunities(
            list({x['opportunity_id']: x for x in items}.values())
        )
        for item in deduplicated:
            item.pop("_source_event_id", None)
        return {'status': 'completed', 'items': deduplicated}
    except RuntimeLeaseLostError:
        raise
    except Exception as exc:
        traceback.print_exc()
        quote = getattr(exc, 'quote', '')
        return {'status': 'failed', 'items': [], 'stage': stage, 'error_type': type(exc).__name__, 'error_message': str(exc)[:300], 'code': 'AGENT_TIMEOUT' if isinstance(exc, TimeoutError) else getattr(exc, 'code', 'AGENT_FAILED'), 'evidence_preview': preview(quote) if quote else None, 'document_chars': len(text) or None}
    finally:
        deadline.reset(token)

def run_batch(
    runner,
    scan,
    agents,
    seconds=600,
    task_seconds=28800,
    workers=2,
    retry_failed=False,
    cancel_event=None,
):
    folder = runner.root / 'results' / scan.scan_id
    lease_guard = runner.lease_guard
    announcements = [Announcement.model_validate(x) for x in json.loads((folder / 'announcements.json').read_text(encoding='utf-8'))['items']]
    records = {}
    pending = []
    for a in announcements:
        checkpoint = folder / 'agents' / a.announcement_id / 'result.json'
        if checkpoint.exists():
            record = json.loads(checkpoint.read_text(encoding='utf-8'))
            if retry_failed and record['status'] == 'failed' and a.text_path and __import__('pathlib').Path(a.text_path).exists():
                from uuid import uuid4
                runner._write_json(checkpoint.parent / 'history' / f'{uuid4().hex}.json', record)
                pending.append(a)
            else:
                records[a.announcement_id] = record
        elif a.text_path and __import__('pathlib').Path(a.text_path).exists():
            pending.append(a)
        else:
            records[a.announcement_id] = {'status': 'skipped', 'items': []}
    scan.status = 'analyzing'
    scan.progress.total = len(announcements)
    baseline = [e for e in scan.errors if e.scope != 'analysis']
    last_persist = 0.0

    def persist(force=False):
        nonlocal last_persist
        now = monotonic()
        if not force and now - last_persist < 2:
            return
        all_items = {
            o['opportunity_id']: o
            for r in records.values()
            for o in r['items']
        }
        all_items = link_related_disclosures(list(all_items.values()))
        opportunities = {
            item['opportunity_id']: item
            for item in all_items
            if item.get('analysis_outcome') != 'no_clear_entry_point'
        }
        non_opportunities = {
            item['opportunity_id']: item
            for item in all_items
            if item.get('analysis_outcome') == 'no_clear_entry_point'
        }
        runner._write_json(
            folder / 'opportunities.json', {'items': list(opportunities.values())}
        )
        runner._write_json(
            folder / 'non_opportunities.json',
            {'items': list(non_opportunities.values())},
        )
        scan.errors = baseline + [ScanError(scope='analysis', announcement_id=k, code=r['code'], message='公告分析失败；已保存检查点。', details={'stage': r.get('stage', 'agents'), 'error_type': r.get('error_type', 'TimeoutError' if r['code'] == 'TASK_TIMEOUT' else 'Unknown'), 'error_message': r.get('error_message', ''), 'announcement_budget_seconds': seconds, 'batch_budget_seconds': task_seconds, 'action': _analysis_action(r['code']), **({'evidence_preview': r['evidence_preview']} if r.get('evidence_preview') else {}), **({'document_chars': r['document_chars']} if r.get('document_chars') else {})}) for k,r in records.items() if r['status']=='failed']
        scan.counts.analysis_failed = sum(r['status']=='failed' for r in records.values())
        scan.counts.opportunities = len(opportunities)
        scan.counts.non_opportunities = len(non_opportunities)
        scan.progress.completed = len(records)
        scan.progress.stage = 'agents'
        runner._save(scan)
        last_persist = now

    persist(force=True)
    end = monotonic() + task_seconds
    with ThreadPoolExecutor(max_workers=workers) as pool:
        active = {}
        while active or (
            pending and not (cancel_event is not None and cancel_event.is_set())
        ):
            runner._assert_lease()
            while pending and len(active) < workers and not (
                cancel_event is not None and cancel_event.is_set()
            ):
                a = pending.pop(0)
                if monotonic() >= end:
                    records[a.announcement_id] = {'status':'failed','items':[], 'code':'TASK_TIMEOUT'}
                    runner._write_json(folder/'agents'/a.announcement_id/'result.json',records[a.announcement_id])
                else:
                    active[pool.submit(
                        analyze_one,
                        a,
                        agents,
                        folder/'agents'/a.announcement_id,
                        min(seconds,end-monotonic()),
                        runner,
                        lease_guard,
                    )] = a
            if active:
                done,_ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                for f in done:
                    a = active.pop(f)
                    records[a.announcement_id] = f.result()
                    runner._write_json(
                        folder/'agents'/a.announcement_id/'result.json',
                        records[a.announcement_id],
                        lease_guard=lease_guard,
                    )
            persist()
    persist(force=True)
    if cancel_event is not None and cancel_event.is_set():
        return scan
    scan.progress.stage = 'analysis_completed'
    runner._save(scan)
    return scan
