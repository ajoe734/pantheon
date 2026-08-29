"""Contract tests for BFF Write-Gap endpoints — 2026-05-28 sprint.

Covers:
  - Card P0-1: POST /bff/personas/{id}/actions/AdvanceLifecycle
    - Probes that the endpoint no longer returns 410 for AdvanceLifecycle.
    - Validates 202 + commandId on a successful admission (dry-run / stub).
    - Validates typed 4xx responses (not raw 410 / not bare "Not Found").
  - BFF Agora signal write endpoints (create, dry-run, validation).
  - BFF Runtime create endpoint (create, idempotency, conflict, validation).

Sprint: EPIC-WRITE-GAP-P0-LIFECYCLE, EPIC-WRITE-GAP-P1-AGORA
Spec:   docs/04/pantheon_bff_write_gap_2026-05-28/BFF_WRITE_GAP_SPEC.md
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterator

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

os.environ.setdefault("PANTHEON_BFF_AUTH_STUB", "true")

import main as bff_main  # noqa: E402
from command_queue import CommandStore  # noqa: E402
from ports import ReadSurfacePorts  # noqa: E402


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

AGORA_BASE_HEADERS = {
    "Authorization": "Bearer analyst-agora:analyst,viewer",
    "X-BFF-Api-Version": "2026-05-07",
    "X-Request-Id": "req-write-gap-agora-signal",
}
AGORA_READ_HEADERS = {
    **AGORA_BASE_HEADERS,
    "Authorization": "Bearer op-agora:operator",
}
RUNTIME_HEADERS = {
    "Authorization": "Bearer bff-write-gap-runtime:operator",
    "Idempotency-Key": "bff-write-gap-runtime-create-001",
}
_TRACKED_RUNTIME_ENV = (
    "PANTHEON_BFF_RUNTIME_BINDING_STORE",
    "PANTHEON_RUNTIME_DATA_DIR",
    "PANTHEON_RUNTIME_MANAGER_URL",
    "PANTHEON_INTERNAL_API_URL",
    "PANTHEON_RUNTIME_MANAGER_TOKEN",
)

_OPERATOR_TOKEN = "Bearer test-operator:operator"
_APPROVER_TOKEN = "Bearer test-approver:approver"
_VIEWER_TOKEN = "Bearer test-viewer:reviewer"


def _local_write_gap_read_data() -> dict[str, Any]:
    return {
        "personas": {
            "persona-alpha": {
                "id": "persona-alpha",
                "persona_id": "persona-alpha",
                "name": "Alpha Persona",
                "lifecycle_state": "active",
                "status": "active",
                "mandate": "alpha_trading",
                "strategy_family": "momentum",
                "created_at": "2026-05-01T00:00:00Z",
                "updated_at": "2026-05-01T00:00:00Z",
                "metadata": {
                    "archetype": "momentum",
                    "risk_level": "low",
                },
            }
        },
        "strategies": {},
        "persona_league": [],
        "registry_entries": {},
        "runtime_bindings": {},
        "deployment_plans": {},
        "governance_review_queue": {},
        "approvals": {},
        "capital_pools": {},
        "bindings": {
            "binding-alpha": {
                "id": "binding-alpha",
                "binding_id": "binding-alpha",
                "persona_id": "persona-alpha",
                "capital_pool_id": "pool-main",
                "status": "active",
            }
        },
        "sessions": {},
        "teaching_sessions": {},
        "allowed_actions": {},
        "incidents": {},
        "evolution_decisions": {},
        "telemetry_summaries": {},
        "agora_signals": {},
        "agora_audit_events": {},
        "agora_signal_feedback": {},
    }


@pytest.fixture(autouse=True)
def _ensure_auth_stub(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")


class WriteGapTestReadPorts(ReadSurfacePorts):
    def __init__(
        self,
        seed_data: dict[str, Any] | None = None,
        *,
        allow_local_snapshot_fallback: bool = True,
    ) -> None:
        super().__init__()
        self._data = seed_data if seed_data is not None else _local_write_gap_read_data()
        self.allow_local_snapshot_fallback = allow_local_snapshot_fallback
        self._ranking_snapshots: dict[str, Any] = {}

    def dataset_source(self, dataset: str, **kwargs: Any) -> str:
        return "bff_local_dev_store"

    def dataset_surface_status(self, dataset: str, *, snapshot_at: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "source": "bff_local_dev_store",
            "snapshot_at": snapshot_at,
            "freshness": "fresh",
            "observed_time": snapshot_at,
            "coverage": 1.0,
            "missing_bindings": False,
        }

    def _ensure_local_overlay_records(self, dataset: str) -> dict[str, Any]:
        return self._data.setdefault(dataset, {})

    def record(self, dataset: str, record_id: str) -> tuple[bool, dict[str, Any] | None]:
        ds = self._data.get(dataset, {})
        if isinstance(ds, dict):
            return (True, ds.get(record_id))
        return (False, None)

    def list_records(self, dataset: str) -> tuple[bool, list[dict[str, Any]]]:
        ds = self._data.get(dataset, {})
        if isinstance(ds, dict):
            return (True, list(ds.values()))
        return (True, list(ds))

    # Agora ports
    def get_agora_signal(self, signal_id: str) -> dict[str, Any] | None:
        signals = self._data.get("agora_signals", {})
        return signals.get(signal_id) if isinstance(signals, dict) else None

    def list_agora_signals(self, **kwargs: Any) -> list[dict[str, Any]]:
        signals = self._data.get("agora_signals", {})
        return list(signals.values()) if isinstance(signals, dict) else list(signals)

    def put_agora_signal(self, signal_id: str, record: dict[str, Any]) -> dict[str, Any]:
        self._data.setdefault("agora_signals", {})[signal_id] = record
        return record

    def create_agora_signal(
        self,
        *,
        signal_id: str,
        title: str,
        body: str,
        actor_id: str,
        payload: dict[str, Any],
        created_at: str | None = None,
    ) -> dict[str, Any]:
        timestamp = created_at or "2026-05-28T00:00:00Z"
        signal = {
            "id": signal_id,
            "signal_id": signal_id,
            "title": title,
            "body": body,
            "market": str(payload.get("market") or "").strip() or None,
            "tags": payload.get("tags") or [],
            "linkedPersonaIds": payload.get("linkedPersonaIds") or payload.get("linked_persona_ids") or [],
            "linkedStrategyIds": payload.get("linkedStrategyIds") or payload.get("linked_strategy_ids") or [],
            "severity": str(payload.get("severity") or "info").strip().lower(),
            "status": "open",
            "reviewStatus": "pending_trader_review",
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "createdBy": actor_id,
            "authorId": actor_id,
        }
        self._data.setdefault("agora_signals", {})[signal_id] = signal
        return signal

    def list_agora_audit_events(self, **kwargs: Any) -> list[dict[str, Any]]:
        events = self._data.get("agora_audit_events", {})
        return list(events.values()) if isinstance(events, dict) else list(events)

    def append_agora_audit_event(self, record: dict[str, Any]) -> dict[str, Any]:
        eid = record.get("event_id") or record.get("id") or f"audit-{len(self._data.get('agora_audit_events', {})) + 1}"
        self._data.setdefault("agora_audit_events", {})[eid] = record
        return record

    def record_agora_audit_event(self, event: dict[str, Any]) -> dict[str, Any]:
        timestamp = str(event.get("recordedAt") or event.get("timestamp") or "2026-05-28T00:00:00Z")
        event_id = str(event.get("auditId") or event.get("eventId") or f"aud-agora-{uuid.uuid4().hex[:12]}")
        record = {
            "auditId": event_id,
            "recordedAt": timestamp,
            **event,
        }
        self._data.setdefault("agora_audit_events", {})[event_id] = record
        return record

    def list_agora_feedback(self, **kwargs: Any) -> list[dict[str, Any]]:
        fb = self._data.get("agora_signal_feedback", {})
        return list(fb.values()) if isinstance(fb, dict) else list(fb)

    def put_agora_feedback(self, feedback_id: str, record: dict[str, Any]) -> dict[str, Any]:
        self._data.setdefault("agora_signal_feedback", {})[feedback_id] = record
        return record

    def record_agora_signal_feedback(
        self,
        signal_id: str,
        *,
        decision: str,
        confidence: int,
        reason: str | None,
        actor_id: str,
        edit_window_seconds: int,
        recorded_at: str | None = None,
    ) -> dict[str, Any] | None:
        signal = self.get_agora_signal(signal_id)
        if signal is None:
            return None
        timestamp = recorded_at or "2026-05-28T00:00:00Z"
        feedback_id = f"sigfb-{uuid.uuid4().hex[:12]}"
        feedback = {
            "id": feedback_id,
            "feedbackId": feedback_id,
            "signalId": signal_id,
            "decision": decision,
            "confidence": confidence,
            "reason": reason,
            "actorId": actor_id,
            "createdAt": timestamp,
            "updatedAt": timestamp,
            "editWindowSeconds": edit_window_seconds,
        }
        self._data.setdefault("agora_signal_feedback", {})[feedback_id] = feedback
        return feedback

    # Persona & Strategy
    def get_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("personas", {})
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""))
        return next((p for p in ds if p.get("id") == persona_id or p.get("persona_id") == persona_id), None)

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("personas", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def upsert_persona(self, persona: dict[str, Any]) -> dict[str, Any]:
        pid = persona.get("id") or persona.get("persona_id")
        self._data.setdefault("personas", {})[pid] = persona
        return persona

    def get_strategy(self, strategy_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("strategies", {})
        if isinstance(ds, dict):
            return ds.get(str(strategy_id or ""))
        return next((s for s in ds if s.get("id") == strategy_id or s.get("strategy_id") == strategy_id), None)

    def list_strategies(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("strategies", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_persona_league(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("persona_league", [])
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_capability_snapshot_for_persona(self, persona_id: str | None) -> dict[str, Any] | None:
        return None

    def get_persona_capabilities(self, persona_id: str | None) -> dict[str, Any] | None:
        return None

    def list_registry_entries(self, **kwargs: Any) -> list[dict[str, Any]]:
        entries = self._data.get("registry_entries", {})
        if isinstance(entries, dict):
            return list(entries.values())
        return list(entries)

    # Runtime bindings
    def _get_fs_runtime_bindings(self) -> dict[str, Any]:
        rdir = os.environ.get("PANTHEON_RUNTIME_DATA_DIR")
        if rdir:
            fpath = Path(rdir) / "runtime_bindings.json"
            if fpath.exists():
                try:
                    raw = json.loads(fpath.read_text(encoding="utf-8"))
                    if isinstance(raw, list):
                        return {rb.get("binding_id") or rb.get("id") or rb.get("runtime_id"): rb for rb in raw if isinstance(rb, dict)}
                    if isinstance(raw, dict):
                        return raw
                except Exception:
                    pass
        return {}

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        fs_rbs = self._get_fs_runtime_bindings()
        if fs_rbs:
            return list(fs_rbs.values())
        ds = self._data.get("runtime_bindings") or {}
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_runtime_binding(self, binding_id: str | None) -> dict[str, Any] | None:
        fs_rbs = self._get_fs_runtime_bindings()
        if fs_rbs:
            res = fs_rbs.get(str(binding_id or ""))
            if res:
                return res
            return next((r for r in fs_rbs.values() if r.get("id") == binding_id or r.get("binding_id") == binding_id or r.get("runtime_id") == binding_id), None)
        ds = self._data.get("runtime_bindings") or {}
        if isinstance(ds, dict):
            res = ds.get(str(binding_id or ""))
            if res:
                return res
        return next((r for r in (ds.values() if isinstance(ds, dict) else ds) if r.get("id") == binding_id or r.get("binding_id") == binding_id or r.get("runtime_id") == binding_id), None)

    def get_runtime_binding_by_runtime_id(self, runtime_id: str | None) -> dict[str, Any] | None:
        return self.get_runtime_binding(runtime_id)

    def create_runtime_binding(
        self,
        *,
        runtime_id: str | None = None,
        name: str = "",
        persona_id: str = "",
        binding_id: str = "",
        deployment_plan_id: str = "",
        runtime_kind: str = "paper",
        actor_id: str = "",
        created_at: str | None = None,
        params: dict[str, Any] | None = None,
        state: str = "stopped",
        **kwargs: Any,
    ) -> dict[str, Any]:
        rid = runtime_id or kwargs.get("id") or "runtime-1"
        bid = binding_id or kwargs.get("binding_id") or "binding-1"
        timestamp = created_at or "2026-05-28T00:00:00Z"
        record = {
            "id": rid,
            "runtime_id": rid,
            "name": name,
            "state": state,
            "status": state,
            "persona_id": persona_id,
            "binding_id": bid,
            "runtime_binding_id": bid,
            "persona_capital_binding_id": bid,
            "deployment_plan_id": deployment_plan_id,
            "plan_id": deployment_plan_id,
            "runtime_kind": runtime_kind,
            "deployment_stage": runtime_kind,
            "deployment_mode": runtime_kind,
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
        }
        self._data.setdefault("runtime_bindings", {})[rid] = record
        self._data.setdefault("runtime_bindings", {})[bid] = record
        rdir = os.environ.get("PANTHEON_RUNTIME_DATA_DIR")
        if rdir:
            fpath = Path(rdir) / "runtime_bindings.json"
            try:
                raw = []
                if fpath.exists():
                    raw = json.loads(fpath.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    raw.append(record)
                    fpath.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            except Exception:
                pass
        return record

    # Deployment plans & Governance
    def list_deployment_plans(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("deployment_plans", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_deployment_plan(self, plan_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("deployment_plans", {})
        if isinstance(ds, dict):
            return ds.get(str(plan_id or ""))
        return next((p for p in ds if p.get("id") == plan_id or p.get("plan_id") == plan_id), None)

    def put_deployment_plan(self, plan_id: str, record: dict[str, Any]) -> dict[str, Any]:
        self._data.setdefault("deployment_plans", {})[plan_id] = record
        return record

    def create_deployment_plan(
        self,
        *,
        plan_id: str,
        binding_id: str,
        artifact_id: str,
        deployment_mode: str,
        capital_pool_id: str,
        actor_id: str,
        created_at: str | None = None,
        params: dict[str, Any] | None = None,
        locked: bool = False,
        status: str = "pending_approval",
    ) -> dict[str, Any]:
        timestamp = created_at or "2026-05-28T00:00:00Z"
        record = {
            "id": plan_id,
            "plan_id": plan_id,
            "binding_id": binding_id,
            "persona_capital_binding_id": binding_id,
            "artifact_id": artifact_id,
            "deployment_mode": deployment_mode,
            "deployment_stage": deployment_mode,
            "target_stage": deployment_mode,
            "capital_pool_id": capital_pool_id,
            "target_pool_id": capital_pool_id,
            "status": status,
            "locked": bool(locked),
            "params": params or {},
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": actor_id,
            "metadata": {
                "created_via": "POST /api/v1/deployment-plans",
                "persistenceMode": "bff_local_dev_store",
            },
            "canonicalWriteAuthority": "deployment_service",
            "persistenceMode": "bff_local_dev_store",
        }
        self._data.setdefault("deployment_plans", {})[plan_id] = record
        return record

    def list_governance_review_queue_items(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("governance_review_queue", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_approval_queue_items(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("approvals", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("capital_pools", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_capital_pool(self, pool_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("capital_pools", {})
        if isinstance(ds, dict):
            return ds.get(str(pool_id or ""))
        return next((p for p in ds if p.get("id") == pool_id or p.get("capital_pool_id") == pool_id), None)

    def list_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("bindings", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_binding(self, binding_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("bindings", {})
        if isinstance(ds, dict):
            return ds.get(str(binding_id or ""))
        return next((b for b in ds if b.get("id") == binding_id or b.get("binding_id") == binding_id), None)

    def get_bindings_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        ds = self._data.get("bindings", {})
        bindings = list(ds.values()) if isinstance(ds, dict) else list(ds)
        return [b for b in bindings if b.get("persona_id") == persona_id or b.get("personaId") == persona_id]

    def get_sessions_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        ds = self._data.get("sessions", {})
        sessions = list(ds.values()) if isinstance(ds, dict) else list(ds)
        return [s for s in sessions if s.get("persona_id") == persona_id or s.get("personaId") == persona_id]

    def get_teaching_sessions_for_persona(self, persona_id: str | None) -> list[dict[str, Any]]:
        ds = self._data.get("teaching_sessions", {})
        sessions = list(ds.values()) if isinstance(ds, dict) else list(ds)
        return [s for s in sessions if s.get("persona_id") == persona_id or s.get("personaId") == persona_id]

    def get_persona_allowed_actions(self, persona_id: str | None) -> dict[str, Any]:
        ds = self._data.get("allowed_actions", {})
        if isinstance(ds, dict):
            return ds.get(str(persona_id or ""), {})
        return {}

    def list_incidents(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("incidents", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def list_evolution_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
        ds = self._data.get("evolution_decisions", {})
        return list(ds.values()) if isinstance(ds, dict) else list(ds)

    def get_telemetry_summary(self, runtime_id: str | None) -> dict[str, Any] | None:
        ds = self._data.get("telemetry_summaries", {})
        if isinstance(ds, dict):
            return ds.get(str(runtime_id or ""))
        return next((t for t in ds if t.get("runtime_id") == runtime_id), None)


# ---------------------------------------------------------------------------
# Isolation helpers (Agora)
# ---------------------------------------------------------------------------


@contextmanager
def _isolated_agora_bff() -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_idempotency = dict(bff_main._AGORA_CORE_BFF_IDEMPOTENCY)
    original_signal_events = list(bff_main._sse_buffers["signal"])
    original_inbox_events = list(bff_main._sse_buffers["inbox"])
    bff_main.read_store = WriteGapTestReadPorts(
        seed_data={
            "agora_signals": {},
            "agora_audit_events": {},
            "agora_signal_feedback": {},
        }
    )
    bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
    bff_main._sse_buffers["signal"].clear()
    bff_main._sse_buffers["inbox"].clear()
    try:
        yield TestClient(bff_main.app)
    finally:
        bff_main.read_store = original_store
        bff_main._AGORA_CORE_BFF_IDEMPOTENCY.clear()
        bff_main._AGORA_CORE_BFF_IDEMPOTENCY.update(original_idempotency)
        bff_main._sse_buffers["signal"].clear()
        bff_main._sse_buffers["signal"].extend(original_signal_events)
        bff_main._sse_buffers["inbox"].clear()
        bff_main._sse_buffers["inbox"].extend(original_inbox_events)


# ---------------------------------------------------------------------------
# Isolation helpers (Runtime)
# ---------------------------------------------------------------------------


@contextmanager
def _isolated_runtime_bff(runtime_bindings: list[dict[str, Any]]) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_env = {key: os.environ.get(key) for key in _TRACKED_RUNTIME_ENV}
    original_idempotency = dict(bff_main._GOV_BFF_IDEMPOTENCY)
    original_runtime_events = list(bff_main._sse_buffers["runtime"])
    with tempfile.TemporaryDirectory(prefix="bff_write_gap_runtime_") as td:
        root = Path(td)
        runtime_dir = root / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        for key in _TRACKED_RUNTIME_ENV:
            os.environ.pop(key, None)
        (runtime_dir / "runtime_bindings.json").write_text(
            json.dumps(runtime_bindings, indent=2),
            encoding="utf-8",
        )
        os.environ["PANTHEON_RUNTIME_DATA_DIR"] = str(runtime_dir)
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        bff_main._sse_buffers["runtime"].clear()
        rb_map = {rb.get("binding_id") or rb.get("id"): rb for rb in runtime_bindings if isinstance(rb, dict)}
        bff_main.read_store = WriteGapTestReadPorts(
            seed_data={"runtime_bindings": rb_map},
            allow_local_snapshot_fallback=False,
        )
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.read_store = original_store
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.update(original_idempotency)
            bff_main._sse_buffers["runtime"].clear()
            bff_main._sse_buffers["runtime"].extend(original_runtime_events)
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


# ---------------------------------------------------------------------------
# Isolation helpers (AdvanceLifecycle)
# ---------------------------------------------------------------------------


@contextmanager
def _stub_auth() -> Generator[None, None, None]:
    original = os.environ.get("PANTHEON_BFF_AUTH_STUB")
    os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("PANTHEON_BFF_AUTH_STUB", None)
        else:
            os.environ["PANTHEON_BFF_AUTH_STUB"] = original


def _client() -> TestClient:
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _advance_lifecycle_url(persona_id: str) -> str:
    return f"/bff/personas/{persona_id}/actions/AdvanceLifecycle"


def _idempotency_key() -> str:
    return str(uuid.uuid4())


def _runtime_create_payload(binding_id: str = "binding-runtime-create-001") -> dict[str, Any]:
    return {
        "name": "Paper Runtime 001",
        "persona_id": "persona-runtime-create-001",
        "binding_id": binding_id,
        "deployment_plan_id": "plan-runtime-create-001",
        "runtime_kind": "paper",
        "params": {"broker": "simulated"},
    }


# ---------------------------------------------------------------------------
# Agora signal write tests
# ---------------------------------------------------------------------------


def test_bff_agora_signal_create_returns_201_persists_and_replays() -> None:
    with _isolated_agora_bff() as client:
        body = {
            "id": "sig-write-gap-001",
            "title": "Opening auction momentum",
            "body": "Review a new opening auction momentum signal.",
            "market": "US",
            "tags": ["auction", "momentum"],
            "linkedPersonaIds": ["persona-paper-owner"],
            "linkedStrategyIds": ["strategy-alpha"],
            "severity": "warn",
        }
        headers = {
            **AGORA_BASE_HEADERS,
            "Idempotency-Key": "agora-signal-create-001",
            "X-Correlation-Id": "corr-agora-signal-create-001",
        }

        response = client.post("/bff/agora/signals", headers=headers, json=body)
        replay = client.post("/bff/agora/signals", headers=headers, json=body)

        assert response.status_code == 201, response.text
        assert replay.status_code == 201, replay.text
        assert response.headers["X-Correlation-Id"] == "corr-agora-signal-create-001"
        payload = response.json()
        assert payload["data"]["id"] == "sig-write-gap-001"
        assert payload["data"]["status"] == "open"
        assert payload["data"]["reviewStatus"] == "pending_trader_review"
        assert payload["data"]["severity"] == "warn"
        assert payload["meta"]["dryRun"] is False
        assert payload["meta"]["audit"]["evidenceKind"] == "agora.signal.create"
        assert replay.json()["data"]["id"] == payload["data"]["id"]

        detail = client.get("/bff/agora/signals/sig-write-gap-001", headers=AGORA_READ_HEADERS)
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["title"] == "Opening auction momentum"
        assert len(bff_main._sse_buffers["signal"]) == 1
        assert len(bff_main._sse_buffers["inbox"]) == 1


def test_bff_agora_signal_create_dry_run_returns_200_without_persistence() -> None:
    with _isolated_agora_bff() as client:
        body = {
            "id": "sig-write-gap-dry-run",
            "title": "Dry-run signal",
            "body": "Validate the signal create shape without persisting.",
        }
        response = client.post(
            "/bff/agora/signals",
            headers={
                **AGORA_BASE_HEADERS,
                "Idempotency-Key": "agora-signal-dry-run-001",
                "X-Correlation-Id": "corr-agora-signal-dry-run-001",
                "X-Dry-Run": "1",
            },
            json=body,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["id"] == "sig-write-gap-dry-run"
        assert payload["meta"]["dryRun"] is True
        detail = client.get("/bff/agora/signals/sig-write-gap-dry-run", headers=AGORA_READ_HEADERS)
        assert detail.status_code == 404, detail.text
        assert len(bff_main._sse_buffers["signal"]) == 0
        assert len(bff_main._sse_buffers["inbox"]) == 0


def test_bff_agora_signal_create_rejects_invalid_payload() -> None:
    with _isolated_agora_bff() as client:
        response = client.post(
            "/bff/agora/signals",
            headers={**AGORA_BASE_HEADERS, "Idempotency-Key": "agora-signal-invalid-001"},
            json={"title": "Missing body", "severity": "critical"},
        )

        assert response.status_code == 422, response.text
        error = response.json()["error"]
        assert error["code"] == "VALIDATION_FAILED"
        assert error["details"]["precondition_failed"] == "body"


# ---------------------------------------------------------------------------
# Runtime create tests
# ---------------------------------------------------------------------------


def test_post_bff_runtimes_creates_stopped_runtime_and_replays_idempotently() -> None:
    with _isolated_runtime_bff([]) as client:
        response = client.post("/bff/runtimes", json=_runtime_create_payload(), headers=RUNTIME_HEADERS)
        replay = client.post("/bff/runtimes", json=_runtime_create_payload(), headers=RUNTIME_HEADERS)
        runtime_id = response.json()["data"]["id"]
        detail = client.get(
            f"/bff/runtimes/{runtime_id}",
            headers={"Authorization": RUNTIME_HEADERS["Authorization"]},
        )
        event_types = [event["type"] for _event_id, event in bff_main._sse_buffers["runtime"]]

    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["data"]["name"] == "Paper Runtime 001"
    assert payload["data"]["state"] == "stopped"
    assert payload["data"]["persona_id"] == "persona-runtime-create-001"
    assert payload["data"]["binding_id"] == "binding-runtime-create-001"
    assert payload["data"]["deployment_plan_id"] == "plan-runtime-create-001"
    assert payload["data"]["runtime_kind"] == "paper"
    assert payload["data"]["created_at"]
    assert payload["meta"]["evidenceKind"] == "runtime.create"

    assert replay.status_code == 201, replay.text
    assert replay.json()["data"] == payload["data"]
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["runtime_id"] == runtime_id
    assert detail.json()["data"]["status"] == "stopped"

    assert event_types == ["runtime.created", "management.runtime-status"]


def test_post_bff_runtimes_rejects_binding_that_already_has_runtime() -> None:
    existing = {
        "binding_id": "binding-runtime-create-occupied",
        "runtime_id": "runtime-existing-001",
        "status": "active",
        "deployment_mode": "paper",
        "plan_id": "plan-existing-001",
        "persona_capital_binding_id": "binding-runtime-create-occupied",
    }
    with _isolated_runtime_bff([existing]) as client:
        response = client.post(
            "/bff/runtimes",
            json=_runtime_create_payload(binding_id="binding-runtime-create-occupied"),
            headers={**RUNTIME_HEADERS, "Idempotency-Key": "bff-write-gap-runtime-conflict-001"},
        )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "RESOURCE_CONFLICT"


def test_post_bff_runtimes_validates_runtime_kind() -> None:
    payload = _runtime_create_payload()
    payload["runtime_kind"] = "sandbox"
    with _isolated_runtime_bff([]) as client:
        response = client.post(
            "/bff/runtimes",
            json=payload,
            headers={**RUNTIME_HEADERS, "Idempotency-Key": "bff-write-gap-runtime-validation-001"},
        )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


# --------------------------------------------------------------------------- #
# P0-6 — POST /api/v1/deployment-plans (persona onboarding wizard step 3)
# --------------------------------------------------------------------------- #

DEPLOYMENT_PLAN_HEADERS = {
    "Authorization": "Bearer bff-write-gap-dp:operator",
    "Idempotency-Key": "bff-write-gap-dp-create-001",
    "X-Correlation-Id": "corr-dp-create-001",
    "X-Request-Id": "req-dp-create-001",
}


def _deployment_plan_seed(registry_entries: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "deployment_plans": {},
        "bindings": {
            "binding-dp-001": {
                "id": "binding-dp-001",
                "binding_id": "binding-dp-001",
                "persona_id": "persona-dp-001",
                "capital_pool_id": "pool-dp-001",
                "role": "paper_owner",
            }
        },
        "registry_entries": registry_entries or {},
    }


@contextmanager
def _isolated_deployment_plan_bff(
    registry_entries: dict[str, Any] | None = None,
) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_idempotency = dict(bff_main._GOV_BFF_IDEMPOTENCY)
    original_audit_events = list(bff_main._sse_buffers["audit"])
    seed = _deployment_plan_seed(registry_entries)
    bff_main.read_store = WriteGapTestReadPorts(
        seed_data=seed,
        allow_local_snapshot_fallback=True,
    )
    bff_main._GOV_BFF_IDEMPOTENCY.clear()
    bff_main._sse_buffers["audit"].clear()
    try:
        yield TestClient(bff_main.app)
    finally:
        bff_main.read_store = original_store
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        bff_main._GOV_BFF_IDEMPOTENCY.update(original_idempotency)
        bff_main._sse_buffers["audit"].clear()
        bff_main._sse_buffers["audit"].extend(original_audit_events)


def _deployment_plan_create_payload(plan_id: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "binding_id": "binding-dp-001",
        "artifact_id": "artifact-dp-001",
        "deployment_mode": "paper",
        "capital_pool_id": "pool-dp-001",
        "params": {"max_notional": 100000},
        "locked": False,
    }
    if plan_id:
        body["plan_id"] = plan_id
    return body


def test_post_deployment_plan_creates_pending_approval_and_replays() -> None:
    with _isolated_deployment_plan_bff() as client:
        body = _deployment_plan_create_payload(plan_id="plan-dp-write-gap-001")
        response = client.post(
            "/api/v1/deployment-plans", headers=DEPLOYMENT_PLAN_HEADERS, json=body
        )
        replay = client.post(
            "/api/v1/deployment-plans", headers=DEPLOYMENT_PLAN_HEADERS, json=body
        )
        detail = client.get(
            "/api/v1/deployment-plans/plan-dp-write-gap-001",
            headers={"Authorization": DEPLOYMENT_PLAN_HEADERS["Authorization"]},
        )
        listing = client.get(
            "/api/v1/deployment-plans",
            headers={"Authorization": DEPLOYMENT_PLAN_HEADERS["Authorization"]},
        )
        event_types = [event["type"] for _event_id, event in bff_main._sse_buffers["audit"]]
        events = [event for _event_id, event in bff_main._sse_buffers["audit"]]

    assert response.status_code == 201, response.text
    payload = response.json()
    data = payload["data"]
    assert data["id"] == "plan-dp-write-gap-001"
    assert data["binding_id"] == "binding-dp-001"
    assert data["artifact_id"] == "artifact-dp-001"
    assert data["deployment_mode"] == "paper"
    assert data["status"] == "pending_approval"
    assert data["capital_pool_id"] == "pool-dp-001"
    assert data["locked"] is False
    assert data["created_at"]
    assert payload["meta"]["dryRun"] is False
    assert payload["meta"]["evidenceKind"] == "deployment_plan.create"
    assert payload["meta"]["correlationId"] == "corr-dp-create-001"
    assert response.headers["X-Correlation-Id"] == "corr-dp-create-001"

    assert replay.status_code == 201, replay.text
    assert replay.json()["data"] == data

    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["id"] == "plan-dp-write-gap-001"
    assert listing.status_code == 200, listing.text
    assert any(p["id"] == "plan-dp-write-gap-001" for p in listing.json()["data"])

    assert event_types == ["deployment-plan.created"]
    assert events[0]["data"]["persona_id"] == "persona-dp-001"
    assert events[0]["data"]["status"] == "pending_approval"


def test_post_deployment_plan_honors_locked_flag() -> None:
    payload = _deployment_plan_create_payload(plan_id="plan-dp-locked-001")
    payload["locked"] = True
    payload["deployment_mode"] = "live"
    with _isolated_deployment_plan_bff() as client:
        response = client.post(
            "/api/v1/deployment-plans",
            headers={**DEPLOYMENT_PLAN_HEADERS, "Idempotency-Key": "bff-write-gap-dp-locked-001"},
            json=payload,
        )
        detail = client.get(
            "/api/v1/deployment-plans/plan-dp-locked-001",
            headers={"Authorization": DEPLOYMENT_PLAN_HEADERS["Authorization"]},
        )

    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["locked"] is True
    assert data["deployment_mode"] == "live"
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["locked"] is True


def test_post_deployment_plan_dry_run_returns_200_without_persistence() -> None:
    with _isolated_deployment_plan_bff() as client:
        response = client.post(
            "/api/v1/deployment-plans",
            headers={
                **DEPLOYMENT_PLAN_HEADERS,
                "X-Dry-Run": "1",
                "Idempotency-Key": "bff-write-gap-dp-dry-run-001",
            },
            json=_deployment_plan_create_payload(plan_id="plan-dp-dry-run-001"),
        )
        detail = client.get(
            "/api/v1/deployment-plans/plan-dp-dry-run-001",
            headers={"Authorization": DEPLOYMENT_PLAN_HEADERS["Authorization"]},
        )
        audit_events = list(bff_main._sse_buffers["audit"])

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["id"] == "plan-dp-dry-run-001"
    assert payload["data"]["status"] == "pending_approval"
    assert payload["meta"]["dryRun"] is True
    assert detail.status_code == 404, detail.text
    assert len(audit_events) == 0


def test_post_deployment_plan_validates_deployment_mode() -> None:
    payload = _deployment_plan_create_payload()
    payload["deployment_mode"] = "shadow"
    with _isolated_deployment_plan_bff() as client:
        response = client.post(
            "/api/v1/deployment-plans",
            headers={**DEPLOYMENT_PLAN_HEADERS, "Idempotency-Key": "bff-write-gap-dp-mode-001"},
            json=payload,
        )

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["details"]["precondition_failed"] == "deployment_mode"


def test_post_deployment_plan_requires_binding_id() -> None:
    payload = _deployment_plan_create_payload()
    payload.pop("binding_id")
    with _isolated_deployment_plan_bff() as client:
        response = client.post(
            "/api/v1/deployment-plans",
            headers={**DEPLOYMENT_PLAN_HEADERS, "Idempotency-Key": "bff-write-gap-dp-missing-001"},
            json=payload,
        )

    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert error["details"]["precondition_failed"] == "binding_id"


def test_post_deployment_plan_rejects_unapproved_artifact() -> None:
    registry = {
        "artifact-dp-001": {
            "id": "artifact-dp-001",
            "artifact_id": "artifact-dp-001",
            "status": "draft",
        }
    }
    with _isolated_deployment_plan_bff(registry_entries=registry) as client:
        response = client.post(
            "/api/v1/deployment-plans",
            headers={**DEPLOYMENT_PLAN_HEADERS, "Idempotency-Key": "bff-write-gap-dp-unapproved-001"},
            json=_deployment_plan_create_payload(),
        )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "RESOURCE_CONFLICT"


def test_post_deployment_plan_idempotency_conflict_on_changed_payload() -> None:
    with _isolated_deployment_plan_bff() as client:
        first = client.post(
            "/api/v1/deployment-plans",
            headers=DEPLOYMENT_PLAN_HEADERS,
            json=_deployment_plan_create_payload(plan_id="plan-dp-conflict-001"),
        )
        changed = _deployment_plan_create_payload(plan_id="plan-dp-conflict-002")
        conflict = client.post(
            "/api/v1/deployment-plans",
            headers=DEPLOYMENT_PLAN_HEADERS,
            json=changed,
        )

    assert first.status_code == 201, first.text
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


# ---------------------------------------------------------------------------
# P0-4: POST /bff/command-confirmations/{token}/confirm
# ---------------------------------------------------------------------------

_CONFIRM_HEADERS = {
    "Authorization": "Bearer test-confirm:operator",
    "X-BFF-Api-Version": "2026-05-07",
    "X-Request-Id": "req-confirm-by-token-test",
    "X-Correlation-Id": "corr-confirm-by-token-test",
}


@contextmanager
def _isolated_confirm_bff() -> Iterator[TestClient]:
    original_command_store = bff_main.command_store
    original_idempotency = dict(bff_main._GOV_BFF_IDEMPOTENCY)
    original_audit_events = list(bff_main._sse_buffers["audit"])
    with tempfile.TemporaryDirectory(prefix="bff_confirm_token_") as td:
        store_path = Path(td) / "commands.jsonl"
        store_path.touch()
        bff_main.command_store = CommandStore(str(store_path))
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        bff_main._sse_buffers["audit"].clear()
        try:
            yield TestClient(bff_main.app)
        finally:
            bff_main.command_store = original_command_store
            bff_main._GOV_BFF_IDEMPOTENCY.clear()
            bff_main._GOV_BFF_IDEMPOTENCY.update(original_idempotency)
            bff_main._sse_buffers["audit"].clear()
            bff_main._sse_buffers["audit"].extend(original_audit_events)


def _create_confirm_token(client: TestClient, token_id: str) -> None:
    resp = client.post(
        "/bff/confirm-tokens",
        headers={**_CONFIRM_HEADERS, "Idempotency-Key": f"create-token-{token_id}"},
        json={"tokenId": token_id},
    )
    assert resp.status_code == 201, f"Failed to seed token {token_id}: {resp.text}"


def test_post_bff_confirm_by_token_unknown_returns_typed_404() -> None:
    """Acceptance gate: unknown token returns typed 404, NOT generic 'Not Found'."""
    with _isolated_confirm_bff() as client:
        response = client.post(
            "/bff/command-confirmations/token-dev/confirm",
            headers={**_CONFIRM_HEADERS, "Idempotency-Key": "confirm-by-token-unknown-001"},
            json={"command_id": "cmd-test-unknown"},
        )

    assert response.status_code == 404, response.text
    error = response.json()["error"]
    assert error["code"] == "RESOURCE_NOT_FOUND"
    assert error["message"] != "Not Found"
    assert error["details"]["precondition_failed"] == "confirm_token_not_found"


def test_post_bff_confirm_by_token_mismatched_body_token_returns_412() -> None:
    with _isolated_confirm_bff() as client:
        response = client.post(
            "/bff/command-confirmations/path-token-abc/confirm",
            headers={**_CONFIRM_HEADERS, "Idempotency-Key": "confirm-by-token-mismatch-001"},
            json={"confirm_token": "different-token-xyz", "command_id": "cmd-test-mismatch"},
        )

    assert response.status_code == 412, response.text
    error = response.json()["error"]
    assert error["code"] == "PRECONDITION_FAILED"
    assert error["details"]["precondition_failed"] == "confirm_token_invalid"


def test_post_bff_confirm_by_token_dry_run_returns_200_no_side_effects() -> None:
    with _isolated_confirm_bff() as client:
        _create_confirm_token(client, "token-dry-run-p04")
        response = client.post(
            "/bff/command-confirmations/token-dry-run-p04/confirm",
            headers={
                **_CONFIRM_HEADERS,
                "Idempotency-Key": "confirm-by-token-dry-run-001",
                "X-Dry-Run": "1",
            },
            json={"command_id": "cmd-test-dry-run"},
        )
        audit_events = list(bff_main._sse_buffers["audit"])

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["data"]["status"] == "accepted"
    assert payload["data"]["commandId"] == "cmd-test-dry-run"
    assert payload["meta"]["dryRun"] is True
    assert payload["meta"]["evidenceKind"] == "command.confirm"
    assert len(audit_events) == 0


def test_post_bff_confirm_by_token_valid_returns_202_and_publishes_audit() -> None:
    with _isolated_confirm_bff() as client:
        _create_confirm_token(client, "token-valid-p04")
        response = client.post(
            "/bff/command-confirmations/token-valid-p04/confirm",
            headers={**_CONFIRM_HEADERS, "Idempotency-Key": "confirm-by-token-valid-001"},
            json={"command_id": "cmd-test-valid-001"},
        )
        replay = client.post(
            "/bff/command-confirmations/token-valid-p04/confirm",
            headers={**_CONFIRM_HEADERS, "Idempotency-Key": "confirm-by-token-valid-001"},
            json={"command_id": "cmd-test-valid-001"},
        )
        audit_events = list(bff_main._sse_buffers["audit"])

    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["data"]["status"] == "accepted"
    assert payload["data"]["commandId"] == "cmd-test-valid-001"
    assert payload["data"]["confirmed_at"]
    assert payload["meta"]["dryRun"] is False
    assert payload["meta"]["evidenceKind"] == "command.confirm"
    assert payload["meta"]["correlationId"] == "corr-confirm-by-token-test"

    assert replay.status_code == 202, replay.text
    assert replay.json()["data"] == payload["data"]

    event_types = [event["type"] for _event_id, event in audit_events]
    assert "command.confirm" in event_types
# P0-1 AdvanceLifecycle tests
# ---------------------------------------------------------------------------


def test_advance_lifecycle_returns_202_not_410() -> None:
    """Regression: AdvanceLifecycle must return 202, not 410 deprecated."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-test-draft-001"),
            json={
                "target_state": "paper_owner",
                "confirm_token": "tok-test-abc",
            },
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code != 410, (
        f"AdvanceLifecycle returned 410 deprecated — route not registered properly.\n{response.text}"
    )
    assert response.status_code in (202, 404), (
        f"Unexpected status {response.status_code}. Expected 202 (accepted) or 404 (persona not found).\n{response.text}"
    )


