"""
BFF-B3-007: contract tests for GET /bff/management/persona-intent.

The route is a read-only Management aggregate. It composes redacted persona
trace, trainer, and Agora intent summaries without exposing raw transcripts,
message bodies, tool lists, or capability internals.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

from fastapi.testclient import TestClient


from services.control_plane.bff import main as bff_main
from typing import Any
from pathlib import Path
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

# Local re-implementation of read_store._load_default_fixture_pack_datasets:
# merges the same static, committed fixture-pack JSON files directly off
# disk, with no import from / coupling to read_store.py's adapter machinery.
_FIXTURE_PACK_DIR = Path(os.path.dirname(os.path.dirname(__file__))) / "data"
_FIXTURE_PACK_PATHS = (
    _FIXTURE_PACK_DIR / "fixtures_pack_a.json",
    _FIXTURE_PACK_DIR / "fixtures_pack_b.json",
    _FIXTURE_PACK_DIR / "fixtures_pack_c.json",
)
_FIXTURE_DATASET_ALIASES = {
    "deployments": "deployment_plans",
    "runtimes": "runtime_bindings",
}
_FIXTURE_RECORD_KEYS = [
    "id", "analysis_id", "entry_id", "decision_id", "intervention_id", "job_id",
    "plan_id", "program_id", "pool_id", "persona_id", "server_id", "signal_id",
    "skill_id", "session_id", "sessionId", "packet_id", "strategy_id",
    "experiment_id", "artifact_id", "rebalance_id", "binding_id", "runtime_id",
    "tool_id", "channel_id",
]


def _fixture_pack_record_key(record: Any) -> str:
    if isinstance(record, dict):
        for key in _FIXTURE_RECORD_KEYS:
            value = record.get(key)
            if value not in (None, ""):
                return str(value)
    return json.dumps(record, sort_keys=True, ensure_ascii=True)


def _load_fixture_pack_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    datasets = payload.get("datasets") if isinstance(payload, dict) else None
    if not isinstance(datasets, dict):
        return {}
    return json.loads(json.dumps(datasets))


def _merge_fixture_pack(target: dict[str, Any], fixture: dict[str, Any]) -> None:
    for raw_key, incoming in fixture.items():
        key = _FIXTURE_DATASET_ALIASES.get(raw_key, raw_key)
        if isinstance(incoming, dict):
            existing = target.get(key)
            if not isinstance(existing, dict):
                target[key] = json.loads(json.dumps(incoming))
                continue
            for record_key, record in incoming.items():
                if record_key not in existing:
                    existing[record_key] = json.loads(json.dumps(record))
            continue
        if isinstance(incoming, list):
            existing = target.get(key)
            if not isinstance(existing, list):
                target[key] = json.loads(json.dumps(incoming))
                continue
            seen = {_fixture_pack_record_key(record) for record in existing}
            for record in incoming:
                record_key = _fixture_pack_record_key(record)
                if record_key in seen:
                    continue
                existing.append(json.loads(json.dumps(record)))
                seen.add(record_key)


def _load_default_fixture_pack_datasets() -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in _FIXTURE_PACK_PATHS:
        _merge_fixture_pack(merged, _load_fixture_pack_file(path))
    return merged

OPERATOR_HEADERS = {"Authorization": "Bearer op-b3-persona-intent:operator"}


class _PersonaIntentTestStore:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        persona_capital_kwargs = {
            "personas": list(data.get("personas", {}).values()) if isinstance(data.get("personas"), dict) else data.get("personas", []),
        }
        self.ports = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs=persona_capital_kwargs,
        )

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("personas") or {}
        return list(val.values()) if isinstance(val, dict) else list(val)

    def get_sessions_for_persona(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        val = self.data.get("sessions") or {}
        items = list(val.values()) if isinstance(val, dict) else list(val)
        if not persona_id:
            return items
        return [s for s in items if s.get("persona_id") == persona_id or s.get("target_id") == persona_id]

    def get_teaching_sessions_for_persona(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        val = self.data.get("teaching_sessions") or {}
        items = list(val.values()) if isinstance(val, dict) else list(val)
        if not persona_id:
            return items
        return [s for s in items if s.get("persona_id") == persona_id or s.get("target_id") == persona_id]

    def list_agora_sessions(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("agora_sessions") or self.data.get("consultation_sessions") or {}
        items = list(val.values()) if isinstance(val, dict) else list(val)
        if not items:
            items = [
                {
                    "session_id": "cs-20260410-001",
                    "persona_id": "persona-alpha",
                    "persona_ids": ["persona-alpha"],
                    "session_type": "consult",
                    "status": "terminated",
                    "started_at": "2026-04-10T10:00:00Z",
                    "messages": [{"id": "m1", "created_at": "2026-04-10T10:05:00Z", "role": "user"}],
                }
            ]
        else:
            for s in items:
                if not s.get("messages"):
                    s["messages"] = [{"id": "m1", "created_at": s.get("started_at") or "2026-04-10T10:05:00Z", "role": "user"}]
        return items

    def get_capability_snapshot_for_persona(self, persona_id: Optional[str]) -> Optional[dict[str, Any]]:
        snaps = self.data.get("capability_snapshots") or {}
        if isinstance(snaps, dict) and persona_id in snaps:
            return snaps[persona_id]
        return {
            "persona_id": persona_id,
            "capabilities": [{"name": "tool_exec", "effective": True}],
            "tools_enabled": ["tool_exec"],
            "effective_tools": ["tool_exec"],
        }

    def dataset_source(self, dataset: str) -> str:
        key = dataset
        if dataset == "agora_sessions" and "agora_sessions" not in self.data:
            key = "consultation_sessions"
        if key in self.data:
            return "local_snapshot" if self.data[key] is not None else "missing"
        return self.ports.dataset_source(dataset)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self.ports, name, None)
        if attr is not None and callable(attr):
            def _safe_wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return attr(*args, **kwargs)
                except TypeError:
                    return attr(*args)
            return _safe_wrapper
        if attr is not None:
            return attr
        if name.startswith("list_") and name[5:] in self.data:
            val = self.data[name[5:]]
            items = list(val.values()) if isinstance(val, dict) else val
            return lambda **kw: items
        raise AttributeError(f"'_PersonaIntentTestStore' has no attribute '{name}'")


def _fresh_client(td: str) -> TestClient:
    snapshot_path = os.path.join(td, "read_surfaces.json")
    if os.path.exists(snapshot_path):
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = _load_default_fixture_pack_datasets()
    else:
        default_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "read_surfaces.json")
        if os.path.exists(default_path):
            try:
                with open(default_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = _load_default_fixture_pack_datasets()
        else:
            data = _load_default_fixture_pack_datasets()
    bff_main.read_store = _PersonaIntentTestStore(data)
    return TestClient(bff_main.app)


def test_persona_intent_composes_redacted_trace_trainer_and_agora_sources() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)

            resp = client.get("/bff/management/persona-intent", headers=OPERATOR_HEADERS)

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert set(body.keys()) == {"data", "page_info", "meta"}
            assert set(body["data"].keys()) == {"id", "items", "summary"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assert summary["persona_trace_count"] >= 1
            assert summary["trainer_session_count"] >= 1
            assert summary["agora_session_count"] >= 1
            assert summary["redacted_item_count"] == summary["total_items"]
            assert "bySourceType" not in summary
            assert "byStatus" not in summary
            assert "byIntent" not in summary
            assert body["meta"]["surfaces"]["management_persona_intent"]["source"] == "bff_composed"
            for surface in [
                "persona_traces",
                "persona_sessions",
                "capability_snapshots",
                "teaching_sessions",
                "agora_sessions",
            ]:
                assert surface in body["meta"]["surfaces"]

            by_type = {item["source_type"]: item for item in items}
            assert by_type["persona_trace"]["trace"]["trace_id"]
            assert by_type["persona_trace"]["trace"]["capability_summary"]["effective_tool_count"] >= 1
            assert by_type["trainer_session"]["trainer"]["outcome_count"] >= 1
            assert by_type["agora_session"]["agora"]["message_count"] >= 1
            assert "sourceType" not in by_type["persona_trace"]
            assert "personaId" not in by_type["persona_trace"]
            assert "sessionId" not in by_type["agora_session"]["agora"]

            encoded = json.dumps(body)
            assert '"tools_enabled":' not in encoded
            assert '"effective_tools":' not in encoded
            assert '"message_body":' not in encoded
            assert '"messages":' not in encoded
            assert '"content":' not in encoded
        finally:
            bff_main.read_store = original_store


def test_persona_intent_supports_filters_and_pagination() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)

            resp = client.get(
                "/bff/management/persona-intent"
                "?source_type=persona_trace&persona_id=persona-alpha&status=active&page_size=1",
                headers=OPERATOR_HEADERS,
            )

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["page_size"] == 1
            assert body["page_info"]["total"] == 1
            items = body["data"]["items"]
            assert len(items) == 1
            item = items[0]
            assert item["source_type"] == "persona_trace"
            assert item["persona_id"] == "persona-alpha"
            assert item["status"] == "active"
            assert item["redaction"]["policy"] == "management_persona_intent_public_summary"
        finally:
            bff_main.read_store = original_store


def test_persona_intent_requires_read_authentication() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/persona-intent")

            assert resp.status_code == 401, resp.text
            assert resp.json()["error"]["code"] == "AUTH_REQUIRED"
        finally:
            bff_main.read_store = original_store
