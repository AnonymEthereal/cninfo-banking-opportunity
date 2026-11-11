import os
import re
from core.llm_client import OpenAICompatibleClient
from core.models import EventExtractionResult, OpportunityDraft, MarketingAnalysis

class EvidenceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

class LiveEventAgent:
    def __init__(self, client):
        self.client = client

    def analyze(self, announcement, text):
        result = self.client.generate_json(
            f"Extract independent business events, or an empty events list if none. Define one event as one distinct business matter: a transaction, financing, procurement, investment, or reportable fact. The same matter repeated in the title, 重要内容提示, body, or appendix is ONE event; merge its evidence and never output a second event for the same matter, the same transaction, or the same numbers. Set worth_analyzing=false for procedural content that cannot become a banking opportunity (meeting logistics and voting, underwriters' or directors' participation, risk disclaimers, boilerplate). Only mark worth_analyzing=true for procurement, sales, investment/M&A, new financing, value-added, or reporting facts. Every evidence.text MUST be a verbatim contiguous quote from the source, never paraphrase or combine sentences. Preserve original numbers and punctuation. announcement_id={announcement.announcement_id}\n{text}",
            EventExtractionResult)
        result.announcement_id = announcement.announcement_id
        for event in result.events:
            check_evidence(event.evidence, text)
        return result

def check_evidence(evidence, text):
    source = re.sub(r"\s+", "", text)
    if not evidence or any(not e.text.strip() or re.sub(r"\s+", "", e.text) not in source for e in evidence):
        raise EvidenceError('EVIDENCE_MISMATCH')

class LiveClassifyAgent:
    def __init__(self, client):
        self.client = client

    def analyze(self, event, text):
        result = self.client.generate_json(
            f"Classify this event into 采购类、销售类、投资并购类、融资新增类、增值类、报告类、其他类. "
            f"Every evidence.text and amount.original_text must be a verbatim contiguous source quote, not a summary. Preserve punctuation. Use value=null and undisclosed=true if no amount is disclosed. Do not confuse investment, financing and credit limits. Describe only this event in event_summary; do not restate the whole announcement or other events. {event.model_dump_json()}\n{text}", OpportunityDraft)
        if result.category not in {'采购类','销售类','投资并购类','融资新增类','增值类','报告类','其他类'}:
            raise EvidenceError('UNKNOWN_CATEGORY')
        check_evidence(result.evidence, text)
        result.event_id = event.event_id
        for amount in [result.amount, *result.other_amounts]:
            amount.undisclosed = amount.value is None
            if amount.value is not None and (not amount.original_text or re.sub(r'\s+', '', amount.original_text) not in re.sub(r'\s+', '', text)):
                raise EvidenceError('AMOUNT_EVIDENCE_MISMATCH')
        return result

class LiveMarketingAgent:
    def __init__(self, client):
        self.client = client

    def analyze(self, draft, text):
        if draft.category not in {'采购类','投资并购类','融资新增类','增值类'}:
            return MarketingAnalysis()
        return self.client.generate_json(
            f"Suggest potential banking products, actions and verification questions. These are hypotheses, not confirmed client needs. {draft.model_dump_json()}\n{text}", MarketingAnalysis)

def live_agents():
    values = [os.getenv(name, '').strip() for name in ('LLM_API_KEY','LLM_BASE_URL','LLM_MODEL')]
    if not all(values):
        raise ValueError('Configure LLM_API_KEY, LLM_BASE_URL and LLM_MODEL first')
    client = OpenAICompatibleClient(*values)
    return client, (LiveEventAgent(client), LiveClassifyAgent(client), LiveMarketingAgent(client))
