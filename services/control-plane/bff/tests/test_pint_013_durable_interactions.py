from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import pytest

from services.control_plane.bff import main as bff_main

from services.control_plane.bff.agora.interaction.router import SubmitInteractionRequest
from services.control_plane.bff.agora.interaction.store import InteractionLifecycleStore
from services.control_plane.bff.agora.interaction.worker import AgoraInteractionWorker
from test_agora_persona_interactions import AUTH, client


def _v19_request(c, monkeypatch, *, mode="challenge", persona_ids=("ready",), request_text="Challenge this thesis",
                 resolve_key=None, double_resolve=False):
    from services.control_plane.bff.agora.trading_room.router import _get_store

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


def test_replay_persists_only_returned_receipt_and_eligibility_uses_it(monkeypatch):
    """A replay candidate from a later clock tick must never become canonical."""
    from services.control_plane.bff import main as bff_main

    c = client(monkeypatch)
    real_datetime = datetime

    class TickingDateTime(real_datetime):
        current = real_datetime.now(timezone.utc).replace(microsecond=0)

        @classmethod
        def now(cls, tz=None):
            cls.current += timedelta(seconds=2)
            value = cls.current
            return value if tz is not None else value.replace(tzinfo=None)

    # models.utc_now() is the exact callable captured by the already-mounted
    # router.  Advancing it makes the discarded replay candidate observably
    # different from the first receipt instead of relying on second-level
    # wall-clock timing.
    monkeypatch.setitem(bff_main.utc_now.__globals__, "datetime", TickingDateTime)

    original_save = InteractionLifecycleStore.save_context_binding
    saves = []

    def recording_save(store, binding, *, owner_user_id):
        before = len(store._context_bindings)
        saved = original_save(store, binding, owner_user_id=owner_user_id)
        saves.append((store, dict(binding), before, len(store._context_bindings)))
        return saved

    monkeypatch.setattr(InteractionLifecycleStore, "save_context_binding", recording_save)
    body = _v19_request(
        c,
        monkeypatch,
        resolve_key=f"clocked-replay-{uuid.uuid4().hex}",
        double_resolve=True,
    )

    assert len(saves) == 2
    store, returned_binding, first_before, first_after = saves[0]
    _, replay_binding, replay_before, replay_after = saves[1]
    assert replay_binding == returned_binding
    assert first_after == first_before + 1
    assert replay_before == replay_after == first_after
    assert store.latest_context_binding(
        returned_binding["tenant_id"], "interaction-user", returned_binding["workshop_id"],
    ) == returned_binding
    assert body["participants"][0]["captured_at"] == returned_binding["resolved_at"]

    browser_capture = TickingDateTime.current.isoformat().replace("+00:00", "Z")
    body["human_request"]["submitted_at"] = browser_capture
    body["context_snapshot"]["captured_at"] = browser_capture
    submitted = _submit(c, body)
    assert submitted.status_code == 202, submitted.text


