from __future__ import annotations

import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from persona_provisioning import MemoryPersonaProvisioningStore
from ports import create_in_memory_read_surface_ports
from read_store import _load_default_fixture_pack_datasets
from test_persona_provisioning_coordinator import FakeOwnerTransport, _schedule_receipt


from pathlib import Path
import json

HEADERS = {"Authorization": "Bearer op-pm12:operator,reviewer"}


@pytest.fixture(autouse=True)
def _canonical_persona_owner_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeOwnerTransport()
    monkeypatch.setattr(bff_main, "_PERSONA_PROVISIONING_STORE", MemoryPersonaProvisioningStore())
    monkeypatch.setattr(bff_main, "_PersonaOwnerHttpTransport", lambda: transport)
    monkeypatch.setattr(bff_main, "_register_persona_cron_required", _schedule_receipt)
    try:
        from services.persona.runtime_profile import build_persona_runtime_profile
        monkeypatch.setattr(bff_main, "build_persona_runtime_profile", build_persona_runtime_profile, raising=False)
    except ImportError:
        monkeypatch.setattr(bff_main, "build_persona_runtime_profile", lambda *a, **kw: type("Profile", (), {"to_dict": lambda s: {}})(), raising=False)


class _Pm12LeagueTestStore:
    def __init__(self, fallback: bool = True) -> None:
        self.fallback = fallback
        if fallback:
            raw_data = dict(_load_default_fixture_pack_datasets())
            data_file = Path(__file__).resolve().parent.parent / "data" / "read_surfaces.json"
            if data_file.exists():
                try:
                    loaded = json.loads(data_file.read_text(encoding="utf-8"))
                    for k, v in loaded.items():
                        if k not in raw_data or not raw_data[k]:
                            raw_data[k] = v
                        elif isinstance(raw_data[k], dict) and isinstance(v, dict):
                            raw_data[k] = {**raw_data[k], **v}
                        elif isinstance(raw_data[k], list) and isinstance(v, list):
                            raw_data[k] = [*raw_data[k], *v]
                except Exception:
                    pass
        else:
            raw_data = {}
        self.raw_data = raw_data
        self.personas = {}
        self.ranking_snapshots = {}

    def dataset_source(self, dataset: str) -> str:
        if dataset == "evidence_refs" and self.fallback:
            return "local_snapshot"
        if self.fallback and dataset in self.raw_data and self.raw_data[dataset]:
            return "local_snapshot"
        return "missing"

    def list_personas(self, **kwargs: Any) -> list[dict[str, Any]]:
        res = list(self.personas.values())
        if self.fallback:
            personas = self.raw_data.get("personas", {})
            raw_list = list(personas.values()) if isinstance(personas, dict) else personas
            existing_ids = {p.get("persona_id") or p.get("id") for p in res}
            for p in raw_list:
                if isinstance(p, dict):
                    pid = p.get("persona_id") or p.get("id")
                    if pid not in existing_ids:
                        res.append(dict(p))
        return res

    def get_persona(self, persona_id: str) -> dict[str, Any] | None:
        if persona_id in self.personas:
            return self.personas[persona_id]
        for p in self.list_personas():
            if p.get("persona_id") == persona_id or p.get("id") == persona_id:
                return p
        return None

    def create_persona(self, **kwargs: Any) -> dict[str, Any]:
        pid = kwargs.get("persona_id") or kwargs.get("id")
        meta = dict(kwargs.get("metadata") or {})
        if "archetype" in kwargs and "archetype" not in meta:
            meta["archetype"] = kwargs["archetype"]
        rec = {"id": pid, "persona_id": pid, **kwargs, "metadata": meta}
        self.personas[pid] = rec
        return rec

    def list_persona_league(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.fallback:
            entries = self.raw_data.get("persona_league", {})
            return [dict(e) for e in (entries.values() if isinstance(entries, dict) else entries) if isinstance(e, dict)]
        return []

    def get_persona_league_entry(self, persona_id: str) -> dict[str, Any] | None:
        for e in self.list_persona_league():
            if e.get("persona_id") == persona_id or e.get("id") == persona_id:
                return e
        return None

    def list_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.fallback:
            bindings = self.raw_data.get("bindings", {})
            return [dict(b) for b in (bindings.values() if isinstance(bindings, dict) else bindings) if isinstance(b, dict)]
        return []

    def get_bindings_for_persona(self, persona_id: str) -> list[dict[str, Any]]:
        return [b for b in self.list_bindings() if b.get("persona_id") == persona_id]

    def list_runtime_bindings(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.fallback:
            bindings = self.raw_data.get("runtime_bindings", {})
            raw_list = [dict(b) for b in (bindings.values() if isinstance(bindings, dict) else bindings) if isinstance(b, dict)]
            for b in raw_list:
                if (b.get("id") == "runtime-042" or b.get("runtime_id") == "runtime-042") and not b.get("persona_id"):
                    b["persona_id"] = "persona-alpha"
                    b["binding_id"] = "binding-042"
            return raw_list
        return []

    def get_runtime_binding(self, runtime_id: str) -> dict[str, Any] | None:
        for b in self.list_runtime_bindings():
            if b.get("runtime_id") == runtime_id or b.get("id") == runtime_id:
                return b
        return None

    def list_capital_pools(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.fallback:
            pools = self.raw_data.get("capital_pools", {})
            return [dict(p) for p in (pools.values() if isinstance(pools, dict) else pools) if isinstance(p, dict)]
        return []

    def get_capital_pool(self, pool_id: str) -> dict[str, Any] | None:
        for p in self.list_capital_pools():
            if p.get("pool_id") == pool_id or p.get("id") == pool_id:
                return p
        return None

    def list_evidence_refs(self, **kwargs: Any) -> list[dict[str, Any]]:
        if self.fallback:
            refs = self.raw_data.get("evidence_refs", {})
            raw_list = [dict(r) for r in (refs.values() if isinstance(refs, dict) else refs) if isinstance(r, dict)]
            if not raw_list:
                raw_list = [
                    {
                        "id": "ev-q1-001",
                        "ref_id": "ev-q1-001",
                        "persona_id": "persona-alpha",
                        "created_at": "2026-02-15T00:00:00Z",
                        "source_document": {
                            "captured_at": "2026-02-15T00:00:00Z",
                        },
                        "source": "paper_monitoring",
                    }
                ]
            else:
                for r in raw_list:
                    r["created_at"] = "2026-02-15T00:00:00Z"
                    doc = dict(r.get("source_document") or {})
                    doc["captured_at"] = "2026-02-15T00:00:00Z"
                    r["source_document"] = doc
            return raw_list
        return []

    def get_sessions_for_persona(self, persona_id: str) -> list[dict[str, Any]]:
        if self.fallback:
            sessions = self.raw_data.get("sessions") or self.raw_data.get("persona_sessions") or {}
            raw_list = [dict(s) for s in (sessions.values() if isinstance(sessions, dict) else sessions) if isinstance(s, dict)]
            matching = [s for s in raw_list if s.get("persona_id") == persona_id or persona_id in s.get("personas", [])]
            return matching
        return []

    def list_sessions_for_persona(self, persona_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self.get_sessions_for_persona(persona_id)

    def get_teaching_sessions_for_persona(self, persona_id: str) -> list[dict[str, Any]]:
        if self.fallback:
            sessions = self.raw_data.get("teaching_sessions", {})
            raw_list = [dict(s) for s in (sessions.values() if isinstance(sessions, dict) else sessions) if isinstance(s, dict)]
            return [s for s in raw_list if s.get("persona_id") == persona_id]
        return []

    def list_teaching_sessions_for_persona(self, persona_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        return self.get_teaching_sessions_for_persona(persona_id)

    def get_capability_snapshot_for_persona(self, persona_id: str) -> dict[str, Any]:
        return {
            "snapshot_id": f"cap-{persona_id}",
            "persona_id": persona_id,
            "effective_skills": ["trend_follower", "risk_manager"],
            "effective_tools": ["order_router"],
            "effective_workflows": [],
            "restrictions": [],
            "source_refs": [],
        }

    def put_ranking_snapshot(self, record: dict[str, Any]) -> dict[str, Any]:
        sid = record.get("ranking_snapshot_id") or record.get("id")
        self.ranking_snapshots[sid] = record
        return record

    def get_ranking_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        return self.ranking_snapshots.get(snapshot_id)

    def list_authoritative_paper_runtime_monitoring_sessions(self) -> list[dict[str, Any]]:
        return []

    def get_persona_allowed_actions(self, persona_id: str) -> dict[str, bool]:
        return {"paper_deploy": True, "kill_switch": True}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("list_"):
            return lambda *a, **kw: []
        if name.startswith("get_"):
            return lambda *a, **kw: None
        raise AttributeError(f"'_Pm12LeagueTestStore' has no attribute '{name}'")


def _fresh_client(td: str, *, fallback: bool = True) -> TestClient:
    bff_main.read_store = _Pm12LeagueTestStore(fallback=fallback)
    bff_main._STRATEGY_PERSONA_BFF_IDEMPOTENCY.clear()
    bff_main._STRATEGY_BFF_OVERLAY.clear()
    bff_main._PERSONA_BFF_OVERLAY.clear()
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _create_persona(client: TestClient, name: str, *, archetype: str, key: str) -> str:
    response = client.post(
        "/bff/personas",
        headers={**HEADERS, "Idempotency-Key": key},
        json={"name": name, "archetype": archetype},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["data"]["id"])


def test_pm12_persona_league_returns_composed_table() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get("/bff/management/persona-league", headers=HEADERS)

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            assert set(body["data"]) >= {"items", "summary"}
            assert body["page_info"]["total"] >= 1
            assert "GET /bff/personas/{id}/capabilities" in body["meta"]["composition_sources"]
            assert body["meta"]["surfaces"]["persona_league"]["status"] == "ok"
            assert "persona_sessions" in body["meta"]["surfaces"]

            rows = {row["id"]: row for row in body["data"]["items"]}
            row = rows["persona-alpha"]
            assert row["persona_id"] == "persona-alpha"
            assert row["route_policy_summary"]["rule_count"] >= 0
            assert row["capability_summary"]["skill_count"] >= 0
            assert row["binding_summary"]["total"] >= 1
            assert row["session_summary"]["total"] >= 1
            assert row["evaluation_summary"]["total"] >= 1
            assert row["memory_summary"]["total"] >= 0
            assert row["health_summary"]["health"] in {"healthy", "degraded"}
            assert row["links"]["detail"] == "/bff/personas/persona-alpha"
            assert row["links"]["route_policy"] == "/bff/personas/persona-alpha/route-policy"
            assert "allowed_action_summary" in row
            assert "personaId" not in row
            assert "routePolicy" not in row
            assert "capabilities" not in row
            assert "allowedActions" not in row
        finally:
            bff_main.read_store = original


def test_pm12_persona_league_filters_searches_and_paginates() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td, fallback=False)
            macro_id = _create_persona(client, "Macro PM12", archetype="macro", key="pm12-macro")
            _create_persona(client, "Risk PM12", archetype="risk", key="pm12-risk")

            response = client.get(
                "/bff/management/persona-league",
                headers=HEADERS,
                params={"state": "provisioning", "archetype": "macro", "q": "macro", "page_size": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["page_info"]["total"] == 1
            assert body["page_info"]["next_page_token"] is None
            assert body["data"]["items"][0]["id"] == macro_id
            assert body["data"]["items"][0]["archetype"] == "macro"
        finally:
            bff_main.read_store = original


def test_pm12_persona_league_rankings_returns_computed_blocks() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/persona-league/rankings",
                headers=HEADERS,
                params={"criteria": "overall,pnl", "limit": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assert [block["criteria"] for block in items] == ["overall", "pnl"]
            assert items[0]["items"][0]["rank"] == 1
            assert items[0]["items"][0]["persona_id"]
            assert "overall_score" in items[0]["items"][0]
            assert summary["persona_count"] >= 1
            assert "rankingId" not in items[0]
            assert "formulaVersion" not in items[0]
            assert "rankedCount" not in items[0]
            assert "personaId" not in items[0]["items"][0]
            assert "overallScore" not in items[0]["items"][0]
            assert "scoreField" not in items[0]["items"][0]
            assert "personaCount" not in summary
            assert "topPersonaId" not in summary
            assert body["meta"]["surfaces"]["persona_league_rankings"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/persona-league" in body["meta"]["composition_sources"]
        finally:
            bff_main.read_store = original


def test_pm12_persona_league_movers_returns_current_snapshot_movers() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/persona-league/movers",
                headers=HEADERS,
                params={"direction": "new", "limit": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assert "movers" not in body
            assert "movers" not in body["data"]
            assert summary["persona_count"] >= 1
            assert summary["mover_count"] >= 1
            assert summary["returned_count"] == 1
            assert summary["direction"] == "new"
            assert summary["baseline_status"] == "unavailable"
            assert summary["new_count"] == summary["persona_count"]
            assert items[0]["current_rank"] == 1
            assert items[0]["previous_rank"] is None
            assert items[0]["rank_delta"] is None
            assert items[0]["score_delta"] is None
            assert items[0]["direction"] == "new"
            assert items[0]["baseline_status"] == "unavailable"
            assert items[0]["movement"]["basis"] == "current_persona_league_snapshot_no_historical_baseline"
            assert body["page_info"]["total"] == summary["mover_count"]
            assert "personaCount" not in summary
            assert "moverCount" not in summary
            assert "returnedCount" not in summary
            assert "baselineStatus" not in summary
            assert "currentRank" not in items[0]
            assert "previousRank" not in items[0]
            assert "rankDelta" not in items[0]
            assert "scoreDelta" not in items[0]
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["persona_league_movers"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/persona-league/rankings" in body["meta"]["composition_sources"]
        finally:
            bff_main.read_store = original


def test_pm12_persona_league_movers_rejects_invalid_direction() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/persona-league/movers",
                headers=HEADERS,
                params={"direction": "sideways"},
            )

            assert response.status_code == 422, response.text
            body = response.json()
            assert "detail" not in body
            assert body["error"]["code"] == "VALIDATION_FAILED"
            assert body["field"] == "direction"
        finally:
            bff_main.read_store = original


def test_pm12_persona_league_tiers_returns_config_and_current_assignments() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get("/bff/management/persona-league/tiers", headers=HEADERS)

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            items = body["data"]["items"]
            summary = body["data"]["summary"]
            assignments = body["data"]["related"]["assignments"]
            assert len(items) == 4
            assert items[0]["tier_id"] == "tier-1"
            assert summary["formula_version"] == "pm12-default-v1"
            assert summary["persona_count"] == len(assignments)
            assert "tierId" not in items[0]
            assert "formulaVersion" not in summary
            assert "personaCount" not in summary
            assert "minScore" not in items[0]
            assert "governancePosture" not in items[0]
            assert "personaIds" not in items[0]
            assert "personaId" not in assignments[0]
            assert "overallScore" not in assignments[0]
            assert "byTier" not in summary
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["persona_league_tiers"]["status"] in {"ok", "degraded"}
        finally:
            bff_main.read_store = original


def test_pm12_persona_league_heatmap_uses_snake_case_rows() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/persona-league/heatmap",
                headers=HEADERS,
                params={"bucket": "day", "bucket_count": 2, "limit": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            data = body["data"]
            summary = data["summary"]
            row = data["items"][0]
            bucket = data["buckets"][0]
            cell = row["cells"][0]

            assert data["heatmap_id"] == "persona-league-heatmap"
            assert data["formula_version"] == "pm12-default-v1"
            assert summary["returned_persona_count"] == 1
            assert summary["bucket_count"] == 2
            assert row["persona_id"]
            assert row["runtime_ids"]
            assert bucket["bucket_id"]
            assert bucket["start_at"]
            assert bucket["end_exclusive_at"]
            assert cell["persona_id"] == row["persona_id"]
            assert cell["bucket_id"] == bucket["bucket_id"]
            assert cell["composite_score"] >= 0
            assert cell["formula_version"] == "pm12-default-v1"
            assert cell["observed_telemetry_count"] >= 0

            assert "heatmapId" not in data
            assert "formulaVersion" not in data
            assert "returnedPersonaCount" not in summary
            assert "bucketId" not in bucket
            assert "startAt" not in bucket
            assert "endExclusiveAt" not in bucket
            assert "personaId" not in row
            assert "tierId" not in row
            assert "latestScore" not in row
            assert "runtimeIds" not in row
            assert "personaId" not in cell
            assert "bucketId" not in cell
            assert "overallScore" not in cell
            assert "formulaVersion" not in cell
            assert "observedTelemetryCount" not in cell
        finally:
            bff_main.read_store = original


def test_pm12_quarterly_ranking_returns_formula_window_and_evidence() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q1", "page_size": 1},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            data = body["data"]
            items = data["items"]
            summary = data["summary"]
            assert "rankings" not in body
            assert "rankings" not in data
            assert summary["quarter"] == "2026-Q1"
            assert summary["formula_version"] == "pm12-default-v1"
            assert data["quarter_window"]["start_at"] == "2026-01-01T00:00:00Z"
            assert data["quarter_window"]["end_exclusive_at"] == "2026-04-01T00:00:00Z"
            assert data["formula"]["weights"]["pnl"] == 0.35
            assert body["page_info"]["page_size"] == 1
            assert body["page_info"]["total"] >= 1
            assert items[0]["rank"] == 1
            assert items[0]["quarter"] == "2026-Q1"
            assert items[0]["score_field"] == "overall_score"
            assert data["evidence_refs"]
            assert summary["evidence_ref_count"] == len(data["evidence_refs"])
            assert "quarterWindow" not in data
            assert "formulaVersion" not in summary
            assert "evidenceRefs" not in data
            assert "evidenceRefCount" not in summary
            assert "scoreField" not in items[0]
            assert "quarterWindow" not in items[0]
            assert "formulaVersion" not in items[0]
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["surfaces"]["quarterly_ranking"]["status"] in {"ok", "degraded"}
            assert "GET /bff/management/persona-league" in body["meta"]["composition_sources"]
            assert "GET /api/v1/knowledge/evidence" in body["meta"]["composition_sources"]
        finally:
            bff_main.read_store = original


def test_pm12_quarterly_ranking_formula_returns_weights_and_governance_trace() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/quarterly-ranking/formula",
                headers=HEADERS,
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["data"] == body["formula"]
            assert body["formula"]["formula_version"] == "pm12-default-v1"
            assert body["formula"]["score_field"] == "overall_score"
            assert body["formula"]["weights"] == {
                "pnl": 0.35,
                "risk": 0.25,
                "execution": 0.25,
                "activity": 0.15,
            }
            assert body["summary"]["weight_total"] == 1.0
            assert body["summary"]["evidence_ref_count"] == len(body["evidence_refs"])
            assert body["version_history"][0]["formula_version"] == "pm12-default-v1"
            assert body["version_history"][0]["governance_evidence_refs"]
            assert body["formula"]["change_control"]["requires_governance_evidence"] is True
            assert "formulaVersion" not in body["formula"]
            assert "weightTotal" not in body["summary"]
            assert "evidenceRefs" not in body
            assert "versionHistory" not in body
            assert "changeControl" not in body["formula"]
            assert body["meta"]["version_policy"] == "formula_version_changes_require_governance_evidence"
            assert body["meta"]["surfaces"]["quarterly_ranking_formula"]["status"] == "ok"
        finally:
            bff_main.read_store = original


def test_pm12_quarterly_ranking_drilldown_uses_snake_case_lightweight_sources() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/quarterly-ranking/drilldown",
                headers=HEADERS,
                params={"quarter": "2026-Q1", "persona_id": "persona-alpha"},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            data = body["data"]
            summary = body["summary"]
            contribution = body["contributions"][0]
            assert data["quarter_window"]["start_at"] == "2026-01-01T00:00:00Z"
            assert data["persona_id"] == "persona-alpha"
            assert data["summary"]["persona_id"] == "persona-alpha"
            assert summary["component_count"] == len(data["contributions"])
            assert contribution["score_field"]
            assert contribution["weighted_contribution"] >= 0
            assert contribution["contribution_share"] >= 0
            assert data["source_breakdown"]["route_policy_summary"]["rule_count"] >= 0
            assert data["source_breakdown"]["session_count"] >= 0
            assert data["links"]["parent_ranking"].endswith("quarter=2026-Q1")

            assert "rankingItem" not in body
            assert "contributionBreakdown" not in body
            assert "sourceBreakdown" not in body
            assert "quarterWindow" not in body
            assert "evidenceRefs" not in body
            assert "correlationId" not in body["meta"]
            assert "quarterWindow" not in data
            assert "personaId" not in data
            assert "rankingItem" not in data
            assert "contributionBreakdown" not in data
            assert "sourceBreakdown" not in data
            assert "evidenceRefs" not in data
            assert "parentRanking" not in data["links"]
            assert "scoreField" not in contribution
            assert "weightedContribution" not in contribution
            assert "contributionShare" not in contribution
            assert "rankedCount" not in summary
            assert "formulaVersion" not in summary
            assert "evidenceRefCount" not in summary
            assert "capabilities" not in data["source_breakdown"]
            assert "sessions" not in data["source_breakdown"]
            assert "memory" not in data["source_breakdown"]
            assert "allowedActions" not in data["source_breakdown"]
        finally:
            bff_main.read_store = original


def test_pm12_quarterly_ranking_recommendations_are_governance_only() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={"quarter": "2026-Q1", "page_size": 3},
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            data = body["data"]
            items = data["items"]
            summary = data["summary"]
            assert "recommendations" not in body
            assert "recommendations" not in data
            assert summary["quarter"] == "2026-Q1"
            assert data["quarter_window"]["start_at"] == "2026-01-01T00:00:00Z"
            assert body["page_info"]["page_size"] == 3
            assert body["page_info"]["total"] >= len(items) >= 1
            assert body["meta"]["policy"] == "read_only_governance_advisory"
            assert body["meta"]["live_capital_mutation"] is False
            assert summary["live_capital_mutation_count"] == 0
            assert summary["human_gate_decision_count"] == body["page_info"]["total"]
            assert "human_gate_decision" in body["meta"]["governance_destinations"]
            assert "GET /bff/management/human-inbox" in body["meta"]["composition_sources"]
            assert body["meta"]["surfaces"]["quarterly_ranking_recommendations"]["status"] in {"ok", "degraded"}

            allowed = set(summary["allowed_actions"])
            assert allowed == {
                "promote_to_canary_candidate",
                "increase_research_budget",
                "grant_tool_access",
                "reduce_capital_access",
                "require_retraining",
                "freeze_persona",
                "suspend_persona",
                "retire_persona",
            }
            assert "quarterWindow" not in data
            assert "allowedActions" not in summary
            assert "humanGateDecisionCount" not in summary
            assert "liveCapitalMutationCount" not in summary
            for recommendation in items:
                assert recommendation["action_id"] in allowed
                assert recommendation["recommendation_type"] == "governance_advisory"
                assert recommendation["requires_human_gate_decision"] is True
                assert recommendation["live_capital_mutation"] is False
                assert recommendation["governance"]["live_capital_mutation"] is False
                assert "human_inbox" in recommendation["governance"]["destinations"]
                assert "actionId" not in recommendation
                assert "recommendationType" not in recommendation
                assert "requiresHumanGateDecision" not in recommendation
                assert "liveCapitalMutation" not in recommendation
                assert "liveCapitalMutation" not in recommendation["governance"]
        finally:
            bff_main.read_store = original


def test_pm12_quarterly_ranking_rejects_invalid_quarter() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-05"},
            )

            assert response.status_code == 422, response.text
            body = response.json()
            assert "detail" not in body
            assert body["error"]["code"] == "VALIDATION_FAILED"
            assert body["field"] == "quarter"

            recommendations = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={"quarter": "2026-05"},
            )

            assert recommendations.status_code == 422, recommendations.text
            recommendations_body = recommendations.json()
            assert "detail" not in recommendations_body
            assert recommendations_body["error"]["code"] == "VALIDATION_FAILED"
            assert recommendations_body["field"] == "quarter"
        finally:
            bff_main.read_store = original


def test_pm12_performance_attribution_pages_before_projection_and_uses_snake_case() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_projector = bff_main._pm12_performance_attribution_rows
        projected_entry_counts: list[int] = []
        try:
            client = _fresh_client(td)

            def recording_projector(entries, *args, **kwargs):
                projected_entry_counts.append(len(entries))
                return original_projector(entries, *args, **kwargs)

            bff_main._pm12_performance_attribution_rows = recording_projector
            response = client.get(
                "/bff/management/performance-attribution",
                headers=HEADERS,
                params={
                    "dimension": "persona,strategy,pool,asset,broker,runtime,regime",
                    "page_size": 1,
                },
            )

            assert response.status_code == 200, response.text
            body = response.json()
            assert set(body) == {"data", "page_info", "meta"}
            data = body["data"]
            summary = data["summary"]
            items = data["items"]
            assert "items" not in body
            assert "rows" not in body
            assert "summary" not in body
            assert "rows" not in data
            assert body["page_info"]["page_size"] == 1
            assert body["page_info"]["total"] == summary["row_count"]
            assert summary["returned_row_count"] == len(items) == 1
            assert projected_entry_counts == [1]
            assert summary["supported_dimensions"]
            assert summary["runtime_count"] >= 1
            assert "supportedDimensions" not in summary
            assert "rowCount" not in summary
            assert "returnedRowCount" not in summary
            assert "runtimeCount" not in summary
            assert "totalPnl" not in summary

            item = items[0]
            assert item["dimension_key"]
            assert item["runtime_count"] >= 1
            assert item["holding_count"] >= 1
            assert "dimensionKey" not in item
            assert "totalPnl" not in item
            assert "runtimeCount" not in item
            assert "holdingCount" not in item
            assert "sourceRefs" not in item
            assert "capitalPool" not in item["links"]
            assert "runtime_ids" in item["source_refs"]
            assert "runtimeIds" not in item["source_refs"]

            metrics = item["metrics"]
            assert "total_pnl" in metrics
            assert "runtime_count" in metrics
            assert "pnl_contribution_pct" in metrics
            assert "totalPnl" not in metrics
            assert "runtimeCount" not in metrics
            assert "pnlContributionPct" not in metrics
        finally:
            bff_main._pm12_performance_attribution_rows = original_projector
            bff_main.read_store = original_store


def test_pm12_persona_league_requires_auth() -> None:
    with tempfile.TemporaryDirectory() as td:
        original = bff_main.read_store
        try:
            client = _fresh_client(td)
            response = client.get("/bff/management/persona-league")

            assert response.status_code == 401, response.text

            rankings = client.get("/bff/management/persona-league/rankings")
            assert rankings.status_code == 401, rankings.text

            movers = client.get("/bff/management/persona-league/movers")
            assert movers.status_code == 401, movers.text

            tiers = client.get("/bff/management/persona-league/tiers")
            assert tiers.status_code == 401, tiers.text

            heatmap = client.get("/bff/management/persona-league/heatmap")
            assert heatmap.status_code == 401, heatmap.text

            quarterly = client.get("/bff/management/quarterly-ranking")
            assert quarterly.status_code == 401, quarterly.text

            recommendations = client.get("/bff/management/quarterly-ranking/recommendations")
            assert recommendations.status_code == 401, recommendations.text

            formula = client.get("/bff/management/quarterly-ranking/formula")
            assert formula.status_code == 401, formula.text
        finally:
            bff_main.read_store = original
