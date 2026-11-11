from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

HEADERS = ["opportunity_id","announcement_id","stock_code","company_name","announcement_title","publish_date","category","subcategory","event_summary","amount_value","currency","amount_type","amount_undisclosed","bank_products","marketing_direction","suggested_action","evidence","pending_verification","source_url"]

def export_xlsx(opportunities, scan, path: Path):
    wb = Workbook(); ws = wb.active; ws.title = "商机明细"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}1"
    for o in opportunities:
        d = o.model_dump(); amount = d["amount"]
        ws.append([d.get("opportunity_id"), d.get("announcement_id"), d.get("stock_code"), d.get("company_name"), d.get("announcement_title"), str(d.get("publish_date")), d.get("category"), d.get("subcategory"), d.get("event_summary"), amount.get("value"), amount.get("currency"), amount.get("type"), amount.get("undisclosed", False), ", ".join(d.get("bank_products") or []), d.get("marketing_direction"), d.get("suggested_action"), "\n".join(x["text"] for x in d.get("evidence") or []), "\n".join(d.get("pending_verification") or []), d.get("source_url")])
    for row in ws.iter_rows(min_row=2):
        for cell in row: cell.alignment = Alignment(vertical="top", wrap_text=True)
    for index, width in {1:24, 2:22, 3:12, 4:18, 5:32, 6:14, 7:14, 8:16, 9:42, 10:16, 11:10, 12:18, 13:16, 14:28, 15:32, 16:32, 17:48, 18:36, 19:48}.items(): ws.column_dimensions[get_column_letter(index)].width = width
    summary = wb.create_sheet("扫描概览")
    summary.append(["字段", "值"])
    summary.append(["扫描 ID", scan.scan_id]); summary.append(["状态", str(scan.status)])
    summary.append(["企业数量", scan.counts.companies]); summary.append(["公告数量", scan.counts.announcements]); summary.append(["商机数量", scan.counts.opportunities]); summary.append(["采集失败", scan.counts.collection_failed]); summary.append(["正文解析失败", scan.counts.document_failed]); summary.append(["分析失败", scan.counts.analysis_failed])
    errors = wb.create_sheet("异常明细") if scan.errors else None
    if errors is not None:
        errors.append(["scope", "code", "announcement_id", "message"])
        for error in scan.errors:
            errors.append([error.scope, error.code, error.announcement_id, error.message])
        errors.freeze_panes = "A2"
        errors.auto_filter.ref = errors.dimensions
        errors.column_dimensions["D"].width = 60
    for sheet in wb:
        for row in sheet:
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    cell.data_type = "s"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    wb.save(temp_path); temp_path.replace(path)
    return path
