import os
from decimal import Decimal, InvalidOperation

from core.evidence import EvidenceMatcher, preview
from core.amounts import (
    has_unparsed_amount,
    normalize_currency,
    parse_disclosed_amounts,
)
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
    def __init__(
        self,
        code: str,
        quote: str | None = None,
        expected: str | None = None,
        amount=None,
    ):
        self.code = code
        self.quote = quote or ""
        self.expected = expected
        self.amount = amount
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
    if error.code == "AMOUNT_VALUE_MISMATCH":
        expected = (
            f" The normalized base-unit value is {error.expected}."
            if error.expected is not None
            else " The quote contains multiple or unsupported amount expressions; return one precise amount quote or set value=null."
        )
        return (
            "\nThe previous response's amount.value could not be verified against "
            f"amount.original_text {preview(error.quote)!r}.{expected} "
            "Use the numeric value in base currency units (元 for CNY, dollars for USD, etc.), "
            "or return null only when no amount is disclosed. Return the full corrected JSON object only."
        )
    if error.code == "AMOUNT_CURRENCY_MISMATCH":
        return (
            "\nThe previous response's amount.currency conflicts with the currency stated in "
            f"amount.original_text {preview(error.quote)!r}. Use the stated currency code, "
            "or null when the source does not identify a currency. Return the full corrected JSON object only."
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
    """Repair once, then keep a disclosed but unverifiable amount for review."""
    attempt = 0
    fallback_count = 0
    result = None
    while True:
        if result is None:
            result = client.generate_json(prompt, schema)
        try:
            return validate(result)
        except EvidenceError as exc:
            if attempt < repairs:
                attempt += 1
                prompt = prompt + _repair_hint(exc)
                result = None
                continue
            if (
                exc.amount is not None
                and exc.code in {"AMOUNT_VALUE_MISMATCH", "AMOUNT_CURRENCY_MISMATCH"}
            ):
                amount = exc.amount
                amount.currency = None
                amount.undisclosed = False
                amount.verification_status = "pending"
                if exc.code == "AMOUNT_VALUE_MISMATCH":
                    amount.value = None
                fallback_count += 1
                if fallback_count <= 1 + len(result.other_amounts):
                    continue
            raise


def _check_amount_quote(amount, matcher: EvidenceMatcher) -> None:
    if amount.value is not None and not amount.original_text:
        raise EvidenceError(
            "AMOUNT_EVIDENCE_MISMATCH", amount.original_text, amount=amount
        )
    if amount.original_text is not None and not matcher.contains(amount.original_text):
        raise EvidenceError("AMOUNT_EVIDENCE_MISMATCH", amount.original_text, amount=amount)


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
            "not a summary. Preserve punctuation. Normalize disclosed monetary values to base currency "
            "units (for example, 1亿元 = 100000000 元). Set currency only when the source supports it; "
            "otherwise use null. Leave amount.verification_status null; the application sets it "
            "after deterministic validation. Use value=null and undisclosed=true if no amount is disclosed. "
            "Do not confuse investment, financing and credit limits. Describe only this "
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
                if (
                    amount.verification_status == "pending"
                    and amount.undisclosed is False
                ):
                    _check_amount_quote(amount, matcher)
                    amount.currency = None
                    continue
                amount.undisclosed = amount.value is None
                _check_amount_quote(amount, matcher)
                parsed_amounts = parse_disclosed_amounts(amount.original_text)
                if len(parsed_amounts) > 1:
                    raise EvidenceError(
                        "AMOUNT_VALUE_MISMATCH",
                        amount.original_text,
                        amount=amount,
                    )
                parsed = parsed_amounts[0] if parsed_amounts else None
                if amount.value is None:
                    amount.currency = None
                    amount.verification_status = None
                    if parsed is not None:
                        raise EvidenceError(
                            "AMOUNT_VALUE_MISMATCH",
                            amount.original_text,
                            str(parsed.value),
                            amount,
                        )
                    amount_quote = amount.original_text
                    if (
                        amount_quote is None
                        or (
                            not has_unparsed_amount(amount_quote)
                            and not parse_disclosed_amounts(amount_quote)
                        )
                    ):
                        evidence_amount_quote = next(
                            (
                                item.text
                                for item in result.evidence
                                if has_unparsed_amount(item.text)
                                or parse_disclosed_amounts(item.text)
                            ),
                            None,
                        )
                        if evidence_amount_quote is not None:
                            amount_quote = evidence_amount_quote
                    if amount.original_text is None and amount_quote is not None:
                        evidence_amounts = parse_disclosed_amounts(amount_quote)
                        if evidence_amounts:
                            amount.original_text = amount_quote
                            expected = (
                                str(evidence_amounts[0].value)
                                if len(evidence_amounts) == 1
                                else None
                            )
                            raise EvidenceError(
                                "AMOUNT_VALUE_MISMATCH",
                                amount.original_text,
                                expected,
                                amount,
                            )
                    if has_unparsed_amount(amount_quote):
                        amount.original_text = amount_quote
                        amount.undisclosed = False
                        amount.verification_status = "pending"
                    continue
                _check_amount_quote(amount, matcher)
                if parsed is None:
                    # Retain the model's value for review, but don't present it
                    # as checked or guess a currency for unsupported notation.
                    amount.currency = None
                    amount.verification_status = "pending"
                    continue
                try:
                    reported_value = Decimal(str(amount.value))
                except InvalidOperation:
                    reported_value = Decimal("NaN")
                if reported_value != parsed.value:
                    raise EvidenceError(
                        "AMOUNT_VALUE_MISMATCH",
                        amount.original_text,
                        str(parsed.value),
                        amount,
                    )
                if parsed.currency:
                    reported_currency = normalize_currency(amount.currency)
                    if reported_currency and reported_currency != parsed.currency:
                        raise EvidenceError(
                            "AMOUNT_CURRENCY_MISMATCH",
                            amount.original_text,
                            amount=amount,
                        )
                    amount.currency = parsed.currency
                else:
                    # Currency is not derivable from a bare unit such as "万元".
                    amount.currency = None
                amount.verification_status = "verified"
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
            "hypotheses, not confirmed client needs. Use only the structured event facts and "
            "the attached verbatim evidence. Do not introduce facts absent from them. "
            f"{draft.model_dump_json()}"
        )
        return self.client.generate_json(prompt, MarketingAnalysis)


def live_agents():
    values = [os.getenv(name, "").strip() for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")]
    if not all(values):
        raise ValueError("Configure LLM_API_KEY, LLM_BASE_URL and LLM_MODEL first")
    client = OpenAICompatibleClient(*values)
    return client, (LiveEventAgent(client), LiveClassifyAgent(client), LiveMarketingAgent(client))
