from core.company_directory import QUICK_COMPANIES, validate_company_text
from core.models import Company
from core.company_directory import search_companies, merge_companies


def test_fuzzy_search_and_additive_selection():
    assert search_companies("招商", QUICK_COMPANIES)[0].stock_code == "600036"
    assert search_companies("宁德时带", QUICK_COMPANIES)[0].stock_code == "300750"
    assert all(item.stock_code.startswith("600") for item in search_companies("600", QUICK_COMPANIES))
    first, second = QUICK_COMPANIES[:2]
    merged = merge_companies([first.dict()], [first.dict(), second.dict()])
    assert len(merged) == 2
    assert merged[0]["stock_code"] == first.stock_code


def test_manual_names_codes_and_duplicates():
    result = validate_company_text("平安银行，600036\n000001；比亚迪", QUICK_COMPANIES)
    assert result["valid"]
    assert [item["stock_code"] for item in result["companies"]] == ["000001", "600036", "002594"]
    assert result["summary"]["duplicate_rows"] == 1


def test_unknown_and_ambiguous_names_block_scan():
    assert not validate_company_text("不存在", QUICK_COMPANIES)["valid"]
    directory = [Company(stock_code="000001", company_name="同名"), Company(stock_code="000002", company_name="同名")]
    assert validate_company_text("同名", directory)["errors"][0]["code"] == "AMBIGUOUS_COMPANY"
    assert not validate_company_text("", directory)["valid"]


def test_manual_large_list():
    directory = [Company(stock_code=str(index), company_name=f"Company{index}") for index in range(51)]
    result = validate_company_text("\n".join(item.stock_code for item in directory), directory)
    assert result["valid"]
    assert len(result["companies"]) == 51
    assert len(merge_companies([], result["companies"])) == 51
