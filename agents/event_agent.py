from typing import Protocol
from core.models import Announcement

class EventAgent(Protocol):
    def analyze(self, announcement: Announcement, text: str) -> dict: ...

class MockEventAgent:
    def analyze(self, announcement: Announcement, text: str) -> dict:
        return {"announcement_id": announcement.announcement_id, "events": [], "evidence": []}
