from __future__ import annotations

import os
import sys
import tempfile
import time
import urllib.request
import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

# Bootstrap repo paths
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Setup isolated env paths for testing
ENV_PATCHES = {
    "EVOLUTION_DATA_DIR": tempfile.mkdtemp(),
    "RESEARCH_ORCHESTRATOR_DATA_DIR": tempfile.mkdtemp(),
    "TRAINING_SESSION_DATA_DIR": tempfile.mkdtemp(),
    "RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS": "8",
    "RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS": "false",
    "PANTHEON_OFFLINE_GATE_ENABLED": "false",
}


@pytest.fixture(autouse=True)
def clean_env_and_modules():
    with mock.patch.dict(os.environ, ENV_PATCHES):
        # Reset registry store singleton
        from services.registry.storage import reset_store
        reset_store()
        
        yield


def import_service_app(package_name: str, sys_path_dirs: list[Path]):
    for d in reversed(sys_path_dirs):
        sys.path.insert(0, str(d))
    try:
        # Clear modules to prevent collision
        for mod in ("models", "store", "policy_lineage", "incident", "sweep", "postmortem_bridge"):
            sys.modules.pop(mod, None)
            
        import importlib
        module = importlib.import_module(package_name)
        return module.app
    finally:
        for d in sys_path_dirs:
            try:
                sys.path.remove(str(d))
            except ValueError:
                pass


