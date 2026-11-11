import json
import os
from pathlib import Path
import streamlit as st
from core.calendar import default_date_range
from core.company_input_ui import render_company_input
from core.models import Company, DateRange, ScanStatus, Opportunity
from core.scanner import ScanOrchestrator
from collectors.cninfo import CninfoCollector
from agents.event_agent import MockEventAgent
from agents.classify_agent import MockClassifyAgent
from agents.marketing_agent import MockMarketingAgent
from agents.live import live_agents
from core.task_manager import TaskManager

st.set_page_config(page_title="上市公司商机雷达", page_icon="◈", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
<style>
:root { --navy:#0f172a; --slate:#1e293b; --green:#22c55e; --ink:#f8fafc; }
.stApp { background: #f8fafc; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] { background: #eef2f6; }
.hero { background: linear-gradient(115deg,#0f172a 0%,#1e293b 62%,#14532d 100%); color: var(--ink); border-radius: 16px; padding: 1.8rem 2rem; margin-bottom: 1.2rem; box-shadow: 0 12px 30px rgba(15,23,42,.16); }
.hero-kicker { color: #86efac; font-size: .78rem; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }
.hero h1 { margin: .3rem 0 .5rem; font-family: 'IBM Plex Sans', sans-serif; font-size: 2.2rem; }
.hero p { color: #cbd5e1; margin: 0; }
button:focus-visible { outline: 3px solid #15803d !important; outline-offset: 3px; }
@media (max-width: 640px) { .hero { padding: 1.2rem; } .hero h1 { font-size: 1.6rem; } }
@media (prefers-color-scheme: dark) { .stApp { background: #0f172a; } [data-testid="stSidebar"] { background: #1e293b; } [data-testid="stMetric"] { background: #1e293b; border-color: #475569; } }
[data-testid="stMetric"] { background: white; border: 1px solid #e2e8f0; border-radius: 12px; padding: .8rem 1rem; box-shadow: 0 4px 16px rgba(15,23,42,.05); }
div[data-testid="stExpander"] { border: 1px solid #dbe4ee; border-radius: 10px; }
div[data-testid="stButton"] button, div[data-testid="stDownloadButton"] button { border-radius: 8px; font-weight: 600; }
</style>
<div class="hero"><div class="hero-kicker">Opportunity intelligence · v1</div><h1>上市公司商机雷达</h1><p>从公告事实到银行跟进线索，统一查看、核验与导出。</p></div>
""", unsafe_allow_html=True)
configured = all(os.getenv(key, '').strip() for key in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL'))
with st.sidebar:
    st.markdown("### 扫描设置")
    st.caption("先校验名单，再启动扫描。任务运行中可回到结果区刷新状态。")
    use_live = st.checkbox('使用真实 LLM 分析', value=False, disabled=not configured)
if use_live:
    st.info('本次启动将把公开公告正文发送到配置的模型服务进行分析。')
else:
    st.warning('演示模式使用 Mock Agent，空结果不代表没有商机。')
if not configured:
    st.caption('请设置 LLM_API_KEY、LLM_BASE_URL 和 LLM_MODEL 环境变量后重启页面。')

@st.cache_resource
def orchestrator():
    return ScanOrchestrator(CninfoCollector())

@st.cache_resource
def task_manager():
    return TaskManager(orchestrator())

if "validation" not in st.session_state:
    st.session_state.validation = None
if "scan_id" not in st.session_state:
    st.session_state.scan_id = None

with st.sidebar:
    st.divider()
    st.subheader("历史任务")
    history = []
    for path in (Path(orchestrator().root) / 'results').glob('*/scan.json'):
        try:
            history.append(orchestrator().load(path.parent.name))
        except Exception:
            continue
    history.sort(key=lambda item: item.created_at, reverse=True)
    by_id = {item.scan_id: item for item in history}
    history_id = st.selectbox("选择历史任务", [None] + list(by_id), format_func=lambda key: "请选择任务" if key is None else f"{by_id[key].created_at:%m-%d %H:%M} · {by_id[key].counts.companies} 家 · {by_id[key].status} · {key}")
    if st.button("查看该任务", disabled=history_id is None):
        st.session_state.scan_id = history_id
        st.rerun()

st.session_state.validation = render_company_input()

st.subheader("02 · 确认扫描范围")
default_range = default_date_range()
col1, col2 = st.columns(2)
with col1:
    start = st.date_input("开始日期", default_range.start_date)
with col2:
    end = st.date_input("结束日期", default_range.end_date)
company_count = len(st.session_state.validation["companies"]) if st.session_state.validation else 0
active_scan = orchestrator().load(st.session_state.scan_id) if st.session_state.scan_id else None
busy = active_scan is not None and active_scan.status not in (ScanStatus.COMPLETED, ScanStatus.COMPLETED_WITH_ERRORS, ScanStatus.FAILED, ScanStatus.AWAITING_REVIEW)
st.caption(f"本次扫描 {company_count} 家企业，日期范围 {start} 至 {end}。仅对采集并成功解析的公告调用分析，无公告企业不调用模型。")
st.caption("先检索公告，确认后才下载正文并分析；本次检索不会调用模型。")
if end < start:
    st.error("结束日期不能早于开始日期。")
if busy:
    st.info("当前任务尚未结束，请先查看或恢复当前任务，避免重复提交。")
if st.button("检索公告", type="primary", disabled=busy or end < start or not (st.session_state.validation and st.session_state.validation["valid"])):
    client = None
    scan = None
    try:
        agents = (MockEventAgent(), MockClassifyAgent(), MockMarketingAgent())
        companies = [Company.model_validate(item) for item in st.session_state.validation["companies"]]
        scan = orchestrator().create(companies, DateRange(start_date=start, end_date=end))
        scan.analysis_mode = 'live' if use_live else 'mock'
        scan.require_review = True
        orchestrator()._save(scan)
        st.session_state.scan_id = scan.scan_id
        st.session_state.scan_mode = 'live' if use_live else 'mock'
        resources = [client] if client is not None else []
        task_manager().submit(scan.scan_id, agents, resources=resources)
        st.success(f"公告检索已提交：{scan.scan_id}，完成后请先预览公告并确认分析。")
    except Exception as exc:
        if scan is not None:
            from core.models import ScanError
            failed = orchestrator().load(scan.scan_id)
            failed.status = ScanStatus.FAILED
            failed.errors.append(ScanError(scope='task', code='TASK_FAILED', message='任务执行失败，请检查配置和本地文件。'))
            orchestrator()._save(failed)
        st.error('扫描失败，请检查任务异常和配置。')

st.header("03 · 公告预览与分析")
if st.session_state.scan_id:
    scan_id = st.session_state.scan_id
    scan = orchestrator().load(scan_id)
    st.session_state.scan_mode = scan.analysis_mode
    if scan.analysis_mode == 'unknown':
        st.warning("旧任务没有保存分析模式。恢复前请选择原任务实际使用的模式。")
        legacy_mode = st.selectbox("旧任务分析模式", [None, 'mock', 'live'], format_func=lambda value: {None: '请选择', 'mock': '演示分析', 'live': '真实模型分析'}[value])
        if st.button("保存任务模式", disabled=legacy_mode is None):
            scan.analysis_mode = legacy_mode
            orchestrator()._save(scan)
            st.rerun()
    future = task_manager().futures.get(scan_id)
    announcements_path = Path(orchestrator().root) / 'results' / scan_id / 'announcements.json'
    announcement_rows = json.loads(announcements_path.read_text(encoding='utf-8')).get('items', []) if announcements_path.exists() else []
    with st.expander(f"检索公告（{len(announcement_rows)} 条）", expanded=scan.status == 'awaiting_review'):
        keyword = st.text_input("筛选公告标题或企业", key=f'announcement_search_{scan_id}')
        preview = [item for item in announcement_rows if not keyword or keyword.casefold() in (item['title'] + item['company_name']).casefold()]
        if preview:
            st.dataframe([{'企业': item['company_name'], '标题': item['title'], '日期': str(item['publish_date']), '公告 ID': item['announcement_id'], '原文': item['source_url']} for item in preview], hide_index=True, width='stretch', column_config={'原文': st.column_config.LinkColumn('原文')})
        else:
            st.info("暂无符合条件的公告。")
    if scan.status == 'awaiting_review':
        st.info(f"公告检索完成，等待确认。分析模式：{scan.analysis_mode}；确认后将处理全部 {len(announcement_rows)} 条公告，标题筛选仅影响预览。")
    idle = future is None or future.done()
    if idle:
        with st.expander("分析时间预算", expanded=scan.status == 'awaiting_review'):
            per_minutes = st.number_input("单篇公告预算（分钟）", min_value=1, value=max(1, scan.announcement_timeout // 60), step=1, key=f'per_budget_{scan_id}')
            batch_hours = st.number_input("整批分析预算（小时）", min_value=1, value=max(1, scan.analysis_timeout // 3600), step=1, key=f'batch_budget_{scan_id}')
            if st.button("保存时间预算", key=f'save_budget_{scan_id}'):
                scan.announcement_timeout = int(per_minutes * 60)
                scan.analysis_timeout = int(batch_hours * 3600)
                orchestrator()._save(scan)
                st.success("预算已保存，后续分析及重试生效。模型请求本身仍受 HTTP 超时和重试规则约束。")
    if scan.status == 'awaiting_review' and idle:
        if st.button("确认继续分析全部公告", type='primary', disabled=scan.analysis_mode == 'unknown' or not announcement_rows):
            client = None
            try:
                if scan.analysis_mode == 'live':
                    client, agents = live_agents()
                else:
                    agents = (MockEventAgent(), MockClassifyAgent(), MockMarketingAgent())
                scan.analysis_confirmed = True
                scan.announcement_timeout = int(per_minutes * 60)
                scan.analysis_timeout = int(batch_hours * 3600)
                orchestrator()._save(scan)
                task_manager().submit(scan_id, agents, resources=[client] if client else [])
                st.rerun()
            except Exception:
                if client:
                    client.close()
                st.error("分析未能启动，请检查配置后重试。")
    if (scan.counts.collection_failed or scan.counts.document_failed) and (future is None or future.done()):
        st.caption("重试失败采集和正文解析，复用成功检查点；新增或修复的公告会继续分析。真实模式可能产生模型费用，旧记录与报告会留档。")
        if st.button("重试采集与解析失败项", disabled=scan.analysis_mode == 'unknown'):
            client = None
            try:
                if scan.analysis_mode == 'live':
                    client, agents = live_agents()
                else:
                    agents = (MockEventAgent(), MockClassifyAgent(), MockMarketingAgent())
                task_manager().submit(scan_id, agents, resources=[client] if client else [], retry_sources=True)
                st.rerun()
            except Exception:
                if client:
                    client.close()
                st.error("重试未能启动，请检查配置与任务文件。")
    interrupted = scan.status not in (ScanStatus.COMPLETED, ScanStatus.COMPLETED_WITH_ERRORS, ScanStatus.AWAITING_REVIEW) and (future is None or future.done())
    if interrupted:
        st.warning("任务当前未运行，可从已保存进度继续。成功采集的企业与已完成的公告分析会复用；失败分析记录暂不自动重试。")
        if st.button("从检查点继续当前任务", disabled=scan.analysis_mode == 'unknown'):
            client = None
            try:
                if st.session_state.get('scan_mode') == 'live':
                    client, agents = live_agents()
                else:
                    agents = (MockEventAgent(), MockClassifyAgent(), MockMarketingAgent())
                task_manager().submit(scan_id, agents, resources=[client] if client else [])
                st.rerun()
            except Exception:
                if client:
                    client.close()
                st.error("恢复失败，请检查模型配置与任务文件。")
    if scan.counts.analysis_failed and (future is None or future.done()):
        st.caption("只重试失败公告，复用已成功分析；旧失败检查点留档。真实模式会再次调用模型并产生费用。")
        if st.button("重试失败的公告分析", disabled=scan.analysis_mode == 'unknown'):
            client = None
            try:
                if scan.analysis_mode == 'live':
                    client, agents = live_agents()
                else:
                    agents = (MockEventAgent(), MockClassifyAgent(), MockMarketingAgent())
                task_manager().submit(scan_id, agents, resources=[client] if client else [], retry_failed=True)
                st.rerun()
            except Exception:
                if client:
                    client.close()
                st.error("重试未能启动，请检查任务配置。")
    if scan.status in (ScanStatus.COMPLETED, ScanStatus.COMPLETED_WITH_ERRORS):
        if scan.counts.announcements == 0:
            if scan.counts.collection_failed:
                st.warning("未获取到公告，且部分企业采集失败；请先检查错误，不能判断这些企业没有公告。")
            else:
                st.info("所选企业在当前日期范围内未采集到公告。可以增加企业或扩大日期范围后重新扫描。")
        elif scan.counts.opportunities == 0:
            if scan.errors:
                st.warning("已采集到公告，但存在处理失败；当前零商机不能代表所有公告均无商机。")
            elif st.session_state.get('scan_mode', 'mock') == 'live':
                st.info("公告分析已完成，未识别到符合条件的商机。可以扩大企业或日期范围。")
    st.caption(f"任务 ID：{scan_id} · 更新于 {scan.updated_at:%Y-%m-%d %H:%M:%S}")
    if scan.progress.total:
        st.progress(min(scan.progress.completed / scan.progress.total, 1.0))
    metrics = st.columns(4)
    metrics[0].metric("企业", scan.counts.companies)
    metrics[1].metric("采集失败", scan.counts.collection_failed)
    metrics[2].metric("解析失败", scan.counts.document_failed)
    metrics[3].metric("分析失败", scan.counts.analysis_failed)
    st.write(f"任务状态：{scan.status}；阶段：{scan.progress.stage}；进度：{scan.progress.completed}/{scan.progress.total}；公告：{scan.counts.announcements}；商机：{scan.counts.opportunities}")
    if scan.status not in (ScanStatus.COMPLETED, ScanStatus.COMPLETED_WITH_ERRORS, ScanStatus.FAILED):
        if st.button("刷新任务状态"):
            st.rerun()
    result_path = Path(orchestrator().root) / "results" / scan_id / "opportunities.json"
    opportunities = []
    if result_path.exists():
        opportunities = [Opportunity.model_validate(item) for item in json.loads(result_path.read_text(encoding="utf-8")).get("items", [])]
    if opportunities:
        categories = sorted({item.category for item in opportunities})
        filters = st.columns(3)
        selected = filters[0].multiselect("按类别筛选", categories, default=categories)
        companies = sorted({item.company_name for item in opportunities})
        chosen_companies = filters[1].multiselect("按企业筛选", companies, default=companies)
        statuses = sorted({item.analysis_status for item in opportunities})
        chosen_statuses = filters[2].multiselect("按分析状态筛选", statuses, default=statuses)
        filtered = [item for item in opportunities if item.category in selected and item.company_name in chosen_companies and item.analysis_status in chosen_statuses]
        filtered.sort(key=lambda item: (item.publish_date, item.company_name, item.opportunity_id), reverse=True)
        rows = [{"企业": item.company_name, "股票代码": item.stock_code, "日期": str(item.publish_date), "类别": item.category, "事件": item.event_summary, "金额": "未披露" if item.amount.value is None else str(item.amount.value), "币种": item.amount.currency, "金额类型": item.amount.type, "银行产品": "、".join(item.bank_products), "分析状态": item.analysis_status, "公告 ID": item.announcement_id, "来源": item.source_url} for item in filtered]
        st.caption(f"筛选后 {len(rows)} 条，共 {len(opportunities)} 条；下载报告包含完整结果。")
        st.dataframe(rows, width='stretch')
        detail_choice = st.selectbox("查看商机证据与跟进建议", [item.opportunity_id for item in filtered], format_func=lambda key: next(f"{item.company_name} · {item.category} · {item.event_summary[:45]}" for item in filtered if item.opportunity_id == key)) if filtered else None
        for item in [item for item in filtered if item.opportunity_id == detail_choice]:
            with st.expander(f"{item.company_name} · {item.category} · {item.opportunity_id}"):
                st.write(item.announcement_title)
                st.write("营销方向：", item.marketing_direction or "待分析")
                st.write("跟进建议：", item.suggested_action or "待补充")
                for evidence in item.evidence:
                    st.text(evidence.text + (f"（第 {evidence.source_page} 页）" if evidence.source_page else ""))
                st.write("待核实：", "；".join(item.pending_verification) or "无")
                if item.source_url.startswith(("https://", "http://")):
                    st.link_button("查看公告原文", item.source_url)
    else:
        if scan.status not in (ScanStatus.COMPLETED, ScanStatus.COMPLETED_WITH_ERRORS, ScanStatus.FAILED):
            st.info("任务处理中，结果尚未就绪。")
        elif scan.status == ScanStatus.FAILED:
            st.error("任务失败，请查看下方错误信息。")
        elif st.session_state.get('scan_mode', 'mock') == 'mock':
            st.info('演示分析未生成商机；不能据此判断真实公告是否包含商机。')
        elif scan.errors:
            st.warning('当前没有商机结果，但任务存在异常，不能判断为无商机。')
        else:
            st.info('本次分析未识别到商机。')
    report_dir = Path(orchestrator().root) / "reports" / scan_id
    reports_ready = scan.status in (ScanStatus.COMPLETED, ScanStatus.COMPLETED_WITH_ERRORS)
    if reports_ready and (report_dir / "report.xlsx").exists():
        st.download_button("下载 XLSX", (report_dir / "report.xlsx").read_bytes(), file_name=f"{scan_id}.xlsx")
    if reports_ready and (report_dir / "report.html").exists():
        st.download_button("下载 HTML", (report_dir / "report.html").read_bytes(), file_name=f"{scan_id}.html")
    if scan.errors:
        st.warning("任务包含异常")
        st.dataframe([{'错误码': error.code, '范围': error.scope, '公告 ID': error.announcement_id, '说明': error.message, '阶段': error.details.get('stage', ''), '错误类型': error.details.get('error_type', ''), '建议': error.details.get('action', '可从检查点恢复或重试失败项。')} for error in scan.errors], width='stretch', hide_index=True)
        for index, error in enumerate(scan.errors):
            with st.expander(f"异常 {index + 1} · {error.code}"):
                st.json(error.model_dump())
