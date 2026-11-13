import math
import os
from pathlib import Path

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

MODE_LABELS = {"mock": "演示模式", "live": "真实模型分析", "unknown": "模式未知"}
MODE_HELP = {
    "mock": "演示模式只验证流程：Mock Agent 不识别真实商机，报告为空属正常。",
    "live": "真实模型分析会把公告正文发送到已配置的模型服务，并产生调用费用。",
}
STATUS_LABELS = {
    "queued": "排队中",
    "collecting": "检索公告",
    "awaiting_review": "等待确认",
    "analyzing": "分析中",
    "exporting": "导出报告",
    "completed": "已完成",
    "completed_with_errors": "完成（含失败项）",
    "failed": "失败",
}
STATUS_COLORS = {
    "queued": "gray",
    "collecting": "blue",
    "awaiting_review": "orange",
    "analyzing": "blue",
    "exporting": "blue",
    "completed": "green",
    "completed_with_errors": "orange",
    "failed": "red",
}
STAGE_LABELS = {
    "queued": "排队",
    "collecting": "采集公告",
    "collection_completed": "采集完成",
    "awaiting_review": "等待确认",
    "documents": "下载与解析正文",
    "agents": "公告分析",
    "analysis_completed": "分析完成",
    "exporting": "生成报告",
    "completed": "完成",
}
TERMINAL = {ScanStatus.COMPLETED.value, ScanStatus.COMPLETED_WITH_ERRORS.value}
ALL_CATEGORY = "全部类别"
ALL_STATUS = "全部状态"


@st.cache_resource(show_spinner=False)
def app_service(fingerprint: tuple) -> AppService:
    """Build the long-lived application service.

    ``fingerprint`` covers the backend modules. A deploy that only touches
    ``core/`` still re-runs this script, and without a changing cache key the
    cached instance would keep classes imported before the update.
    """
    orchestrator = ScanOrchestrator(CninfoCollector())
    return AppService(orchestrator, TaskManager(orchestrator))