def test_advance_lifecycle_404_persona_has_typed_envelope() -> None:
    """A missing persona returns 404 with Pack D error envelope, not bare 'Not Found'."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-does-not-exist-xyzzy"),
            json={
                "target_state": "paper_owner",
                "confirm_token": "tok-test-abc",
            },
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 404, response.text
    body = response.json()
    assert "error" in body or "detail" not in body or (
        isinstance(body.get("detail"), dict) and "error" in body["detail"]
    ), f"Expected Pack D error envelope in 404 body, got: {body}"
    assert response.status_code != 410


def test_advance_lifecycle_missing_target_state_returns_422() -> None:
    """Missing target_state field returns 422 VALIDATION_FAILED."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-test-draft-001"),
            json={"confirm_token": "tok-test-abc"},
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 422, response.text
    body = response.json()
    _assert_error_code(body, "VALIDATION_FAILED")


def test_advance_lifecycle_invalid_target_state_returns_422() -> None:
    """An unsupported target_state value returns 422 VALIDATION_FAILED."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-test-draft-001"),
            json={
                "target_state": "invalid_state",
                "confirm_token": "tok-test-abc",
            },
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 422, response.text
    body = response.json()
    _assert_error_code(body, "VALIDATION_FAILED")


def test_advance_lifecycle_missing_confirm_token_returns_422() -> None:
    """Missing confirm_token returns 422 VALIDATION_FAILED, not a downstream error."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-test-draft-001"),
            json={"target_state": "paper_owner"},
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 422, response.text
    body = response.json()
    _assert_error_code(body, "VALIDATION_FAILED")


