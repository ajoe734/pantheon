from __future__ import annotations

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import SimpleNamespace

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


@pytest.fixture
def smoke(monkeypatch):
    """Run the deployed CLI entrypoint with isolated, real owner stores."""
    helper = _load_helper()
    workshops = FakeStore()
    proposals = helper.ProposalStore(backend="memory")
    datasets = helper.AgoraDatasetStore(backend="memory")
    for name in (helper.WORKSHOP_BACKEND_ENV, helper.GOVERNANCE_BACKEND_ENV,
                 helper.DATASET_BACKEND_ENV):
        monkeypatch.setenv(name, "postgres")
    monkeypatch.setattr(helper, "make_workshop_store", lambda: workshops)
    monkeypatch.setattr(helper, "ProposalStore", lambda: proposals)
    monkeypatch.setattr(helper, "AgoraDatasetStore", lambda: datasets)

    def run(action):
        return helper.main([action, "--workshop-id", "ws-race", "--tenant-id", "tenant",
                            "--user-id", "viewer"])

    assert run("seed") == 0
    return helper, datasets, run


def _natural_batch(store):
    from agora.evidence.worker import run_extraction_batch
    return run_extraction_batch(store, worker_id="natural-lifespan-batch")


def _assert_single_effects(store, *, attempts=1):
    scope = {"tenant_id": "tenant", "user_id": "viewer"}
    entry = store.get_inbox_entry("evidence-ws-race", **scope)
    record = store.get("evidence-ws-race", **scope)
    handoffs = store.list_handoffs(**scope)
    assert entry["status"] == "processed" and entry["attempt_count"] == attempts
    assert len(store.list_by_dataset("observe", **scope)) == 1
    assert len(handoffs) == 1
    assert handoffs[0]["evidence_ids"] == [record.evidence_id]
    assert handoffs[0]["dataset_version_id"] == record.dataset_version_id
    assert _natural_batch(store) == {"processed": 0, "failed": 0, "handoffs_created": 0}


def test_cli_accepts_natural_consumption_before_verify(smoke):
    _, store, run = smoke
    assert _natural_batch(store) == {"processed": 1, "failed": 0, "handoffs_created": 1}
    assert store.get_backlog("tenant", "viewer") == []
    assert run("verify") == 0
    _assert_single_effects(store)


def test_cli_accepts_already_acknowledged_durable_handoff(smoke):
    _, store, run = smoke
    assert _natural_batch(store)["processed"] == 1
    handoff = store.list_handoffs(tenant_id="tenant", user_id="viewer")[0]
    acknowledged, is_new = store.acknowledge_handoff(
        handoff["handoff_id"], tenant_id="tenant", user_id="viewer",
        acknowledgement_id="ack-local", dataset_version_id=handoff["dataset_version_id"],
        downstream_ref="observe://local-fixture", acknowledged_by="local-consumer",
        request_digest="local-ack-digest", acknowledged_at="2026-07-14T00:03:00Z",
    )
    assert is_new and acknowledged["ack_status"] == "acknowledged"
    assert run("verify") == 0
    _assert_single_effects(store)


def test_cli_accepts_consumer_winning_pending_read_to_claim(smoke, monkeypatch):
    _, store, run = smoke
    claim = store.claim_inbox
    competed = []

    def compete(**kwargs):
        if kwargs["worker_id"] == "restart-persistence-smoke" and not competed:
            competed.append(_natural_batch(store))
        return claim(**kwargs)

    monkeypatch.setattr(store, "claim_inbox", compete)
    assert run("verify") == 0
    assert competed == [{"processed": 1, "failed": 0, "handoffs_created": 1}]
    _assert_single_effects(store)


def _controlled_clock(helper, monkeypatch, *, on_wait=lambda: None):
    elapsed = [0.0]
    waits = []

    def wait(seconds):
        assert 0 < seconds <= helper.EVIDENCE_POLL_SECONDS
        waits.append(seconds)
        on_wait()
        elapsed[0] += seconds

    monkeypatch.setattr(helper, "time", SimpleNamespace(
        monotonic=lambda: elapsed[0], sleep=wait,
    ))
    return waits


