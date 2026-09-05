"""
LOOP-AUTO-BFF-004 Cross-Loop Operator Drills

Acceptance criteria:
  1. Drill proves one full source-to-health flow.
  2. Drill proves one runtime-to-incident-to-evolution-proposal flow.
  3. Final evidence states maturity reached and remaining blockers.

Drill 1: Source health connector truth → BFF persona panel projection → fail-closed loop-health label
Drill 2: Heartbeat-loss threshold breach → IncidentCase → resolved → Postmortem draft
         → published → EvolutionDecision proposal (evidence chain integrity verified)

Run:
    python3 -m pytest services/control-plane/bff/test_loop_auto_bff004_cross_loop_drill.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

# ---- Isolate evolution service storage BEFORE any service module imports ----
_DRILL_EVO_TMP = tempfile.mkdtemp(prefix="bff004_drill_evo_")
os.environ.setdefault("EVOLUTION_DATA_DIR", _DRILL_EVO_TMP)
os.environ.setdefault("INCIDENT_DATA_DIR", _DRILL_EVO_TMP)

# ---- Path setup ----
_BFF_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BFF_DIR.parents[2]
_CP_GOV = _BFF_DIR.parent / "governance"
_EVO_DIR = _REPO_ROOT / "services" / "evolution"
_POSTMORTEMS_DIR = _REPO_ROOT / "services" / "postmortems"
_INCIDENTS_DIR = _REPO_ROOT / "services" / "incidents"

for _p in (_REPO_ROOT, _BFF_DIR, _CP_GOV):
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)

from services.control_plane.bff import main as bff_main
from fastapi.testclient import TestClient  # noqa: E402

# ---- Incident domain — shared across incidents / postmortems / evolution ----
from services.incident.incident import IncidentStore  # noqa: E402
from services.incident.pg_store import build_incident_store  # noqa: E402
from services.incidents.consumer import ThresholdTelemetryIncidentConsumer  # noqa: E402
from services.postmortems.consumer import ResolvedIncidentPostmortemDraftConsumer  # noqa: E402

# ---- Evolution service — must be loaded with its own dir first on sys.path ----
# services/evolution/main.py uses a flat `from models import ...` (no try/except),
# which conflicts with BFF's models.py.  Temporarily move the evolution dir to the
# front of sys.path and clear the cached BFF models module so the loader picks up
# services/evolution/models.py instead.
_saved_bff_models = sys.modules.pop("models", None)
if str(_EVO_DIR) not in sys.path:
    sys.path.insert(0, str(_EVO_DIR))

import services.evolution.main as evo_main  # noqa: E402

# Restore BFF models in cache now that evolution is loaded
sys.modules.pop("models", None)
if _saved_bff_models is not None:
    sys.modules["models"] = _saved_bff_models

# ---- Constants ----
_BFF_HEADERS = {"Authorization": "Bearer bff004-drill:operator,reviewer,admin:mfa::tenant-dev"}

# ---------------------------------------------------------------------------
# Drill fixtures
# ---------------------------------------------------------------------------

_HEARTBEAT_LOSS_PAYLOAD: Dict[str, Any] = {
    "incident_id": "inc-bff004-drill-heartbeat-001",
    "title": "CROSS-LOOP DRILL: Runtime heartbeat loss exceeds liveness threshold",
    "telemetry_event": {
        "event_id": "tel-bff004-drill-hb-001",
        "event_type": "runtime.heartbeat_loss",
        "created_at": "2026-06-27T14:00:00Z",
        "severity": "critical",
        "runtime_binding_id": "rtb-bff004-drill-001",
        "deployment_stage": "paper",
        "deployment_plan_id": "plan-bff004-drill-001",
        "capital_pool_id": "pool-bff004-drill-001",
        "persona_capital_binding_id": "pcb-bff004-drill-001",
        "artifact_id": "artifact-bff004-drill-001",
        "artifact_version": "1.0.0",
        "runtime_id": "runtime-bff004-drill-001",
        "trace_id": "trace-bff004-drill-001",
        "heartbeat_lag_ms": 95000,
        "metrics": {
            "heartbeat_lag_ms": 95000,
            "last_heartbeat_age_s": 95,
            "missed_heartbeat_count": 3,
        },
        "description": "Cross-loop drill: heartbeat not received for 95 s. Liveness threshold is 30 s.",
    },
    "threshold_snapshot": {
        "policy_source": "KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md section 4.1",
        "signal_type": "governance_incident",
        "metric_name": "heartbeat_lag_ms",
        "comparator": "gt",
        "observed_value": 95000,
        "threshold_value": 30000,
        "window": "liveness-check",
        "breached": True,
        "note": "Cross-loop drill: heartbeat lag of 95 s exceeds the 30 s liveness threshold.",
    },
}

_SOURCE_HEALTH_TRUTH: Dict[str, Any] = {
    "tw-finmind-broker-daily-report": {
        "health": {
            "source_id": "tw-finmind-broker-daily-report",
            "status": "ok",
            "last_success_at": "2026-06-27T05:00:00Z",
            "latest_watermark": "2026-06-27",
            "row_count_last_run": 312,
            "metadata": {},
        },
        "connector": {
            "connector_id": "tw-finmind-broker-daily-report",
            "status": "enabled",
            "schedule": {
                "configured": True,
                "enabled": True,
                "interval_seconds": 86400,
            },
            "freshness": {
                "status": "fresh",
                "last_success_at": "2026-06-27T05:00:00Z",
            },
            "health_metrics": {},
        },
    },
}

_LOOP_HEALTH_STORE: Dict[str, Any] = {
    "source_ingestion": {
        "loop_id": "source_ingestion",
        "truth_level": "reconciled_live_proof",
        "controller_health": {
            "status": "healthy",
            "controller_name": "source-provisioning-health-probe",
            "last_heartbeat_at": "2026-06-27T06:00:00Z",
        },
        "last_success": {
            "at": "2026-06-27T06:01:00Z",
            "summary": "Source connector health probe confirmed reconciled live truth.",
            "evidence_refs": ["bff004-drill-src-health-001"],
        },
        "downstream_actual_state": {
            "status": "ok",
            "source": "source-health-probe",
            "checked_at": "2026-06-27T06:01:00Z",
        },
        "evidence_packet": {
            "packet_id": "bff004-drill-source-health-packet-001",
            "refs": ["docs/deployment/evidence/loop-auto-bff-004/README.md"],
        },
    }
}


# ---------------------------------------------------------------------------
# Drill 1 — Source-to-Health Flow
# ---------------------------------------------------------------------------

class TestDrill1SourceToHealth:
    """
    Drill 1: source_ingestion health truth flows to BFF operator panel.

    Chain: SourceHealth connector record (source_ingestion service)
           → BFF _overlay_source_health_truth projection
           → persona panel shows health_source=source_ingest, live_ingestion_enabled=True
           → loop-health keeps the historical fixture visible without promoting it
    """

    def test_source_health_connector_truth_projects_to_persona_panel(self, monkeypatch):
        """
        Step 1a: SourceHealth ok record → BFF source-health overlay projects:
          - health_source = source_ingest  (not static metadata)
          - live_ingestion_enabled = True
          - provider_statuses.finmind = read_ok  (not read_unavailable)
        """
        monkeypatch.setattr(
            bff_main,
            "_source_ingest_truth_by_connector",
            lambda: _SOURCE_HEALTH_TRUTH,
        )

        dss: Dict[str, Any] = {
            "state": "partial_readback",
            "provider_statuses": {"finmind": "read_unavailable"},
        }
        sources = [{"provider_key": "finmind", "status": "read_unavailable"}]
        required_sources = [
            {
                "dataset": "tw_broker_top",
                "market": "TW",
                "cadence": "daily",
                "source_class": "live_push",
                "connector_candidates": ["tw-finmind-broker-daily-report"],
            }
        ]

        out_dss, out_sources, bindings = bff_main._overlay_source_health_truth(
            dss,
            sources,
            required_data_sources=required_sources,
        )

        # Persona panel reads live source_ingest truth, not static metadata
        assert out_dss["source_health_source"] == "source_ingest", (
            "Expected source_health_source=source_ingest, got: " + str(out_dss.get("source_health_source"))
        )
        assert out_dss["live_ingestion_enabled"] is True, (
            "Expected live_ingestion_enabled=True when SourceHealth is ok"
        )
        assert out_dss["provider_statuses"]["finmind"] == "read_ok", (
            "Expected finmind=read_ok when SourceHealth status=ok, got: "
            + str(out_dss["provider_statuses"].get("finmind"))
        )

        # Required-source binding shows connector health from source_ingest
        assert len(bindings) >= 1
        binding = next(
            (b for b in bindings if b.get("connector_id") == "tw-finmind-broker-daily-report"),
            None,
        )
        assert binding is not None, "Expected a binding for tw-finmind-broker-daily-report"
        assert binding.get("health_source") == "source_ingest", (
            "Binding health_source should be source_ingest, got: " + str(binding.get("health_source"))
        )

    def test_loop_health_endpoint_does_not_promote_historical_drill_fixture(self, monkeypatch, tmp_path):
        """
        Step 1b: a service-store fixture may report a reconciled truth level, but
        it is not current controller-runtime proof and cannot raise liveness.
        """
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")

        async def _fetch_controller_records(tenant_id: str, environment: str):
            return True, list(_LOOP_HEALTH_STORE.values())

        monkeypatch.setattr(
            bff_main.loop_truth,
            "fetch_controller_store_health_records",
            _fetch_controller_records,
        )

        client = TestClient(bff_main.app, raise_server_exceptions=False)
        response = client.get("/bff/v5/loop-health/source_ingestion", headers=_BFF_HEADERS)

        assert response.status_code == 200, response.text
        data = response.json()["data"]
        packet = data["evidence_packet"]

        assert packet["highest_truth_level"] == "reconciled_live_proof", (
            "Expected highest_truth_level=reconciled_live_proof, got: "
            + str(packet.get("highest_truth_level"))
        )
        assert packet["accepted_live_liveness"] is False
        assert packet["operator_truth"]["truth_level"] == "reconciled_live_proof"
        assert packet["operator_truth"]["label"] == "Reconciled live truth"
        assert packet["operator_truth"]["is_live_truth"] is False
        assert packet["operator_truth"]["degraded"] is True
        assert packet["runtime_record_evidence_basis"] == "missing"
        assert packet["runtime_controller_record_qualified"] is False
        # The claimed level remains inspectable, but it is not accepted.
        assert packet["operator_truth"]["source_type"] == "live_truth"
        assert data["controller_health"]["reported_status"] == "healthy"
        assert data["controller_health"]["status"] == "unobserved"
        assert data["controller_health"]["source"] == "registry_metadata"
        assert data["controller_health"]["current_record_accepted"] is False
        assert data["live_status"]["is_reconciled"] is False
        assert data["live_status"]["is_live"] is False


# ---------------------------------------------------------------------------
# Drill 2 — Runtime-to-Incident-to-Evolution-Proposal Flow
# ---------------------------------------------------------------------------

class TestDrill2RuntimeToIncidentToEvolutionProposal:
    """
    Drill 2: Runtime anomaly chain — heartbeat loss to evolution proposal.

    Chain: heartbeat_loss threshold breach (incidents service)
           → IncidentCase opened (status=open, telemetry_event_ids=[...])
           → incident resolved (status=resolved)
           → Postmortem draft created (postmortems service)
           → postmortem published (status=published)
           → EvolutionDecision proposed (evolution service)
           → proposal links incident_id, postmortem_id, is_active=True
    """

    def test_full_runtime_to_evolution_proposal_chain(self, monkeypatch, tmp_path):
        """
        Full cross-loop chain proving heartbeat loss propagates to an evolution proposal
        with a complete evidence chain: incident → postmortem → evolution decision.
        """
        # Shared IncidentStore for all three services in this drill
        shared_store = build_incident_store(tmp_path / "drill_incidents.json")

        # Wire evolution service to use our shared store
        monkeypatch.setattr(evo_main, "incident_store", shared_store)

        # Reset evolution decision store for this test
        evo_main.store._decisions.clear()
        evo_sto_path = getattr(evo_main.store, "_storage_path", None)
        if evo_sto_path and Path(evo_sto_path).exists():
            Path(evo_sto_path).unlink()

        # ---- Step 1: Threshold breach → IncidentCase opened ----
        consumer = ThresholdTelemetryIncidentConsumer(incident_store=shared_store)
        result = consumer.consume(_HEARTBEAT_LOSS_PAYLOAD)

        assert result.created is True, "Incident must be created on first consume"
        incident_id = result.incident.incident_id
        assert result.incident.status == "open"
        assert "tel-bff004-drill-hb-001" in result.incident.telemetry_event_ids, (
            "Incident must carry the telemetry_event_id from the threshold breach"
        )

        # ---- Step 2: Incident resolved ----
        shared_store.update_incident_status(incident_id, "resolved")
        resolved_inc = shared_store.get_incident(incident_id)
        assert resolved_inc is not None
        assert resolved_inc.status == "resolved"
        assert resolved_inc.resolved_at is not None

        # ---- Step 3: Postmortem draft from resolved incident ----
        pm_consumer = ResolvedIncidentPostmortemDraftConsumer(incident_store=shared_store)
        pm_result = pm_consumer.consume({"incident_id": incident_id})

        assert pm_result.created is True, "Postmortem draft must be created from resolved incident"
        pm_id = pm_result.postmortem.postmortem_id
        assert pm_result.postmortem.incident_id == incident_id
        assert pm_result.postmortem.status == "draft"

        # ---- Step 4: Postmortem published ----
        shared_store.update_postmortem_status(pm_id, "published")
        published_pm = shared_store.get_postmortem(pm_id)
        assert published_pm is not None
        assert published_pm.status == "published"
        assert published_pm.published_at is not None, "published_at must be set on publish"

        # ---- Step 5: Evolution proposal from published postmortem ----
        evo_client = TestClient(evo_main.app, raise_server_exceptions=False)
        response = evo_client.post(
            "/api/evolution/proposals/from-postmortem-published",
            json={"postmortem_id": pm_id},
        )

        assert response.status_code == 201, (
            f"Expected 201 for new proposal, got {response.status_code}: {response.text}"
        )
        proposal = response.json()

        # ---- Step 6: Verify evidence chain integrity ----
        assert proposal["decision_state"] == "proposed", (
            "Proposal must be in 'proposed' state — not auto-approved"
        )
        assert proposal["linked_postmortem_id"] == pm_id, (
            "Proposal must be linked to the published postmortem"
        )
        assert (
            proposal.get("linked_incident_id") == incident_id
            or (proposal.get("metadata") or {}).get("incident_id") == incident_id
        ), "Proposal must link back to the originating incident"
        assert proposal["is_active"] is True
        # No approval gate bypass — decision must still require review
        assert proposal["approval_decision_id"] is None, (
            "New proposals must not be auto-approved"
        )

    def test_chain_is_idempotent_on_duplicate_postmortem_publish(self, monkeypatch, tmp_path):
        """
        Drill 2b: Duplicate publish event for the same postmortem returns the existing
        proposal with HTTP 200 (idempotent). Only one proposal is created.
        """
        shared_store = build_incident_store(tmp_path / "drill_incidents_idem.json")
        monkeypatch.setattr(evo_main, "incident_store", shared_store)
        evo_main.store._decisions.clear()
        evo_sto_path = getattr(evo_main.store, "_storage_path", None)
        if evo_sto_path and Path(evo_sto_path).exists():
            Path(evo_sto_path).unlink()

        # Create incident with a different id so it doesn't collide with Drill 2a
        payload = {**_HEARTBEAT_LOSS_PAYLOAD, "incident_id": "inc-bff004-drill-idem-001"}
        consumer = ThresholdTelemetryIncidentConsumer(incident_store=shared_store)
        result = consumer.consume(payload)
        incident_id = result.incident.incident_id

        shared_store.update_incident_status(incident_id, "resolved")
        pm_consumer = ResolvedIncidentPostmortemDraftConsumer(incident_store=shared_store)
        pm_result = pm_consumer.consume({"incident_id": incident_id})
        pm_id = pm_result.postmortem.postmortem_id
        shared_store.update_postmortem_status(pm_id, "published")

        evo_client = TestClient(evo_main.app, raise_server_exceptions=False)

        r1 = evo_client.post(
            "/api/evolution/proposals/from-postmortem-published",
            json={"postmortem_id": pm_id},
        )
        assert r1.status_code == 201, r1.text
        decision_id_1 = r1.json()["decision_id"]

        r2 = evo_client.post(
            "/api/evolution/proposals/from-postmortem-published",
            json={"postmortem_id": pm_id},
        )
        assert r2.status_code == 200, (
            f"Second call must return 200 (idempotent), got {r2.status_code}: {r2.text}"
        )
        decision_id_2 = r2.json()["decision_id"]
        assert decision_id_1 == decision_id_2, (
            "Duplicate publish must return the same proposal, not create a second one"
        )

    def test_incident_without_resolution_blocks_postmortem_draft(self, tmp_path):
        """
        Drill 2c (guard): An open (unresolved) incident cannot produce a postmortem draft.
        The gate enforces that postmortems only follow from resolved incidents.
        """
        shared_store = build_incident_store(tmp_path / "drill_incidents_guard.json")

        consumer = ThresholdTelemetryIncidentConsumer(incident_store=shared_store)
        result = consumer.consume({
            **_HEARTBEAT_LOSS_PAYLOAD,
            "incident_id": "inc-bff004-drill-guard-001",
        })
        incident_id = result.incident.incident_id
        # Do NOT resolve — incident stays open

        pm_consumer = ResolvedIncidentPostmortemDraftConsumer(incident_store=shared_store)
        import pytest
        from services.postmortems.consumer import PostmortemDraftConsumerError  # type: ignore

        with pytest.raises((PostmortemDraftConsumerError, Exception)) as exc_info:
            pm_consumer.consume({"incident_id": incident_id})

        assert "resolved" in str(exc_info.value).lower() or "open" in str(exc_info.value).lower(), (
            "Gate must mention resolved/open status, got: " + str(exc_info.value)
        )
