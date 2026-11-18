import re
import unicodedata
from difflib import SequenceMatcher
from decimal import Decimal, InvalidOperation

from .models import Event

_PUNCTUATION = re.compile(r"[\s，。；：、,.;:!?！？（）()《》〈〉\"'“”‘’\-—_…·]+")
_NUMBER = re.compile(r"(?<!\d)\d[\d,]*(?:\.\d+)?")
_FACT_UNIT = re.compile(
    r"^(?:万元人民币|亿元人民币|万亿元人民币|万亿美元|亿美元|万港元|亿港元|"
    r"万元|亿元|万亿元|人民币|美元|美金|港币|港元|CNY|RMB|USD|HKD|"
    r"个百分点|%|万股|股|万份|份|元|万|亿)",
    re.IGNORECASE,
)
_PREFIX_CURRENCY = re.compile(
    r"(?:人民币|RMB|CNY|美元|美金|USD|港币|港元|HKD)\s*$",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    return _PUNCTUATION.sub("", text or "").casefold()


def _evidence_texts(event: Event) -> set[str]:
    return {normalize_text(item.text) for item in event.evidence if item.text and item.text.strip()}


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _numeric_facts(*texts: str) -> set[str]:
    facts = set()
    for text in texts:
        normalized = unicodedata.normalize("NFKC", text or "")
        for match in _NUMBER.finditer(normalized):
            tail = normalized[match.end() :].lstrip()
            unit = _FACT_UNIT.match(tail)
            # Plain years and sequence numbers are weak anchors and should not
            # cause two otherwise distinct events to collapse.
            if unit is None:
                continue
            before = normalized[: match.start()]
            currency = _PREFIX_CURRENCY.search(before)
            prefix = normalize_text(currency.group()) if currency else ""
            number = Decimal(match.group().replace(",", "")).normalize()
            canonical = format(number, "f")
            facts.add(f"{prefix}{canonical}{normalize_text(unit.group())}")
    return facts


def _has_shared_numeric_anchor(left: set[str], right: set[str]) -> bool:
    return bool(left and right and left & right)


def _set_overlap(left: set[str], right: set[str]) -> tuple[float, float]:
    if not left or not right:
        return 0.0, 0.0
    shared = len(left & right)
    return shared / len(left | right), shared / min(len(left), len(right))


def _compatible_fields(left: dict, right: dict, fields: tuple[str, ...]) -> bool:
    for field in fields:
        first = normalize_text(str(left.get(field) or ""))
        second = normalize_text(str(right.get(field) or ""))
        if first and second and first != second:
            return False
    return True


def _soft_compatible_fields(
    left: dict, right: dict, fields: tuple[str, ...], similarity: float = 0.8
) -> bool:
    for field in fields:
        first = normalize_text(str(left.get(field) or ""))
        second = normalize_text(str(right.get(field) or ""))
        if first and second and _similarity(first, second) < similarity:
            return False
    return True


def _shares_context_anchor(
    left: dict, right: dict, fields: tuple[str, ...], similarity: float = 0.8
) -> bool:
    return any(
        normalize_text(str(left.get(field) or ""))
        and normalize_text(str(right.get(field) or ""))
        and _similarity(
            normalize_text(str(left.get(field) or "")),
            normalize_text(str(right.get(field) or "")),
        )
        >= similarity
        for field in fields
    )


def _same_numeric_value(left, right) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return str(left) == str(right)


def dedupe_events(
    events: list[Event],
    summary_ratio: float = 0.82,
    evidence_overlap: float = 0.5,
    evidence_containment: float = 0.8,
    related_summary_ratio: float = 0.55,
) -> list[Event]:
    kept: list[Event] = []
    for event in events:
        duplicate = None
        for existing in kept:
            same_summary = _similarity(normalize_text(event.summary), normalize_text(existing.summary)) >= summary_ratio
            current, prior = _evidence_texts(event), _evidence_texts(existing)
            overlap, containment = _set_overlap(current, prior)
            summary_similarity = _similarity(
                normalize_text(event.summary), normalize_text(existing.summary)
            )
            current_numbers = _numeric_facts(
                event.summary, *(item.text for item in event.evidence)
            )
            prior_numbers = _numeric_facts(
                existing.summary, *(item.text for item in existing.evidence)
            )
            shared_numbers = _has_shared_numeric_anchor(
                current_numbers, prior_numbers
            )
            shared_number_count = len(current_numbers & prior_numbers)
            if (
                overlap >= evidence_overlap
                or (same_summary and shared_numbers)
                or (
                    containment >= evidence_containment
                    and shared_numbers
                    and summary_similarity >= related_summary_ratio
                )
                or (
                    shared_number_count >= 2
                    and summary_similarity >= 0.5
                )
            ):
                duplicate = existing
                break
        if duplicate is None:
            kept.append(event)
            continue
        duplicate.worth_analyzing = bool(duplicate.worth_analyzing) or bool(event.worth_analyzing)
        known = _evidence_texts(duplicate)
        for item in event.evidence:
            normalized = normalize_text(item.text)
            if normalized and normalized not in known:
                duplicate.evidence.append(item)
                known.add(normalized)
        if len(normalize_text(event.summary)) > len(normalize_text(duplicate.summary)):
            duplicate.summary = event.summary
    return kept


def link_related_disclosures(
    items: list[dict], max_days: int = 14, summary_ratio: float = 0.6
) -> list[dict]:
    """Mark possible same-matter disclosures without deleting source records."""
    from datetime import date, datetime

    for item in items:
        item["related_announcement_ids"] = []
    for index, item in enumerate(items):
        for other in items[index + 1 :]:
            if item.get("announcement_id") == other.get("announcement_id"):
                continue
            if item.get("stock_code") != other.get("stock_code"):
                continue
            if item.get("category") != other.get("category"):
                continue
            subcategory = normalize_text(str(item.get("subcategory") or ""))
            if not subcategory or subcategory != normalize_text(
                str(other.get("subcategory") or "")
            ):
                continue
            try:
                first_date = item.get("publish_date")
                second_date = other.get("publish_date")
                if isinstance(first_date, str):
                    first_date = date.fromisoformat(first_date[:10])
                elif isinstance(first_date, datetime):
                    first_date = first_date.date()
                if isinstance(second_date, str):
                    second_date = date.fromisoformat(second_date[:10])
                elif isinstance(second_date, datetime):
                    second_date = second_date.date()
                if abs((first_date - second_date).days) > max_days:
                    continue
            except (TypeError, ValueError):
                continue
            if (
                _similarity(
                    normalize_text(item.get("event_summary", "")),
                    normalize_text(other.get("event_summary", "")),
                )
                < summary_ratio
                and not (
                    _compatible_fields(item, other, ("counterparty", "project_name"))
                    and any(
                        item.get(field) and item.get(field) == other.get(field)
                        for field in ("counterparty", "project_name")
                    )
                )
            ):
                continue
            first_id = other.get("announcement_id")
            second_id = item.get("announcement_id")
            if first_id:
                item["related_announcement_ids"].append(first_id)
            if second_id:
                other["related_announcement_ids"].append(second_id)
    for item in items:
        item["related_announcement_ids"] = sorted(
            set(item.get("related_announcement_ids", []))
        )
    return items


def _opportunity_evidence(item: dict) -> set[str]:
    return {normalize_text(evidence.get("text", "")) for evidence in item.get("evidence", []) if evidence.get("text")}


def _merge_unique_values(target: list, incoming: list, key) -> list:
    merged = list(target or [])
    seen = {key(value) for value in merged}
    for value in incoming or []:
        value_key = key(value)
        if value_key not in seen:
            merged.append(value)
            seen.add(value_key)
    return merged


def _merge_opportunity(duplicate: dict, item: dict) -> None:
    previous_outcome = duplicate.get("analysis_outcome")
    incoming_outcome = item.get("analysis_outcome")
    outcome_conflict = (
        previous_outcome in {"opportunity_identified", "no_clear_entry_point"}
        and incoming_outcome in {"opportunity_identified", "no_clear_entry_point"}
        and previous_outcome != incoming_outcome
    )
    duplicate_evidence = duplicate.get("evidence") or []
    incoming_evidence = item.get("evidence") or []
    duplicate["evidence"] = _merge_unique_values(
        duplicate_evidence,
        incoming_evidence,
        lambda evidence: normalize_text(evidence.get("text", "")),
    )
    duplicate["other_amounts"] = _merge_unique_values(
        duplicate.get("other_amounts") or [],
        item.get("other_amounts") or [],
        lambda amount: (
            normalize_text(str(amount.get("type") or "")),
            str(amount.get("value")),
            normalize_text(str(amount.get("currency") or "")),
            normalize_text(str(amount.get("original_text") or "")),
        ),
    )
    duplicate["bank_products"] = _merge_unique_values(
        duplicate.get("bank_products") or [],
        item.get("bank_products") or [],
        lambda value: normalize_text(str(value)),
    )
    duplicate["pending_verification"] = _merge_unique_values(
        duplicate.get("pending_verification") or [],
        item.get("pending_verification") or [],
        lambda value: normalize_text(str(value)),
    )
    duplicate["related_announcement_ids"] = _merge_unique_values(
        duplicate.get("related_announcement_ids") or [],
        item.get("related_announcement_ids") or [],
        lambda value: str(value),
    )
    for field in ("counterparty", "project_name", "subcategory"):
        if item.get(field) and (
            not duplicate.get(field)
            or len(str(item[field])) > len(str(duplicate[field]))
        ):
            duplicate[field] = item[field]
    if duplicate.get("amount", {}).get("value") is None and item.get("amount", {}).get("value") is not None:
        duplicate["amount"] = item["amount"]
    if outcome_conflict:
        duplicate["analysis_conflict"] = True
        duplicate["analysis_outcome"] = "no_clear_entry_point"
        duplicate["analysis_status"] = "partial"
        duplicate["bank_products"] = []
        duplicate["marketing_direction"] = None
        duplicate["suggested_action"] = None
        duplicate["analysis_summary"] = (
            "同一事项的重复分析结论不一致，已按保守原则暂不计为商机；"
            "请人工复核后再决定是否跟进。"
        )
        duplicate["pending_verification"] = _merge_unique_values(
            duplicate.get("pending_verification") or [],
            ["重复事件的商机判断存在分歧，需人工复核。"],
            lambda value: normalize_text(str(value)),
        )
    elif duplicate.get("analysis_conflict"):
        duplicate["analysis_outcome"] = "no_clear_entry_point"
        duplicate["bank_products"] = []
        duplicate["marketing_direction"] = None
        duplicate["suggested_action"] = None
    elif item.get("analysis_outcome") == "opportunity_identified":
        duplicate["analysis_outcome"] = "opportunity_identified"
        for field in ("analysis_summary", "marketing_direction", "suggested_action"):
            if item.get(field):
                duplicate[field] = item[field]
    elif incoming_outcome == "no_clear_entry_point":
        duplicate["analysis_outcome"] = "no_clear_entry_point"
        duplicate["bank_products"] = []
        duplicate["marketing_direction"] = None
        duplicate["suggested_action"] = None
        if item.get("analysis_summary"):
            duplicate["analysis_summary"] = item["analysis_summary"]
    elif duplicate.get("analysis_outcome") is None:
        duplicate["analysis_outcome"] = incoming_outcome
    if not duplicate.get("analysis_summary") and item.get("analysis_summary"):
        duplicate["analysis_summary"] = item["analysis_summary"]
    if not duplicate.get("analysis_error") and item.get("analysis_error"):
        duplicate["analysis_error"] = item["analysis_error"]


def dedupe_opportunities(items: list[dict], summary_ratio: float = 0.85, evidence_overlap: float = 0.5) -> list[dict]:
    kept: list[dict] = []
    for item in items:
        duplicate = None
        for existing in kept:
            item_event_id = item.get("_source_event_id")
            existing_event_id = existing.get("_source_event_id")
            same_source_event = (
                item_event_id is None
                or existing_event_id is None
                or item_event_id == existing_event_id
            )
            same_category = item.get("category") == existing.get("category")
            summary_similarity = _similarity(
                normalize_text(item.get("event_summary", "")),
                normalize_text(existing.get("event_summary", "")),
            )
            same_summary = summary_similarity >= summary_ratio
            current, prior = _opportunity_evidence(item), _opportunity_evidence(existing)
            overlap, containment = _set_overlap(current, prior)
            current_numbers = _numeric_facts(
                item.get("event_summary", ""),
                *(evidence.get("text", "") for evidence in item.get("evidence", [])),
            )
            prior_numbers = _numeric_facts(
                existing.get("event_summary", ""),
                *(evidence.get("text", "") for evidence in existing.get("evidence", [])),
            )
            # Compare nested amount facts without treating absent values as a
            # conflict (older checkpoints often have only an empty amount).
            item_amount = item.get("amount") or {}
            prior_amount = existing.get("amount") or {}
            item_value, prior_value = item_amount.get("value"), prior_amount.get("value")
            item_type, prior_type = item_amount.get("type"), prior_amount.get("type")
            same_announcement = (
                bool(item.get("announcement_id"))
                and item.get("announcement_id") == existing.get("announcement_id")
            )
            same_stock = (
                bool(item.get("stock_code"))
                and item.get("stock_code") == existing.get("stock_code")
            )
            shared_number_count = len(current_numbers & prior_numbers)
            context_compatible = _soft_compatible_fields(
                item, existing, ("counterparty", "project_name")
            )
            context_anchor = _shares_context_anchor(
                item, existing, ("counterparty", "project_name")
            )
            strong_cross_event_duplicate = (
                not same_source_event
                and same_announcement
                and same_stock
                and context_compatible
                and (
                    (
                        shared_number_count >= 2
                        and summary_similarity >= (0.85 if same_category else 0.5)
                        and (
                            not same_category
                            or context_anchor
                            or overlap >= 0.25
                            or containment >= 0.5
                        )
                    )
                    or (
                        overlap >= 0.5
                        and summary_similarity >= 0.6
                    )
                )
            )
            amount_value_compatible = not (
                item_value is not None
                and prior_value is not None
                and not _same_numeric_value(item_value, prior_value)
            )
            amount_type_compatible = not (
                item_type
                and prior_type
                and normalize_text(str(item_type)) != normalize_text(str(prior_type))
            )
            amount_currency_compatible = not (
                item_amount.get("currency")
                and prior_amount.get("currency")
                and normalize_text(str(item_amount["currency"]))
                != normalize_text(str(prior_amount["currency"]))
            )
            amount_compatible = (
                amount_value_compatible
                and amount_currency_compatible
                and (
                    amount_type_compatible
                    or (
                        strong_cross_event_duplicate
                        and item_value is not None
                        and prior_value is not None
                        and _same_numeric_value(item_value, prior_value)
                    )
                )
            )
            summary_has_shared_fact = (
                same_summary
                and _has_shared_numeric_anchor(current_numbers, prior_numbers)
            )
            if not same_source_event and not strong_cross_event_duplicate:
                continue
            if not same_category and not strong_cross_event_duplicate:
                continue
            if amount_compatible and context_compatible and (
                overlap >= evidence_overlap
                or summary_has_shared_fact
                or strong_cross_event_duplicate
            ):
                duplicate = existing
                break
        if duplicate is None:
            kept.append(item)
            continue
        _merge_opportunity(duplicate, item)
    return kept
