from core.models import Announcement, Event, OpportunityDraft, MarketingAnalysis
from agents.event_agent import MockEventAgent
from agents.classify_agent import MockClassifyAgent
from agents.marketing_agent import MockMarketingAgent

def test_mock_agent_contracts():
    announcement = Announcement(announcement_id="a", stock_code="000001", company_name="A", title="公告", publish_date="2026-11-06", source_url="https://example")
    event = Event(event_id="e1", summary="项目建设")
    draft = MockClassifyAgent().analyze(event, "正文")
    marketing = MockMarketingAgent().analyze(draft, "正文")
    assert draft.category == "其他类"
    assert isinstance(marketing, MarketingAnalysis)
