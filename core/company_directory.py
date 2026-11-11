"""Company identity resolution shared by manual entry and the picker."""
import re
from difflib import SequenceMatcher
from .models import Company

QUICK_COMPANIES = [
    Company(stock_code=code, company_name=name)
    for code, name in [
        ("000001", "平安银行"), ("000002", "万科A"),
        ("000333", "美的集团"), ("000651", "格力电器"),
        ("000858", "五粮液"), ("002594", "比亚迪"),
        ("300750", "宁德时代"), ("600036", "招商银行"),
        ("600519", "贵州茅台"), ("601318", "中国平安"),
        ("601398", "工商银行"), ("601857", "中国石油"),
    ]
]


def search_companies(query: str, directory: list[Company], limit: int = 30) -> list[Company]:
    query = re.sub(r"[\s（）()]+", "", query).casefold()
    if not query:
        return directory[:limit]
    ranked = []
    for company in directory:
        name = re.sub(r"[\s（）()]+", "", company.company_name).casefold()
        score = 0.0
        if query in (company.stock_code, name):
            score = 4.0
        elif query in name or company.stock_code.startswith(query):
            score = 3.0
        elif len(query) >= 2 and not query.isdigit():
            score = SequenceMatcher(None, query, name).ratio()
            if score < 0.55:
                score = 0.0
        if score:
            ranked.append((score, company))
    ranked.sort(key=lambda item: (-item[0], item[1].stock_code))
    return [company for _, company in ranked[:limit]]


def merge_companies(existing: list[dict], additions: list[dict]) -> list[dict]:
    merged = {item["stock_code"]: item for item in existing}
    for item in additions:
        merged.setdefault(item["stock_code"], item)
    return list(merged.values())


def load_company_directory() -> list[Company]:
    import httpx
    companies = {}
    with httpx.Client(timeout=20) as client:
        # Despite its filename, CNINFO's szse_stock list includes Shanghai stocks.
        for market in ("szse",):
            response = client.get(f"https://www.cninfo.com.cn/new/data/{market}_stock.json")
            response.raise_for_status()
            items = response.json()["stockList"]
            for item in items:
                company = Company(stock_code=item["code"], company_name=item["zwjc"])
                companies[company.stock_code] = company
    if not companies:
        raise ValueError("EMPTY_COMPANY_DIRECTORY")
    return sorted(companies.values(), key=lambda company: company.stock_code)


def validate_company_text(text: str, directory: list[Company]) -> dict:
    tokens = [token.strip() for token in re.split(r"[\s,，;；、]+", text) if token.strip()]
    companies, errors, seen = [], [], set()
    duplicates = 0
    for row, token in enumerate(tokens, 1):
        matches = [company for company in directory if token == company.stock_code or token.casefold() == company.company_name.casefold()]
        if len(matches) != 1:
            errors.append({"row": row, "code": "COMPANY_NOT_FOUND" if not matches else "AMBIGUOUS_COMPANY", "message": f"{token}：未找到唯一企业，请加载完整名录后使用准确简称或 6 位代码。"})
            continue
        company = matches[0]
        if company.stock_code in seen:
            duplicates += 1
            continue
        seen.add(company.stock_code)
        companies.append(company.model_dump())
    if not tokens:
        errors.append({"row": 1, "code": "COMPANIES_REQUIRED", "message": "请至少输入一家企业。"})
    return {"valid": bool(companies) and not errors, "companies": companies, "summary": {"total_rows": len(tokens), "valid_rows": len(companies), "duplicate_rows": duplicates, "error_rows": len(errors)}, "errors": errors}
