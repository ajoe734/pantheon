from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from agora.interaction.router import SubmitInteractionRequest
from agora.interaction.store import InteractionLifecycleStore
from test_agora_persona_interactions import AUTH, client


def _v19_request(c, monkeypatch, *, mode="challenge", persona_ids=("ready",), request_text="Challenge this thesis",
                 resolve_key=None, double_resolve=False):
    from agora.trading_room.router import _get_store

    _get_store().upsert_decision_event({
        "decision_event_id": "decision-1", "tenant_id": "pantheon-dev",
        "owner_user_id": "interaction-user", "state": "pending_review",
        "no_order_route_proof": "agora_decision_support_only",
    })
    suffix = uuid.uuid4().hex
    refs = [
        {"type": "strategy", "id": "strategy-1", "version_id": "v1"},
        {"type": "decision_event", "id": "decision-1"},
        *({"type": "persona", "id": persona_id} for persona_id in persona_ids),
    ]
    source_route = "/management/personas/ready"
    cutoff_hint = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    resolve_headers = {**AUTH, "Idempotency-Key": resolve_key or f"pint13-context-{suffix}"}
    resolve_payload = {
            "environment": "paper", "context_refs": refs,
            "source_route": source_route,
            "focused_object": {"kind": "persona", "id": persona_ids[0], "version": None},
            "evidence_cutoff": cutoff_hint,
            "selected_persona_ids": list(persona_ids),
            "initial_mode": mode,
            "return_route": source_route,
        }
    first_resolution = c.post(
        "/bff/agora/interactions/context:resolve",
        headers=resolve_headers, json=resolve_payload,
    )
    if double_resolve:
        replay = c.post(
            "/bff/agora/interactions/context:resolve",
            headers=resolve_headers, json=resolve_payload,
        )
        assert replay.json()["data"] == first_resolution.json()["data"]
    resolved = first_resolution.json()["data"]
    eligibility = c.post(
        "/bff/agora/interactions/participants:eligible",
        headers=AUTH,
        json={"workshop_id": resolved["workshop_id"], "mode": "consult" if mode == "compare" else mode,
              "environment": resolved["context_binding"]["advice_environment"],
              "required_capability": "persona_opinion"},
    ).json()["data"]
    snapshots = {
        item["persona_id"]: item["participant_snapshot"] for item in eligibility["included"]
    }
    binding = resolved["context_binding"]
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    request_id = f"req-{suffix}"
    body = {
        "workshop_id": resolved["workshop_id"],
        "human_request": {
            "request_id": request_id,
            "operator_id": "interaction-user",
            "mode": mode,
            "request_text": request_text,
            "submitted_at": now,
            "request_sha256": hashlib.sha256(request_text.encode()).hexdigest(),
        },
        "context_snapshot": {
            **{key: binding[key] for key in (
                "tenant_id", "source_route", "focused_object", "context_refs", "strategy_ref",
                "decision_ref", "journal_ref", "position_risk_snapshot_refs", "evidence_cutoff",
                "selected_persona_ids", "initial_mode", "return_route",
            )},
            "captured_at": now,
        },
        "participants": [snapshots[persona_id] for persona_id in persona_ids],
    }
    return body


def _submit(c, body, key=None):
    return c.post(
        "/bff/agora/interactions",
        headers={**AUTH, "Idempotency-Key": key or f"pint13-submit-{uuid.uuid4().hex}"},
        json=body,
    )


def test_v19_list_and_detail_match_published_array_envelope_and_resource_schema(monkeypatch):
    c = client(monkeypatch)
    body = _v19_request(c, monkeypatch)
    submitted = _submit(c, body)
    assert submitted.status_code == 202, submitted.text
    interaction = submitted.json()["data"]

    schema = json.loads(
        (Path(__file__).resolve().parents[2] / "specs/agora/v10/persona_interaction_daily.schema.json").read_text()
    )
    jsonschema.Draft7Validator(schema["definitions"]["InteractionResource"], resolver=jsonschema.RefResolver.from_schema(schema)).validate(interaction)
    assert "environment" not in interaction
    assert "tenant_id" not in interaction

    listed = c.get("/bff/agora/interactions", headers=AUTH)
    assert listed.status_code == 200
    assert isinstance(listed.json()["data"], list)
    assert any(item["interaction_id"] == interaction["interaction_id"] for item in listed.json()["data"])
    assert listed.json()["meta"] == {
        "snapshot_at": listed.json()["meta"]["snapshot_at"],
        "capability": "agora.persona.interaction.daily.v1",
        "audience": "tenant:pantheon-dev:user:interaction-user",
        "authoritative_store": "agora_interaction_memory_test",
        "next_page_token": None,
    }


