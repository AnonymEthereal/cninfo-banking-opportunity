"""Excel import preview with explicit identity resolution."""
from io import BytesIO
import re
from openpyxl import Workbook, load_workbook
from .company_directory import search_companies


def company_template() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "企业名单"
    sheet.append(["股票代码", "公司名称"])
    sheet.append(["000001", "平安银行"])
    sheet.append(["600036", None])
    sheet.append([None, "比亚迪"])
    sheet.column_dimensions["A"].width = 18
    sheet.column_dimensions["B"].width = 26
    sheet.freeze_panes = "A2"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def preview_company_import(content: bytes, directory, existing=()) -> dict:
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        rows = list(workbook.active.iter_rows(values_only=True))
    finally:
        workbook.close()
    aliases = {"股票代码": "code", "证券代码": "code", "stock_code": "code", "公司名称": "name", "企业名称": "name", "证券简称": "name", "company_name": "name"}
    headers = [aliases.get(str(value).strip().lower()) for value in rows[0]] if rows else []
    if not any(headers):
        raise ValueError("首行需包含股票代码或公司名称列（也支持 stock_code、company_name）。")
    if headers.count("code") > 1 or headers.count("name") > 1:
        raise ValueError("存在重复的代码列或名称列，请每种字段只保留一列。")
    by_code = {company.stock_code: company for company in directory}
    seen = {item["stock_code"] for item in existing}
    result = []
    for row_number, values in enumerate(rows[1:], 2):
        if all(value is None or str(value).strip() == "" for value in values):
            continue
        def field(kind):
            value = values[headers.index(kind)] if kind in headers else None
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            return str(value).strip() if value is not None else ""
        code, name = field("code"), field("name")
        item = {"row": row_number, "input_code": code, "input_name": name, "status": "error", "message": "", "company": None, "candidates": []}
        company = None
        if code:
            if not re.fullmatch(r"[0-9]{1,6}", code):
                item["message"] = "股票代码须为 1–6 位数字，将自动补足前导零。"
            else:
                company = by_code.get(code.zfill(6))
                if company is None:
                    item["message"] = "当前名录中不存在该代码，请核对或刷新完整名录。"
                elif name and name.casefold() != company.company_name.casefold():
                    item.update(status="pending", message="名称与代码不一致，请确认企业身份。", candidates=[company.model_dump()] + [c.model_dump() for c in search_companies(name, directory, 10) if c.stock_code != company.stock_code])
                    company = None
        elif name:
            exact = [c for c in directory if c.company_name.casefold() == name.casefold()]
            if len(exact) == 1:
                company = exact[0]
            else:
                matches = search_companies(name, directory, 10)
                item.update(status="pending" if matches else "error", message="请选择匹配企业。" if matches else "未找到匹配企业，请修改名称。", candidates=[c.model_dump() for c in matches])
        else:
            item["message"] = "股票代码和公司名称不能同时为空。"
        if company:
            item["company"] = company.model_dump()
            item["status"] = "duplicate" if company.stock_code in seen else "valid"
            item["message"] = "与文件或已选名单重复，自动跳过。" if item["status"] == "duplicate" else "已匹配名录。"
            seen.add(company.stock_code)
        result.append(item)
    return {"rows": result}
