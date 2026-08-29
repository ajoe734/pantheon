"""
BFF-B3-002: contract tests for GET /bff/management/persona-fleet.

The route is a read-only Management aggregate. It composes the existing B2
persona facade with persona-capital bindings, runtime bindings, telemetry,
trainer sessions, and evolution decisions.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from typing import Any
from pathlib import Path
from ports import create_in_memory_read_surface_ports

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


@pytest.fixture(autouse=True)
def _enable_market_persona_seed(monkeypatch):
    """This legacy-fleet contract must explicitly opt into demo records."""

    monkeypatch.setenv("PANTHEON_BFF_MARKET_PERSONA_SEED", "1")


OPERATOR_HEADERS = {"Authorization": "Bearer op-b3:operator"}
PERSONA_FLEET_ROW_HARD_LIMIT_BYTES = 8_000
PERSONA_FLEET_FORBIDDEN_LIST_KEYS = {
    "currentResearchProjects",
    "current_research_projects",
    "dataSourceRefs",
    "data_source_refs",
    "dataSourceStatus",
    "data_source_status",
    "dataSources",
    "requiredDataSources",
    "required_data_sources",
    "researchRefs",
    "research_refs",
    "researchStatus",
    "research_status",
    "sourceHealthBindings",
    "source_health_bindings",
}


class _PersonaFleetTestStore:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        raw_personas = self.data.get("personas", {})
        if isinstance(raw_personas, list):
            self._data = {p.get("persona_id") or p.get("id"): p for p in raw_personas}
        elif isinstance(raw_personas, dict):
            self._data = dict(raw_personas)
        else:
            self._data = {}
        self.evolution_decisions_override = None

        persona_capital_kwargs = {
            "evolution_decisions": list(data.get("evolution_decisions", {}).values()) if isinstance(data.get("evolution_decisions"), dict) else data.get("evolution_decisions", []),
            "personas": list(data.get("personas", {}).values()) if isinstance(data.get("personas"), dict) else data.get("personas", []),
            "candidate_artifacts": list(data.get("candidate_artifacts", {}).values()) if isinstance(data.get("candidate_artifacts"), dict) else data.get("candidate_artifacts", []),
            "evolution_programs": list(data.get("evolution_programs", {}).values()) if isinstance(data.get("evolution_programs"), dict) else data.get("evolution_programs", []),
            "bindings": list(data.get("bindings", {}).values()) if isinstance(data.get("bindings"), dict) else data.get("bindings", []),
            "capital_pools": list(data.get("capital_pools", {}).values()) if isinstance(data.get("capital_pools"), dict) else data.get("capital_pools", []),
        }
        lifecycle_kwargs = {
            "incidents": data.get("incidents", {}),
            "postmortems": data.get("postmortems", {}),
            "kill_switch": data.get("kill_switch", {"enabled": False, "status": "armed"}),
            "governance_audit_events": list(data.get("governance_audit_events", {}).values()) if isinstance(data.get("governance_audit_events"), dict) else data.get("governance_audit_events", []),
            "freeze_orders": data.get("freeze_orders", {}),
            "all_rollbacks": list((data.get("all_rollbacks") or data.get("rollbacks", {})).values()) if isinstance(data.get("all_rollbacks") or data.get("rollbacks"), dict) else (data.get("all_rollbacks") or data.get("rollbacks", [])),
            "telemetry_summaries": list(data.get("telemetry_summaries", {}).values()) if isinstance(data.get("telemetry_summaries"), dict) else data.get("telemetry_summaries", []),
        }
        ooda_kwargs = {
            "approval_decisions": list(data.get("approval_decisions", {}).values()) if isinstance(data.get("approval_decisions"), dict) else data.get("approval_decisions", []),
            "mutation_reviews": list(data.get("mutation_reviews", {}).values()) if isinstance(data.get("mutation_reviews"), dict) else data.get("mutation_reviews", []),
        }

        class _PersonaShim:
            def __init__(outer_self):
                outer_self.outer = self
            def list_personas(outer_self, **kw):
                return outer_self.outer.list_personas(**kw)
            def get_persona(outer_self, pid):
                return outer_self.outer.get_persona(pid)
            def get_bindings_for_persona(outer_self, pid):
                return outer_self.outer.get_bindings_for_persona(pid)
            def list_sessions_for_persona(outer_self, pid, **kw):
                return []
            def list_teaching_sessions_for_persona(outer_self, pid, **kw):
                return []
            def get_persona_capabilities(outer_self, pid):
                return outer_self.outer.get_capability_snapshot_for_persona(pid)
            def get_capability_snapshot_for_persona(outer_self, pid):
                return outer_self.outer.get_capability_snapshot_for_persona(pid)

        from domain_ports.persona_training import PersonaTrainingDomainPort
        training_port = PersonaTrainingDomainPort(persona_port=_PersonaShim())

        self.ports = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs=persona_capital_kwargs,
            lifecycle_telemetry_governance_kwargs=lifecycle_kwargs,
            ooda_management_kwargs=ooda_kwargs,
        )
        self.ports.persona_training = training_port

    def create_persona(self, **kwargs: Any) -> dict[str, Any]:
        pid = kwargs.get("persona_id") or kwargs.get("id")
        rec = dict(kwargs)
        rec["id"] = pid
        rec["persona_id"] = pid
        if "created_at" in rec and "updated_at" not in rec:
            rec["updated_at"] = rec["created_at"]
        self._data[pid] = rec
        if "personas" in self.data:
            if isinstance(self.data["personas"], dict):
                self.data["personas"][pid] = rec
            elif isinstance(self.data["personas"], list):
                self.data["personas"].append(rec)
        return rec

    def list_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("bindings") or self.data.get("persona_bindings") or {}
        return list(val.values()) if isinstance(val, dict) else list(val)

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("runtime_bindings") or {}
        return list(val.values()) if isinstance(val, dict) else list(val)

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        return list(self._data.values())

    def get_persona(self, persona_id: Optional[str]) -> Optional[dict[str, Any]]:
        if not persona_id:
            return None
        return self._data.get(persona_id)

    def get_bindings_for_persona(self, persona_id: Optional[str]) -> list[dict[str, Any]]:
        if not persona_id:
            return []
        bindings = self.list_bindings()
        return [b for b in bindings if b.get("persona_id") == persona_id]

    def list_persona_league(self, **kwargs: Any) -> list[dict[str, Any]]:
        val = self.data.get("persona_league") or []
        if not val:
            personas = self.list_personas()
            return [
                {
                    "id": p.get("persona_id") or p.get("id"),
                    "persona_id": p.get("persona_id") or p.get("id"),
                    "name": p.get("name"),
                    "rank": i + 1,
                    "league_tier": "champion",
                    "score": 90.0,
                }
                for i, p in enumerate(personas)
            ]
        return list(val.values()) if isinstance(val, dict) else list(val)

    def list_evolution_decisions(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.evolution_decisions_override is not None:
            return self.evolution_decisions_override(**kwargs)
        val = self.data.get("evolution_decisions") or {}
        return list(val.values()) if isinstance(val, dict) else list(val)

    def get_capability_snapshot_for_persona(self, persona_id: Optional[str]) -> Optional[dict[str, Any]]:
        snaps = self.data.get("capability_snapshots") or {}
        if isinstance(snaps, dict) and persona_id in snaps:
            return snaps[persona_id]
        return {"persona_id": persona_id, "capabilities": []}

    def put_ranking_snapshot(self, record: dict[str, Any]) -> dict[str, Any]:
        self.data.setdefault("ranking_snapshots", {})[record.get("ranking_snapshot_id") or record.get("id")] = record
        return record

    def get_ranking_snapshot(self, snapshot_id: str) -> Optional[dict[str, Any]]:
        return (self.data.get("ranking_snapshots") or {}).get(snapshot_id)

    def get_quarterly_ranking_snapshot(self, period: str = "2026Q2", formula_version: str = "v1") -> Optional[dict[str, Any]]:
        items = []
        for i, p in enumerate(self.list_personas()):
            pid = p.get("persona_id") or p.get("id")
            items.append({
                "persona_id": pid,
                "rank": i + 1,
                "quarter": period,
                "score_field": "overall_score",
                "overall_score": 85.0,
            })
        import hashlib
        content_hash = hashlib.sha256(json.dumps(items, sort_keys=True).encode("utf-8")).hexdigest()
        return {
            "id": f"ranking-snapshot-{period}-{formula_version}",
            "period": period,
            "formula_version": formula_version,
            "items": items,
            "content_digest": content_hash,
            "created_at": "2026-06-03T08:00:00Z",
        }

    def dataset_source(self, dataset: str) -> str:
        key = dataset
        if dataset == "persona_bindings" and "persona_bindings" not in self.data:
            key = "bindings"
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
        if name.startswith("get_") and name[4:] in self.data:
            val = self.data[name[4:]]
            if isinstance(val, dict):
                return lambda item_id, **kw: val.get(item_id)
        raise AttributeError(f"'_PersonaFleetTestStore' has no attribute '{name}'")


def _fresh_client(td: str) -> TestClient:
    snapshot_path = os.path.join(td, "read_surfaces.json")
    if os.path.exists(snapshot_path):
        try:
            with open(snapshot_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = dict(_load_default_fixture_pack_datasets())
    else:
        default_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "read_surfaces.json")
        if os.path.exists(default_path):
            try:
                with open(default_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = dict(_load_default_fixture_pack_datasets())
        else:
            data = dict(_load_default_fixture_pack_datasets())

    raw_fixture = _load_default_fixture_pack_datasets()
    for k, v in raw_fixture.items():
        if k not in data or not data[k]:
            data[k] = v
        elif isinstance(data[k], dict) and isinstance(v, dict):
            data[k] = {**data[k], **v}
        elif isinstance(data[k], list) and isinstance(v, list):
            data[k] = [*data[k], *v]

    if os.environ.get("PANTHEON_BFF_MARKET_PERSONA_SEED") == "1":
        try:
            # Deliberate, narrow exception: _merge_market_persona_fleet is a
            # ~685-line synthetic US/TW/CRYPTO fleet generator with its own
            # env-gated config-driven discovery (_market_persona_seed_enabled)
            # and market-data-provider defaulting logic. It is not static
            # fixture data (unlike _load_default_fixture_pack_datasets above)
            # so it is not something a local test double can faithfully
            # reproduce without duplicating read_store's own business logic;
            # importing the real function here is the honest choice.
            from read_store import _merge_market_persona_fleet
            _merge_market_persona_fleet(data)
        except Exception:
            pass

    bff_main.read_store = _PersonaFleetTestStore(data)
    bff_main._PERSONA_BFF_OVERLAY.clear()
    bff_main._STRATEGY_BFF_OVERLAY.clear()
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    return TestClient(bff_main.app)


def test_persona_fleet_treats_deployed_lifecycle_as_operational() -> None:
    health = bff_main._project_persona_fleet_health(
        persona={"persona_id": "persona-deployed", "lifecycle_state": "deployed"},
        runtime_bindings=[{"runtime_id": "runtime-deployed", "status": "active"}],
        telemetry_summaries=[{"runtime_id": "runtime-deployed", "collected_at": "2026-06-03T08:00:00Z"}],
        active_incidents=[],
    )

    assert health["status"] == "healthy"
    assert "persona_lifecycle_not_active" not in health["reasons"]


def test_persona_fleet_composes_persona_bindings_telemetry_training_and_evolution() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/persona-fleet", headers=OPERATOR_HEADERS)

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert len(json.dumps(body).encode("utf-8")) < 250_000
            assert set(body) == {"data", "page_info", "meta"}
            assert set(body["data"]) == {"items", "summary"}
            assert "items" not in body
            assert "summary" not in body
            assert "persona_fleet" not in body["data"]
            assert "persona_league" not in body["data"]
            assert "capital_pools" not in body["data"]
            assert "runtime_bindings" not in body["data"]
            assert "human_inbox" not in body["data"]
            assert body["data"]["summary"]["total_personas"] >= 1
            assert body["meta"]["surfaces"]["persona_fleet"]["source"] in {
                "bff_composed_slim_list",
                "service_store",
                "local_snapshot",
            }

            alpha = next(item for item in body["data"]["items"] if item["id"] == "persona-alpha")
            assert alpha["name"] == "Alpha Persona"
            assert alpha["capital_pool_id"] is None
            assert alpha["legacy_paper_capital_pool_id"] == "pool-main"
            assert alpha["health"] in {"healthy", "degraded", "critical"}
            assert alpha["governance_required"] is True
            assert "data_source_summary" in alpha
            assert "data_sources" in alpha
            assert "research_summary" in alpha
            assert "performance_summary" in alpha
            assert not PERSONA_FLEET_FORBIDDEN_LIST_KEYS.intersection(alpha)
            assert len(json.dumps(alpha).encode("utf-8")) < PERSONA_FLEET_ROW_HARD_LIMIT_BYTES

            tw = next(item for item in body["data"]["items"] if item["id"] == "persona-tw-equity")
            assert tw["data_source_summary"]["provider_count"] == 5
            assert tw["data_source_summary"]["provider_status_counts"]["read_ok"] >= 1
            assert [source["provider_key"] for source in tw["data_sources"]] == [
                "shioaji",
                "twse",
                "tpex",
                "mops",
                "finmind",
            ]
            assert tw["data_sources"][0] == {
                "provider_key": "shioaji",
                "provider": "Shioaji quote",
                "market": "TW",
                "source_class": "broker_execution",
                "status": "read_ok",
                "order_capable_provider": True,
                "read_only": True,
                "order_side_effects_allowed": False,
                "capital_side_effects_allowed": False,
            }
            assert "evidence_ref" not in tw["data_sources"][0]
            assert not PERSONA_FLEET_FORBIDDEN_LIST_KEYS.intersection(tw)
            assert tw["research_summary"]["current_project_count"] == 1
            assert tw["research_summary"]["stage"] == "management_review_linked"
            assert len(json.dumps(tw).encode("utf-8")) < PERSONA_FLEET_ROW_HARD_LIMIT_BYTES
            assert body["data"]["summary"]["execution_boundary"] == {
                "approved_artifacts_only": True,
                "live_capital_side_effects": False,
                "human_gate_required_for_capital_changes": True,
            }
            assert body["meta"]["related"]["human_inbox"]["href"] == "/bff/management/human-inbox"
        finally:
            bff_main.read_store = original


def test_persona_fleet_supports_health_filter_and_pagination() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            all_resp = client.get(
                "/bff/management/persona-fleet?page_size=50",
                headers=OPERATOR_HEADERS,
            )
            assert all_resp.status_code == 200, all_resp.text
            existing_health = all_resp.json()["data"]["items"][0]["health"]
            resp = client.get(
                f"/bff/management/persona-fleet?health={existing_health}&page_size=1",
                headers=OPERATOR_HEADERS,
            )

            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["page_info"]["page_size"] == 1
            assert len(body["data"]["items"]) == 1
            assert body["data"]["items"][0]["health"] == existing_health
            assert body["data"]["summary"]["total_personas"] >= 1
            assert "page_info" not in body["data"]

            existing_stage = all_resp.json()["data"]["items"][0]["deployment_stage"]
            stage_resp = client.get(
                f"/bff/management/persona-fleet?deployment_stage={existing_stage}&page_size=50",
                headers=OPERATOR_HEADERS,
            )
            assert stage_resp.status_code == 200, stage_resp.text
            stage_items = stage_resp.json()["data"]["items"]
            assert stage_items
            assert {item["deployment_stage"] for item in stage_items} == {existing_stage}
        finally:
            bff_main.read_store = original


def test_persona_fleet_compact_sources_use_market_defaults_for_custom_crypto_rows() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            _fresh_client(td)
            persona = {
                "id": "persona-custom-crypto",
                "persona_id": "persona-custom-crypto",
                "name": "Crypto-Alt-Hunter",
                "lifecycle_state": "paper_owner",
                "metadata": {},
            }
            context_metadata, context_persona = bff_main._persona_fleet_context_overlay(
                persona,
                {},
                bff_main._persona_fleet_context_defaults_by_market(),
            )
            row = bff_main._project_persona_fleet_list_row(
                persona=persona,
                league_entry={},
                binding={},
                runtime={},
                active_incidents=[],
                telemetry_summaries=[],
                context_metadata=context_metadata,
                context_persona=context_persona,
                snapshot_at="2026-07-03T00:00:00Z",
            )

            assert row is not None
            assert row["data_source_summary"]["provider_count"] == 2
            assert [source["provider_key"] for source in row["data_sources"]] == ["kraken", "coingecko"]
            assert [source["status"] for source in row["data_sources"]] == [
                "datasource_smoke_ok",
                "read_unavailable",
            ]
        finally:
            bff_main.read_store = original


def test_persona_fleet_requires_authentication() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/persona-fleet")

            assert resp.status_code == 401, resp.text
            body = resp.json()
            error = body.get("error") or (body.get("detail") or {}).get("error") or {}
            assert error["code"] in {"AUTH_REQUIRED", "AUTH_REQUIRED"}
        finally:
            bff_main.read_store = original


def test_legacy_management_fleet_alias_is_not_registered() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            resp = client.get("/bff/management/fleet", headers=OPERATOR_HEADERS)

            assert resp.status_code == 404, resp.text
        finally:
            bff_main.read_store = original


def test_persona_fleet_mutation_evolution_contract() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            persona_id = "persona-20260528-04688755"
            bff_main.read_store.create_persona(
                persona_id=persona_id,
                name="Crypto-Alt-Hunter",
                actor_id="pantheon-dev-browser",
                created_at="2026-06-03T08:00:00Z",
                lifecycle_state="active",
                metadata={
                    "deployment_stage": "paper",
                    "capital_mode": "paper",
                },
            )

            decisions = []
            decision_reads = 0

            def list_decisions(**_kwargs):
                nonlocal decision_reads
                decision_reads += 1
                return decisions

            bff_main.read_store.list_evolution_decisions = list_decisions

            resp = client.get("/bff/management/persona-fleet?page_size=50", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            fallback = next(item for item in resp.json()["data"]["items"] if item["id"] == persona_id)
            assert decision_reads == 1
            assert fallback["last_mutation_kind"] == "fleet_summary"
            assert fallback["mutation_entry_id"] is None
            assert fallback["evolution_entry_id"] is None
            assert fallback["mutation_confidence"] == "fallback"
            assert fallback["last_mutation_at"] == "2026-06-03T08:00:00Z"
            assert any("No formal mutation entry id declared" in diag for diag in fallback["mutation_diagnostics"])
            assert fallback["evolution_href"] == (
                f"/management/evolution-journal?persona={persona_id}&source=fleet_summary"
            )

            decisions[:] = [
                {
                    "id": "evo-dec-focus",
                    "decision_id": "evo-dec-focus",
                    "target_id": persona_id,
                    "action_type": "retrain",
                    "risk_level": "medium",
                    "status": "approved",
                    "created_at": "2026-06-05T12:00:00Z",
                    "updated_at": "2026-06-05T12:00:00Z",
                }
            ]

            resp = client.get("/bff/management/persona-fleet?page_size=50", headers=OPERATOR_HEADERS)
            assert resp.status_code == 200, resp.text
            formal = next(item for item in resp.json()["data"]["items"] if item["id"] == persona_id)
            assert formal["last_mutation_kind"] == "formal_mutation"
            assert formal["mutation_entry_id"] == "evo-dec-focus"
            assert formal["evolution_entry_id"] == "evo-dec-focus"
            assert formal["mutation_confidence"] == "formal"
            assert formal["last_mutation_label"] == "2026-06-05"
            assert formal["last_mutation_at"] == "2026-06-05T12:00:00Z"
            assert formal["evolution_href"] == (
                f"/management/evolution-journal?persona={persona_id}&mutation_review=evo-dec-focus"
            )

            decisions[:] = [
                {
                    "id": "NaN",
                    "decision_id": "NaN",
                    "target_id": persona_id,
                    "action_type": "retrain",
                    "risk_level": "medium",
                    "status": "approved",
                    "created_at": "2026-06-06T12:00:00Z",
                    "updated_at": "2026-06-06T12:00:00Z",
                },
                {
                    "id": "2026-06-06",
                    "decision_id": "2026-06-06",
                    "target_id": persona_id,
                    "action_type": "retrain",
                    "risk_level": "medium",
                    "status": "approved",
                    "created_at": "2026-06-06T11:00:00Z",
                    "updated_at": "2026-06-06T11:00:00Z",
                }
            ]

            resp = client.get("/bff/management/persona-fleet?page_size=50", headers=OPERATOR_HEADERS)
            invalid = next(item for item in resp.json()["data"]["items"] if item["id"] == persona_id)
            assert invalid["last_mutation_kind"] == "fleet_summary"
            assert invalid["mutation_entry_id"] is None
            assert invalid["evolution_entry_id"] is None
            assert "NaN" not in invalid["evolution_href"]
            assert "2026-06-06" not in invalid["evolution_href"]

            unavailable = bff_main._persona_fleet_mutation_projection(
                persona_id=persona_id,
                updated_at=None,
                evolution_decisions=[],
                artifact_ids=set(),
                incident_ids=set(),
            )
            assert unavailable["last_mutation_kind"] == "unavailable"
            assert unavailable["evolution_href"] is None

        finally:
            bff_main.read_store = original


def test_paper_persona_fleet_rank_matches_quarterly_ranking_target() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            persona_id = "persona-20260528-04688755"
            bff_main.read_store.create_persona(
                persona_id=persona_id,
                name="Crypto-Alt-Hunter",
                actor_id="pantheon-dev-browser",
                created_at="2026-06-03T08:00:00Z",
                lifecycle_state="active",
                metadata={
                    "deployment_stage": "paper",
                    "capital_mode": "paper",
                },
            )

            fleet_resp = client.get(
                "/bff/management/persona-fleet?page_size=100",
                headers=OPERATOR_HEADERS,
            )
            ranking_resp = client.get(
                "/bff/management/quarterly-ranking?page_size=200",
                headers=OPERATOR_HEADERS,
            )
            assert fleet_resp.status_code == 200, fleet_resp.text
            assert ranking_resp.status_code == 200, ranking_resp.text

            fleet_row = next(
                item for item in fleet_resp.json()["data"]["items"]
                if item["id"] == persona_id
            )
            ranking_row = next(
                item for item in ranking_resp.json()["data"]["items"]
                if item["persona_id"] == persona_id
            )
            assert fleet_row["capital_mode"] == "paper"
            assert fleet_row["league_rank"] == ranking_row["rank"]
            assert fleet_row["league_score"] == ranking_row["score"]
            assert fleet_row["rank"]["basis"] == "quarterly_ranking"
            assert fleet_row["rank"]["period"] == "quarter"
        finally:
            bff_main.read_store = original


def test_paper_rank_snapshot_is_captured_before_broader_fleet_reads() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            persona_id = "persona-20260528-04688755"
            bff_main.read_store.create_persona(
                persona_id=persona_id,
                name="Crypto-Alt-Hunter",
                actor_id="pantheon-dev-browser",
                created_at="2026-06-03T08:00:00Z",
                lifecycle_state="active",
                metadata={
                    "deployment_stage": "paper",
                    "capital_mode": "paper",
                },
            )
            expected_resp = client.get(
                "/bff/management/quarterly-ranking?page_size=200",
                headers=OPERATOR_HEADERS,
            )
            assert expected_resp.status_code == 200, expected_resp.text
            expected = next(
                item for item in expected_resp.json()["data"]["items"]
                if item["persona_id"] == persona_id
            )

            list_personas = bff_main.read_store.list_personas
            broader_fleet_read_seen = False

            def order_sensitive_list_personas(*args, **kwargs):
                nonlocal broader_fleet_read_seen
                include_defaults = bool(kwargs.get("include_market_persona_defaults"))
                if include_defaults:
                    broader_fleet_read_seen = True
                elif broader_fleet_read_seen:
                    return []
                return list_personas(*args, **kwargs)

            bff_main.read_store.list_personas = order_sensitive_list_personas
            fleet_resp = client.get(
                "/bff/management/persona-fleet?page_size=100",
                headers=OPERATOR_HEADERS,
            )
            assert fleet_resp.status_code == 200, fleet_resp.text
            fleet_row = next(
                item for item in fleet_resp.json()["data"]["items"]
                if item["id"] == persona_id
            )
            assert broader_fleet_read_seen is True
            assert fleet_row["league_rank"] == expected["rank"]
            assert fleet_row["league_score"] == expected["score"]
            assert fleet_row["rank"]["basis"] == "quarterly_ranking"
        finally:
            bff_main.read_store = original


def test_sd_agc_03_persona_list_fleet_detail_admitted_identity_symmetry() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            list_resp = client.get("/bff/personas?page_size=100", headers=OPERATOR_HEADERS)
            fleet_resp = client.get("/bff/management/persona-fleet?page_size=100", headers=OPERATOR_HEADERS)

            assert list_resp.status_code == 200, list_resp.text
            assert fleet_resp.status_code == 200, fleet_resp.text

            list_items = list_resp.json()["data"]
            fleet_items = fleet_resp.json()["data"]["items"]

            list_ids = [item["id"] for item in list_items]
            fleet_ids = [item["id"] for item in fleet_items]

            # Invariant 1: Persona list and fleet share identical admitted identity set
            assert set(list_ids) == set(fleet_ids)
            assert len(list_ids) == len(fleet_ids)
            assert len(list_ids) >= 1

            # Invariant 2: Page info and summary totals are consistent
            list_page_info = list_resp.json()["page_info"]
            fleet_summary = fleet_resp.json()["data"]["summary"]
            assert list_page_info["canonical_total"] == len(list_ids)
            assert fleet_summary["canonical_total"] == len(fleet_ids)

            # Invariant 3: Every fleet detail link resolves with 200 and the same ID
            for item in fleet_items:
                persona_id = item["id"]
                detail_resp = client.get(f"/bff/personas/{persona_id}", headers=OPERATOR_HEADERS)
                assert detail_resp.status_code == 200, f"Detail lookup for {persona_id} failed: {detail_resp.text}"
                detail_data = detail_resp.json()["data"]
                assert detail_data["id"] == persona_id
                assert detail_data["name"]
        finally:
            bff_main.read_store = original


def test_sd_agc_03_foreign_identities_and_unadmitted_catalog_defaults_return_404() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            # Store without fallback - only dev-probe is admitted
            store = _PersonaFleetTestStore(
                {
                    "personas": [
                        {
                            "id": "persona-dev-probe",
                            "persona_id": "persona-dev-probe",
                            "name": "dev-probe",
                            "lifecycle_state": "paper",
                            "status": "healthy",
                            "created_at": "2026-06-03T08:27:44Z",
                            "updated_at": "2026-06-03T08:27:44Z",
                            "metadata": {"owner": "pantheon-dev-browser", "tenant_id": "pantheon-dev"},
                            "canonicalWriteAuthority": "persona_registry_service",
                            "persistenceMode": "bff_local_dev_store",
                        },
                        {
                            "id": "persona-other-tenant",
                            "persona_id": "persona-other-tenant",
                            "name": "other-tenant-persona",
                            "lifecycle_state": "paper",
                            "status": "healthy",
                            "created_at": "2026-06-03T08:27:44Z",
                            "updated_at": "2026-06-03T08:27:44Z",
                            "metadata": {"owner": "pantheon-dev-browser", "tenant_id": "tenant-other"},
                            "canonicalWriteAuthority": "persona_registry_service",
                            "persistenceMode": "bff_local_dev_store",
                        },
                    ]
                }
            )
            bff_main.read_store = store
            bff_main._PERSONA_BFF_OVERLAY.clear()
            bff_main._STRATEGY_BFF_OVERLAY.clear()
            bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
            bff_main.read_store._data = {
                "persona-dev-probe": {
                    "id": "persona-dev-probe",
                    "persona_id": "persona-dev-probe",
                    "name": "dev-probe",
                    "lifecycle_state": "paper",
                    "status": "healthy",
                    "created_at": "2026-06-03T08:27:44Z",
                    "updated_at": "2026-06-03T08:27:44Z",
                    "metadata": {"owner": "pantheon-dev-browser", "tenant_id": "pantheon-dev"},
                    "canonicalWriteAuthority": "persona_registry_service",
                    "persistenceMode": "bff_local_dev_store",
                },
                "persona-other-tenant": {
                    "id": "persona-other-tenant",
                    "persona_id": "persona-other-tenant",
                    "name": "other-tenant-persona",
                    "lifecycle_state": "paper",
                    "status": "healthy",
                    "created_at": "2026-06-03T08:27:44Z",
                    "updated_at": "2026-06-03T08:27:44Z",
                    "metadata": {"owner": "pantheon-dev-browser", "tenant_id": "tenant-other"},
                    "canonicalWriteAuthority": "persona_registry_service",
                    "persistenceMode": "bff_local_dev_store",
                },
            }

            client = TestClient(bff_main.app)

            # 1. Admitted persona resolves
            admitted_resp = client.get("/bff/personas/persona-dev-probe", headers=OPERATOR_HEADERS)
            assert admitted_resp.status_code == 200, admitted_resp.text
            assert admitted_resp.json()["data"]["id"] == "persona-dev-probe"

            # 2. Foreign-tenant persona returns 404
            foreign_resp = client.get("/bff/personas/persona-other-tenant", headers=OPERATOR_HEADERS)
            assert foreign_resp.status_code == 404, foreign_resp.text
            assert foreign_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

            # 3. Unadmitted catalog default returns 404 (not ghost navigable)
            catalog_default_resp = client.get("/bff/personas/persona-crypto", headers=OPERATOR_HEADERS)
            assert catalog_default_resp.status_code == 404, catalog_default_resp.text
            assert catalog_default_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

            # 4. Unknown random identity returns 404
            unknown_resp = client.get("/bff/personas/persona-nonexistent-999", headers=OPERATOR_HEADERS)
            assert unknown_resp.status_code == 404, unknown_resp.text
            assert unknown_resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

            # 5. List and Fleet contain only persona-dev-probe
            fleet_resp = client.get("/bff/management/persona-fleet", headers=OPERATOR_HEADERS)
            assert fleet_resp.status_code == 200
            fleet_items = fleet_resp.json()["data"]["items"]
            assert len(fleet_items) == 1
            assert fleet_items[0]["id"] == "persona-dev-probe"
            assert fleet_resp.json()["data"]["summary"]["catalog_default_total"] > 0
        finally:
            bff_main.read_store = original

