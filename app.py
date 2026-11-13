import os

import streamlit as st

from collectors.cninfo import CninfoCollector
from core.app_service import AppService
from core.calendar import default_date_range
from core.company_input_ui import render_company_input
from core.errors import DomainError
from core.models import Company, DateRange, ScanStatus
from core.scanner import ScanOrchestrator
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


@st.cache_resource
def app_service() -> AppService:
    orchestrator = ScanOrchestrator(CninfoCollector())
    return AppService(orchestrator, TaskManager(orchestrator))


def set_flash(message: str, kind: str = "success") -> None:
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop("_flash", None)
    if item:
        getattr(st, item[0])(item[1])


def render_scan_body(scan_id: str, auto_refresh: bool = False) -> None:
    view = service.get_scan_view(scan_id)
    scan = view.scan
    if view.capabilities.blocked_by_other_scan:
        st.info("另一个任务正在运行；本任务的确认、重试和恢复入口暂不可用。")
    if scan.analysis_mode == 'unknown':
        st.warning("旧任务没有保存分析模式。恢复前请选择原任务实际使用的模式。")
        legacy_mode = st.selectbox(
            "旧任务分析模式",
            [None, 'mock', 'live'],
            format_func=lambda value: {None: '请选择', 'mock': '演示分析', 'live': '真实模型分析'}[value],
            key=f"legacy_mode_{scan_id}",
        )
        if st.button("保存任务模式", disabled=legacy_mode is None, key=f"save_legacy_{scan_id}"):
            try:
                view = service.set_legacy_mode(scan_id, legacy_mode)
                scan = view.scan
                st.success("任务模式已保存。")
            except DomainError as exc:
                st.error(f"{exc.message}（{exc.code}）")

    with st.expander(f"检索公告（{view.announcements_total} 条）", expanded=scan.status == ScanStatus.AWAITING_REVIEW):
        keyword = st.text_input("筛选公告标题或企业", key=f'announcement_search_{scan_id}')
        try:
            announcement_page = service.list_announcements(scan_id, query=keyword)
        except DomainError as exc:
            st.error(f"{exc.message}（{exc.code}）")
            announcement_page = None
        if announcement_page and announcement_page.items:
            st.dataframe(
                [
                    {
                        '企业': item.company_name,
                        '标题': item.title,
                        '日期': str(item.publish_date),
                        '公告 ID': item.announcement_id,
                        '原文': item.source_url,
                    }
                    for item in announcement_page.items
                ],
                hide_index=True,
                width='stretch',
                column_config={'原文': st.column_config.LinkColumn('原文')},
            )
        elif announcement_page is not None:
            st.info("暂无符合条件的公告。")
    if scan.status == ScanStatus.AWAITING_REVIEW:
        st.info(
            f"公告检索完成，等待确认。分析模式：{scan.analysis_mode}；"
            f"确认后将处理全部 {view.announcements_total} 条公告，标题筛选仅影响预览。"
        )

    idle = not view.capabilities.is_running
    per_minutes = max(1, scan.announcement_timeout // 60)
    batch_hours = max(1, scan.analysis_timeout // 3600)
    if idle and view.capabilities.can_save_budget:
        with st.expander("分析时间预算", expanded=scan.status == ScanStatus.AWAITING_REVIEW):
            per_minutes = st.number_input(
                "单篇公告预算（分钟）",
                min_value=1,
                value=per_minutes,
                step=1,
                key=f'per_budget_{scan_id}',
            )
            batch_hours = st.number_input(
                "整批分析预算（小时）",
                min_value=1,
                value=batch_hours,
                step=1,
                key=f'batch_budget_{scan_id}',
            )
            if st.button("保存时间预算", key=f'save_budget_{scan_id}'):
                try:
                    view = service.save_budgets(scan_id, int(per_minutes), int(batch_hours))
                    scan = view.scan
                    st.success("预算已保存，后续分析及重试生效。模型请求本身仍受 HTTP 超时和重试规则约束。")
                except DomainError as exc:
                    st.error(f"{exc.message}（{exc.code}）")

    if scan.status == ScanStatus.AWAITING_REVIEW and idle and view.capabilities.can_confirm:
        if st.button("确认继续分析全部公告", type='primary', key=f'confirm_{scan_id}'):
            try:
                service.confirm_analysis(scan_id, int(per_minutes), int(batch_hours))
                set_flash("分析已启动。")
                st.rerun(scope="app")
            except DomainError as exc:
                st.error(f"{exc.message}（{exc.code}）")

    if (scan.counts.collection_failed or scan.counts.document_failed) and idle and view.capabilities.can_retry_sources:
        st.caption("重试失败采集和正文解析，复用成功检查点；新增或修复的公告会继续分析。真实模式可能产生模型费用，旧记录与报告会留档。")
        if st.button("重试采集与解析失败项", key=f'retry_sources_{scan_id}'):
            try:
                service.retry_sources(scan_id)
                set_flash("采集与解析重试已提交。")
                st.rerun(scope="app")
            except DomainError as exc:
                st.error(f"{exc.message}（{exc.code}）")

    if view.capabilities.can_resume:
        st.warning("任务当前未运行，可从已保存进度继续。成功采集的企业与已完成的公告分析会复用；失败分析记录暂不自动重试。")
        if st.button("从检查点继续当前任务", key=f'resume_{scan_id}'):
            try:
                service.resume_scan(scan_id)
                set_flash("任务已从检查点继续。")
                st.rerun(scope="app")
            except DomainError as exc:
                st.error(f"{exc.message}（{exc.code}）")

    if scan.counts.analysis_failed and idle and view.capabilities.can_retry_failed:
        st.caption("只重试失败公告，复用已成功分析；旧失败检查点留档。真实模式会再次调用模型并产生费用。")
        if st.button("重试失败的公告分析", key=f'retry_failed_{scan_id}'):
            try:
                service.retry_failed_analysis(scan_id)
                set_flash("失败公告分析重试已提交。")
                st.rerun(scope="app")
            except DomainError as exc:
                st.error(f"{exc.message}（{exc.code}）")

    if scan.status in (ScanStatus.COMPLETED, ScanStatus.COMPLETED_WITH_ERRORS):
        if scan.counts.announcements == 0:
            if scan.counts.collection_failed:
                st.warning("未获取到公告，且部分企业采集失败；请先检查错误，不能判断这些企业没有公告。")
            else:
                st.info("所选企业在当前日期范围内未采集到公告。可以增加企业或扩大日期范围后重新扫描。")
        elif scan.counts.opportunities == 0:
            if scan.errors:
                st.warning("已采集到公告，但存在处理失败；当前零商机不能代表所有公告均无商机。")
            elif scan.analysis_mode == 'live':
                st.info("公告分析已完成，未识别到符合条件的商机。可以扩大企业或日期范围。")
    st.caption(f"任务 ID：{scan_id} · 更新于 {scan.updated_at:%Y-%m-%d %H:%M:%S}")
    if scan.progress.total:
        st.progress(min(scan.progress.completed / scan.progress.total, 1.0))
    metrics = st.columns(4)
    metrics[0].metric("企业", scan.counts.companies)
    metrics[1].metric("采集失败", scan.counts.collection_failed)
    metrics[2].metric("解析失败", scan.counts.document_failed)
    metrics[3].metric("分析失败", scan.counts.analysis_failed)
    st.write(
        f"任务状态：{scan.status}；阶段：{scan.progress.stage}；"
        f"进度：{scan.progress.completed}/{scan.progress.total}；"
        f"公告：{scan.counts.announcements}；商机：{scan.counts.opportunities}"
    )

    try:
        opportunity_page = service.list_opportunities(scan_id)
    except DomainError as exc:
        st.error(f"{exc.message}（{exc.code}）")
        opportunity_page = None
    opportunities = opportunity_page.items if opportunity_page else []
    if opportunities:
        filters = st.columns(3)
        selected = filters[0].multiselect("按类别筛选", opportunity_page.categories, default=opportunity_page.categories)
        chosen_companies = filters[1].multiselect("按企业筛选", opportunity_page.companies, default=opportunity_page.companies)
        chosen_statuses = filters[2].multiselect("按分析状态筛选", opportunity_page.statuses, default=opportunity_page.statuses)
        filtered = [
            item
            for item in opportunities
            if item.category in selected
            and item.company_name in chosen_companies
            and item.analysis_status in chosen_statuses
        ]
        filtered.sort(key=lambda item: (item.publish_date, item.company_name, item.opportunity_id), reverse=True)
        rows = [
            {
                "企业": item.company_name,
                "股票代码": item.stock_code,
                "日期": str(item.publish_date),
                "类别": item.category,
                "事件": item.event_summary,
                "金额": "未披露" if item.amount.value is None else str(item.amount.value),
                "币种": item.amount.currency,
                "金额类型": item.amount.type,
                "银行产品": "、".join(item.bank_products),
                "分析状态": item.analysis_status,
                "公告 ID": item.announcement_id,
                "来源": item.source_url,
            }
            for item in filtered
        ]
        st.caption(f"筛选后 {len(rows)} 条，共 {len(opportunities)} 条；下载报告包含完整结果。")
        st.dataframe(rows, width='stretch')
        detail_choice = st.selectbox(
            "查看商机证据与跟进建议",
            [item.opportunity_id for item in filtered],
            format_func=lambda key: next(
                f"{item.company_name} · {item.category} · {item.event_summary[:45]}"
                for item in filtered
                if item.opportunity_id == key
            ),
            key=f'detail_{scan_id}',
        ) if filtered else None
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
        elif scan.analysis_mode == 'mock':
            st.info('演示分析未生成商机；不能据此判断真实公告是否包含商机。')
        elif scan.errors:
            st.warning('当前没有商机结果，但任务存在异常，不能判断为无商机。')
        else:
            st.info('本次分析未识别到商机。')

    if view.capabilities.can_download and view.reports.get('xlsx'):
        try:
            st.download_button(
                "下载 XLSX",
                service.get_report_bytes(scan_id, 'xlsx'),
                file_name=f"{scan_id}.xlsx",
                key=f'download_xlsx_{scan_id}',
            )
        except DomainError as exc:
            st.error(f"{exc.message}（{exc.code}）")
    if view.capabilities.can_download and view.reports.get('html'):
        try:
            st.download_button(
                "下载 HTML",
                service.get_report_bytes(scan_id, 'html'),
                file_name=f"{scan_id}.html",
                key=f'download_html_{scan_id}',
            )
        except DomainError as exc:
            st.error(f"{exc.message}（{exc.code}）")

    if scan.errors:
        st.warning("任务包含异常")
        st.dataframe(
            [
                {
                    '错误码': error.code,
                    '范围': error.scope,
                    '公告 ID': error.announcement_id,
                    '说明': error.message,
                    '阶段': error.details.get('stage', ''),
                    '错误类型': error.details.get('error_type', ''),
                    '建议': error.details.get('action', '可从检查点恢复或重试失败项。'),
                }
                for error in scan.errors
            ],
            width='stretch',
            hide_index=True,
        )
        for index, error in enumerate(scan.errors):
            with st.expander(f"异常 {index + 1} · {error.code}"):
                st.json(error.model_dump())

    if auto_refresh and not view.capabilities.is_running:
        st.rerun(scope="app")


def render_scan_section(scan_id: str) -> None:
    try:
        view = service.get_scan_view(scan_id)
    except DomainError as exc:
        st.error(f"{exc.message}（{exc.code}）")
        if st.button("清除无效任务", key="clear_invalid_scan"):
            st.session_state.scan_id = None
            st.rerun()
        return
    if view.capabilities.is_running:
        live_scan(scan_id)
    else:
        render_scan_body(scan_id, auto_refresh=False)


@st.fragment(run_every="2s")
def live_scan(scan_id: str) -> None:
    render_scan_body(scan_id, auto_refresh=True)


service = app_service()
configured = all(os.getenv(key, '').strip() for key in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL'))

with st.sidebar:
    st.markdown("### 扫描设置")
    st.caption("先校验名单，再启动扫描。任务运行中结果区会自动刷新。")
    use_live = st.checkbox('使用真实 LLM 分析', value=False, disabled=not configured)
if use_live:
    st.info('本次启动将把公开公告正文发送到配置的模型服务进行分析。')
else:
    st.warning('演示模式使用 Mock Agent，空结果不代表没有商机。')
if not configured:
    st.caption('请设置 LLM_API_KEY、LLM_BASE_URL 和 LLM_MODEL 环境变量后重启页面。')

if "scan_id" not in st.session_state:
    st.session_state.scan_id = None

with st.sidebar:
    st.divider()
    st.subheader("历史任务")
    try:
        history = service.list_history(limit=100)
        by_id = {item.scan_id: item for item in history}
    except DomainError as exc:
        history, by_id = [], {}
        st.error(f"历史任务读取失败：{exc.message}")
    history_id = st.selectbox(
        "选择历史任务",
        [None] + list(by_id),
        format_func=lambda key: "请选择任务" if key is None else (
            f"{by_id[key].created_at:%m-%d %H:%M} · {by_id[key].companies} 家 · "
            f"{by_id[key].status} · {key}"
        ),
    )
    if st.button("查看该任务", disabled=history_id is None):
        st.session_state.scan_id = history_id
        st.rerun()

validation = render_company_input()
show_flash()

st.subheader("02 · 确认扫描范围")
default_range = default_date_range()
col1, col2 = st.columns(2)
with col1:
    start = st.date_input("开始日期", default_range.start_date)
with col2:
    end = st.date_input("结束日期", default_range.end_date)

company_count = len(validation["companies"]) if validation else 0
scan_id = st.session_state.scan_id
view = None
if scan_id:
    try:
        view = service.get_scan_view(scan_id)
    except DomainError as exc:
        st.error(f"{exc.message}（{exc.code}）")
        st.session_state.scan_id = None
        scan_id = None
        view = None

runtime = service.get_runtime_state()
busy = runtime.active or bool(view and view.capabilities.is_running)
st.caption(f"本次扫描 {company_count} 家企业，日期范围 {start} 至 {end}。仅对采集并成功解析的公告调用分析，无公告企业不调用模型。")
st.caption("先检索公告，确认后才下载正文并分析；本次检索不会调用模型。")
if end < start:
    st.error("结束日期不能早于开始日期。")
if runtime.active and (scan_id is None or runtime.scan_id != scan_id):
    st.info(f"任务 {runtime.scan_id} 正在运行，请等待其结束后再创建新任务。")
elif runtime.stale:
    st.caption("检测到上次任务锁未正常释放，恢复任务时会自动接管。")
if busy:
    st.info("当前任务尚未结束，请先查看或恢复当前任务，避免重复提交。")

if st.button("检索公告", type="primary", disabled=busy or end < start or not (validation and validation["valid"])):
    try:
        companies = [Company.model_validate(item) for item in validation["companies"]]
        started = service.start_scan(
            companies,
            DateRange(start_date=start, end_date=end),
            'live' if use_live else 'mock',
        )
        st.session_state.scan_id = started.scan.scan_id
        set_flash(f"公告检索已提交：{started.scan.scan_id}，完成后请先预览公告并确认分析。")
        st.rerun()
    except DomainError as exc:
        st.error(f"{exc.message}（{exc.code}）")

st.header("03 · 公告预览与分析")
if scan_id:
    render_scan_section(scan_id)
