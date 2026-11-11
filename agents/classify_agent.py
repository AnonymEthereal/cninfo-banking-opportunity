from typing import Protocol
from core.models import Event, OpportunityDraft

class ClassifyAgent(Protocol):
    def analyze(self, event: Event, text: str) -> OpportunityDraft: ...

class MockClassifyAgent:
    def analyze(self, event: Event, text: str) -> OpportunityDraft:
        return OpportunityDraft(event_id=event.event_id, category="其他类", event_summary=event.summary, evidence=event.evidence)