def test_memory_context_binding_replay_does_not_add_rows_or_rewind_latest():
    store = InteractionLifecycleStore()

    def binding(name, resolved_at):
        return {
            "binding_id": f"binding-{name}",
            "tenant_id": "tenant",
            "workshop_id": "workshop",
            "context_digest": f"digest-{name}",
            "resolved_at": resolved_at,
        }

    first = binding("first", "2026-07-18T00:00:00Z")
    later = binding("later", "2026-07-18T00:00:02Z")
    store.save_context_binding(first, owner_user_id="operator")
    store.save_context_binding(first, owner_user_id="operator")
    assert len(store._context_bindings) == 1
    assert store.latest_context_binding("tenant", "operator", "workshop") == first

    store.save_context_binding(later, owner_user_id="operator")
    store.save_context_binding(first, owner_user_id="operator")
    assert len(store._context_bindings) == 2
    assert store.latest_context_binding("tenant", "operator", "workshop") == later


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
    from services.control_plane.bff import main as bff_main

    c = client(monkeypatch)
    submitted = _submit(c, _v19_request(c, monkeypatch)).json()["data"]
    assert submitted["status"] == "queued"
    interaction_id = submitted["interaction_id"]
    worker = AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
    )
    worker.run_once()
    initial_detail = c.get(f"/bff/agora/interactions/{interaction_id}", headers=AUTH).json()["data"]
    assert initial_detail["status"] == "failed"
    original = initial_detail["provider_invocations"][0]
    bff_main.read_store.list_personas = lambda **_kwargs: [{
        "persona_id": "ready", "tenant_id": "pantheon-dev", "display_name": "DRIFTED",
        "lifecycle_state": "active", "environment_ceiling": "paper",
    }]
    retried = c.post(
        f"/bff/agora/interactions/{interaction_id}:retry",
        headers={**AUTH, "Idempotency-Key": f"retry-drift-{uuid.uuid4().hex}"},
        json={"reason": "verify persisted freeze"},
    )
    assert retried.status_code == 202, retried.text
    worker.run_once()
    retried_detail = c.get(f"/bff/agora/interactions/{interaction_id}", headers=AUTH).json()["data"]
    invocations = retried_detail["provider_invocations"]
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


def test_memory_recovery_filters_status_before_limit():
    store = InteractionLifecycleStore()

    def create(interaction_id, created_at):
        record = {
            "interaction_id": interaction_id, "tenant_id": "tenant", "owner_user_id": "user",
            "workshop_id": "workshop", "status": "queued",
            "human_request": {"operator_id": "operator"},
            "created_at": created_at, "updated_at": created_at,
        }
        store.create_request(
            record, idempotency_scope="scope", idempotency_key=f"key-{interaction_id}",
            fingerprint=f"fingerprint-{interaction_id}", trace_id=f"trace-{interaction_id}",
        )

    old_id = "old-queued"
    create(old_id, "2026-07-17T00:00:00Z")
    for index in range(25):
        interaction_id = f"new-completed-{index:02d}"
        create(interaction_id, f"2026-07-17T00:01:{index:02d}Z")
        store.finalize(
            interaction_id, status="completed", synthesis=None,
            missing_participant_ids=[], degraded_participant_ids=[], outbox=[],
        )

    assert [
        item["interaction_id"] for item in store.recoverable("tenant", "user", limit=25)
    ] == [old_id]


def test_memory_recovery_takes_deterministic_oldest_queued_batch():
    store = InteractionLifecycleStore()
    for index in range(30):
        interaction_id = f"queued-{index:02d}"
        created_at = f"2026-07-17T00:00:{index:02d}Z"
        store.create_request(
            {
                "interaction_id": interaction_id, "tenant_id": "tenant", "owner_user_id": "user",
                "workshop_id": "workshop", "status": "queued",
                "human_request": {"operator_id": "operator"},
                "created_at": created_at, "updated_at": created_at,
            },
            idempotency_scope="scope", idempotency_key=f"key-{index}",
            fingerprint=f"fingerprint-{index}", trace_id=f"trace-{index}",
        )
    assert [
        item["interaction_id"] for item in store.recoverable("tenant", "user", limit=25)
    ] == [f"queued-{index:02d}" for index in range(25)]