def backend_fingerprint() -> tuple:
    core_dir = Path(__file__).resolve().parent / "core"
    stamps = []
    for name in (
        "app_service.py",
        "scanner.py",
        "task_manager.py",
        "runtime.py",
        "storage.py",
        "batch.py",
        "models.py",
    ):
        try:
            stat = (core_dir / name).stat()
            stamps.append((name, stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamps.append((name, 0, 0))
    return tuple(stamps)


def status_value(scan) -> str:
    return getattr(scan.status, "value", str(scan.status))


def set_flash(message: str, kind: str = "success") -> None:
    st.session_state["_flash"] = (kind, message)


def show_flash() -> None:
    item = st.session_state.pop("_flash", None)
    if item:
        getattr(st, item[0])(item[1])


def render_pager(state_key: str, page: int, total_pages: int, summary: str) -> int:
    """Prev/next pager. Buttons write the next page back into session state."""
    total_pages = max(1, int(total_pages))
    page = min(max(int(page), 1), total_pages)
    st.session_state[state_key] = page
    if total_pages <= 1:
        st.caption(summary)
        return page
    left, middle, right = st.columns([1, 3, 1], vertical_alignment="center")
    if left.button("← 上一页", key=f"{state_key}_prev", disabled=page <= 1, width="stretch"):
        st.session_state[state_key] = page - 1
    middle.markdown(
        f"<div style='text-align:center;color:#475569;font-size:.85rem'>"
        f"{summary} · 第 {page} / {total_pages} 页</div>",
        unsafe_allow_html=True,
    )
    if right.button("下一页 →", key=f"{state_key}_next", disabled=page >= total_pages, width="stretch"):
        st.session_state[state_key] = page + 1
    return page


def format_amount(item) -> str:
    if item.amount.value is None:
        return "未披露"
    value = item.amount.value
    if isinstance(value, float) and value.is_integer():
        text = f"{int(value):,}"
    else:
        text = f"{value:,}"
    currency = f" {item.amount.currency}" if item.amount.currency else ""
    return f"{text}{currency}"


def render_announcements(scan_id: str, total: int, expanded: bool) -> None:
    with st.expander(f"公告预览（{total} 条）", expanded=expanded):
        keyword = st.text_input(
            "筛选公告",
            key=f"ann_query_{scan_id}",
            placeholder="输入企业名、股票代码或标题关键词",
            label_visibility="collapsed",
        )
        page_key = f"ann_page_{scan_id}"
        seen_key = f"ann_query_seen_{scan_id}"
        if st.session_state.get(seen_key) != keyword:
            st.session_state[seen_key] = keyword
            st.session_state[page_key] = 1
        try:
            stamp = service.data_stamp(scan_id, "announcements.json")
            page_size = 25
            meta = cached_announcements(scan_id, keyword, 0, 0, stamp)
            max_page = max(1, math.ceil(meta.total / page_size))
            page = min(max(int(st.session_state.get(page_key, 1)), 1), max_page)
            page_data = cached_announcements(
                scan_id, keyword, page_size, (page - 1) * page_size, stamp
            )
            if page_data.items:
                st.dataframe(
                    [
                        {
                            "企业": item.company_name,
                            "标题": item.title,
                            "日期": str(item.publish_date),
                            "原文": item.source_url,
                        }
                        for item in page_data.items
                    ],
                    hide_index=True,
                    width="stretch",
                    column_config={"原文": st.column_config.LinkColumn("原文", display_text="打开")},
                )
            elif keyword:
                st.info("没有匹配该关键词的公告。")
            else:
                st.info("暂无公告。")
            render_pager(page_key, page, max_page, f"共 {meta.total} 条公告")
        except DomainError as exc:
            st.error(f"{exc.message}（{exc.code}）")


def render_opportunity_detail(item) -> None:
    with st.container(border=True):
        st.markdown(f"**{item.company_name}** · {item.category} · {item.analysis_status}")
        st.caption(item.announcement_title)
        st.markdown(f"**事件**：{item.event_summary}")
        amount = format_amount(item)
        if item.amount.value is not None and item.amount.type:
            amount = f"{amount}（{item.amount.type}）"
        st.markdown(f"**金额**：{amount}")
        st.markdown(f"**银行产品**：{'、'.join(item.bank_products) or '—'}")
        st.markdown(f"**营销方向**：{item.marketing_direction or '待分析'}")
        st.markdown(f"**跟进建议**：{item.suggested_action or '待补充'}")
        if item.evidence:
            st.markdown("**原文证据**")
            for evidence in item.evidence:
                suffix = f"（第 {evidence.source_page} 页）" if evidence.source_page else ""
                st.write(f"· {evidence.text}{suffix}")
        st.markdown(f"**待核实**：{'；'.join(item.pending_verification) or '无'}")
        st.caption(f"公告 ID：{item.announcement_id} · 商机 ID：{item.opportunity_id}")
        if item.source_url.startswith(("https://", "http://")):
            st.link_button("查看公告原文", item.source_url)


def render_opportunities(scan_id: str, scan) -> None:
    try:
        stamp = service.data_stamp(scan_id, "opportunities.json")
        facets = cached_opportunities(scan_id, None, None, "", 0, 0, stamp)
    except DomainError as exc:
        st.error(f"{exc.message}（{exc.code}）")
        return
    if not facets.total:
        render_empty_opportunities(scan)
        return

    filters = st.columns([3, 2, 2], vertical_alignment="bottom")
    keyword = filters[0].text_input(
        "搜索",
        key=f"opp_query_{scan_id}",
        placeholder="搜索企业、股票代码或事件关键词",
        label_visibility="collapsed",
    )
    category = filters[1].selectbox(
        "类别",
        [ALL_CATEGORY] + facets.categories,
        key=f"opp_category_{scan_id}",
        label_visibility="collapsed",
    )
    analysis_status = filters[2].selectbox(
        "分析状态",
        [ALL_STATUS] + facets.statuses,
        key=f"opp_status_{scan_id}",
        label_visibility="collapsed",
    )
    chosen_category = None if category == ALL_CATEGORY else category
    chosen_status = None if analysis_status == ALL_STATUS else analysis_status

    signature = (keyword, category, analysis_status)
    signature_key = f"opp_signature_{scan_id}"
    page_key = f"opp_page_{scan_id}"
    if st.session_state.get(signature_key) != signature:
        st.session_state[signature_key] = signature
        st.session_state[page_key] = 1

    try:
        page_size = 25
        summary = cached_opportunities(
            scan_id, chosen_category, chosen_status, keyword, 0, 0, stamp
        )
        max_page = max(1, math.ceil(summary.total / page_size))
        page = min(max(int(st.session_state.get(page_key, 1)), 1), max_page)
        page_data = cached_opportunities(
            scan_id,
            chosen_category,
            chosen_status,
            keyword,
            page_size,
            (page - 1) * page_size,
            stamp,
        )
    except DomainError as exc:
        st.error(f"{exc.message}（{exc.code}）")
        return

    if not page_data.items:
        st.info("当前筛选条件下没有商机。")
        render_pager(page_key, page, max_page, f"共 {summary.total} 条匹配商机")
        return

    rows = [
        {
            "企业": item.company_name,
            "日期": str(item.publish_date),
            "类别": item.category,
            "事件": item.event_summary,
            "金额": format_amount(item),
            "状态": item.analysis_status,
        }
        for item in page_data.items
    ]
    event = st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="single-row",
        key=f"opp_table_{scan_id}_{abs(hash(signature))}_{page}",
    )
    st.caption("点击任意一行查看证据、营销方向与跟进建议。")
    selected_rows = event.selection.rows if event and getattr(event, "selection", None) else []
    if selected_rows and 0 <= selected_rows[0] < len(page_data.items):
        render_opportunity_detail(page_data.items[selected_rows[0]])
    render_pager(
        page_key,
        page,
        max_page,
        f"筛选后 {summary.total} 条 · 全部 {facets.total} 条",
    )


def render_empty_opportunities(scan) -> None:
    status = status_value(scan)
    if scan.analysis_mode == "mock" and status in TERMINAL:
        st.info("演示模式未生成商机；这不代表真实公告中没有商机。")
    elif status not in TERMINAL and status != "failed":
        st.info("任务处理中，结果尚未就绪。")
    elif status == "failed":
        st.error("任务失败，请查看下方异常信息。")
    elif scan.errors:
        st.warning("当前没有商机结果，但任务存在异常，不能判断为无商机。")
    else:
        st.info("本次分析未识别到商机。可以扩大企业或日期范围后重新扫描。")


def render_budget_inputs(scan_id: str, scan, allow_save: bool, expanded: bool = False):
    with st.expander("分析时间预算", expanded=expanded):
        columns = st.columns(2)
        per_minutes = columns[0].number_input(
            "单篇公告预算（分钟）",
            min_value=1,
            value=max(1, scan.announcement_timeout // 60),
            step=1,
            key=f"per_budget_{scan_id}",
        )
        batch_hours = columns[1].number_input(
            "整批分析预算（小时）",
            min_value=1,
            value=max(1, scan.analysis_timeout // 3600),
            step=1,
            key=f"batch_budget_{scan_id}",
        )
        st.caption("预算控制单篇与整批分析的超时；模型请求本身另有 HTTP 超时与重试规则。")
        if allow_save and st.button("保存预算", key=f"save_budget_{scan_id}"):
            try:
                service.save_budgets(scan_id, int(per_minutes), int(batch_hours))
                st.success("预算已保存，后续分析及重试生效。")
            except DomainError as exc:
                st.error(f"{exc.message}（{exc.code}）")
    return int(per_minutes), int(batch_hours)


def render_reports(scan_id: str, view) -> None:
    if not view.capabilities.can_download:
        return
    columns = st.columns(2)
    if view.reports.get("xlsx"):
        try:
            columns[0].download_button(
                "下载 XLSX",
                cached_report(scan_id, "xlsx", service.report_stamp(scan_id, "xlsx")),
                file_name=f"{scan_id}.xlsx",
                key=f"download_xlsx_{scan_id}",
                width="stretch",
            )
        except DomainError as exc:
            columns[0].error(f"{exc.message}（{exc.code}）")
    if view.reports.get("html"):
        try:
            columns[1].download_button(
                "下载 HTML",
                cached_report(scan_id, "html", service.report_stamp(scan_id, "html")),
                file_name=f"{scan_id}.html",
                key=f"download_html_{scan_id}",
                width="stretch",
            )
        except DomainError as exc:
            columns[1].error(f"{exc.message}（{exc.code}）")


def render_errors(scan) -> None:
    if not scan.errors:
        return
    failures = (
        scan.counts.collection_failed
        + scan.counts.document_failed
        + scan.counts.analysis_failed
    )
    with st.expander(f"异常明细（{len(scan.errors)} 条，失败 {failures} 项）", expanded=False):
        st.dataframe(
            [
                {
                    "错误码": error.code,
                    "范围": error.scope,
                    "公告 ID": error.announcement_id,
                    "说明": error.message,
                    "阶段": STAGE_LABELS.get(error.details.get("stage", ""), error.details.get("stage", "")),
                    "错误类型": error.details.get("error_type", ""),
                    "建议": error.details.get("action", "可从检查点恢复或重试失败项。"),
                }
                for error in scan.errors
            ],
            width="stretch",
            hide_index=True,
        )
        st.json([error.model_dump() for error in scan.errors])


def render_retry_actions(scan_id: str, scan, view) -> None:
    if not view.capabilities.can_retry_sources and not view.capabilities.can_retry_failed:
        return
    columns = st.columns(2)
    if scan.counts.collection_failed or scan.counts.document_failed:
        if view.capabilities.can_retry_sources:
            with columns[0]:
                st.caption("复用成功检查点，只重试失败的采集与正文解析。")
                if st.button("重试采集与解析失败项", key=f"retry_sources_{scan_id}", width="stretch"):
                    try:
                        service.retry_sources(scan_id)
                        set_flash("采集与解析重试已提交。")
                        st.rerun(scope="app")
                    except DomainError as exc:
                        st.error(f"{exc.message}（{exc.code}）")
    if view.capabilities.can_retry_failed:
        with columns[1]:
            st.caption("只重试失败公告，复用已完成的分析结果。")
            if st.button("重试失败的公告分析", key=f"retry_failed_{scan_id}", width="stretch"):
                try:
                    service.retry_failed_analysis(scan_id)
                    set_flash("失败公告分析重试已提交。")
                    st.rerun(scope="app")
                except DomainError as exc:
                    st.error(f"{exc.message}（{exc.code}）")


def render_scan_body(scan_id: str, auto_refresh: bool = False) -> None:
    try:
        view = load_scan_view(scan_id)
    except DomainError as exc:
        st.error(f"{exc.message}（{exc.code}）")
        if auto_refresh:
            # Leave the auto-refreshing fragment so the page can offer recovery actions.
            st.rerun(scope="app")
        return
    scan = view.scan
    status = status_value(scan)

    header = st.columns([2, 2, 3, 2], vertical_alignment="center")
    header[0].badge(STATUS_LABELS.get(status, status), color=STATUS_COLORS.get(status, "gray"))
    header[1].badge(
        MODE_LABELS.get(scan.analysis_mode, scan.analysis_mode),
        color="violet" if scan.analysis_mode == "live" else "gray",
    )
    header[2].caption(f"更新于 {scan.updated_at:%Y-%m-%d %H:%M:%S}")
    header[3].caption(f"任务 `{scan_id[-8:]}`")

    if view.capabilities.blocked_by_other_scan:
        st.info("另一个任务正在运行；本任务的确认、重试和恢复入口暂不可用。")
    if scan.analysis_mode == "mock" and status in TERMINAL:
        st.warning("演示模式结果仅用于验证流程，不能用于判断真实商机。需要真实结论请用真实模型重新扫描。")
    if scan.analysis_mode == "unknown":
        st.warning("旧任务没有保存分析模式。恢复前请选择原任务实际使用的模式。")
        legacy_mode = st.selectbox(
            "旧任务分析模式",
            [None, "mock", "live"],
            format_func=lambda value: {None: "请选择", "mock": "演示模式", "live": "真实模型分析"}[value],
            key=f"legacy_mode_{scan_id}",
        )
        if st.button("保存任务模式", disabled=legacy_mode is None, key=f"save_legacy_{scan_id}"):
            try:
                view = service.set_legacy_mode(scan_id, legacy_mode)
                scan = view.scan
                st.success("任务模式已保存。")
            except DomainError as exc:
                st.error(f"{exc.message}（{exc.code}）")

    failures = (
        scan.counts.collection_failed
        + scan.counts.document_failed
        + scan.counts.analysis_failed
    )
    metrics = st.columns(4)
    metrics[0].metric("企业", scan.counts.companies)
    metrics[1].metric("公告", scan.counts.announcements)
    metrics[2].metric("商机", scan.counts.opportunities)
    metrics[3].metric("异常", failures)
    if failures:
        st.caption(
            f"采集失败 {scan.counts.collection_failed} · 正文解析失败 "
            f"{scan.counts.document_failed} · 分析失败 {scan.counts.analysis_failed}"
        )
    if scan.progress.total and status not in TERMINAL:
        stage = STAGE_LABELS.get(scan.progress.stage, scan.progress.stage)
        st.progress(
            min(scan.progress.completed / scan.progress.total, 1.0),
            text=f"{stage} · {scan.progress.completed}/{scan.progress.total}",
        )

    if status == ScanStatus.AWAITING_REVIEW.value:
        st.info("公告检索完成。核对公告后选择分析方式，再确认开始分析。")
        render_announcements(scan_id, view.announcements_total, expanded=True)
        render_retry_actions(scan_id, scan, view)
        with st.container(border=True):
            st.markdown("#### 确认分析")
            mode_options = ["mock", "live"] if configured else ["mock"]
            default_mode = scan.analysis_mode if scan.analysis_mode in mode_options else "mock"
            mode = st.segmented_control(
                "分析方式",
                mode_options,
                default=default_mode,
                format_func=lambda value: MODE_LABELS[value],
                key=f"review_mode_{scan_id}",
                required=True,
            )
            if not configured:
                st.caption("未配置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL，当前只能使用演示模式。")
            st.caption(MODE_HELP.get(mode or "mock"))
            per_minutes, batch_hours = render_budget_inputs(
                scan_id, scan, allow_save=False, expanded=False
            )
            if st.button(
                f"确认并开始分析（{view.announcements_total} 条公告）",
                type="primary",
                key=f"confirm_{scan_id}",
                disabled=not mode,
            ):
                try:
                    service.confirm_analysis(
                        scan_id, per_minutes, batch_hours, mode=mode
                    )
                    set_flash("分析已启动，结果区会自动刷新。")
                    st.rerun(scope="app")
                except DomainError as exc:
                    st.error(f"{exc.message}（{exc.code}）")
        st.caption(f"任务创建于 {scan.created_at:%Y-%m-%d %H:%M}")
        render_errors(scan)
        return

    if view.capabilities.can_resume:
        st.warning("任务当前未运行，可从已保存进度继续；成功检查点会复用，失败分析不会自动重试。")
        if st.button("从检查点继续当前任务", key=f"resume_{scan_id}"):
            try:
                service.resume_scan(scan_id)
                set_flash("任务已从检查点继续。")
                st.rerun(scope="app")
            except DomainError as exc:
                st.error(f"{exc.message}（{exc.code}）")

    render_retry_actions(scan_id, scan, view)

    if view.capabilities.can_save_budget and (
        status not in TERMINAL or scan.counts.analysis_failed
    ):
        render_budget_inputs(scan_id, scan, allow_save=True, expanded=False)

    st.markdown("#### 商机结果")
    render_opportunities(scan_id, scan)
    render_announcements(scan_id, view.announcements_total, expanded=False)

    if status in TERMINAL:
        render_reports(scan_id, view)

    render_errors(scan)

    if auto_refresh and not view.capabilities.is_running:
        st.rerun(scope="app")


def render_scan_section(scan_id: str) -> None:
    try:
        view = load_scan_view(scan_id)
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


service = app_service(backend_fingerprint())
if not hasattr(service.orchestrator, "save"):
    # The process still holds pre-deploy backend classes. Only a restart fixes
    # that, so show an actionable message instead of a redacted traceback.
    st.error("应用刚完成更新，后台模块仍是旧版本。请点击右上角 Manage app → Reboot 重启应用后重试。")
    if st.button("清除缓存并重试", key="reload_stale_backend"):
        app_service.clear()
        st.rerun()
    st.stop()


@st.cache_data(ttl=1, show_spinner=False)
def cached_scan_view(scan_id: str, stamp: tuple[int, int]):
    return service.get_scan_view(scan_id)


def load_scan_view(scan_id: str):
    return cached_scan_view(scan_id, service.scan_stamp(scan_id))


@st.cache_data(ttl=1, show_spinner=False)
def cached_announcements(scan_id: str, query: str, limit: int, offset: int, stamp: tuple[int, int]):
    return service.list_announcements(scan_id, query=query, limit=limit, offset=offset)


@st.cache_data(ttl=1, show_spinner=False)
def cached_opportunities(
    scan_id: str,
    category: str | None,
    analysis_status: str | None,
    query: str,
    limit: int,
    offset: int,
    stamp: tuple[int, int],
):
    return service.list_opportunities(
        scan_id,
        categories=[category] if category else None,
        statuses=[analysis_status] if analysis_status else None,
        query=query,
        limit=limit,
        offset=offset,
    )


@st.cache_data(ttl=5, show_spinner=False)
def cached_history():
    return service.list_history(limit=100)


@st.cache_data(ttl=60, show_spinner=False)
def cached_report(scan_id: str, fmt: str, stamp: tuple[int, int]):
    return service.get_report_bytes(scan_id, fmt)


configured = all(os.getenv(key, "").strip() for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"))

if "scan_id" not in st.session_state:
    st.session_state.scan_id = None

with st.sidebar:
    st.markdown("### 历史任务")
    st.caption("内部单实例：历史任务共享可见，选择后直接载入结果。")
    try:
        history = cached_history()
        by_id = {item.scan_id: item for item in history}
    except DomainError as exc:
        history, by_id = [], {}
        st.error(f"历史任务读取失败：{exc.message}")
    pending = st.session_state.pop("_history_picker_target", None)
    if pending and pending in by_id:
        st.session_state["history_picker"] = pending

    def _switch_history() -> None:
        chosen = st.session_state.get("history_picker")
        if chosen:
            st.session_state.scan_id = chosen

    st.selectbox(
        "选择历史任务",
        [None] + list(by_id),
        format_func=lambda key: "请选择任务" if key is None else (
            f"{by_id[key].created_at:%m-%d %H:%M} · {by_id[key].companies} 家 · "
            f"{STATUS_LABELS.get(by_id[key].status, by_id[key].status)} · "
            f"{by_id[key].scan_id[-6:]}"
        ),
        key="history_picker",
        on_change=_switch_history,
        label_visibility="collapsed",
    )
    if st.session_state.scan_id:
        st.caption(f"当前任务：`{st.session_state.scan_id}`")
    st.divider()
    st.caption(f"模型配置：{'已就绪' if configured else '未配置，仅演示模式'}")

validation = render_company_input()
show_flash()

st.subheader("02 · 确认扫描范围")
default_range = default_date_range()
scope_left, scope_right = st.columns([2, 3])
with scope_left:
    start = st.date_input("开始日期", default_range.start_date)
    end = st.date_input("结束日期", default_range.end_date)
with scope_right:
    mode_options = ["mock", "live"] if configured else ["mock"]
    selected_mode = st.segmented_control(
        "分析方式",
        mode_options,
        default=st.session_state.get("scan_mode_choice", "mock"),
        format_func=lambda value: MODE_LABELS[value],
        key="scan_mode_choice",
        required=True,
        label_visibility="collapsed",
    )
    if not configured:
        st.caption("未配置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL，只能使用演示模式。")
    st.caption(MODE_HELP.get(selected_mode or "mock"))

company_count = len(validation["companies"]) if validation else 0
scan_id = st.session_state.scan_id
view = None
if scan_id:
    try:
        view = load_scan_view(scan_id)
    except DomainError as exc:
        st.error(f"{exc.message}（{exc.code}）")
        st.session_state.scan_id = None
        scan_id = None
        view = None

runtime = service.get_runtime_state()
busy = runtime.active or bool(view and view.capabilities.is_running)
st.caption(
    f"已选 {company_count} 家企业 · {start} 至 {end} · "
    f"{MODE_LABELS.get(selected_mode or 'mock')}。仅对成功解析的公告调用分析。"
)
if end < start:
    st.error("结束日期不能早于开始日期。")
if runtime.active and (scan_id is None or runtime.scan_id != scan_id):
    st.info(f"任务 {runtime.scan_id} 正在运行，请等待其结束后再创建新任务。")
elif runtime.stale:
    st.caption("检测到上次任务锁未正常释放，恢复任务时会自动接管。")
if busy:
    st.info("当前任务尚未结束，请先查看或恢复当前任务，避免重复提交。")

can_start = bool(validation and validation["valid"]) and end >= start and selected_mode and not busy
if st.button("检索公告", type="primary", disabled=not can_start, help=None if can_start else "请先建立名单、确认日期范围并选择分析方式。"):
    try:
        companies = [Company.model_validate(item) for item in validation["companies"]]
        started = service.start_scan(
            companies,
            DateRange(start_date=start, end_date=end),
            selected_mode,
        )
        st.session_state.scan_id = started.scan.scan_id
        st.session_state["_history_picker_target"] = started.scan.scan_id
        cached_history.clear()
        set_flash(f"公告检索已提交：{started.scan.scan_id}，完成后请核对公告并确认分析。")
        st.rerun()
    except DomainError as exc:
        st.error(f"{exc.message}（{exc.code}）")

st.header("03 · 公告预览与分析")
if scan_id:
    render_scan_section(scan_id)
