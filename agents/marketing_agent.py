from typing import Protocol
from core.models import OpportunityDraft, MarketingAnalysis

class MarketingAgent(Protocol):
    def analyze(self, draft: OpportunityDraft, text: str) -> MarketingAnalysis: ...

class MockMarketingAgent:
    def analyze(self, draft: OpportunityDraft, text: str) -> MarketingAnalysis:
        return MarketingAnalysis()
