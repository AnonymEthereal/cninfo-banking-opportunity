"""Refresh the committed A-share metadata snapshot used by the category picker.

Data source: Eastmoney's public company-profile report
(``RPT_F10_BASIC_ORGINFO`` on datacenter-web.eastmoney.com), which returns all
listed and OTC companies in a handful of 5000-row pages. The script keeps only
SSE/SZSE/BSE A shares, derives board/status/industry/city with the helpers in
``core/company_meta.py``, and rewrites ``resources/company_meta.json``.

Run it only when the universe changes materially (quarterly is plenty):

    python scripts/refresh_company_meta.py

The result is reviewed as a Git diff; committing the snapshot keeps page loads
fast and offline, and makes the picker testable without network access.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.company_meta import (  # noqa: E402
    FIELDS,
    SNAPSHOT_PATH,
    build_city_gazetteer,
    build_record,
    is_a_share,
    load_snapshot,
)

API_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_F10_BASIC_ORGINFO"
COLUMNS = (
    "SECURITY_CODE,SECURITY_NAME_ABBR,SECURITY_TYPE,TRADE_MARKET,"
    "INDUSTRYCSRC1,PROVINCE,REG_ADDRESS,LISTING_DATE,BOARD_NAME_LEVEL"
)
HEADERS = {
    "Referer": "https://data.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (compatible; cninfo-banking-opportunity/1.0)",
}


def fetch_rows(page_size: int, timeout: float, pause: float) -> list[dict]:
    rows: list[dict] = []
    with httpx.Client(timeout=timeout, headers=HEADERS) as client:
        page = 1
        while True:
            response = client.get(
                API_URL,
                params={
                    "reportName": REPORT_NAME,
                    "columns": COLUMNS,
                    "pageNumber": page,
                    "pageSize": page_size,
                    "sortColumns": "SECURITY_CODE",
                    "sortTypes": 1,
                },
            )
            response.raise_for_status()
            result = response.json().get("result") or {}
            batch = result.get("data") or []
            if not batch:
                break
            rows.extend(batch)
            pages = int(result.get("pages") or 0)
            if pages and page >= pages:
                break
            page += 1
            time.sleep(pause)
    return rows


def build_items(rows: list[dict]) -> list[list[str]]:
    gazetteer = build_city_gazetteer(rows)
    seen: set[str] = set()
    items: list[list[str]] = []
    for row in rows:
        if not is_a_share(row):
            continue
        record = build_record(row, gazetteer)
        if record.stock_code in seen or not record.company_name:
            continue
        seen.add(record.stock_code)
        items.append([getattr(record, field) for field in FIELDS])
    items.sort(key=lambda item: item[0])
    return items


def write_snapshot(path: Path, items: list[list[str]]) -> None:
    payload = {
        "source": "eastmoney:RPT_F10_BASIC_ORGINFO",
        "generated_at": date.today().isoformat(),
        "count": len(items),
        "fields": list(FIELDS),
        "items": items,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def summarize(items: list[list[str]]) -> str:
    columns = {field: index for index, field in enumerate(FIELDS)}
    board = Counter(item[columns["board"]] for item in items)
    status = Counter(item[columns["status"]] for item in items)
    industry = Counter(item[columns["industry"]] for item in items)
    missing_city = sum(1 for item in items if not item[columns["city"]])
    lines = [
        f"companies={len(items)}",
        f"boards={sorted(board.items())}",
        f"statuses={sorted(status.items())}",
        f"industries={len(industry)}",
        f"missing_city={missing_city}",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=SNAPSHOT_PATH)
    parser.add_argument("--page-size", type=int, default=5000)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--pause", type=float, default=0.3, help="seconds between pages")
    args = parser.parse_args()

    print(f"fetching {REPORT_NAME} ...")
    rows = fetch_rows(args.page_size, args.timeout, args.pause)
    print(f"rows={len(rows)}")
    items = build_items(rows)
    if len(items) < 4000:
        print("refusing to write: fewer A shares than expected, source layout probably changed")
        return 1
    write_snapshot(args.output, items)
    print(summarize(items))
    print(f"wrote {args.output} ({args.output.stat().st_size / 1024:.0f} KiB)")
    if args.output == SNAPSHOT_PATH:
        print(f"reload check: {len(load_snapshot())} companies parsed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
