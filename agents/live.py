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

MARKETING_GUIDANCE = {
    "采购类": (
        "围绕采购标的、付款安排、交付周期和交易对手分析。只有公告支持时，考虑设备或项目融资、"
        "供应链金融、结算、票据或保函；不要仅凭采购金额推断资金缺口。"
    ),
    "销售类": (
        "围绕合同金额、履约进度、客户及回款安排分析。只有披露支持时，考虑收款结算、应收账款融资、"
        "保理或履约保函；不得把合同金额当成已回款金额，也不得推断买方信用风险。"
    ),
    "投资并购类": (
        "围绕交易结构、出资或支付安排、交易进度和项目资金需求分析。只有披露支持时，考虑并购融资、"
        "项目融资、资金监管、资本金及交易结算；不得把项目总投资当成实际融资需求。"
    ),
    "融资新增类": (
        "围绕融资工具、披露额度与实际提款、资金用途、期限和到期安排分析。可在证据支持时考虑"
        "融资结构、债务接续、银团、担保和结算；不得把授信额度写成已提款金额或新增融资需求。"
    ),
    "增值类": (
        "围绕披露资金规模、可用期限、流动性约束和资金管理安排分析。只有证据支持时，考虑存款、"
        "现金管理或适配的资金增值产品；不得假定资金可自由支配或推断未披露的闲置资金。"
    ),
    "报告类": (
        "只基于本事件和证据中明确披露的经营、财务变化分析可能的结算、现金管理或融资服务切入点。"
        "本次不做完整的 16 项财务指标分析，不得从单一指标直接推断融资需求。"
    ),
    "其他类": (
        "结合事件类型判断是否存在具体银行业务切入点，例如新设子公司的账户、资本金及结算安排。"
        "股东或高管变化本身不代表银行需求；没有明确依据时应如实给出暂未识别到切入点。"
    ),
}

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
            "transaction, or overlapping core facts. Do not create separate "
            "events for the same underlying risk merely because it is described from different angles "
            "or could fit more than one category. Split only when each event is independently actionable "
            "and supported by distinct facts. Set worth_analyzing=false for procedural "
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
            "Set amount to the event's core transaction, procurement, investment, or financing amount; "
            "do not use stock prices, ratios, statutory thresholds, dates, or incidental fees as the core "
            "banking amount. If only these measures are disclosed, leave the core amount null and mark it "
            "undisclosed; keep material secondary amounts in other_amounts with a precise type. "
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
        guidance = MARKETING_GUIDANCE.get(draft.category)
        if guidance is None:
            raise ValueError(f"Unsupported opportunity category: {draft.category}")
        prompt = (
            "请为这条商机生成有针对性的银行业务分析。结合事件类别给出的分析重点："
            f"{guidance}\n"
            "仅依据结构化事件事实和其中的公告原文证据，不得引入未披露事实。明确区分公告事实与业务推断；"
            "银行产品和营销方向只能作为待验证的潜在线索，不能表述为客户已确认的需求。"
            "analysis_summary 用一至两句话说明判断依据和建议切入点，并与证据一致。"
            "如目前没有足够依据识别明确切入点，设置 analysis_outcome='no_clear_entry_point'，"
            "bank_products 返回空数组、marketing_direction 和 suggested_action 返回 null，"
            "并在 analysis_summary 说明原因；不要为了填字段而强行推荐产品。"
            "如识别到有依据的切入点，设置 analysis_outcome='opportunity_identified'，"
            "给出对应的潜在产品、切入方向、建议动作和关键待核实事项。"
            f"\n结构化事件：{draft.model_dump_json()}"
        )
        return self.client.generate_json(prompt, MarketingAnalysis)


def live_agents():
    values = [os.getenv(name, "").strip() for name in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")]
    if not all(values):
        raise ValueError("Configure LLM_API_KEY, LLM_BASE_URL and LLM_MODEL first")
    client = OpenAICompatibleClient(*values)
    return client, (LiveEventAgent(client), LiveClassifyAgent(client), LiveMarketingAgent(client))
