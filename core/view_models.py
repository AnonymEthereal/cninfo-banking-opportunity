from datetime import datetime

from pydantic import BaseModel, Field

from .models import Announcement, Opportunity, Scan


class ScanCapabilities(BaseModel):
    """Server-derived actions so the UI never has to infer task state."""

    is_running: bool = False
    blocked_by_other_scan: bool = False
    can_confirm: bool = False
    can_save_budget: bool = False
    can_retry_sources: bool = False
    can_retry_failed: bool = False
    can_resume: bool = False
    can_download: bool = False


class RuntimeState(BaseModel):
    active: bool = False
    stale: bool = False
    scan_id: str | None = None
    started_at: str | None = None
    heartbeat_at: str | None = None



class ScanView(BaseModel):
    scan: Scan
    capabilities: ScanCapabilities = Field(default_factory=ScanCapabilities)
    announcements_total: int = 0
    opportunities_total: int = 0
    reports: dict[str, bool] = Field(default_factory=dict)


class ScanSummary(BaseModel):
    scan_id: str
    created_at: datetime
    updated_at: datetime
    status: str
    analysis_mode: str
    companies: int = 0
    announcements: int = 0
    opportunities: int = 0


class AnnouncementPage(BaseModel):
    items: list[Announcement] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int | None = None


class OpportunityPage(BaseModel):
    items: list[Opportunity] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int | None = None
    categories: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(default_factory=list)