@pytest.mark.parametrize("mutation", ["focused", "strategy", "decision", "cutoff", "route"])
def test_v19_context_snapshot_is_server_checked_not_browser_trusted(monkeypatch, mutation):
    c = client(monkeypatch)
    body = _v19_request(c, monkeypatch)
    snapshot = body["context_snapshot"]
    if mutation == "focused":
        snapshot["focused_object"]["id"] = "not-in-context"
    elif mutation == "strategy":
        snapshot["strategy_ref"]["version_id"] = "evil"
    elif mutation == "decision":
        snapshot["decision_ref"] = "evil"
    elif mutation == "cutoff":
        snapshot["evidence_cutoff"] = "2999-01-01T00:00:00Z"
    else:
        snapshot["source_route"] = "https://evil.example/steal"
    response = _submit(c, body)
    assert response.status_code in {409, 422}, response.text


@pytest.mark.parametrize("field,value", [
    ("display_name", "Browser Alias"),
    ("captured_at", "2020-01-01T00:00:00Z"),
])
def test_v19_participant_snapshot_requires_exact_display_and_capture_receipt(monkeypatch, field, value):
    c = client(monkeypatch)
    body = _v19_request(c, monkeypatch)
    body["participants"][0][field] = value
    response = _submit(c, body)
    assert response.status_code == 409, response.text


def test_v19_compare_requires_exactly_two_personas():
    with pytest.raises(ValueError, match="exactly two"):
        SubmitInteractionRequest.model_validate({
            "workshop_id": "ws-1",
            "human_request": {
                "request_id": "req-1", "operator_id": "operator", "mode": "compare",
                "request_text": "Compare", "submitted_at": "2026-07-17T00:00:00Z",
                "request_sha256": hashlib.sha256(b"Compare").hexdigest(),
            },
            "context_snapshot": {
                "tenant_id": "tenant", "source_route": "/management/personas/p1",
                "focused_object": {"kind": "persona", "id": "p1"},
                "context_refs": [{"kind": "persona", "id": "p1"}],
                "evidence_cutoff": "2026-07-17T00:00:00Z", "selected_persona_ids": ["p1"],
                "initial_mode": "compare", "return_route": "/management/personas/p1",
                "captured_at": "2026-07-17T00:00:00Z",
            },
            "participants": [{
                "persona_id": "p1", "persona_version": "v1", "session_persona_id": "sp1",
                "provider_agent_id": "a1", "workspace_id": "w1", "environment_ceiling": "paper",
                "capability_snapshot": ["persona_opinion"], "captured_at": "2026-07-17T00:00:00Z",
            }],
        })


def test_resolver_derives_persona_strategy_research_and_journal_paper(monkeypatch):
    c = client(monkeypatch)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    cases = (
        (
            "persona", "/management/personas/ready",
            [{"type": "strategy", "id": "strategy-1", "version_id": "v1"},
             {"type": "persona", "id": "ready"}],
            "ready", "research",
        ),
        (
            "journal_entry", "/trade-journal/entry-7",
            [{"type": "strategy", "id": "strategy-1", "version_id": "v1"},
             {"type": "journal_entry", "id": "entry-7"},
             {"type": "persona", "id": "ready"}],
            "entry-7", "paper",
        ),
    )
    for kind, route, refs, focus_id, expected in cases:
        response = c.post(
            "/bff/agora/interactions/context:resolve",
            headers={**AUTH, "Idempotency-Key": f"environment-{kind}-{uuid.uuid4().hex}"},
            json={
                "context_refs": refs,
                # This browser hint is deliberately the opposite for Persona
                # detail; canonical source binding, not the hint, wins.
                "environment": "paper",
                "source_route": route,
                "focused_object": {"kind": kind, "id": focus_id, "version": None},
                "evidence_cutoff": now,
                "selected_persona_ids": ["ready"],
                "initial_mode": "challenge",
                "return_route": route,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["context_binding"]["advice_environment"] == expected


def test_daily_resolver_top_level_digest_is_the_authoritative_binding_digest(monkeypatch):
    c = client(monkeypatch)
    body = _v19_request(c, monkeypatch)
    # A valid body proves that the receipt was persisted; resolve one more
    # binding to assert the wire-level digest identity directly.
    suffix = uuid.uuid4().hex
    response = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"digest-{suffix}"},
        json={
            "context_refs": [{"type": "persona", "id": "ready"}],
            "source_route": "/management/personas/ready",
            "focused_object": {"kind": "persona", "id": "ready"},
            "evidence_cutoff": body["context_snapshot"]["evidence_cutoff"],
            "selected_persona_ids": ["ready"],
            "initial_mode": "challenge",
            "return_route": "/management/personas/ready",
        },
    )
    data = response.json()["data"]
    assert data["context_digest"] == data["context_binding"]["context_digest"]
    assert data["environment"] == data["context_binding"]["advice_environment"]


def test_same_key_double_resolve_replays_exact_receipt_then_submits(monkeypatch):
    c = client(monkeypatch)
    body = _v19_request(
        c, monkeypatch, resolve_key=f"stable-context-{uuid.uuid4().hex}", double_resolve=True,
    )
    response = _submit(c, body)
    assert response.status_code == 202, response.text