def test_evoloop_004_generative_loop_e2e():
    # Load registry
    registry_app = import_service_app(
        "services.registry.service",
        [REPO_ROOT, REPO_ROOT / "services" / "registry"]
    )
    registry_models = sys.modules.get("models")
    registry_store = sys.modules.get("store")

    # Load research
    research_app = import_service_app(
        "services.research.main",
        [REPO_ROOT, REPO_ROOT / "services" / "research"]
    )
    research_models = sys.modules.get("models")
    research_store_module = sys.modules.get("store")

    # Load evolution
    evolution_app = import_service_app(
        "services.evolution.main",
        [REPO_ROOT, REPO_ROOT / "services" / "evolution"]
    )
    evolution_models = sys.modules.get("models")
    evolution_store_module = sys.modules.get("store")

    # Load hyphenated training-session in isolation
    sys_path_dir = REPO_ROOT / "services" / "training-session"
    sys.path.insert(0, str(sys_path_dir))
    try:
        # Clear current generic modules to prevent collisions
        cached_models = sys.modules.pop("models", None)
        cached_store = sys.modules.pop("store", None)
        cached_policy_lineage = sys.modules.pop("policy_lineage", None)

        import importlib.util
        spec = importlib.util.spec_from_file_location("training_session_main", sys_path_dir / "main.py")
        assert spec and spec.loader
        training_module = importlib.util.module_from_spec(spec)
        sys.modules["training_session_main"] = training_module
        spec.loader.exec_module(training_module)
        training_app = training_module.app

        # Save training session's specific modules
        training_models = sys.modules.pop("models", None)
        training_store = sys.modules.pop("store", None)
        training_policy_lineage = sys.modules.pop("policy_lineage", None)

        # Restore cached generic modules
        if cached_models:
            sys.modules["models"] = cached_models
        if cached_store:
            sys.modules["store"] = cached_store
        if cached_policy_lineage:
            sys.modules["policy_lineage"] = cached_policy_lineage
    finally:
        try:
            sys.path.remove(str(sys_path_dir))
        except ValueError:
            pass

    # Initialize TestClients
    clients = {
        "evolution": TestClient(evolution_app),
        "research": TestClient(research_app),
        "training": TestClient(training_app),
        "registry": TestClient(registry_app),
    }

    # Custom urlopen router to map local service URLs to TestClient in-process
    def mock_urlopen(req, data=None, timeout=None):
        if isinstance(req, str):
            url = req
            method = "GET"
            headers = {}
        else:
            url = req.full_url
            method = req.get_method()
            headers = req.headers
            data = req.data

        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.netloc
        path = parsed.path
        if parsed.query:
            path += f"?{parsed.query}"

        # Clean/set sys.modules for target app to resolve local imports correctly
        target_client = None
        if "research-orchestrator" in host or "8101" in host:
            target_client = clients["research"]
            if research_models:
                sys.modules["models"] = research_models
            if research_store_module:
                sys.modules["store"] = research_store_module
        elif "training-session" in host or "8099" in host:
            target_client = clients["training"]
            # Swap in training-session specific modules
            if training_models:
                sys.modules["models"] = training_models
            if training_store:
                sys.modules["store"] = training_store
            if training_policy_lineage:
                sys.modules["policy_lineage"] = training_policy_lineage
        elif "registry" in host or "8087" in host:
            target_client = clients["registry"]
            if registry_models:
                sys.modules["models"] = registry_models
            if registry_store:
                sys.modules["store"] = registry_store
        elif "evolution" in host or "8093" in host:
            target_client = clients["evolution"]
            if evolution_models:
                sys.modules["models"] = evolution_models
            if evolution_store_module:
                sys.modules["store"] = evolution_store_module

        if not target_client:
            raise urllib.error.URLError(f"Unknown mock host: {host}")

        body = data.decode("utf-8") if data else None
        headers_dict = {str(k): str(v) for k, v in headers.items()}

        response = target_client.request(
            method=method,
            url=path,
            data=body,
            headers=headers_dict
        )

        class MockResponse:
            def __init__(self, res):
                self.res = res
                self.status = res.status_code
                self.reason = getattr(res, "reason_phrase", getattr(res, "reason", "OK"))
            def read(self):
                return self.res.content
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

        return MockResponse(response)

    # Patch urllib urlopen with our mock router
    with mock.patch("urllib.request.urlopen", side_effect=mock_urlopen):
        # 1. Verify parent artifact (v1) exists in registry (should be bootstrapped on startup)
        registry_client = clients["registry"]
        
        # Swap registry models/store in sys.modules to query registry store correctly
        if registry_models:
            sys.modules["models"] = registry_models
        if registry_store:
            sys.modules["store"] = registry_store
            
        v1_resp = registry_client.get("/api/registry/strategy-artifacts/artifact-tw-session-momentum-v1")
        assert v1_resp.status_code == 200, f"Bootstrap check failed: {v1_resp.text}"
        v1_data = v1_resp.json()["entry"]["metadata"]["strategy_artifact"]
        assert v1_data["version"] == "1.0.0"
        parent_lookback = v1_data["parameters"]["lookback_bars"]
        parent_threshold = v1_data["parameters"]["momentum_threshold"]

        # 2. Propose a retrain decision in the evolution service
        evo_client = clients["evolution"]
        decision_id = "decision-evoloop-004"
        
        proposal = {
            "decision_id": decision_id,
            "target_type": "candidate_artifact",
            "target_id": "artifact-tw-session-momentum-v1",
            "target_version": "1.0.0",
            "action_type": "retrain",
            "rationale": "Breach mitigation - trigger retrain to adapt parameters",
            "created_by_id": "test-controller",
            "created_by_role": "evolution_controller",
            "risk_level": "low",
            "target_stage": "paper",
            "threshold_snapshots": [
                {
                    "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.1",
                    "signal_type": "performance_degradation",
                    "metric_name": "sharpe_ratio",
                    "comparator": "lt",
                    "observed_value": 0.3,
                    "threshold_value": 0.5,
                    "window": "30d",
                    "breached": True,
                }
            ],
        }
        
        if evolution_models:
            sys.modules["models"] = evolution_models
        if evolution_store_module:
            sys.modules["store"] = evolution_store_module
            
        res = evo_client.post("/api/evolution/proposals", json=proposal)
        assert res.status_code == 201, res.text

        # 3. Review and approve the proposal
        res = evo_client.post(
            f"/api/evolution/proposals/{decision_id}/review",
            json={
                "actor_role": "reviewer_on_duty",
                "actor_id": "reviewer-01",
                "approval_decision_id": f"appr-{decision_id}",
            },
        )
        assert res.status_code == 200, res.text

        res = evo_client.post(
            f"/api/evolution/proposals/{decision_id}/approve",
            json={
                "actor_role": "automated_gate",
                "actor_id": "gate-01",
                "approval_decision_id": f"appr-{decision_id}",
            },
        )
        assert res.status_code == 200, res.text

        # 4. Execute the decision - this will trigger the retrain process
        execute_payload = {
            "actor_role": "evolution_controller",
            "actor_id": "gate-01",
        }
        res = evo_client.post(f"/api/evolution/proposals/{decision_id}/execute", json=execute_payload)
        assert res.status_code == 200, res.text

        # 5. Wait for the background retrain executor thread to finish
        # Since it runs in the background, we poll the research orchestrator runs store.
        research_client = clients["research"]
        
        # Helper to retrieve runs directly from the memory store of research app
        if research_models:
            sys.modules["models"] = research_models
        if research_store_module:
            sys.modules["store"] = research_store_module
            
        from services.research.main import get_store as get_research_store
        rstore = get_research_store()
        
        # Wait up to 10 seconds for completion
        completed_run = None
        start_time = time.time()
        while time.time() - start_time < 10.0:
            # Re-ensure modules for research storage read inside thread/polling loop
            if research_models:
                sys.modules["models"] = research_models
            if research_store_module:
                sys.modules["store"] = research_store_module
            runs = rstore.list_runs()
            if runs:
                run = runs[0]
                if run.get("status") in ("completed", "failed"):
                    completed_run = run
                    break
            time.sleep(0.1)

        assert completed_run is not None, "Research run did not start or complete in time."
        assert completed_run.get("status") == "completed", f"Research run failed: {completed_run.get('rejection')}"

        # 6. Verify work item (task) trace
        task_id = completed_run["task_id"]
        task = rstore.get_task(task_id)
        assert task is not None
        assert task["source_refs"][0]["id"] == decision_id, "Work item is not traceable to decision_id"

        # 7. Verify training session creation and completion
        # Set sys.modules for training store readback
        if training_models:
            sys.modules["models"] = training_models
        if training_store:
            sys.modules["store"] = training_store
        tstore = training_module.get_store()
        sessions = tstore.list_sessions()
        assert len(sessions) > 0, "No training sessions created"
        session = sessions[0]
        assert session["status"] == "completed"
        session_id = session["session_id"]

        # 8. Verify the mutated artifact v2 is registered in the registry
        v2_artifact_id = "artifact-tw-session-momentum-v2"
        
        if registry_models:
            sys.modules["models"] = registry_models
        if registry_store:
            sys.modules["store"] = registry_store
            
        v2_resp = registry_client.get(f"/api/registry/strategy-artifacts/{v2_artifact_id}")
        assert v2_resp.status_code == 200, f"Mutated artifact not found in registry: {v2_resp.text}"
        
        v2_entry = v2_resp.json()["entry"]
        v2_artifact = v2_entry["metadata"]["strategy_artifact"]
        
        # 9. Verify parameters have a real difference from parent v1
        child_lookback = v2_artifact["parameters"]["lookback_bars"]
        child_threshold = v2_artifact["parameters"]["momentum_threshold"]
        
        assert (child_lookback != parent_lookback) or (child_threshold != parent_threshold), (
            f"No parameter changes: parent=({parent_lookback}, {parent_threshold}), "
            f"child=({child_lookback}, {child_threshold})"
        )

        # 10. Verify lineage contains {v1, decision_id, work_item_id, session_id}
        lineage = v2_artifact["lineage"]
        assert lineage["parent_registry_ids"] == ["artifact-tw-session-momentum-v1"]
        assert decision_id in lineage["source_run_ids"]
        assert task_id in lineage["source_run_ids"] # work_item_id
        assert session_id in lineage["source_run_ids"] # session_id
        
        print("E2E Generative Loop integration test successfully passed!")
