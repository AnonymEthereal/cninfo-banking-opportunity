"""Conservative parsing for common disclosed monetary figures.

Only amounts with an explicit monetary marker or currency are normalized.
Counts, percentages, Chinese-number numerals, and ambiguous multi-amount quotes
are deliberately left for review rather than guessed.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


_CURRENCY_PATTERN = (
    r"人民币|RMB|CNY|美元|美金|USD|港币|港元|HKD|欧元|EUR|日元|JPY"
)
_AMOUNT_TOKEN = re.compile(
    rf"(?<![\d.])"
    rf"(?P<prefix>{_CURRENCY_PATTERN})?\s*"
    rf"(?P<number>(?:\d{{1,3}}(?:,\d{{3}})+|\d+)(?:\.\d+)?)\s*"
    rf"(?P<unit>万亿|亿|万|千|百)?\s*"
    rf"(?P<yuan>元)?\s*(?P<suffix_currency>{_CURRENCY_PATTERN})?",
    re.IGNORECASE,
)
_MULTIPLIERS = {
    None: Decimal("1"),
    "百": Decimal("100"),
    "千": Decimal("1000"),
    "万": Decimal("10000"),
    "亿": Decimal("100000000"),
    "万亿": Decimal("1000000000000"),
}
_CURRENCIES = {
    "人民币": "CNY",
    "RMB": "CNY",
    "CNY": "CNY",
    "美元": "USD",
    "美金": "USD",
    "USD": "USD",
    "港币": "HKD",
    "港元": "HKD",
    "HKD": "HKD",
    "欧元": "EUR",
    "EUR": "EUR",
    "日元": "JPY",
    "JPY": "JPY",
}
_UNPARSED_AMOUNT_MARKER = re.compile(
    r"人民币|RMB|CNY|美元|美金|USD|港币|港元|HKD|欧元|EUR|日元|JPY|元",
    re.IGNORECASE,
)
_NUMERAL = re.compile(r"\d|[零〇一二两三四五六七八九十百千万亿兆]")


@dataclass(frozen=True)
class ParsedAmount:
    value: Decimal
    currency: str | None


def parse_disclosed_amounts(text: str | None) -> list[ParsedAmount]:
    """Parse supported monetary tokens, preserving ambiguity for the caller."""
    if not text:
        return []
    normalized = unicodedata.normalize("NFKC", text)
    matches: list[ParsedAmount] = []
    for match in _AMOUNT_TOKEN.finditer(normalized):
        tail = normalized[match.end() :].lstrip()
        if re.match(r"(?:million|billion|trillion|thousand|mn|bn|m)\b", tail, re.I):
            continue
        prefix = match.group("prefix")
        suffix_currency = match.group("suffix_currency")
        yuan = match.group("yuan")
        unit = match.group("unit")
        # A bare quantity such as "3.5万股" is not a monetary amount.
        if yuan is None and prefix is None and suffix_currency is None:
            continue
        try:
            number = Decimal(match.group("number").replace(",", ""))
        except InvalidOperation:
            continue
        currency_marker = prefix or suffix_currency
        currency = (
            _CURRENCIES.get(currency_marker.upper())
            if currency_marker
            else None
        )
        matches.append(
            ParsedAmount(value=number * _MULTIPLIERS[unit], currency=currency)
        )
    return matches


def parse_disclosed_amount(text: str | None) -> ParsedAmount | None:
    """Normalize one unambiguous monetary amount to its base currency unit."""
    matches = parse_disclosed_amounts(text)
    return matches[0] if len(matches) == 1 else None


def has_unparsed_amount(text: str | None) -> bool:
    """Detect likely disclosed Chinese-number amounts unsupported by the parser."""
    if not text:
        return False
    normalized = unicodedata.normalize("NFKC", text)
    return bool(_NUMERAL.search(normalized) and _UNPARSED_AMOUNT_MARKER.search(normalized))


def normalize_currency(value: str | None) -> str | None:
    if not value:
        return None
    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    return _CURRENCIES.get(normalized, normalized)
