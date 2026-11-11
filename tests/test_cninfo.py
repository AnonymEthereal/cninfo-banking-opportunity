from datetime import date
from core.models import Company, DateRange
from collectors.cninfo import CninfoCollector

class Response:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload

class Client:
    def __init__(self, payload): self.payload = payload; self.calls = []
    def post(self, *args, **kwargs): self.calls.append(kwargs.get("data", {}).get("pageNum")); return Response(self.payload() if callable(self.payload) else self.payload)

def test_cninfo_response_is_normalized():
    collector = CninfoCollector(client=Client({"classifiedAnnouncements": [{"announcementId": "a1", "announcementTitle": "公告", "announcementTime": "2026-11-06", "adjunctUrl": "/x.pdf"}]}))
    result = collector.collect(Company(stock_code="1", company_name="A"), DateRange(start_date=date(2026,11,1), end_date=date(2026,11,6)))
    assert len(result) == 1
    assert result[0].source_url.endswith("/x.pdf")

def test_cninfo_invalid_response_fails():
    collector = CninfoCollector(client=Client([]))
    try:
        collector.collect(Company(stock_code="1", company_name="A"), DateRange(start_date=date(2026,11,1), end_date=date(2026,11,6)))
    except ValueError as exc:
        assert str(exc) == "COLLECTOR_INVALID_RESPONSE"
    else:
        raise AssertionError("expected invalid response error")

def test_cninfo_paginates_and_uses_beijing_date():
    def payload():
        return {"announcements": [{"announcementId": "a", "announcementTitle": "公告", "announcementTime": 1766160000000, "adjunctUrl": "x.pdf"}], "totalpages": 2} if client.calls == [1] else {"announcements": [], "totalpages": 2}
    client = Client(payload)
    result = CninfoCollector(client=client).collect(Company(stock_code="1", company_name="A"), DateRange(start_date=date(2025,1,1), end_date=date(2025,12,31)))
    assert client.calls == [1, 2]
    assert len(result) == 1
    assert result[0].publish_date == date(2025,12,20)
