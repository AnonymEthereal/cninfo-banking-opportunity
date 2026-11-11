from typing import Protocol
from core.models import Company, DateRange, Announcement


class AnnouncementCollector(Protocol):
    def collect(self, company: Company, date_range: DateRange) -> list[Announcement]: ...