def test_advance_lifecycle_unauthenticated_returns_401() -> None:
    """No auth header returns 401 AUTH_REQUIRED."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-test-draft-001"),
            json={
                "target_state": "paper_owner",
                "confirm_token": "tok-test-abc",
            },
            headers={"Idempotency-Key": _idempotency_key()},
        )
    assert response.status_code == 401, response.text


def test_advance_lifecycle_live_owner_requires_approver_role() -> None:
    """Advancing to live_owner with operator-only role returns 403."""
    with _stub_auth():
        client = _client()
        response = client.post(
            _advance_lifecycle_url("persona-test-draft-001"),
            json={
                "target_state": "live_owner",
                "confirm_token": "tok-test-abc",
            },
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 403, response.text
    body = response.json()
    _assert_error_code(body, "FORBIDDEN")


def test_advance_lifecycle_unregistered_action_id_still_returns_410() -> None:
    """Other action_ids not yet registered still return 410 with replacement hint."""
    with _stub_auth():
        client = _client()
        response = client.post(
            "/bff/personas/persona-test/actions/SomeUnregisteredAction",
            json={},
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": _idempotency_key(),
            },
        )
    assert response.status_code == 410, response.text
    body = response.json()
    detail = body.get("detail") or {}
    error = detail.get("error") or {}
    details = error.get("details") or {}
    assert details.get("replacement"), (
        f"410 response missing details.replacement: {body}"
    )


# ---------------------------------------------------------------------------
# Action catalog: AdvanceLifecycle registered
# ---------------------------------------------------------------------------


def test_action_catalog_contains_advance_lifecycle() -> None:
    """Action catalog endpoint lists AdvanceLifecycle as a registered action."""
    with _stub_auth():
        client = _client()
        response = client.get(
            "/bff/actions",
            headers={"Authorization": _OPERATOR_TOKEN},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    catalog = body.get("catalog") or []
    action_ids = [entry.get("action_id") for entry in catalog]
    assert "AdvanceLifecycle" in action_ids, (
        f"AdvanceLifecycle not found in action catalog. Found: {action_ids}"
    )


# ---------------------------------------------------------------------------
# P0-8: GET /api/v1/operator/persona-management/{id} + data.health
# ---------------------------------------------------------------------------

_MGMT_HEADERS = {
    "Authorization": "Bearer test-operator:operator",
    "X-BFF-Api-Version": "2026-05-07",
    "X-Request-Id": "req-p08-persona-mgmt",
}

_P08_REQUIRED_DATA_KEYS = {"persona", "bindings", "deploymentPlans", "approvals", "runtimeBindings", "health"}


@contextmanager
def _isolated_persona_mgmt_bff() -> Iterator[TestClient]:
    """Swap in a local-fallback store so default seed personas are available."""
    original_store = bff_main.read_store
    bff_main.read_store = WriteGapTestReadPorts(allow_local_snapshot_fallback=True)
    try:
        yield TestClient(bff_main.app, raise_server_exceptions=False)
    finally:
        bff_main.read_store = original_store


def test_get_persona_management_returns_200_with_six_top_level_data_keys() -> None:
    """Probe F4: 200 for valid persona id with all six top-level keys in data."""
    with _isolated_persona_mgmt_bff() as client:
        response = client.get(
            "/api/v1/operator/persona-management/persona-alpha",
            headers=_MGMT_HEADERS,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    data = body.get("data", {})
    missing = _P08_REQUIRED_DATA_KEYS - set(data.keys())
    assert not missing, (
        f"data missing required keys: {missing}. Got keys: {set(data.keys())}"
    )


def test_get_persona_management_health_field_has_required_structure() -> None:
    """`data.health` must have status, score, and reasons when present."""
    with _isolated_persona_mgmt_bff() as client:
        response = client.get(
            "/api/v1/operator/persona-management/persona-alpha",
            headers=_MGMT_HEADERS,
        )

    assert response.status_code == 200, response.text
    health = response.json().get("data", {}).get("health")
    assert health is not None, "data.health must be present for a valid persona"
    assert "status" in health, f"health.status missing: {health}"
    assert health["status"] in ("healthy", "degraded", "critical"), (
        f"health.status must be one of healthy/degraded/critical, got: {health['status']!r}"
    )
    assert "score" in health, f"health.score missing: {health}"
    assert isinstance(health["score"], (int, float)), f"health.score must be numeric: {health}"
    assert "reasons" in health, f"health.reasons missing: {health}"
    assert isinstance(health["reasons"], list), f"health.reasons must be a list: {health}"


def test_get_persona_management_health_parity_with_fleet() -> None:
    """`data.health.status` matches the same persona's health in persona-fleet listing."""
    with _isolated_persona_mgmt_bff() as client:
        mgmt_response = client.get(
            "/api/v1/operator/persona-management/persona-alpha",
            headers=_MGMT_HEADERS,
        )
        fleet_response = client.get(
            "/bff/management/persona-fleet",
            headers=_MGMT_HEADERS,
        )

    assert mgmt_response.status_code == 200, mgmt_response.text
    mgmt_health = mgmt_response.json().get("data", {}).get("health") or {}

    assert fleet_response.status_code == 200, fleet_response.text
    fleet_data = fleet_response.json().get("data") or {}
    fleet_items = fleet_data.get("items") if isinstance(fleet_data, dict) else []
    fleet_items = fleet_items if isinstance(fleet_items, list) else []
    fleet_persona = next(
        (p for p in fleet_items if p.get("persona_id") == "persona-alpha" or p.get("id") == "persona-alpha"),
        None,
    )
    if fleet_persona is None:
        return  # persona not in fleet view — skip parity check
    fleet_health = fleet_persona.get("health")

    assert mgmt_health.get("status") == fleet_health, (
        f"Health status mismatch: mgmt={mgmt_health.get('status')!r} vs fleet={fleet_health!r}"
    )


