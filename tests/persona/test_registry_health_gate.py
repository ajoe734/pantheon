from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PERSONA_REGISTRY_DIR = ROOT / "services" / "control-plane" / "persona"
if str(PERSONA_REGISTRY_DIR) not in sys.path:
    sys.path.insert(0, str(PERSONA_REGISTRY_DIR))

from persona_registry import Persona, PersonaRegistry  # noqa: E402
from services.persona.registry_health_gate import (  # noqa: E402
    RoleAssignment,
    evaluate_registry_health,
    sponsor_candidate_ids,
)


def make_persona(**overrides: object) -> Persona:
    values = {
        "persona_id": "persona-alpha",
        "name": "Alpha",
        "mandate": "Sponsor multi-persona allocation synthesis",
        "lifecycle_state": "live_owner",
        "created_at": "2026-05-20T00:00:00Z",
    }
    values.update(overrides)
    return Persona(**values)


def test_suspended_and_retired_personas_are_excluded_from_sponsor_candidates() -> None:
    registry = PersonaRegistry()
    registry.create(make_persona(persona_id="persona-active"))
    registry.create(make_persona(persona_id="persona-suspended", status="suspended"))
    registry.create(make_persona(persona_id="persona-retired", lifecycle_state="retired"))

    result = evaluate_registry_health(
        registry,
        [
            {
                "persona_id": "persona-active",
                "role": "sponsor",
                "capital_pool_id": "pool-1",
                "scope_ref": "live",
            },
            {
                "persona_id": "persona-suspended",
                "role": "sponsor",
                "capital_pool_id": "pool-2",
                "scope_ref": "live",
            },
            {
                "persona_id": "persona-retired",
                "role": "sponsor",
                "capital_pool_id": "pool-3",
                "scope_ref": "live",
            },
        ],
    )

    assert result.sponsor_candidate_ids == ("persona-active",)
    assert result.excluded_persona_ids == ("persona-suspended", "persona-retired")
    assert "persona_suspended" in result.issue_codes
    assert "persona_retired" in result.issue_codes


def test_missing_mandate_blocks_sponsor_role() -> None:
    result = evaluate_registry_health(
        [make_persona(persona_id="persona-no-mandate", mandate="   ")],
        [
            RoleAssignment(
                persona_id="persona-no-mandate",
                role="live_owner",
                capital_pool_id="pool-1",
                scope_ref="live",
            )
        ],
    )

    assert result.sponsor_candidate_ids == ()
    assert result.blocked_sponsor_ids == ("persona-no-mandate",)
    assert "missing_mandate" in result.issue_codes


def test_conflicting_sponsor_assignments_require_committee_review() -> None:
    result = evaluate_registry_health(
        [
            make_persona(persona_id="persona-alpha"),
            make_persona(persona_id="persona-beta"),
        ],
        [
            RoleAssignment("persona-alpha", "sponsor", capital_pool_id="pool-1", scope_ref="live"),
            RoleAssignment("persona-beta", "live_owner", capital_pool_id="pool-1", scope_ref="live"),
        ],
    )

    assert result.sponsor_candidate_ids == ("persona-alpha", "persona-beta")
    assert result.committee_review is True
    assert "conflicting_sponsor_assignments" in result.issue_codes


def test_conflicting_sponsor_roles_for_same_persona_require_committee_review() -> None:
    result = evaluate_registry_health(
        [make_persona(persona_id="persona-alpha")],
        [
            RoleAssignment("persona-alpha", "paper_owner", capital_pool_id="pool-1", scope_ref="paper"),
            RoleAssignment("persona-alpha", "live_owner", capital_pool_id="pool-1", scope_ref="paper"),
        ],
    )

    assert result.sponsor_candidate_ids == ("persona-alpha",)
    assert result.committee_review is True
    assert "conflicting_persona_roles" in result.issue_codes


def test_without_role_assignments_uses_per001_lifecycle_sponsor_eligibility() -> None:
    registry = PersonaRegistry()
    registry.create(make_persona(persona_id="persona-live", lifecycle_state="live_owner"))
    registry.create(make_persona(persona_id="persona-paper", lifecycle_state="paper_owner"))
    registry.create(make_persona(persona_id="persona-research", lifecycle_state="research_only"))
    registry.create(
        make_persona(
            persona_id="persona-suspended",
            lifecycle_state="live_owner",
            status="suspended",
        )
    )

    assert sponsor_candidate_ids(registry) == ("persona-live", "persona-paper")
