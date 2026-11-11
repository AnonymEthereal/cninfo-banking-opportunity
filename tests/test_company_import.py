from io import BytesIO
import pytest
from openpyxl import Workbook, load_workbook
from core.company_directory import QUICK_COMPANIES
from core.company_import import preview_company_import, company_template
from core.company_list import validate_company_file


def excel(rows):
    workbook = Workbook()
    for row in rows:
        workbook.active.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_import_preview_mixed_rows():
    content = excel([["股票代码", "公司名称"], [1, None], [None, "比亚迪"], ["000001", "平安银行"], ["600036", "贵州茅台"], [None, "招商"], ["bad", "平安银行"], [None, None], ["999999", None]])
    rows = preview_company_import(content, QUICK_COMPANIES)["rows"]
    assert [row["status"] for row in rows] == ["valid", "valid", "duplicate", "pending", "pending", "error", "error"]
    assert rows[0]["company"]["stock_code"] == "000001"
    assert rows[3]["company"] is None
    assert {item["stock_code"] for item in rows[3]["candidates"]} >= {"600036", "600519"}


def test_import_existing_duplicates_single_column_and_missing_headers():
    result = preview_company_import(excel([["stock_code"], ["000001"]]), QUICK_COMPANIES, [QUICK_COMPANIES[0].dict()])
    assert result["rows"][0]["status"] == "duplicate"
    result = preview_company_import(excel([["company_name"], ["宁德时代"]]), QUICK_COMPANIES)
    assert result["rows"][0]["company"]["stock_code"] == "300750"
    with pytest.raises(ValueError):
        preview_company_import(excel([["unknown"], ["A"]]), QUICK_COMPANIES)


def test_template_and_legacy_null_validation():
    workbook = load_workbook(BytesIO(company_template()))
    assert workbook.active["A2"].value == "000001"
    result = validate_company_file(excel([["stock_code", "company_name"], [None, "A"], [1, None]]))
    assert not result["valid"]
    assert not result["companies"]
