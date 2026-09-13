from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "scripts" / "agora_workshop_restart_persistence_smoke.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"


def test_standalone_cli_imports_without_repository_pythonpath(tmp_path) -> None:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, str(HELPER_PATH), "--help"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "seed" in result.stdout and "verify" in result.stdout


def _load_helper():
    spec = importlib.util.spec_from_file_location("agora_persistence_smoke", HELPER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeStore:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, object]] = {}

    def create_session(self, session: dict[str, object]) -> dict[str, object]:
        row = {**session, "lock_version": 1}
        self.sessions[str(row["workshop_id"])] = row
        return row

    def get_session(self, workshop_id: str):
        return self.sessions.get(workshop_id)


def test_helper_seeds_and_verifies_workshop_proposal_and_outbox() -> None:
    helper = _load_helper()
    store = FakeStore()
    proposals = helper.ProposalStore(backend="memory")
    dataset_store = helper.AgoraDatasetStore(backend="memory")

    helper.seed(
        store,
        proposals,
        workshop_id="ws-run-1",
        tenant_id="tenant",
        user_id="viewer",
        dataset_store=dataset_store,
    )

    scope = helper.command_scope(tenant_id="tenant", user_id="viewer")
    completed_key = f"{scope}:ws-run-1"
    recovery_key = f"{scope}:{helper.recovery_command_key('ws-run-1')}"
    assert proposals._commands[completed_key]["side_effect_state"] == "completed"
    assert proposals._commands[recovery_key]["side_effect_state"] == "pending"

    helper.verify(
        store,
        proposals,
        workshop_id="ws-run-1",
        tenant_id="tenant",
        user_id="viewer",
        dataset_store=dataset_store,
    )

    assert store.sessions["ws-run-1"] == {
        "workshop_id": "ws-run-1",
        "tenant_id": "tenant",
        "user_id": "viewer",
        "status": "open",
        "lock_version": 1,
    }
    history = proposals.history("proposal-ws-run-1", "tenant", "viewer")
    assert [row["revision"] for row in history] == [1, 2, 3]
    assert [event["action"] for event in history[-1]["audit"]] == [
        "create",
        "modify",
        "validate",
    ]
    assert proposals._commands[completed_key]["side_effect_state"] == "completed"
    assert proposals._commands[recovery_key]["side_effect_state"] == "completed"


def test_helper_fails_closed_without_postgres_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _load_helper()
    monkeypatch.setenv(helper.WORKSHOP_BACKEND_ENV, "off")
    monkeypatch.setenv(helper.GOVERNANCE_BACKEND_ENV, "postgres")
    monkeypatch.setenv(helper.DATASET_BACKEND_ENV, "postgres")

    with pytest.raises(RuntimeError, match="requires AGORA_WORKSHOP_STORE_BACKEND=postgres"):
        helper.require_postgres_backends()

    monkeypatch.setenv(helper.WORKSHOP_BACKEND_ENV, "postgres")
    monkeypatch.setenv(helper.GOVERNANCE_BACKEND_ENV, "off")
    monkeypatch.setenv(helper.DATASET_BACKEND_ENV, "postgres")
    with pytest.raises(RuntimeError, match="requires AGORA_GOVERNANCE_STORE_BACKEND=postgres"):
        helper.require_postgres_backends()

    monkeypatch.setenv(helper.WORKSHOP_BACKEND_ENV, "postgres")
    monkeypatch.setenv(helper.GOVERNANCE_BACKEND_ENV, "postgres")
    monkeypatch.setenv(helper.DATASET_BACKEND_ENV, "off")
    with pytest.raises(RuntimeError, match="requires AGORA_DATASET_STORE_BACKEND=postgres"):
        helper.require_postgres_backends()


def test_helper_rejects_missing_or_wrong_persisted_record() -> None:
    helper = _load_helper()
    store = FakeStore()
    proposals = helper.ProposalStore(backend="memory")

    with pytest.raises(RuntimeError, match="was not found"):
        helper.verify(
            store,
            proposals,
            workshop_id="missing",
            tenant_id="tenant",
            user_id="viewer",
        )

    store.sessions["wrong"] = {
        "workshop_id": "wrong",
        "tenant_id": "other",
        "user_id": "viewer",
        "status": "open",
    }
    with pytest.raises(RuntimeError, match="mismatched fields tenant_id"):
        helper.verify(
            store,
            proposals,
            workshop_id="wrong",
            tenant_id="tenant",
            user_id="viewer",
        )


def _dev_persistence_step() -> str:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return next(step["run"] for step in workflow["jobs"]["deploy-dev"]["steps"]
                if step.get("id") == "agora")


def test_workflow_uses_internal_fresh_process_persistence_proof() -> None:
    step = _dev_persistence_step()

    assert "agora-deploy-smoke:operator" not in step
    assert "Authorization:" not in step
    assert "Bearer " not in step
    seed = "agora_workshop_restart_persistence_smoke.py seed"
    restart = "restart operator-bff"
    verify = "agora_workshop_restart_persistence_smoke.py verify"
    assert seed in step
    assert verify in step
    readiness = "wait_for_bff_lifecycle_readiness.py"
    assert step.index(seed) < step.index(restart) < step.index(readiness) < step.index(verify)
    assert step.count("docker compose -p pantheon -f docker-compose.yml exec -T operator-bff") == 2
    assert "--expected-deployment-sha" in step
    assert "run_with_dev_environment_lease.sh" in step
    assert "--tenant-id ${tenant_id_q} --user-id ${user_id_q}" in step
    assert "|| true" not in step
    assert "proposal-${workshop_id}" in step
    assert "exactly-once replay and pending outbox recovery" in step


def test_workflow_does_not_require_optional_startup_log_messages() -> None:
    step = _dev_persistence_step()
    # main() requires all three Postgres backends itself before either operation.
    # Its real write/restart/readback must not depend on INFO logger settings.
    assert "docker inspect" not in step
    assert "docker logs" not in step
    assert "initialized backend=postgres" not in step
