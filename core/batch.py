import json
import traceback
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from time import monotonic
from .deadline import deadline, remaining
from .dedup import dedupe_events, dedupe_opportunities
from .models import Announcement, EventExtractionResult, OpportunityDraft, MarketingAnalysis, Opportunity, ScanError
from .storage import atomic_write_json

def analyze_one(announcement, agents, folder, seconds):
    token = deadline.set(monotonic() + seconds)
    stage = 'read_document'
    try:
        text = __import__('pathlib').Path(announcement.text_path).read_text(encoding='utf-8')
        stage = 'agent_1'
        events = EventExtractionResult.model_validate(agents[0].analyze(announcement, text))
        events.events = dedupe_events(events.events)
        remaining()
        atomic_write_json(folder / 'agent1.json', events)
        items, drafts, marketing = [], [], []
        for event in events.events:
            if not event.worth_analyzing:
                continue
            remaining()
            stage = 'agent_2'
            draft = OpportunityDraft.model_validate(agents[1].analyze(event, text))
            drafts.append(draft.model_dump())
            atomic_write_json(folder / 'agent2.json', {'items': drafts})
            remaining()
            stage = 'agent_3'
            advice = MarketingAnalysis.model_validate(agents[2].analyze(draft, text))
            remaining()
            marketing.append(advice.model_dump())
            atomic_write_json(folder / 'agent3.json', {'items': marketing})
            fields = draft.model_dump(exclude={'event_id'})
            fields.update(advice.model_dump())
            items.append(Opportunity(**fields, opportunity_id=f'{announcement.announcement_id}_{event.event_id}',
                announcement_id=announcement.announcement_id, stock_code=announcement.stock_code,
                company_name=announcement.company_name, publish_date=announcement.publish_date,
                announcement_title=announcement.title, source_url=announcement.source_url).model_dump())
        return {'status': 'completed', 'items': dedupe_opportunities(list({x['opportunity_id']: x for x in items}.values()))}
    except Exception as exc:
        traceback.print_exc()
        return {'status': 'failed', 'items': [], 'stage': stage, 'error_type': type(exc).__name__, 'error_message': str(exc)[:300], 'code': 'AGENT_TIMEOUT' if isinstance(exc, TimeoutError) else getattr(exc, 'code', 'AGENT_FAILED')}
    finally:
        deadline.reset(token)

def run_batch(runner, scan, agents, seconds=600, task_seconds=28800, workers=2, retry_failed=False):
    folder = runner.root / 'results' / scan.scan_id
    announcements = [Announcement.model_validate(x) for x in json.loads((folder / 'announcements.json').read_text(encoding='utf-8'))['items']]
    records = {}
    pending = []
    for a in announcements:
        checkpoint = folder / 'agents' / a.announcement_id / 'result.json'
        if checkpoint.exists():
            record = json.loads(checkpoint.read_text(encoding='utf-8'))
            if retry_failed and record['status'] == 'failed' and a.text_path and __import__('pathlib').Path(a.text_path).exists():
                from uuid import uuid4
                atomic_write_json(checkpoint.parent / 'history' / f'{uuid4().hex}.json', record)
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
        items = {o['opportunity_id']: o for r in records.values() for o in r['items']}
        atomic_write_json(folder / 'opportunities.json', {'items': list(items.values())})
        scan.errors = baseline + [ScanError(scope='analysis', announcement_id=k, code=r['code'], message='公告分析失败；已保存检查点。', details={'stage': r.get('stage', 'agents'), 'error_type': r.get('error_type', 'TimeoutError' if r['code'] == 'TASK_TIMEOUT' else 'Unknown'), 'error_message': r.get('error_message', ''), 'announcement_budget_seconds': seconds, 'batch_budget_seconds': task_seconds, 'action': '超时可增加预算后重试；其他错误请检查模型配置、服务额度或结构化输出。'}) for k,r in records.items() if r['status']=='failed']
        scan.counts.analysis_failed = sum(r['status']=='failed' for r in records.values())
        scan.counts.opportunities = len(items)
        scan.progress.completed = len(records)
        scan.progress.stage = 'agents'
        runner._save(scan)
        last_persist = now

    persist(force=True)
    end = monotonic() + task_seconds
    with ThreadPoolExecutor(max_workers=workers) as pool:
        active = {}
        while pending or active:
            while pending and len(active) < workers:
                a = pending.pop(0)
                if monotonic() >= end:
                    records[a.announcement_id] = {'status':'failed','items':[], 'code':'TASK_TIMEOUT'}
                    atomic_write_json(folder/'agents'/a.announcement_id/'result.json',records[a.announcement_id])
                else:
                    active[pool.submit(analyze_one, a, agents, folder/'agents'/a.announcement_id, min(seconds,end-monotonic()))] = a
            if active:
                done,_ = wait(active, timeout=1, return_when=FIRST_COMPLETED)
                for f in done:
                    a = active.pop(f)
                    records[a.announcement_id] = f.result()
                    atomic_write_json(folder/'agents'/a.announcement_id/'result.json', records[a.announcement_id])
            persist()
    persist(force=True)
    scan.progress.stage = 'analysis_completed'
    runner._save(scan)
    return scan