@pytest.mark.parametrize("ghost_kind", ["focused", "secondary"])
def test_daily_resolver_rejects_ghost_persona_refs(monkeypatch, ghost_kind):
    c = client(monkeypatch)
    ghost = "ghost-persona"
    focused_id = ghost if ghost_kind == "focused" else "ready"
    refs = [{"type": "persona", "id": focused_id}]
    if ghost_kind == "secondary":
        refs.append({"type": "persona", "id": ghost})
    response = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"ghost-{ghost_kind}-{uuid.uuid4().hex}"},
        json={
            "context_refs": refs,
            "source_route": f"/management/personas/{focused_id}",
            "focused_object": {"kind": "persona", "id": focused_id},
            "evidence_cutoff": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "selected_persona_ids": ["ready"],
            "initial_mode": "challenge",
            "return_route": f"/management/personas/{focused_id}",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["reason"].endswith("not_found")


TRADE_EPISODE_ID = "11111111-1111-4111-8111-111111111111"


def _trade_episode_projection(**overrides):
    return {
        "trade_episode_id": TRADE_EPISODE_ID,
        "environment": "paper",
        "persona_id": "ready",
        "strategy_id": "strategy-1",
        "artifact_id": "artifact-7",
        "artifact_version": "artifact-build-42",
        "runtime_binding_id": "22222222-2222-4222-8222-222222222222",
        "capital_pool_id": "pool-7",
        "instrument_id": "SPY",
        "side": "long",
        "status": "reflected",
        "coverage": {
            "state": "complete",
            "missing_refs": [],
            "as_of": "2026-07-17T00:00:00Z",
            "source_system": "lean-telemetry",
        },
        **overrides,
    }


def test_daily_resolver_accepts_scope_checked_trade_episode_from_persona_journal(monkeypatch, tmp_path):
    episode_path = tmp_path / "trade-episodes.json"
    episode_path.write_text(json.dumps([_trade_episode_projection()]))
    monkeypatch.setenv("PANTHEON_BFF_TRADE_EPISODES_STORE", str(episode_path))
    c = client(monkeypatch)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source_route = "/management/personas/ready?tab=tradeJournal"
    response = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"trade-episode-{uuid.uuid4().hex}"},
        json={
            "context_refs": [
                {"type": "strategy", "id": "strategy-1", "version_id": "v1"},
                {"type": "journal_entry", "id": TRADE_EPISODE_ID},
                {"type": "persona", "id": "ready"},
            ],
            "source_route": source_route,
            "focused_object": {"kind": "journal_entry", "id": TRADE_EPISODE_ID},
            "evidence_cutoff": now,
            "selected_persona_ids": ["ready"],
            "initial_mode": "reflect",
            "return_route": source_route,
        },
    )
    assert response.status_code == 200, response.text
    binding = response.json()["data"]["context_binding"]
    assert binding["journal_ref"] == TRADE_EPISODE_ID
    assert binding["focused_object"] == {
        "kind": "journal_entry", "id": TRADE_EPISODE_ID, "version": None,
    }


@pytest.mark.parametrize(
    ("episode_mutation", "source_route"),
    [
        ({"artifact_id": None}, "/management/personas/ready?tab=tradeJournal"),
        ({"coverage": None}, "/management/personas/ready?tab=tradeJournal"),
        ({"side": "flat"}, "/management/personas/ready?tab=tradeJournal"),
        ({"strategy_id": "other-strategy"}, "/management/personas/ready?tab=tradeJournal"),
        ({}, "/management/personas/other?tab=tradeJournal"),
        ({}, "/management/personas/ready?tab=overview"),
    ],
)
def test_daily_resolver_rejects_trade_episode_without_schema_and_route_binding(
    monkeypatch, tmp_path, episode_mutation, source_route,
):
    episode = _trade_episode_projection(**episode_mutation)
    episode_path = tmp_path / "trade-episodes.json"
    episode_path.write_text(json.dumps([episode]))
    monkeypatch.setenv("PANTHEON_BFF_TRADE_EPISODES_STORE", str(episode_path))
    c = client(monkeypatch)
    response = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"bad-trade-episode-{uuid.uuid4().hex}"},
        json={
            "context_refs": [
                {"type": "strategy", "id": "strategy-1", "version_id": "v1"},
                {"type": "journal_entry", "id": TRADE_EPISODE_ID},
                {"type": "persona", "id": "ready"},
            ],
            "source_route": source_route,
            "focused_object": {"kind": "journal_entry", "id": TRADE_EPISODE_ID},
            "evidence_cutoff": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "selected_persona_ids": ["ready"],
            "initial_mode": "reflect",
            "return_route": source_route,
        },
    )
    assert response.status_code == 503, response.text
    assert "journal_entry_scope_unavailable" in response.text


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("position", "position_scope_unavailable"),
        ("performance_window", "performance_window_scope_unavailable"),
        ("human_inbox_item", "human_inbox_item_scope_unavailable"),
    ],
)
def test_daily_resolver_fails_closed_for_frontend_refs_without_scoped_canonical_owner(
    monkeypatch, kind, reason,
):
    c = client(monkeypatch)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    response = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"unsupported-{kind}-{uuid.uuid4().hex}"},
        json={
            "context_refs": [
                {"type": kind, "id": f"{kind}-1"},
                {"type": "persona", "id": "ready"},
            ],
            "source_route": "/management/personas/ready",
            "focused_object": {"kind": "persona", "id": "ready"},
            "evidence_cutoff": now,
            "selected_persona_ids": ["ready"],
            "initial_mode": "challenge",
            "return_route": "/management/personas/ready",
        },
    )
    assert response.status_code == 503, response.text
    assert reason in response.text


def test_daily_resolver_fails_closed_for_focused_decision_without_canonical_source_route(monkeypatch):
    from services.control_plane.bff.agora.trading_room.router import _get_store

    _get_store().upsert_decision_event({
        "decision_event_id": "focused-decision-1",
        "strategy_id": "strategy-1",
        "strategy_spec_registry_id": "v1",
        "state": "pending_review",
        "no_order_route_proof": "agora_decision_support_only",
    })
    c = client(monkeypatch)
    source_route = "/agora/trading-room/focused-decision-1"
    response = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"focused-decision-{uuid.uuid4().hex}"},
        json={
            "context_refs": [
                {"type": "strategy", "id": "strategy-1", "version_id": "v1"},
                {"type": "decision_event", "id": "focused-decision-1"},
                {"type": "persona", "id": "ready"},
            ],
            "source_route": source_route,
            "focused_object": {"kind": "decision_event", "id": "focused-decision-1"},
            "evidence_cutoff": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "selected_persona_ids": ["ready"],
            "initial_mode": "challenge",
            "return_route": source_route,
        },
    )
    assert response.status_code == 503, response.text
    assert "decision_event_source_route_unavailable" in response.text


def test_daily_resolver_rejects_strategy_not_found_in_canonical_registry(monkeypatch):
    c = client(monkeypatch)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    response = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"ghost-strategy-{uuid.uuid4().hex}"},
        json={
            "context_refs": [
                {"type": "strategy", "id": "ghost-strategy", "version_id": "ghost-version"},
                {"type": "persona", "id": "ready"},
            ],
            "source_route": "/management/personas/ready",
            "focused_object": {"kind": "persona", "id": "ready"},
            "evidence_cutoff": now,
            "selected_persona_ids": ["ready"],
            "initial_mode": "challenge",
            "return_route": "/management/personas/ready",
        },
    )
    assert response.status_code == 409, response.text
    assert "strategy_not_found" in response.text


def test_daily_resolver_rejects_canonical_persona_bound_to_another_persona_route(monkeypatch):
    c = client(monkeypatch)
    response = c.post(
        "/bff/agora/interactions/context:resolve",
        headers={**AUTH, "Idempotency-Key": f"persona-route-mismatch-{uuid.uuid4().hex}"},
        json={
            "context_refs": [{"type": "persona", "id": "ready"}],
            "source_route": "/management/personas/another-persona",
            "focused_object": {"kind": "persona", "id": "ready"},
            "evidence_cutoff": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "selected_persona_ids": ["ready"],
            "initial_mode": "challenge",
            "return_route": "/management/personas/another-persona",
        },
    )
    assert response.status_code == 409, response.text
    assert "focused_source_route_mismatch" in response.text


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
    assert submitted["status"] == "queued"
    interaction_id = submitted["interaction_id"]
    AgoraInteractionWorker(
        lifecycle_store=bff_main.interaction_lifecycle,
        workshop_store=bff_main.workshop_store,
        read_store=bff_main.read_store,
    ).run_once()
    initial_detail = c.get(f"/bff/agora/interactions/{interaction_id}", headers=AUTH).json()["data"]
    assert initial_detail["status"] == "failed"
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
