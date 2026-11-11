from datetime import date, datetime
from zoneinfo import ZoneInfo
from hashlib import sha256
from typing import Any
try:
    import httpx
except ImportError:
    httpx = None
from core.config import HTTP_TIMEOUT_SECONDS
from core.models import Announcement, Company, DateRange

class CninfoCollector:
    def __init__(self, base_url: str = "https://www.cninfo.com.cn", client=None):
        if client is None and httpx is None:
            raise RuntimeError("httpx is required for live collection")
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, headers={"User-Agent": "OpportunityRadar/0.1"})

    def collect(self, company: Company, date_range: DateRange) -> list[Announcement]:
        market = "sh" if company.stock_code.startswith("6") else "sz"
        org_id = f"gs{market}{company.stock_code.zfill(7)}"
        payload = {"stock": f"{company.stock_code},{org_id}", "pageNum": 1, "pageSize": 100, "tabName": "fulltext", "column": "sse" if market == "sh" else "szse", "plate": "", "searchkey": "", "category": "", "seDate": f"{date_range.start_date:%Y-%m-%d}~{date_range.end_date:%Y-%m-%d}"}
        payload.update({"sortName": "", "sortType": "", "isHLtitle": "true"})
        items = []
        page = 1
        while True:
            payload["pageNum"] = page
            response = self.client.post(f"{self.base_url}/new/hisAnnouncement/query", data=payload)
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise ValueError("COLLECTOR_INVALID_JSON") from exc
            if not isinstance(data, dict):
                raise ValueError("COLLECTOR_INVALID_RESPONSE")
            page_items = data.get("announcements", []) or data.get("classifiedAnnouncements", []) or []
            items.extend(page_items)
            total_pages = int(data.get("totalpages") or data.get("totalPages") or 0)
            if not page_items or total_pages <= page:
                break
            page += 1
        return [self._parse_item(company, item) for item in items if isinstance(item, dict)]

    def _parse_item(self, company: Company, item: dict[str, Any]) -> Announcement:
        title = str(item.get("announcementTitle") or item.get("title") or "").strip()
        source_url = str(item.get("adjunctUrl") or item.get("url") or "")
        if source_url.startswith("/"):
            source_url = self.base_url + source_url
        elif source_url and not source_url.startswith(("http://", "https://")):
            source_url = "https://static.cninfo.com.cn/" + source_url
        raw_id = item.get("announcementId") or item.get("id") or f"{company.stock_code}|{title}|{source_url}"
        publish = item.get("announcementTime") or item.get("publishDate") or item.get("date")
        if isinstance(publish, (int, float)):
            publish_date = datetime.fromtimestamp(publish / 1000, tz=ZoneInfo("Asia/Shanghai")).date()
        elif isinstance(publish, str):
            try:
                publish_date = date.fromisoformat(publish[:10].replace("/", "-"))
            except ValueError as exc:
                raise ValueError("COLLECTOR_INVALID_DATE") from exc
        else:
            publish_date = publish
        if not title or not source_url or not isinstance(publish_date, date):
            raise ValueError("COLLECTOR_MISSING_FIELDS")
        return Announcement(announcement_id="ann_" + sha256(str(raw_id).encode()).hexdigest()[:20], stock_code=company.stock_code, company_name=company.company_name, title=title, publish_date=publish_date, source_url=source_url)

    def download(self, announcement: Announcement, destination):
        response = self.client.get(announcement.source_url)
        response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        return destination
