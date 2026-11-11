import re
from difflib import SequenceMatcher

from .models import Event

_PUNCTUATION = re.compile(r"[\s，。；：、,.;:!?！？（）()《》〈〉\"'“”‘’\-—_…·]+")


def normalize_text(text: str) -> str:
    return _PUNCTUATION.sub("", text or "").casefold()


def _evidence_texts(event: Event) -> set[str]:
    return {normalize_text(item.text) for item in event.evidence if item.text and item.text.strip()}


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def dedupe_events(events: list[Event], summary_ratio: float = 0.82, evidence_overlap: float = 0.5) -> list[Event]:
    kept: list[Event] = []
    for event in events:
        duplicate = None
        for existing in kept:
            same_summary = _similarity(normalize_text(event.summary), normalize_text(existing.summary)) >= summary_ratio
            current, prior = _evidence_texts(event), _evidence_texts(existing)
            overlap = len(current & prior) / len(current | prior) if current and prior else 0.0
            if same_summary or overlap >= evidence_overlap:
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
            same_summary = _similarity(normalize_text(item.get("event_summary", "")), normalize_text(existing.get("event_summary", ""))) >= summary_ratio
            same_category = item.get("category") == existing.get("category")
            current, prior = _opportunity_evidence(item), _opportunity_evidence(existing)
            overlap = len(current & prior) / len(current | prior) if current and prior else 0.0
            if same_summary or (same_category and overlap >= evidence_overlap):
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
