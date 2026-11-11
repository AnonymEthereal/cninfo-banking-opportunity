from datetime import date, datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, validator, root_validator


class Company(BaseModel):
    stock_code: str
    company_name: str

    @validator("stock_code", pre=True)
    @classmethod
    def stock_code_valid(cls, value: str) -> str:
        value = str(value).strip().zfill(6)
        if len(value) != 6 or not value.isdigit():
            raise ValueError("stock_code must be a 6 digit string")
        return value

    @validator("company_name", pre=True)
    @classmethod
    def name_valid(cls, value: str) -> str:
        value = str(value).strip()
        if not value:
            raise ValueError("company_name is required")
        return value


class DateRange(BaseModel):
    start_date: date
    end_date: date
    timezone: str = "Asia/Shanghai"

    @root_validator(skip_on_failure=True)
    def valid_range(cls, values):
        start, end = values.get("start_date"), values.get("end_date")
        if start and end and end < start:
            raise ValueError("end_date must be on or after start_date")
        return values


class ScanStatus(str, Enum):
    QUEUED = "queued"
    COLLECTING = "collecting"
    AWAITING_REVIEW = "awaiting_review"
    ANALYZING = "analyzing"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class Progress(BaseModel):
    stage: str = "queued"
    completed: int = 0
    total: int = 0


class ScanCounts(BaseModel):
    companies: int = 0
    announcements: int = 0
    opportunities: int = 0
    collection_failed: int = 0
    analysis_failed: int = 0
    document_failed: int = 0


class ScanError(BaseModel):
    scope: str
    code: str
    message: str
    announcement_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Scan(BaseModel):
    scan_id: str
    analysis_mode: str = "unknown"
    require_review: bool = False
    analysis_confirmed: bool = False
    announcement_timeout: int = Field(default=600, ge=1)
    analysis_timeout: int = Field(default=28800, ge=1)
    companies: list[Company]
    date_range: DateRange
    status: ScanStatus = ScanStatus.QUEUED
    progress: Progress = Field(default_factory=Progress)
    counts: ScanCounts = Field(default_factory=ScanCounts)
    errors: list[ScanError] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    class Config:
        use_enum_values = True


class Announcement(BaseModel):
    announcement_id: str
    stock_code: str
    company_name: str
    title: str
    publish_date: date
    source_url: str
    content_path: str | None = None
    text_path: str | None = None


class Evidence(BaseModel):
    text: str
    source_page: int | None = None


class Amount(BaseModel):
    value: float | None = None
    currency: str = "CNY"
    type: str | None = None
    original_text: str | None = None
    undisclosed: bool = False


class Opportunity(BaseModel):
    opportunity_id: str
    announcement_id: str
    stock_code: str
    company_name: str
    publish_date: date
    announcement_title: str
    category: str
    subcategory: str | None = None
    event_summary: str
    amount: Amount = Field(default_factory=Amount)
    other_amounts: list[Amount] = Field(default_factory=list)
    counterparty: str | None = None
    project_name: str | None = None
    bank_products: list[str] = Field(default_factory=list)
    marketing_direction: str | None = None
    suggested_action: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    pending_verification: list[str] = Field(default_factory=list)
    source_url: str
    analysis_status: str = "completed"
    analysis_error: str | None = None

class Event(BaseModel):
    event_id: str
    summary: str
    evidence: list[Evidence] = Field(default_factory=list)
    worth_analyzing: bool = True

class EventExtractionResult(BaseModel):
    announcement_id: str
    events: list[Event] = Field(default_factory=list)

class OpportunityDraft(BaseModel):
    event_id: str
    category: str
    subcategory: str | None = None
    event_summary: str
    amount: Amount = Field(default_factory=Amount)
    other_amounts: list[Amount] = Field(default_factory=list)
    counterparty: str | None = None
    project_name: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)

class MarketingAnalysis(BaseModel):
    bank_products: list[str] = Field(default_factory=list)
    marketing_direction: str | None = None
    suggested_action: str | None = None
    pending_verification: list[str] = Field(default_factory=list)