@pytest.mark.parametrize("start", ["before_verify", "before_claim"])
def test_cli_waits_for_real_competing_processing_lease(smoke, monkeypatch, start):
    helper, store, run = smoke
    claimed, release = Event(), Event()
    complete = store._complete_claim
    claim = store.claim_inbox
    competing = []

    def hold(entry, **kwargs):
        claimed.set()
        assert release.wait(timeout=5), "verifier never observed competing processing lease"
        return complete(entry, **kwargs)

    monkeypatch.setattr(store, "_complete_claim", hold)
    with ThreadPoolExecutor(max_workers=1) as executor:
        def start_consumer():
            competing.append(executor.submit(_natural_batch, store))
            assert claimed.wait(timeout=5)

        def compete(**kwargs):
            if kwargs["worker_id"] == "restart-persistence-smoke" and not competing:
                start_consumer()
            return claim(**kwargs)

        if start == "before_verify":
            start_consumer()
        else:
            monkeypatch.setattr(store, "claim_inbox", compete)

        def finish_consumer():
            release.set()
            assert competing[0].result(timeout=5) == {
                "processed": 1, "failed": 0, "handoffs_created": 1,
            }

        waits = _controlled_clock(helper, monkeypatch, on_wait=finish_consumer)
        try:
            assert run("verify") == 0
        finally:
            release.set()
            competing[0].result(timeout=5)
    assert waits == [helper.EVIDENCE_POLL_SECONDS]
    _assert_single_effects(store)


@pytest.mark.parametrize("state", ["pending", "expired_processing"])
def test_cli_recovers_legacy_claimable_seed_only(smoke, state):
    helper, store, run = smoke
    # Another same-scope item must not be consumed by this targeted verifier.
    other = helper.evidence_for("unrelated")
    store.add_to_inbox(other, "tenant", "viewer", "2026-07-14T00:01:00Z",
                       idempotency_key="unrelated", request_digest=helper.evidence_request_digest(other))
    if state == "expired_processing":
        assert len(store.claim_inbox(
            tenant_id="tenant", user_id="viewer", evidence_id="evidence-ws-race",
            worker_id="pre-restart", now=datetime.now(timezone.utc) - timedelta(minutes=1),
        )) == 1
    assert run("verify") == 0
    assert store.get_inbox_entry("evidence-unrelated", tenant_id="tenant", user_id="viewer")["status"] == "pending"
    # Remove only the isolated unrelated fixture before asserting one effect.
    del store._inbox[("tenant", "viewer", "evidence-unrelated")]
    _assert_single_effects(store, attempts=2 if state == "expired_processing" else 1)


@pytest.mark.parametrize("state", ["pending", "processing"])
def test_cli_fails_closed_at_recovery_deadline(smoke, monkeypatch, state):
    helper, store, run = smoke
    if state == "processing":
        assert len(store.claim_inbox(tenant_id="tenant", user_id="viewer",
                                   worker_id="still-alive")) == 1
    else:
        # A zero recovery budget observes actual pending state without claiming.
        monkeypatch.setattr(helper, "EVIDENCE_RECOVERY_SECONDS", 0)
    waits = _controlled_clock(helper, monkeypatch)
    with pytest.raises(RuntimeError, match=f"recovery deadline: status='{state}'"):
        run("verify")
    assert sum(waits) == helper.EVIDENCE_RECOVERY_SECONDS
    assert store.get("evidence-ws-race", tenant_id="tenant", user_id="viewer") is None
    assert store.list_handoffs(tenant_id="tenant", user_id="viewer") == []


@pytest.mark.parametrize("natural_first", [False, True])
def test_cli_rejects_real_failed_extraction(smoke, natural_first):
    _, store, run = smoke
    key = ("tenant", "viewer", "evidence-ws-race")
    store._inbox[key]["consent_granted"] = False
    if natural_first:
        assert _natural_batch(store) == {"processed": 0, "failed": 1, "handoffs_created": 0}
    with pytest.raises(RuntimeError, match="extraction status='failed'"):
        run("verify")
    assert store._inbox[key]["status"] == "failed"
    assert store.get("evidence-ws-race", tenant_id="tenant", user_id="viewer") is None
    assert store.list_handoffs(tenant_id="tenant", user_id="viewer") == []


