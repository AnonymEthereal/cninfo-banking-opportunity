from core.models import EventExtractionResult, OpportunityDraft, MarketingAnalysis

def validate_event(data: dict) -> EventExtractionResult:
    return EventExtractionResult.parse_obj(data)

def validate_opportunity(data: dict) -> OpportunityDraft:
    return OpportunityDraft.parse_obj(data)

def validate_marketing(data: dict) -> MarketingAnalysis:
    return MarketingAnalysis.parse_obj(data)
