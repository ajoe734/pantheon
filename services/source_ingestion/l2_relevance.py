"""Deterministic finance relevance filter in front of L2 hypothesis extraction.

RC2: external text (paper, news, social, book, filing, macro) used to become a
strategy draft whatever it was about (lottery, crime, weather, politics). This
module decides, with no model call and a checked-in vocabulary
(``l2_relevance_vocabulary.json``), whether a record may even be sent to the
extractor. It runs before the daily budget is reserved and records why a record
was dropped (``RelevanceDecision.reason``).

Rules:
* paper: passes when a metadata category is a finance category, else when its
  title/keywords/abstract carry a finance term (same rule as news);
* news, theme digests, social, book, filing, macro: need at least one finance
  or market term, a curated Taiwan security name, or a *listed* Taiwan
  security code written as a code (``(2330)``, ``2330.TW``, ``代號 2330``;
  a bare four digit number such as a year is never a code).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

VOCABULARY_PATH = Path(__file__).with_name("l2_relevance_vocabulary.json")

# Reason codes. Dropped reasons are the ones counted by source type and reason.
PASS_PAPER_CATEGORY = "paper_finance_category"
PASS_FINANCE_TERM = "finance_term"
PASS_SECURITY_NAME = "security_name"
PASS_SECURITY_CODE = "security_code"
DROP_NO_TEXT = "no_text"
DROP_NO_FINANCE_SIGNAL = "no_finance_signal"
DROP_PAPER_NOT_FINANCE = "paper_not_finance"

_CODE_PATTERNS = (
    re.compile(r"[（(]\s*([1-9]\d{3})\s*[）)]"),
    re.compile(r"(?<![\d.])([1-9]\d{3})\.(?:TWO?|tw)\b"),
    re.compile(r"(?:代號|代碼|股號|證券代號|stock code|ticker)\s*[:：]?\s*([1-9]\d{3})(?!\d)", re.I),
    re.compile(r"(?:TWSE|TPEX|TPE|TYO|台股)\s*[:：]\s*([1-9]\d{3})(?!\d)", re.I),
)
_TEXT_KEYS = ("body", "text", "abstract", "excerpt", "summary")
_SIGNAL_KEYS = ("keywords", "tags", "concepts", "topics", "subjects")
_CATEGORY_KEYS = ("category", "categories", "primary_category", "arxiv_category", "subject", "jel")


@dataclass(frozen=True)
class RelevanceDecision:
    relevant: bool
    reason: str
    matched: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"relevant": self.relevant, "reason": self.reason, "matched": list(self.matched)}


@lru_cache(maxsize=1)
def load_vocabulary() -> Mapping[str, Any]:
    return json.loads(VOCABULARY_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _compiled() -> tuple[re.Pattern[str], tuple[str, ...], Mapping[str, str], tuple[str, ...]]:
    vocab = load_vocabulary()
    english = sorted((t.lower() for t in vocab["finance_terms_en"]), key=len, reverse=True)
    pattern = re.compile(
        r"(?<![a-z0-9])(?:" + "|".join(re.escape(t) for t in english) + r")(?![a-z0-9])", re.I
    )
    chinese = tuple(sorted((t.lower() for t in vocab["finance_terms_zh"]), key=len, reverse=True))
    names = dict(vocab["security_names"])
    prefixes = tuple(p.lower() for p in vocab["paper_category_prefixes"])
    return pattern, chinese, names, prefixes


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, Iterable):
        return [s for v in value for s in _strings(v)]
    return []


def record_text(title: str, metadata: Mapping[str, Any]) -> str:
    parts = [title or ""]
    for key in _TEXT_KEYS + _SIGNAL_KEYS:
        parts.extend(_strings(metadata.get(key)))
    return "\n".join(p for p in parts if p.strip())


def _paper_category(metadata: Mapping[str, Any]) -> str | None:
    _, _, _, prefixes = _compiled()
    for key in _CATEGORY_KEYS:
        for value in _strings(metadata.get(key)):
            for token in re.split(r"[\s,;|]+", value.strip().lower()):
                if token and any(token.startswith(p) for p in prefixes):
                    return token
    return None


def assess_text(
    text: str,
    *,
    listed_codes: Iterable[str] | Callable[[], Iterable[str]] | None = None,
) -> RelevanceDecision:
    """Relevance of a text alone (news-style rule)."""
    if not text.strip():
        return RelevanceDecision(False, DROP_NO_TEXT)
    pattern, chinese, names, _ = _compiled()
    lowered = text.lower()
    english_hit = pattern.search(lowered)
    if english_hit:
        return RelevanceDecision(True, PASS_FINANCE_TERM, (english_hit.group(0),))
    for term in chinese:
        if term in lowered:
            return RelevanceDecision(True, PASS_FINANCE_TERM, (term,))
    for name in names:
        if name in text:
            return RelevanceDecision(True, PASS_SECURITY_NAME, (name,))
    codes = listed_codes() if callable(listed_codes) else listed_codes
    if codes is not None:
        listed = codes if isinstance(codes, (set, frozenset)) else frozenset(codes)
        for code_pattern in _CODE_PATTERNS:
            for match in code_pattern.finditer(text):
                if match.group(1) in listed:
                    return RelevanceDecision(True, PASS_SECURITY_CODE, (match.group(1),))
    return RelevanceDecision(False, DROP_NO_FINANCE_SIGNAL)


def assess_relevance(
    source_type: str,
    title: str,
    metadata: Mapping[str, Any],
    *,
    listed_codes: Iterable[str] | Callable[[], Iterable[str]] | None = None,
) -> RelevanceDecision:
    """Decide whether one external-text record may be sent to extraction."""
    if str(source_type).lower() == "paper":
        category = _paper_category(metadata)
        if category:
            return RelevanceDecision(True, PASS_PAPER_CATEGORY, (category,))
        decision = assess_text(record_text(title, metadata), listed_codes=listed_codes)
        if not decision.relevant and decision.reason == DROP_NO_FINANCE_SIGNAL:
            return RelevanceDecision(False, DROP_PAPER_NOT_FINANCE)
        return decision
    return assess_text(record_text(title, metadata), listed_codes=listed_codes)


_CODES_CACHE: list[Any] = [None, None]


def hosted_listed_codes() -> frozenset[str] | None:
    """Ordinary listed Taiwan codes from the existing listing state, or None when unreadable."""
    try:
        from services.source_ingestion.security_listing import hosted_listing

        hosted = hosted_listing()
        if hosted is None:
            return None
        state, problem = hosted._load()
        if state is None or problem:
            return None
        key = (id(state), state.last_success_date)
        if _CODES_CACHE[0] != key:
            _CODES_CACHE[0] = key
            _CODES_CACHE[1] = frozenset(s for s in state.listed_symbols() if not state.is_warrant(s))
        return _CODES_CACHE[1]
    except Exception:  # noqa: BLE001 - an unreadable listing only disables the code rule
        return None


__all__ = [
    "RelevanceDecision",
    "assess_relevance",
    "assess_text",
    "hosted_listed_codes",
    "load_vocabulary",
    "record_text",
]