@pytest.mark.parametrize("damage", [
    "inbox_missing", "inbox_tenant", "inbox_user", "inbox_receipt", "inbox_digest",
    "record_missing", "record_tenant", "record_user", "record_evidence", "record_receipt", "record_status",
    "record_content", "record_captured", "record_consent",
    "handoff_missing", "handoff_tenant", "handoff_user", "handoff_evidence", "handoff_version",
    "handoff_duplicate", "handoff_kind", "handoff_authority",
])
def test_cli_rejects_missing_or_mismatched_durable_proof(smoke, damage):
    _, store, run = smoke
    assert _natural_batch(store)["processed"] == 1
    key = ("tenant", "viewer", "evidence-ws-race")
    handoff = next(iter(store._handoffs.values()))
    if damage == "inbox_missing":
        del store._inbox[key]
    elif damage.startswith("inbox_"):
        field = {"tenant": "tenant_id", "user": "user_id", "receipt": "admission_receipt_id",
                 "digest": "request_digest"}[damage.removeprefix("inbox_")]
        store._inbox[key][field] = "wrong"
    elif damage == "record_missing":
        del store._records[key]
    elif damage.startswith("record_"):
        field = {"tenant": "tenant_id", "user": "user_id", "evidence": "evidence_id",
                 "receipt": "admission_receipt_id", "status": "status", "content": "content",
                 "captured": "captured_at", "consent": "consent_verified"}[damage.removeprefix("record_")]
        wrong = {"text": "wrong"} if field == "content" else False if field == "consent_verified" else "wrong"
        store._records[key] = store._records[key].model_copy(update={field: wrong})
    elif damage == "handoff_missing":
        store._handoffs.clear()
    elif damage == "handoff_duplicate":
        store._handoffs[("tenant", "viewer", "duplicate")] = {**handoff, "handoff_id": "duplicate"}
    else:
        field = {"tenant": "tenant_id", "user": "user_id", "evidence": "evidence_ids",
                 "version": "dataset_version_id", "kind": "dataset_kind",
                 "authority": "authority_limit"}[damage.removeprefix("handoff_")]
        handoff[field] = ["wrong"] if field == "evidence_ids" else "wrong"
    with pytest.raises(RuntimeError, match="inbox.*(not found|mismatch)|dataset record.*(not found|mismatch)|durable handoff mismatch"):
        run("verify")


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


def test_agora_package_resolves_to_deployed_bff_modules(tmp_path) -> None:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    code = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('agora_persistence_smoke', {str(HELPER_PATH)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "import agora\n"
        "print(agora.__file__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    expected_agora_init = str((ROOT / "services" / "control-plane" / "bff" / "agora" / "__init__.py").resolve())
    assert result.stdout.strip() == expected_agora_init


def test_branch_ci_gate_executes_persistence_smoke_test() -> None:
    branch_ci = yaml.safe_load((ROOT / ".github" / "workflows" / "branch-ci.yml").read_text(encoding="utf-8"))
    steps = branch_ci["jobs"]["smoke"]["steps"]
    delivery_step = next(
        step for step in steps if step.get("name") == "Run delivery diagnostics and exit-status regressions"
    )
    assert "scripts/test_agora_workshop_restart_persistence_smoke.py" in delivery_step["run"]


def test_smoke_helper_and_governance_store_import_without_fastapi(tmp_path) -> None:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    code = (
        "import sys\n"
        "sys.modules['fastapi'] = None\n"
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('agora_persistence_smoke', {str(HELPER_PATH)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "from agora.governance.store import ProposalStore\n"
        "assert ProposalStore is not None\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_branch_ci_gate_installs_fastapi_for_smoke_job() -> None:
    branch_ci = yaml.safe_load((ROOT / ".github" / "workflows" / "branch-ci.yml").read_text(encoding="utf-8"))
    steps = branch_ci["jobs"]["smoke"]["steps"]
    install_step = next(
        step for step in steps if step.get("name") == "Install test dependencies"
    )
    assert "-r requirements.txt" in install_step["run"]
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert any(line.strip().startswith("fastapi") for line in requirements)
