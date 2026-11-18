from pathlib import Path
from uuid import uuid4

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from core.url_safety import safe_https_url


HEADERS = [
    "商机编号",
    "公告编号",
    "股票代码",
    "企业名称",
    "公告标题",
    "披露日期",
    "商机大类",
    "商机细分",
    "事件摘要",
    "核心金额数值",
    "币种",
    "金额类型",
    "金额是否未披露",
    "金额核验状态",
    "切入点判断",
    "是否商机",
    "分析结论有分歧",
    "分析结论",
    "潜在银行产品",
    "营销方向",
    "建议跟进动作",
    "原文证据",
    "待核实事项",
    "公告原文链接",
    "交易对手",
    "项目名称",
    "其他披露金额",
    "可能关联公告编号",
    "分析处理状态",
    "分析异常",
]

_ANALYSIS_OUTCOMES = {
    "opportunity_identified": "已识别切入点",
    "no_clear_entry_point": "暂未识别明确切入点",
}
_IS_OPPORTUNITY = {
    "opportunity_identified": "是",
    "no_clear_entry_point": "否",
}
_ANALYSIS_STATUSES = {
    "completed": "完成",
    "partial": "部分完成",
    "failed": "失败",
}


def _amount_text(amount: dict) -> str:
    parts = []
    if amount.get("type"):
        parts.append(str(amount["type"]))
    if amount.get("value") is not None:
        currency = amount.get("currency") or "币种未明确"
        parts.append(f"{amount['value']} {currency}")
    elif amount.get("undisclosed"):
        parts.append("未披露")
    elif amount.get("original_text"):
        parts.append(str(amount["original_text"]))
    if amount.get("verification_status") == "pending":
        parts.append("待核实")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}：{' '.join(parts[1:])}"


def _row(opportunity) -> list:
    data = opportunity.model_dump()
    amount = data.get("amount") or {}
    currency = amount.get("currency") or (
        "币种未明确" if amount.get("value") is not None else ""
    )
    verification = {
        "verified": "数值已与原文核对",
        "pending": "待核实",
    }.get(
        amount.get("verification_status"),
        "未核验" if amount.get("value") is not None else "",
    )
    evidence = "\n".join(
        f"第{item['source_page']}页：{item['text']}"
        if item.get("source_page")
        else item["text"]
        for item in data.get("evidence") or []
    )
    other_amounts = "\n".join(
        _amount_text(item) for item in data.get("other_amounts") or []
    )
    return [
        data.get("opportunity_id"),
        data.get("announcement_id"),
        data.get("stock_code"),
        data.get("company_name"),
        data.get("announcement_title"),
        str(data.get("publish_date")),
        data.get("category"),
        data.get("subcategory") or "未细分",
        data.get("event_summary"),
        amount.get("value"),
        currency,
        amount.get("type"),
        (
            "是"
            if amount.get("undisclosed")
            else "否" if amount.get("value") is not None else "未确认"
        ),
        verification,
        _ANALYSIS_OUTCOMES.get(data.get("analysis_outcome"), ""),
        _IS_OPPORTUNITY.get(data.get("analysis_outcome"), "待判定"),
        "是" if data.get("analysis_conflict") else "否",
        data.get("analysis_summary"),
        "、".join(data.get("bank_products") or []),
        data.get("marketing_direction"),
        data.get("suggested_action"),
        evidence,
        "\n".join(data.get("pending_verification") or []),
        safe_https_url(data.get("source_url")) or "",
        data.get("counterparty"),
        data.get("project_name"),
        other_amounts,
        "、".join(data.get("related_announcement_ids") or []),
        _ANALYSIS_STATUSES.get(data.get("analysis_status"), data.get("analysis_status")),
        data.get("analysis_error"),
    ]


def _style_detail_sheet(ws, opportunities) -> None:
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    ws.freeze_panes = "A2"
    for opportunity in opportunities:
        ws.append(_row(opportunity))
    if opportunities:
        ws.auto_filter.ref = ws.dimensions
    else:
        ws.merge_cells(
            start_row=2,
            start_column=1,
            end_row=2,
            end_column=len(HEADERS),
        )
        message = ws.cell(
            row=2,
            column=1,
            value=(
                "本次没有可展示的分析记录；请结合“扫描概览”和“异常明细”，"
                "确认是未识别到商机、无可分析事项还是处理失败。"
            ),
        )
        message.alignment = Alignment(vertical="center", wrap_text=True)
        message.font = Font(color="666666", italic=True)
        ws.row_dimensions[2].height = 32
        ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}1"
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"
    widths = {
        1: 24, 2: 24, 3: 12, 4: 18, 5: 36, 6: 14, 7: 16, 8: 18,
        9: 44, 10: 16, 11: 16, 12: 20, 13: 18, 14: 22, 15: 22, 16: 14,
        17: 14, 18: 48, 19: 30, 20: 36, 21: 36, 22: 52, 23: 44, 24: 52,
        25: 24, 26: 28, 27: 48, 28: 28, 29: 18, 30: 36,
    }
    for index, width in widths.items():
        ws.column_dimensions[get_column_letter(index)].width = width


def export_xlsx(
    opportunities,
    scan,
    path: Path,
    lease_guard=None,
    non_opportunities=None,
):
    if lease_guard is not None:
        lease_guard.assert_owned()
    all_items = list(opportunities or []) + list(non_opportunities or [])
    opportunities = list(
        {item.opportunity_id: item for item in all_items}.values()
    )
    wb = Workbook()
    ws = wb.active
    ws.title = "商机明细"
    _style_detail_sheet(ws, opportunities)

    summary = wb.create_sheet("扫描概览")
    summary.append(["字段", "值"])
    for cell in summary[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    summary_rows = [
        ["扫描 ID", scan.scan_id],
        ["状态", str(scan.status)],
        ["扫描开始日期", str(scan.date_range.start_date)],
        ["扫描结束日期", str(scan.date_range.end_date)],
        ["企业数量", scan.counts.companies],
        ["公告数量", scan.counts.announcements],
        ["商机数量", scan.counts.opportunities],
        ["未形成商机事项数", scan.counts.non_opportunities],
        ["采集失败", scan.counts.collection_failed],
        ["正文解析失败", scan.counts.document_failed],
        ["分析失败", scan.counts.analysis_failed],
    ]
    for row in summary_rows:
        summary.append(row)
    summary.freeze_panes = "A2"
    summary.auto_filter.ref = summary.dimensions
    summary.column_dimensions["A"].width = 24
    summary.column_dimensions["B"].width = 48

    if scan.errors:
        errors = wb.create_sheet("异常明细")
        errors.append(["范围", "错误代码", "公告编号", "错误信息"])
        for cell in errors[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
        for error in scan.errors:
            errors.append(
                [error.scope, error.code, error.announcement_id, error.message]
            )
        errors.freeze_panes = "A2"
        errors.auto_filter.ref = errors.dimensions
        errors.column_dimensions["A"].width = 18
        errors.column_dimensions["B"].width = 30
        errors.column_dimensions["C"].width = 28
        errors.column_dimensions["D"].width = 60

    for sheet in wb:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    cell.data_type = "s"

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        if lease_guard is not None:
            lease_guard.assert_owned()
        wb.save(temp_path)
        if lease_guard is not None:
            with lease_guard.protect_write():
                temp_path.replace(path)
        else:
            temp_path.replace(path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return path
