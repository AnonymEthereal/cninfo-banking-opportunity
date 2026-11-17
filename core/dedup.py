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


def _compatible_fields(left: dict, right: dict, fields: tuple[str, ...]) -> bool:
    for field in fields:
        first = normalize_text(str(left.get(field) or ""))
        second = normalize_text(str(right.get(field) or ""))
        if first and second and first != second:
            return False
    return True


def _same_numeric_value(left, right) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return str(left) == str(right)


def dedupe_events(events: list[Event], summary_ratio: float = 0.82, evidence_overlap: float = 0.5) -> list[Event]:
    kept: list[Event] = []
    for event in events:
        duplicate = None
        for existing in kept:
            same_summary = _similarity(normalize_text(event.summary), normalize_text(existing.summary)) >= summary_ratio
            current, prior = _evidence_texts(event), _evidence_texts(existing)
            overlap = len(current & prior) / len(current | prior) if current and prior else 0.0
            current_numbers = _numeric_facts(
                event.summary, *(item.text for item in event.evidence)
            )
            prior_numbers = _numeric_facts(
                existing.summary, *(item.text for item in existing.evidence)
            )
            shared_numbers = _has_shared_numeric_anchor(
                current_numbers, prior_numbers
            )
            if overlap >= evidence_overlap or (
                same_summary and shared_numbers
            ):
                duplicate = existing
                break
        if duplicate is None:
            kept.append(event)
            continue
        duplicate.worth_analyzing = bool(duplicate.worth_analyzing) or bool(event.worth_analyzing)
        if len(_evidence_texts(event)) > len(_evidence_texts(duplicate)):
            duplicate.summary = event.summary
            duplicate.evidence = event.evidence
    return kept


def _opportunity_evidence(item: dict) -> set[str]:
    return {normalize_text(evidence.get("text", "")) for evidence in item.get("evidence", []) if evidence.get("text")}


def dedupe_opportunities(items: list[dict], summary_ratio: float = 0.85, evidence_overlap: float = 0.5) -> list[dict]:
    kept: list[dict] = []
    for item in items:
        duplicate = None
        for existing in kept:
            item_event_id = item.get("_source_event_id")
            existing_event_id = existing.get("_source_event_id")
            if (
                item_event_id is not None
                and existing_event_id is not None
                and item_event_id != existing_event_id
            ):
                continue
            same_category = item.get("category") == existing.get("category")
            if not same_category:
                continue
            same_summary = _similarity(
                normalize_text(item.get("event_summary", "")),
                normalize_text(existing.get("event_summary", "")),
            ) >= summary_ratio
            current, prior = _opportunity_evidence(item), _opportunity_evidence(existing)
            overlap = len(current & prior) / len(current | prior) if current and prior else 0.0
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
            amount_compatible = not (
                item_value is not None
                and prior_value is not None
                and not _same_numeric_value(item_value, prior_value)
            ) and not (
                item_type
                and prior_type
                and normalize_text(str(item_type)) != normalize_text(str(prior_type))
            ) and not (
                item_amount.get("currency")
                and prior_amount.get("currency")
                and normalize_text(str(item_amount["currency"]))
                != normalize_text(str(prior_amount["currency"]))
            )
            context_compatible = _compatible_fields(
                item, existing, ("counterparty", "project_name")
            )
            summary_has_shared_fact = (
                same_summary
                and _has_shared_numeric_anchor(current_numbers, prior_numbers)
            )
            if amount_compatible and context_compatible and (
                overlap >= evidence_overlap or summary_has_shared_fact
            ):
                duplicate = existing
                break
        if duplicate is None:
            kept.append(item)
            continue
        if duplicate.get("amount", {}).get("value") is None and item.get("amount", {}).get("value") is not None:
            duplicate["amount"] = item["amount"]
        if len(item.get("evidence", [])) > len(duplicate.get("evidence", [])):
            duplicate["evidence"] = item["evidence"]
    return kept
