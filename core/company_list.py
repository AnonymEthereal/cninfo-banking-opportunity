from io import BytesIO
from openpyxl import load_workbook
from pydantic import ValidationError as PydanticValidationError
from .models import Company


def validate_company_file(content: bytes) -> dict:
    wb = load_workbook(BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {"valid": False, "companies": [], "summary": {"total_rows": 0, "valid_rows": 0, "duplicate_rows": 0, "error_rows": 1}, "errors": [{"row": 1, "code": "EMPTY_FILE", "message": "Excel 文件为空"}]}
    headers = [str(x).strip().lower() if x is not None else "" for x in rows[0]]
    required = {"stock_code", "company_name"}
    if not required.issubset(headers):
        missing = sorted(required - set(headers))
        return {"valid": False, "companies": [], "summary": {"total_rows": 0, "valid_rows": 0, "duplicate_rows": 0, "error_rows": 1}, "errors": [{"row": 1, "code": "MISSING_COLUMNS", "message": f"缺少列: {', '.join(missing)}"}]}
    idx = {name: headers.index(name) for name in required}
    companies, errors, seen = [], [], set()
    duplicate_rows = 0
    for row_no, row in enumerate(rows[1:], 2):
        if all(v is None or str(v).strip() == "" for v in row):
            continue
        try:
            code, name = row[idx["stock_code"]], row[idx["company_name"]]
            if code is None or name is None or not str(name).strip():
                raise ValueError("股票代码和企业名称不能为空")
            company = Company(stock_code=str(code), company_name=str(name))
            if company.stock_code in seen:
                duplicate_rows += 1
                errors.append({"row": row_no, "code": "DUPLICATE_STOCK_CODE", "message": "股票代码重复"})
                continue
            seen.add(company.stock_code)
            companies.append(company)
        except (ValueError, PydanticValidationError) as exc:
            errors.append({"row": row_no, "code": "INVALID_ROW", "message": str(exc)})
    return {"valid": not errors, "companies": [c.model_dump() for c in companies], "summary": {"total_rows": len(rows) - 1, "valid_rows": len(companies), "duplicate_rows": duplicate_rows, "error_rows": len(errors) - duplicate_rows}, "errors": errors}