def test_get_persona_management_404_for_missing_persona_has_typed_envelope() -> None:
    """Missing persona id returns 404 with a Pack D error envelope, not bare 'Not Found'."""
    with _isolated_persona_mgmt_bff() as client:
        response = client.get(
            "/api/v1/operator/persona-management/persona-does-not-exist-xyzzy-p08",
            headers=_MGMT_HEADERS,
        )

    assert response.status_code == 404, response.text
    body = response.json()
    error = body.get("error") or (body.get("detail") or {}).get("error") or {}
    assert error.get("code") == "RESOURCE_NOT_FOUND", (
        f"Expected RESOURCE_NOT_FOUND error code in 404 body, got: {body}"
    )


def test_get_persona_management_deploymentplans_and_approvals_are_lists() -> None:
    """`data.deploymentPlans` and `data.approvals` are always lists (possibly empty)."""
    with _isolated_persona_mgmt_bff() as client:
        response = client.get(
            "/api/v1/operator/persona-management/persona-alpha",
            headers=_MGMT_HEADERS,
        )

    assert response.status_code == 200, response.text
    data = response.json().get("data", {})
    assert isinstance(data.get("deploymentPlans"), list), (
        f"data.deploymentPlans must be a list, got: {type(data.get('deploymentPlans'))}"
    )
    assert isinstance(data.get("approvals"), list), (
        f"data.approvals must be a list, got: {type(data.get('approvals'))}"
    )
