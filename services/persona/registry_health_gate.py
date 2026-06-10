"""Pre-synthesis persona registry health gate.

This module wraps the PER-001 PersonaRegistry surface without importing or
modifying its contract.  Callers may pass the PER-001 registry object, an
iterable of PER-001 Persona records, or persona-shaped mappings.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any


DEFAULT_SPONSOR_ROLES = frozenset({"sponsor", "paper_owner", "live_owner"})
SPONSOR_LIFECYCLE_STATES = frozenset({"paper_owner", "live_owner"})
EXCLUDED_ADMIN_STATUSES = frozenset({"suspended"})
EXCLUDED_LIFECYCLE_STATES = frozenset({"retired"})


@dataclass(frozen=True)
class RoleAssignment:
    """A pre-synthesis persona role assignment for one pool/scope context."""

    persona_id: str
    role: str
    capital_pool_id: str | None = None
    scope_ref: str | None = None
    source_ref: str | None = None

    @classmethod
    def from_value(cls, value: "RoleAssignment | Mapping[str, Any]") -> "RoleAssignment":
        if isinstance(value, cls):
            return value
        if not isinstance(value, Mapping):
            raise TypeError(
                f"role assignment must be RoleAssignment or mapping, got {type(value)!r}"
            )
        return cls(
            persona_id=str(value.get("persona_id") or "").strip(),
            role=str(value.get("role") or "").strip(),
            capital_pool_id=_optional_string(value.get("capital_pool_id")),
            scope_ref=_optional_string(value.get("scope_ref")),
            source_ref=_optional_string(value.get("source_ref")),
        )


@dataclass(frozen=True)
class RegistryHealthIssue:
    """A deterministic issue emitted by the registry health gate."""

    code: str
    message: str
    persona_id: str | None = None
    role: str | None = None
    capital_pool_id: str | None = None
    scope_ref: str | None = None
    source_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class RegistryHealthGateResult:
    """Output of the pre-synthesis registry health gate."""

    sponsor_candidate_ids: tuple[str, ...]
    excluded_persona_ids: tuple[str, ...]
    blocked_sponsor_ids: tuple[str, ...]
    committee_review: bool
    issues: tuple[RegistryHealthIssue, ...]

    @property
    def issue_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

    @property
    def passed(self) -> bool:
        return not self.blocked_sponsor_ids and not self.excluded_persona_ids

    def to_dict(self) -> dict[str, Any]:
        return {
            "sponsor_candidate_ids": list(self.sponsor_candidate_ids),
            "excluded_persona_ids": list(self.excluded_persona_ids),
            "blocked_sponsor_ids": list(self.blocked_sponsor_ids),
            "committee_review": self.committee_review,
            "issues": [issue.to_dict() for issue in self.issues],
            "passed": self.passed,
        }


def evaluate_registry_health(
    persona_source: Any,
    role_assignments: Sequence[RoleAssignment | Mapping[str, Any]] | None = None,
    *,
    sponsor_roles: Iterable[str] = DEFAULT_SPONSOR_ROLES,
) -> RegistryHealthGateResult:
    """Evaluate which personas may enter pre-synthesis sponsor selection.

    Rules enforced by this gate:
    - suspended administrative status is excluded from sponsor candidates
    - retired lifecycle state is excluded from sponsor candidates
    - blank mandate blocks sponsor eligibility
    - conflicting sponsor role assignments require committee review

    The function intentionally accepts persona-shaped objects by attribute or
    mapping lookup so it can wrap the PER-001 registry without changing its
    public API.
    """

    normalized_sponsor_roles = frozenset(_normalize_role(role) for role in sponsor_roles)
    assignments = tuple(RoleAssignment.from_value(value) for value in role_assignments or ())
    personas = tuple(_iter_personas(persona_source))
    persona_by_id = _personas_by_id(personas)

    issues: list[RegistryHealthIssue] = []
    committee_review = False

    role_conflict_issues = _role_conflict_issues(assignments, normalized_sponsor_roles)
    if role_conflict_issues:
        committee_review = True
        issues.extend(role_conflict_issues)

    candidate_ids = _initial_candidate_ids(personas, assignments, normalized_sponsor_roles)
    sponsor_candidate_ids: list[str] = []
    excluded_persona_ids: list[str] = []
    blocked_sponsor_ids: list[str] = []

    for persona_id in candidate_ids:
        persona = persona_by_id.get(persona_id)
        assignment = _first_sponsor_assignment(persona_id, assignments, normalized_sponsor_roles)
        issue_context = _issue_context(persona_id, assignment)

        if persona is None:
            issues.append(
                RegistryHealthIssue(
                    code="persona_missing",
                    message=f"Persona {persona_id!r} is assigned a sponsor role but is not present in the registry.",
                    **issue_context,
                )
            )
            blocked_sponsor_ids.append(persona_id)
            continue

        admin_status = _string_field(persona, "status").lower()
        lifecycle_state = _string_field(persona, "lifecycle_state").lower()

        if admin_status in EXCLUDED_ADMIN_STATUSES:
            issues.append(
                RegistryHealthIssue(
                    code="persona_suspended",
                    message=f"Persona {persona_id!r} is suspended and cannot enter sponsor selection.",
                    **issue_context,
                )
            )
            excluded_persona_ids.append(persona_id)
            continue

        if lifecycle_state in EXCLUDED_LIFECYCLE_STATES:
            issues.append(
                RegistryHealthIssue(
                    code="persona_retired",
                    message=f"Persona {persona_id!r} is retired and cannot enter sponsor selection.",
                    **issue_context,
                )
            )
            excluded_persona_ids.append(persona_id)
            continue

        if not _string_field(persona, "mandate").strip():
            issues.append(
                RegistryHealthIssue(
                    code="missing_mandate",
                    message=f"Persona {persona_id!r} has no mandate and cannot hold a sponsor role.",
                    **issue_context,
                )
            )
            blocked_sponsor_ids.append(persona_id)
            continue

        if not _lifecycle_allows_sponsorship(persona):
            issues.append(
                RegistryHealthIssue(
                    code="lifecycle_not_sponsor_eligible",
                    message=(
                        f"Persona {persona_id!r} lifecycle_state={lifecycle_state!r} "
                        "does not permit deployment sponsorship."
                    ),
                    **issue_context,
                )
            )
            blocked_sponsor_ids.append(persona_id)
            continue

        sponsor_candidate_ids.append(persona_id)

    return RegistryHealthGateResult(
        sponsor_candidate_ids=tuple(_ordered_unique(sponsor_candidate_ids)),
        excluded_persona_ids=tuple(_ordered_unique(excluded_persona_ids)),
        blocked_sponsor_ids=tuple(_ordered_unique(blocked_sponsor_ids)),
        committee_review=committee_review,
        issues=tuple(issues),
    )


def sponsor_candidate_ids(
    persona_source: Any,
    role_assignments: Sequence[RoleAssignment | Mapping[str, Any]] | None = None,
    *,
    sponsor_roles: Iterable[str] = DEFAULT_SPONSOR_ROLES,
) -> tuple[str, ...]:
    """Convenience wrapper returning only eligible sponsor candidate ids."""

    return evaluate_registry_health(
        persona_source,
        role_assignments,
        sponsor_roles=sponsor_roles,
    ).sponsor_candidate_ids


evaluate_pre_synthesis_registry_health = evaluate_registry_health


def _iter_personas(persona_source: Any) -> Iterable[Any]:
    if persona_source is None:
        return ()
    list_method = getattr(persona_source, "list", None)
    if callable(list_method):
        return tuple(list_method())
    if isinstance(persona_source, Mapping):
        return tuple(persona_source.values())
    return tuple(persona_source)


def _personas_by_id(personas: Iterable[Any]) -> dict[str, Any]:
    by_id: dict[str, Any] = {}
    for persona in personas:
        persona_id = _string_field(persona, "persona_id")
        if persona_id:
            by_id[persona_id] = persona
    return by_id


def _initial_candidate_ids(
    personas: Sequence[Any],
    assignments: Sequence[RoleAssignment],
    sponsor_roles: frozenset[str],
) -> tuple[str, ...]:
    if assignments:
        return tuple(
            _ordered_unique(
                assignment.persona_id
                for assignment in assignments
                if assignment.persona_id and _normalize_role(assignment.role) in sponsor_roles
            )
        )
    return tuple(
        _ordered_unique(
            _string_field(persona, "persona_id")
            for persona in personas
            if _string_field(persona, "persona_id") and _lifecycle_allows_sponsorship(persona)
        )
    )


def _role_conflict_issues(
    assignments: Sequence[RoleAssignment],
    sponsor_roles: frozenset[str],
) -> list[RegistryHealthIssue]:
    issues: list[RegistryHealthIssue] = []
    sponsors_by_context: dict[tuple[str, str], list[RoleAssignment]] = defaultdict(list)
    roles_by_persona_context: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    for assignment in assignments:
        role = _normalize_role(assignment.role)
        if role not in sponsor_roles or not assignment.persona_id:
            continue
        context = (assignment.capital_pool_id or "", assignment.scope_ref or "")
        sponsors_by_context[context].append(assignment)
        roles_by_persona_context[(assignment.persona_id, *context)].add(role)

    for (capital_pool_id, scope_ref), context_assignments in sponsors_by_context.items():
        persona_ids = _ordered_unique(assignment.persona_id for assignment in context_assignments)
        if len(persona_ids) > 1:
            issues.append(
                RegistryHealthIssue(
                    code="conflicting_sponsor_assignments",
                    message=(
                        "Multiple personas are assigned sponsor roles for the same "
                        f"pool/scope context: {', '.join(persona_ids)}."
                    ),
                    capital_pool_id=capital_pool_id or None,
                    scope_ref=scope_ref or None,
                )
            )

    for (persona_id, capital_pool_id, scope_ref), roles in roles_by_persona_context.items():
        if len(roles) > 1:
            issues.append(
                RegistryHealthIssue(
                    code="conflicting_persona_roles",
                    message=(
                        f"Persona {persona_id!r} has multiple sponsor roles in the same "
                        f"pool/scope context: {', '.join(sorted(roles))}."
                    ),
                    persona_id=persona_id,
                    capital_pool_id=capital_pool_id or None,
                    scope_ref=scope_ref or None,
                )
            )

    return issues


def _first_sponsor_assignment(
    persona_id: str,
    assignments: Sequence[RoleAssignment],
    sponsor_roles: frozenset[str],
) -> RoleAssignment | None:
    for assignment in assignments:
        if assignment.persona_id == persona_id and _normalize_role(assignment.role) in sponsor_roles:
            return assignment
    return None


def _issue_context(persona_id: str, assignment: RoleAssignment | None) -> dict[str, str | None]:
    return {
        "persona_id": persona_id,
        "role": assignment.role if assignment else None,
        "capital_pool_id": assignment.capital_pool_id if assignment else None,
        "scope_ref": assignment.scope_ref if assignment else None,
        "source_ref": assignment.source_ref if assignment else None,
    }


def _lifecycle_allows_sponsorship(persona: Any) -> bool:
    lifecycle_state = _field(persona, "lifecycle_state")
    allows_method = getattr(lifecycle_state, "allows_deployment_sponsorship", None)
    if callable(allows_method):
        return bool(allows_method())
    lifecycle_text = _string_value(lifecycle_state).lower()
    return lifecycle_text in SPONSOR_LIFECYCLE_STATES


def _field(value: Any, field_name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field_name)
    return getattr(value, field_name, None)


def _string_field(value: Any, field_name: str) -> str:
    return _string_value(_field(value, field_name))


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value).strip()
    return str(value).strip()


def _optional_string(value: Any) -> str | None:
    text = _string_value(value)
    return text or None


def _normalize_role(role: str) -> str:
    return str(role or "").strip().lower()


def _ordered_unique(values: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered
