"""Canonical task-contract rules shared by materialization and scheduling."""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


def acceptance_identity_mentions(
    acceptance: Any,
    identities: Iterable[str],
) -> dict[str, list[str]]:
    """Return configured agent identities explicitly named by acceptance text."""

    raw_lines = [acceptance] if isinstance(acceptance, str) else (acceptance or [])
    lines = [str(value) for value in raw_lines if str(value).strip()]
    mentions: dict[str, list[str]] = {}
    for raw_identity in identities:
        identity = str(raw_identity or "").strip()
        if not identity:
            continue
        pattern = re.compile(
            rf"(?<![\w]){re.escape(identity)}(?![\w])",
            re.IGNORECASE,
        )
        matched = [line for line in lines if pattern.search(line)]
        if matched:
            mentions[identity] = matched
    return mentions


def validate_role_based_acceptance(
    acceptance: Any,
    identities: Iterable[str],
) -> None:
    """Require new task acceptance to describe roles, not fleet slots."""

    mentions = acceptance_identity_mentions(acceptance, identities)
    if mentions:
        raise ValueError(
            "task acceptance must use owner/reviewer role names instead of "
            "configured agent identities: " + ", ".join(sorted(mentions))
        )


def validate_reassignment_against_acceptance(
    task: Mapping[str, Any],
    *,
    new_owner: str,
    new_reviewer: str,
) -> None:
    """Refuse silently invalidating a legacy identity-pinned contract."""

    changed_identities: list[str] = []
    old_owner = str(task.get("owner") or "").strip()
    old_reviewer = str(task.get("reviewer") or "").strip()
    if old_owner and old_owner.casefold() != str(new_owner or "").strip().casefold():
        changed_identities.append(old_owner)
    if old_reviewer and old_reviewer.casefold() != str(new_reviewer or "").strip().casefold():
        changed_identities.append(old_reviewer)
    mentions = acceptance_identity_mentions(task.get("acceptance"), changed_identities)
    if mentions:
        raise ValueError(
            "reassignment would contradict identity-pinned acceptance; supersede "
            "the task with role-based acceptance first: "
            + ", ".join(sorted(mentions))
        )