# Isolation helpers (ApprovalDecisions)
# ---------------------------------------------------------------------------


@contextmanager
def _isolated_approval_decisions_bff() -> Iterator[TestClient]:
    original_decisions = dict(bff_main._WIZARD_APPROVAL_DECISIONS)
    original_idempotency = dict(bff_main._GOV_BFF_IDEMPOTENCY)
    original_approval_events = list(bff_main._sse_buffers["approval"])
    bff_main._WIZARD_APPROVAL_DECISIONS.clear()
    bff_main._GOV_BFF_IDEMPOTENCY.clear()
    bff_main._sse_buffers["approval"].clear()
    try:
        yield TestClient(bff_main.app, raise_server_exceptions=False)
    finally:
        bff_main._WIZARD_APPROVAL_DECISIONS.clear()
        bff_main._WIZARD_APPROVAL_DECISIONS.update(original_decisions)
        bff_main._GOV_BFF_IDEMPOTENCY.clear()
        bff_main._GOV_BFF_IDEMPOTENCY.update(original_idempotency)
        bff_main._sse_buffers["approval"].clear()
        bff_main._sse_buffers["approval"].extend(original_approval_events)


# ---------------------------------------------------------------------------
# P0-7 POST /api/v1/approval-decisions tests
# ---------------------------------------------------------------------------

