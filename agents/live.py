import os

from core.evidence import EvidenceMatcher, preview
from core.llm_client import OpenAICompatibleClient
from core.models import EventExtractionResult, MarketingAnalysis, OpportunityDraft

CATEGORIES = {"采购类", "销售类", "投资并购类", "融资新增类", "增值类", "报告类", "其他类"}

EVIDENCE_RULES = (
    "Evidence rules: every evidence.text must be a contiguous verbatim substring copied from the "
    "announcement, never a paraphrase. Do not translate, reorder, join separate sentences, or "
    "abbreviate with an ellipsis. Keep each quote short (about 20-80 characters) and preserve the "
    "original wording, numbers and punctuation. If an exact sentence is too long, copy a shorter "
    "fragment that still supports the claim."
)


class EvidenceError(ValueError):
    def __init__(self, code: str, quote: str | None = None):
        self.code = code
        self.quote = quote or ""
        super().__init__(code)


def check_evidence(evidence, text) -> None:
    """Reject quotes that cannot be found in the source (typography ignored)."""
    matcher = text if isinstance(text, EvidenceMatcher) else EvidenceMatcher(text)
    if not evidence:
        raise EvidenceError("EVIDENCE_MISMATCH")
    for item in evidence:
        if not matcher.contains(item.text):
            raise EvidenceError("EVIDENCE_MISMATCH", item.text)


def _repair_hint(error: EvidenceError) -> str:
    if error.code == "AMOUNT_EVIDENCE_MISMATCH":
        return (
            "\nThe previous response was rejected: amount.original_text "
            f"{preview(error.quote)!r} is not a verbatim source quote. Copy the amount exactly as "
            "it appears, or set value=null and undisclosed=true when the amount is not stated "
            "directly. Return the full corrected JSON object only."
        )
    if error.code == "UNKNOWN_CATEGORY":
        return (
            f"\nThe previous response used an unsupported category. Use exactly one of: "
            f"{'、'.join(sorted(CATEGORIES))}. Return the full corrected JSON object only."
        )
    return (
        "\nThe previous response was rejected: evidence "
        f"{preview(error.quote)!r} is not a verbatim contiguous substring of the announcement. "
        "Re-read the source and copy an exact fragment, without ellipsis or joined sentences. "
        "Return the full corrected JSON object only."
    )


def _generate_with_repair(client, prompt: str, schema, validate, repairs: int):
    """Run one agent and give the model one chance to fix a rejected quote."""
    attempt = 0
    while True:
        result = client.generate_json(prompt, schema)
        try:
            return validate(result)
        except EvidenceError as exc:
            if attempt >= repairs:
                raise
            attempt += 1
            prompt = prompt + _repair_hint(exc)


class LiveEventAgent:
    def __init__(self, client, repairs: int = 1):
        self.client = client
        self.repairs = max(0, repairs)

    def analyze(self, announcement, text):
        prompt = (
            "Extract independent business events, or an empty events list if none. Define one event "
            "as one distinct business matter: a transaction, financing, procurement, investment, or "
            "reportable fact. The same matter repeated in the title, 重要内容提示, body, or appendix "
            "is ONE event; merge its evidence and never output a second event for the same matter, "
            "the same transaction, or the same numbers. Set worth_analyzing=false for procedural "
            "content that cannot become a banking opportunity (meeting logistics and voting, "
            "underwriters' or directors' participation, risk disclaimers, boilerplate). Only mark "
            "worth_analyzing=true for procurement, sales, investment/M&A, new financing, value-added, "
            "or reporting facts. Every event with worth_analyzing=true needs at least one exact quote. "
            f"{EVIDENCE_RULES} announcement_id={announcement.announcement_id}\n{text}"
        )
        matcher = EvidenceMatcher(text)

        def validate(result):
            result.announcement_id = announcement.announcement_id
            seen = set()
            for index, event in enumerate(result.events, 1):
                event_id = (event.event_id or "").strip()
                if not event_id or event_id in seen:
                    event_id = f"ev_{index}"
                    suffix = 1
                    while event_id in seen:
                        suffix += 1
                        event_id = f"ev_{index}_{suffix}"
                event.event_id = event_id
                seen.add(event_id)
                # Procedural events never become opportunities; an inexact quote
                # there must not discard the real events in the same announcement.
                if event.worth_analyzing:
                    check_evidence(event.evidence, matcher)
            return result

        return _generate_with_repair(
            self.client, prompt, EventExtractionResult, validate, self.repairs
        )


class LiveClassifyAgent:
    def __init__(self, client, repairs: int = 1):
        self.client = client
        self.repairs = max(0, repairs)

    def analyze(self, event, text):
        prompt = (
            "Classify this event into 采购类、销售类、投资并购类、融资新增类、增值类、报告类、其他类. "
            "Every evidence.text and amount.original_text must be a verbatim contiguous source quote, "
            "not a summary. Preserve punctuation. Use value=null and undisclosed=true if no amount is "
            "disclosed. Do not confuse investment, financing and credit limits. Describe only this "
            f"event in event_summary; do not restate the whole announcement or other events. "
            f"{EVIDENCE_RULES} {event.model_dump_json()}\n{text}"
        )
        matcher = EvidenceMatcher(text)

        def validate(result):
            if result.category not in CATEGORIES:
                raise EvidenceError("UNKNOWN_CATEGORY")
            check_evidence(result.evidence, matcher)
            result.event_id = event.event_id
            for amount in [result.amount, *result.other_amounts]:
                amount.undisclosed = amount.value is None
                if amount.value is not None and not matcher.contains(amount.original_text):
                    raise EvidenceError("AMOUNT_EVIDENCE_MISMATCH", amount.original_text)
            return result

        return _generate_with_repair(
            self.client, prompt, OpportunityDraft, validate, self.repairs
        )


class LiveMarketingAgent:
    def __init__(self, client, repairs: int = 1):
        self.client = client
        self.repairs = max(0, repairs)

    def analyze(self, draft, text):
        if draft.category not in {"采购类", "投资并购类", "融资新增类", "增值类"}:
            return MarketingAnalysis()
        prompt = (
            "Suggest potential banking products, actions and verification questions. These are "
            f"hypotheses, not confirmed client needs. {draft.model_dump_json()}\n{text}"
        )
        return self.client.generate_json(prompt, MarketingAnalysis)


def live_agents():
    values = [os.getenv(name, "").strip() for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")]
    if not all(values):
        raise ValueError("Configure LLM_API_KEY, LLM_BASE_URL and LLM_MODEL first")
    client = OpenAICompatibleClient(*values)
    return client, (LiveEventAgent(client), LiveClassifyAgent(client), LiveMarketingAgent(client))
