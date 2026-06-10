from __future__ import annotations

import pytest

from services.capital.binding_live.sponsor_responsibility import (
    ACTIVE_RESPONSIBILITY_STATUS,
    REQUIRED_ESCALATION_FIELDS,
    REQUIRED_LIVE_OWNER_FIELDS,
    REQUIRED_TOP_LEVEL_FIELDS,
    RESPONSIBILITY_PACKET_VERSION,
    SCHEMA_VERSION,
    SponsorPersonaResponsibility,
    SponsorPersonaResponsibilityError,
    validate_sponsor_responsibility,
)


def _responsibility_packet(**overrides):
    packet = {
        "responsibility_id": "sponsor-resp-001",
        "sponsor_persona_id": "persona-alpha",
        "binding_id": "binding-live-001",
        "capital_pool_id": "pool-main",
        "live_owner": {
            "owner_id": "ops-live-owner",
            "role": "live_owner",
            "binding_id": "binding-live-001",
            "mandate_ref": "support/evidence/CBL/sponsor-mandate.json",
            "contact_ref": "ops://live-owner/on-call",
        },
        "escalation_chain": [
            {
                "level": 1,
                "owner_id": "risk-owner-1",
                "role": "risk_owner",
                "trigger": "risk_limit_breach",
                "action": "pause_and_review",
                "evidence_ref": "support/evidence/CBL/risk-escalation.json",
            },
            {
                "level": 2,
                "owner_id": "operator-1",
                "role": "operator",
                "trigger": "live_owner_unavailable",
                "action": "manual_intervention",
                "evidence_ref": "support/evidence/CBL/operator-escalation.json",
            },
        ],
        "policy_refs": [
            "BINDING_AND_DEPLOYMENT_SEMANTICS.md#3.4",
            "MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md#11-v1-decisions",
        ],
        "status": "active",
    }
    packet.update(overrides)
    return packet


def test_sponsor_responsibility_round_trips_schema_subtrees() -> None:
    packet = SponsorPersonaResponsibility.from_dict(_responsibility_packet())

    assert packet.schema_version == SCHEMA_VERSION
    assert packet.packet_version == RESPONSIBILITY_PACKET_VERSION
    assert packet.status == ACTIVE_RESPONSIBILITY_STATUS
    assert packet.sponsor_persona_id == "persona-alpha"
    assert packet.live_owner.owner_id == "ops-live-owner"
    assert packet.escalation_chain[0].role == "risk_owner"

    encoded = packet.to_dict()
    assert set(encoded) >= set(REQUIRED_TOP_LEVEL_FIELDS)
    assert tuple(encoded["live_owner"])[:3] == REQUIRED_LIVE_OWNER_FIELDS
    assert tuple(encoded["escalation_chain"][0])[:5] == REQUIRED_ESCALATION_FIELDS
    assert encoded["escalation_chain"][1]["level"] == 2
    assert validate_sponsor_responsibility(encoded).responsibility_id == "sponsor-resp-001"


def test_active_responsibility_requires_escalation_chain() -> None:
    packet = _responsibility_packet(escalation_chain=[])

    with pytest.raises(SponsorPersonaResponsibilityError, match="non-empty escalation_chain"):
        SponsorPersonaResponsibility.from_dict(packet)


def test_escalation_levels_must_be_contiguous_from_one() -> None:
    packet = _responsibility_packet()
    packet["escalation_chain"][0]["level"] = 2

    with pytest.raises(SponsorPersonaResponsibilityError, match="contiguous"):
        SponsorPersonaResponsibility.from_dict(packet)


def test_live_owner_binding_must_match_packet_binding() -> None:
    live_owner = dict(_responsibility_packet()["live_owner"])
    live_owner["binding_id"] = "binding-other"

    with pytest.raises(SponsorPersonaResponsibilityError, match="live_owner.binding_id"):
        SponsorPersonaResponsibility.from_dict(_responsibility_packet(live_owner=live_owner))


def test_live_owner_role_must_be_live_owner() -> None:
    live_owner = dict(_responsibility_packet()["live_owner"])
    live_owner["role"] = "advisor"

    with pytest.raises(SponsorPersonaResponsibilityError, match="live_owner.role"):
        SponsorPersonaResponsibility.from_dict(_responsibility_packet(live_owner=live_owner))


def test_revoked_responsibility_can_round_trip_without_escalation_chain() -> None:
    packet = _responsibility_packet(status="revoked", escalation_chain=[])

    responsibility = validate_sponsor_responsibility(packet)

    assert responsibility.status == "revoked"
    assert responsibility.escalation_chain == ()