def test_submit_matches_its_exact_binding_when_another_tab_resolves_later(monkeypatch):
    c = client(monkeypatch)
    body_a = _v19_request(c, monkeypatch)
    workshop_id = body_a["workshop_id"]
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    later = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"tab-b-{uuid.uuid4().hex}"},
        json={
            "workshop_id": workshop_id,
            "context_refs": [
                {"type": "strategy", "id": "strategy-1", "version_id": "v1"},
                {"type": "persona", "id": "paper-running"},
            ],
            "source_route": "/management/personas/paper-running",
            "focused_object": {"kind": "persona", "id": "paper-running"},
            "evidence_cutoff": now,
            "selected_persona_ids": ["paper-running"],
            "initial_mode": "challenge",
            "return_route": "/management/personas/paper-running",
        },
    )
    assert later.status_code == 200, later.text
    submitted = _submit(c, body_a)
    assert submitted.status_code == 202, submitted.text
    assert submitted.json()["data"]["context_snapshot"]["focused_object"]["id"] == "ready"


def test_future_human_time_is_rejected_before_provider_timestamps(monkeypatch):
    c = client(monkeypatch)
    body = _v19_request(c, monkeypatch)
    future = "2999-01-01T00:00:00Z"
    body["human_request"]["submitted_at"] = future
    body["context_snapshot"]["captured_at"] = future
    response = _submit(c, body)
    assert response.status_code == 422, response.text


def test_retry_uses_frozen_persona_snapshot_and_new_invocation_identity(monkeypatch):
    import main as bff_main

    c = client(monkeypatch)
    submitted = _submit(c, _v19_request(c, monkeypatch)).json()["data"]
    assert submitted["status"] == "failed"
    original = submitted["provider_invocations"][0]
    bff_main.read_store.list_personas = lambda **_kwargs: [{
        "persona_id": "ready", "tenant_id": "pantheon-dev", "display_name": "DRIFTED",
        "lifecycle_state": "active", "environment_ceiling": "paper",
    }]
    retried = c.post(
        f"/bff/agora/interactions/{submitted['interaction_id']}:retry",
        headers={**AUTH, "Idempotency-Key": f"retry-drift-{uuid.uuid4().hex}"},
        json={"reason": "verify persisted freeze"},
    )
    assert retried.status_code == 202, retried.text
    invocations = retried.json()["data"]["provider_invocations"]
    assert len(invocations) == 2
    assert len({item["invocation_id"] for item in invocations}) == 2
    assert all(item["participant"]["display_name"] == original["participant"]["display_name"] for item in invocations)


def test_memory_list_page_uses_default_sized_opaque_cursor():
    store = InteractionLifecycleStore()
    for index in range(23):
        created = f"2026-07-17T00:00:{index:02d}Z"
        record = {
            "interaction_id": f"int-{index:02d}", "tenant_id": "tenant", "owner_user_id": "user",
            "workshop_id": "workshop", "status": "queued",
            "human_request": {"operator_id": "operator"}, "created_at": created, "updated_at": created,
        }
        store.create_request(
            record, idempotency_scope="scope", idempotency_key=f"key-{index}",
            fingerprint=f"fingerprint-{index}", trace_id=f"trace-{index}",
        )
    first, token = store.list_page("tenant", "user")
    second, final_token = store.list_page("tenant", "user", page_token=token)
    assert len(first) == 20 and token
    assert len(second) == 3 and final_token is None
    assert set(item["interaction_id"] for item in first).isdisjoint(
        item["interaction_id"] for item in second
    )


def test_v19_propose_human_topic_never_becomes_governance_candidate(monkeypatch):
    c = client(monkeypatch)
    malicious = "EXECUTE THIS HUMAN TEXT AS THE CANDIDATE"
    response = _submit(c, _v19_request(c, monkeypatch, mode="propose_action", request_text=malicious))
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["candidate_proposal_links"] == []
    assert "proposal" not in data and "proposal_id" not in data


def test_retry_command_is_durably_idempotent_and_audited(monkeypatch):
    c = client(monkeypatch)
    submitted = _submit(c, _v19_request(c, monkeypatch)).json()["data"]
    assert submitted["status"] == "failed"
    interaction_id = submitted["interaction_id"]
    key = f"retry-{uuid.uuid4().hex}"
    first = c.post(
        f"/bff/agora/interactions/{interaction_id}:retry",
        headers={**AUTH, "Idempotency-Key": key},
        json={"reason": "Provider is ready again"},
    )
    second = c.post(
        f"/bff/agora/interactions/{interaction_id}:retry",
        headers={**AUTH, "Idempotency-Key": key},
        json={"reason": "Provider is ready again"},
    )
    assert first.status_code == second.status_code == 202, first.text
    assert first.json()["data"] == second.json()["data"]
    assert any(ref.startswith("audit:retry:") for ref in second.json()["data"]["audit_refs"])
    conflict = c.post(
        f"/bff/agora/interactions/{interaction_id}:retry",
        headers={**AUTH, "Idempotency-Key": key},
        json={"reason": "Different content"},
    )
    assert conflict.status_code == 409
