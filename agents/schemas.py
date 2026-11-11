from core.models import EventExtractionResult, OpportunityDraft, MarketingAnalysis

def validate_event(data: dict) -> EventExtractionResult:
    return EventExtractionResult.model_validate(data)

def validate_opportunity(data: dict) -> OpportunityDraft:
    return OpportunityDraft.model_validate(data)

def validate_marketing(data: dict) -> MarketingAnalysis:
    return MarketingAnalysis.model_validate(data)