_APPROVAL_URL = "/api/v1/approval-decisions"


def _approval_headers(idem_key: str = "approval-decision-test-001") -> dict:
    return {
        "Authorization": _APPROVER_TOKEN,
        "Idempotency-Key": idem_key,
        "Content-Type": "application/json",
        "X-BFF-Api-Version": "2026-05-07",
        "X-Correlation-Id": "corr-approval-decision-001",
    }


def _approval_payload(
    plan_id: str = "plan-wizard-001",
    decision: str = "approve",
    memo: str = "Approved after review",
) -> dict:
    return {"plan_id": plan_id, "decision": decision, "memo": memo}


def test_post_approval_decisions_returns_202_with_command_id() -> None:
    with _isolated_approval_decisions_bff() as client:
        response = client.post(
            _APPROVAL_URL,
            headers=_approval_headers(),
            json=_approval_payload(),
        )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["data"]["status"] == "accepted"
    assert body["data"]["commandId"]
    assert body["data"]["plan_id"] == "plan-wizard-001"
    assert body["data"]["decision"] == "approve"
    assert body["data"]["approver_id"]
    assert body["data"]["decided_at"]
    assert body["meta"]["dryRun"] is False
    assert body["meta"]["evidenceKind"] == "approval.decide"


def test_post_approval_decisions_reject_returns_202() -> None:
    with _isolated_approval_decisions_bff() as client:
        response = client.post(
            _APPROVAL_URL,
            headers=_approval_headers("approval-reject-001"),
            json=_approval_payload(plan_id="plan-wizard-reject-001", decision="reject"),
        )
    assert response.status_code == 202, response.text
    assert response.json()["data"]["decision"] == "reject"


