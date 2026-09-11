from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = ROOT / "scripts" / "agora_workshop_restart_persistence_smoke.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "nonprod-deploy.yml"


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


def test_helper_reports_backends_resolved_by_the_store_objects() -> None:
    """The environment stating postgres is not proof the stores resolved to it.
    The report has to come from the constructed objects so a store that fell
    back to memory is caught even while the env still claims postgres."""
    helper = _load_helper()
    memory_workshop = FakeStore()
    proposals = helper.ProposalStore(backend="memory")
    dataset_store = helper.AgoraDatasetStore(backend="memory")

    resolved = helper.resolved_store_backends(memory_workshop, proposals, dataset_store)
    assert resolved == {
        "workshop": "memory",
        "governance": "memory",
        "dataset": "memory",
    }

    with pytest.raises(RuntimeError, match="dataset=memory, governance=memory, workshop=memory"):
        helper.assert_postgres_store_backends(resolved)

    helper.assert_postgres_store_backends(
        {"workshop": "postgres", "governance": "postgres", "dataset": "postgres"}
    )


def test_assert_backends_action_needs_no_record_identifiers() -> None:
    """A backend check has no workshop of its own, and seed/verify still must
    not run without the identifiers that name the record they act on."""
    helper = _load_helper()

    args = helper.parse_args(["assert-backends"])
    assert args.action == "assert-backends"

    with pytest.raises(SystemExit):
        helper.parse_args(["seed"])
    with pytest.raises(SystemExit):
        helper.parse_args(["verify", "--workshop-id", "w", "--tenant-id", "t"])


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


SMOKE_STEP_NAME = "- name: Dev Agora restart persistence smoke under lease"


def _smoke_step() -> str:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    start = workflow.index(SMOKE_STEP_NAME)
    end = workflow.find("\n      - name:", start + 1)
    return workflow[start:] if end == -1 else workflow[start:end]


def test_workflow_uses_internal_fresh_process_persistence_proof() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    step = _smoke_step()

    assert "agora-deploy-smoke:operator" not in workflow
    assert "Authorization:" not in step
    assert "Bearer " not in step
    seed = "agora_workshop_restart_persistence_smoke.py seed"
    restart = "restart operator-bff"
    verify = "agora_workshop_restart_persistence_smoke.py verify"
    assert seed in step
    assert verify in step
    assert step.index(seed) < step.index(restart) < step.index(verify)
    assert step.count("docker compose -p pantheon -f docker-compose.yml exec -T operator-bff") == 3
    assert "proposal-${workshop_id}" in step
    assert "exactly-once replay and pending outbox recovery" in step


def test_workflow_proves_store_backends_from_the_stores_not_container_logs() -> None:
    """The backend precondition must be read off the store objects the service
    builds. The previous probes grepped ``docker logs`` for initialization
    messages, which the uvicorn-hosted BFF never emitted, so the check could
    only ever fail regardless of how the stores had actually resolved."""
    step = _smoke_step()

    assert "docker logs pantheon-operator-bff-1" not in step
    assert "store initialized backend=postgres" not in step
    assert "docker inspect pantheon-operator-bff-1" not in step

    assert_backends = "agora_workshop_restart_persistence_smoke.py assert-backends"
    assert assert_backends in step
    assert step.index(assert_backends) < step.index(
        "agora_workshop_restart_persistence_smoke.py seed"
    )
