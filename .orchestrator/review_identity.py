"""Canonical identity equivalence for independent task review.

Agent labels identify dispatch lanes, not necessarily independent accounts.
Review policy must compare the human/account identity behind those labels before
it treats an owner/reviewer pair as independent.
"""

from __future__ import annotations

import re
from typing import Any


_CODEX_CHATGPT_LANE_ALIASES = frozenset({"codex", "codex2", "codex3"})


def normalize_review_identity(value: Any) -> str:
    """Return the account-equivalence identity used by review gates."""

    label = str(value or "").strip().casefold()
    if not label:
        return ""
    compact = re.sub(r"[\s_()/.\-]+", "", label)
    if compact in _CODEX_CHATGPT_LANE_ALIASES:
        return "chatgpt:codex"
    return f"agent:{label}"


def review_identities_match(left: Any, right: Any) -> bool:
    """Whether two non-empty agent labels resolve to one review identity."""

    left_identity = normalize_review_identity(left)
    right_identity = normalize_review_identity(right)
    return bool(left_identity) and left_identity == right_identity


def review_identities_are_independent(owner: Any, reviewer: Any) -> bool:
    """Whether a reviewer is present and independent from the task owner."""

    return bool(normalize_review_identity(reviewer)) and not review_identities_match(
        owner, reviewer
    )