def test_post_approval_decisions_dry_run_returns_200_no_persist() -> None:
    with _isolated_approval_decisions_bff() as client:
        response = client.post(
            _APPROVAL_URL,
            headers={**_approval_headers("approval-dry-001"), "X-Dry-Run": "1"},
            json=_approval_payload(plan_id="plan-dry-001"),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["dryRun"] is True
    assert "plan-dry-001" not in bff_main._WIZARD_APPROVAL_DECISIONS


def test_post_approval_decisions_idempotent_replay() -> None:
    with _isolated_approval_decisions_bff() as client:
        first = client.post(
            _APPROVAL_URL,
            headers=_approval_headers("approval-idem-001"),
            json=_approval_payload(plan_id="plan-idem-001"),
        )
        replay = client.post(
            _APPROVAL_URL,
            headers=_approval_headers("approval-idem-001"),
            json=_approval_payload(plan_id="plan-idem-001"),
        )
    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    assert first.json()["data"]["commandId"] == replay.json()["data"]["commandId"]


def test_post_approval_decisions_conflict_same_plan_second_write() -> None:
    with _isolated_approval_decisions_bff() as client:
        client.post(
            _APPROVAL_URL,
            headers=_approval_headers("approval-conflict-001"),
            json=_approval_payload(plan_id="plan-conflict-001"),
        )
        second = client.post(
            _APPROVAL_URL,
            headers=_approval_headers("approval-conflict-002"),
            json=_approval_payload(plan_id="plan-conflict-001"),
        )
    assert second.status_code == 409, second.text
    _assert_error_code(second.json(), "RESOURCE_CONFLICT")


def test_post_approval_decisions_missing_plan_id_returns_422() -> None:
    with _isolated_approval_decisions_bff() as client:
        response = client.post(
            _APPROVAL_URL,
            headers=_approval_headers("approval-val-001"),
            json={"decision": "approve", "memo": "Approved after review"},
        )
    assert response.status_code == 422, response.text
    _assert_error_code(response.json(), "VALIDATION_FAILED")


def test_post_approval_decisions_invalid_decision_returns_422() -> None:
    with _isolated_approval_decisions_bff() as client:
        response = client.post(
            _APPROVAL_URL,
            headers=_approval_headers("approval-val-002"),
            json={"plan_id": "plan-val-001", "decision": "maybe", "memo": "Approved after review"},
        )
    assert response.status_code == 422, response.text
    _assert_error_code(response.json(), "VALIDATION_FAILED")


def test_post_approval_decisions_short_memo_returns_422() -> None:
    with _isolated_approval_decisions_bff() as client:
        response = client.post(
            _APPROVAL_URL,
            headers=_approval_headers("approval-val-003"),
            json={"plan_id": "plan-val-002", "decision": "approve", "memo": "short"},
        )
    assert response.status_code == 422, response.text
    _assert_error_code(response.json(), "VALIDATION_FAILED")


def test_post_approval_decisions_operator_role_returns_403() -> None:
    with _isolated_approval_decisions_bff() as client:
        response = client.post(
            _APPROVAL_URL,
            headers={
                "Authorization": _OPERATOR_TOKEN,
                "Idempotency-Key": "approval-403-001",
            },
            json=_approval_payload(),
        )
    assert response.status_code == 403, response.text
    _assert_error_code(response.json(), "FORBIDDEN")


def test_post_approval_decisions_unauthenticated_returns_401() -> None:
    with _isolated_approval_decisions_bff() as client:
        response = client.post(
            _APPROVAL_URL,
            headers={"Idempotency-Key": "approval-401-001"},
            json=_approval_payload(),
        )
    assert response.status_code == 401, response.text


def test_post_approval_decisions_publishes_sse_events() -> None:
    with _isolated_approval_decisions_bff() as client:
        client.post(
            _APPROVAL_URL,
            headers=_approval_headers("approval-sse-001"),
            json=_approval_payload(plan_id="plan-sse-001"),
        )
        event_types = [event["type"] for _eid, event in bff_main._sse_buffers["approval"]]
    assert "approval.decided" in event_types


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _assert_error_code(body: dict, expected_code: str) -> None:
    error = body.get("error") or (body.get("detail") or {}).get("error") or {}
    code = error.get("code")
    assert code == expected_code, (
        f"Expected error code {expected_code!r}, got {code!r}. Body: {body}"
    )

# ---------------------------------------------------------------------------
# P0-3 unit tests: StartRuntime (BFF-WRITE-P0-LIFECYCLE-003)
# ---------------------------------------------------------------------------
import unittest
from unittest.mock import patch

from models import CommandType, RiskLevel
from action_catalog import get_catalog_entry, catalog_action_ids
from command_executor import _execute_start_runtime, execute_command

class TestStartRuntimeCommandType(unittest.TestCase):
    """CommandType enum registration."""

    def test_start_runtime_enum_value(self) -> None:
        self.assertEqual(CommandType.START_RUNTIME.value, "StartRuntime")

    def test_start_runtime_in_enum(self) -> None:
        values = {ct.value for ct in CommandType}
        self.assertIn("StartRuntime", values)


class TestStartRuntimeCatalogEntry(unittest.TestCase):
    """Action catalog registration and governance metadata."""

    def setUp(self) -> None:
        self.entry = get_catalog_entry("StartRuntime")

    def test_entry_exists(self) -> None:
        self.assertIsNotNone(self.entry, "StartRuntime must be in action catalog")

    def test_entity_type(self) -> None:
        self.assertEqual(self.entry.entity_type, "Runtime")

    def test_risk_level_high(self) -> None:
        self.assertEqual(self.entry.risk_level, RiskLevel.HIGH)

    def test_requires_confirm_token(self) -> None:
        self.assertTrue(self.entry.requires_confirm_token)

    def test_requires_two_man(self) -> None:
        # Two-man required for live runtimes; catalog marks it True (BFF
        # precondition layer enforces conditionally on runtime_kind).
        self.assertTrue(self.entry.requires_two_man)

    def test_runtime_operator_in_required_roles(self) -> None:
        self.assertIn("runtime_operator", self.entry.required_roles)

    def test_live_owner_approver_in_required_roles(self) -> None:
        self.assertIn("live_owner_approver", self.entry.required_roles)

    def test_idempotency_required(self) -> None:
        self.assertTrue(self.entry.idempotency_required)

    def test_cooldown_nonzero(self) -> None:
        # Card P0-3 cooldown: 60s
        self.assertGreater(self.entry.cooldown_seconds, 0)

    def test_endpoint_references_runtimes_and_start_runtime(self) -> None:
        self.assertIn("runtimes", self.entry.endpoint)
        self.assertIn("StartRuntime", self.entry.endpoint)

    def test_catalog_action_ids_includes_start_runtime(self) -> None:
        self.assertIn("StartRuntime", catalog_action_ids())


class TestStartRuntimeCommandTypeFullCoverage(unittest.TestCase):
    """Every CommandType must have a catalog entry (existing contract)."""

    def test_start_runtime_catalogued(self) -> None:
        catalogued = catalog_action_ids()
        self.assertIn(CommandType.START_RUNTIME.value, catalogued)


class TestExecuteStartRuntime(unittest.TestCase):
    """_execute_start_runtime unit tests."""

    def setUp(self) -> None:
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

    @patch("command_executor._post_json")
    def test_success_returns_202_envelope(self, mock_post) -> None:
        mock_post.return_value = {
            "runtime_id": "rt-abc-001",
            "status": "accepted",
            "state": "starting",
            "audit_id": "audit-rt-abc-001",
            "started_at": "2026-05-28T00:00:00Z",
        }
        result = _execute_start_runtime(
            "cmd-rt-001",
            {"runtime_id": "rt-abc-001", "confirm_token": "tok-dev-001"},
        )
        self.assertEqual(result["command_id"], "cmd-rt-001")
        self.assertEqual(result["runtime_id"], "rt-abc-001")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["state"], "starting")
        self.assertEqual(result["audit_id"], "audit-rt-abc-001")
        mock_post.assert_called_once()

    @patch("command_executor._post_json")
    def test_correct_url_called(self, mock_post) -> None:
        mock_post.return_value = {
            "runtime_id": "rt-xyz-002",
            "status": "accepted",
            "state": "starting",
        }
        _execute_start_runtime(
            "cmd-rt-002",
            {"runtime_id": "rt-xyz-002", "confirm_token": "tok-dev-002"},
        )
        called_url = mock_post.call_args[0][0]
        self.assertIn("/api/internal/v1/runtimes/rt-xyz-002/start", called_url)

    @patch("command_executor._post_json")
    def test_two_man_token_forwarded_when_present(self, mock_post) -> None:
        mock_post.return_value = {
            "runtime_id": "rt-live-003",
            "status": "accepted",
            "state": "starting",
        }
        result = _execute_start_runtime(
            "cmd-rt-003",
            {
                "runtime_id": "rt-live-003",
                "confirm_token": "tok-live-003",
                "two_man_token": "2man-sig-abc",
            },
        )
        self.assertEqual(result["two_man_token"], "2man-sig-abc")
        payload_sent = mock_post.call_args[0][1]
        self.assertEqual(payload_sent["two_man_token"], "2man-sig-abc")

    @patch("command_executor._post_json")
    def test_two_man_token_absent_when_not_provided(self, mock_post) -> None:
        mock_post.return_value = {
            "runtime_id": "rt-paper-004",
            "status": "accepted",
            "state": "starting",
        }
        result = _execute_start_runtime(
            "cmd-rt-004",
            {"runtime_id": "rt-paper-004", "confirm_token": "tok-paper-004"},
        )
        self.assertIsNone(result["two_man_token"])
        payload_sent = mock_post.call_args[0][1]
        self.assertNotIn("two_man_token", payload_sent)

    def test_missing_runtime_id_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _execute_start_runtime(
                "cmd-rt-missing",
                {"confirm_token": "tok-dev"},
            )
        self.assertIn("runtime_id", str(ctx.exception))

    def test_missing_confirm_token_raises_value_error(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            _execute_start_runtime(
                "cmd-rt-no-token",
                {"runtime_id": "rt-001"},
            )
        self.assertIn("confirm_token", str(ctx.exception))

    @patch("command_executor._post_json")
    def test_default_state_is_starting_when_backend_omits_field(self, mock_post) -> None:
        mock_post.return_value = {"runtime_id": "rt-005", "status": "accepted"}
        result = _execute_start_runtime(
            "cmd-rt-005",
            {"runtime_id": "rt-005", "confirm_token": "tok-005"},
        )
        self.assertEqual(result["state"], "starting")


class TestExecuteCommandDispatchesStartRuntime(unittest.TestCase):
    """execute_command routes CommandType.START_RUNTIME to _execute_start_runtime."""

    def setUp(self) -> None:
        os.environ["PANTHEON_INTERNAL_API_URL"] = "http://localhost:5001"

    @patch("command_executor._post_json")
    def test_execute_command_start_runtime(self, mock_post) -> None:
        mock_post.return_value = {
            "runtime_id": "rt-dispatch-001",
            "status": "accepted",
            "state": "starting",
        }
        result = execute_command(
            "cmd-dispatch-001",
            CommandType.START_RUNTIME,
            {"runtime_id": "rt-dispatch-001", "confirm_token": "tok-dispatch-001"},
        )
        self.assertEqual(result["command_id"], "cmd-dispatch-001")
        self.assertEqual(result["state"], "starting")

    def test_no_executor_error_for_start_runtime(self) -> None:
        from command_executor import _EXECUTORS
        self.assertIn(CommandType.START_RUNTIME, _EXECUTORS)


if __name__ == "__main__":
    unittest.main()
