from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import asyncpg
import jsonschema
import pytest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = importlib.import_module("scripts.run_loop_product_recovery_matrix")
CORE = importlib.import_module("services.loop-control.recovery_harness")
DB_DSN = os.environ.get("RECOVERY_TEST_DATABASE_URL", "")


def arun(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture()
def harness():
    if not DB_DSN:
        pytest.skip("RECOVERY_TEST_DATABASE_URL must name an explicit test database")

    async def available() -> bool:
        try:
            conn = await asyncpg.connect(DB_DSN, timeout=2)
        except Exception:
            return False
        await conn.close()
        return True

    if not arun(available()):
        pytest.skip("local PostgreSQL is unavailable")
    run_id = f"unit-{uuid.uuid4().hex}"
    instance = CORE.PostgresRecoveryHarness(
        DB_DSN,
        run_id=run_id,
        tenant_id=f"tenant-{run_id}",
        environment=f"loop-recovery-{run_id[-12:]}",
        deployment_sha="unit-test-sha",
        isolation_token=uuid.uuid4().hex,
        controller_interval_seconds=1.5,
        max_attempts=2,
    )
    arun(instance.initialize())
    return instance


def test_nonprod_guard_rejects_prod_live_and_unisolated_before_database_use() -> None:
    with pytest.raises(CORE.RecoveryHarnessError, match="non-dev"):
        CORE.require_nonprod_boundary(
            "production", live_broker_enabled=False, isolated_database=True
        )
    with pytest.raises(CORE.RecoveryHarnessError, match="live broker"):
        CORE.require_nonprod_boundary(
            "dev", live_broker_enabled=True, isolated_database=True
        )
    with pytest.raises(CORE.RecoveryHarnessError, match="isolated database"):
        CORE.require_nonprod_boundary(
            "dev", live_broker_enabled=False, isolated_database=False
        )


def test_declared_admission_fault_must_be_observed(harness) -> None:
    command_id = "admission-fault"
    with pytest.raises(CORE.InjectedFault) as caught:
        arun(
            harness.admit(
                command_id,
                "value",
                fault_point="before_outbox_persist",
            )
        )
    assert caught.value.point == "before_outbox_persist"
    assert arun(
        harness.fault_observation_count(command_id, "before_outbox_persist")
    ) == 1
    snapshot = arun(harness.snapshot(command_id))
    assert snapshot["commands"] == []
    assert snapshot["outbox"] == []
    assert snapshot["effects"] == []


def test_worker_rejects_wrong_database_isolation_attestation(harness) -> None:
    impostor = CORE.PostgresRecoveryHarness(
        DB_DSN,
        run_id=harness.run_id,
        tenant_id=harness.tenant_id,
        environment=harness.environment,
        deployment_sha=harness.deployment_sha,
        isolation_token="wrong-isolation-token",
        controller_interval_seconds=harness.controller_interval_seconds,
    )
    with pytest.raises(CORE.RecoveryHarnessError, match="attestation"):
        arun(impostor.verify_isolation_guard())


def test_suppressed_expected_fault_fails_closed(harness, monkeypatch) -> None:
    async def suppress(*_args, **_kwargs):
        return None

    monkeypatch.setattr(harness, "_inject", suppress)
    config = RUNNER.MatrixConfig(
        run_id=harness.run_id,
        environment=harness.environment,
        tenant_id=harness.tenant_id,
        deployment_sha=harness.deployment_sha,
        isolation_token=harness.isolation_token,
        controller_interval_seconds=harness.controller_interval_seconds,
    )
    monkeypatch.setattr(RUNNER, "harness_for", lambda *_args, **_kwargs: harness)
    with pytest.raises(RUNNER.MatrixFailure, match="was not observed"):
        RUNNER.run_fault_scenario(
            DB_DSN,
            config,
            scenario_id="MUTATION_SUPPRESSED_FAULT",
            fault_point="before_outbox_persist",
        )


@pytest.mark.parametrize(
    "fault_point",
    [
        "before_downstream_mutation",
        "after_downstream_mutation",
        "after_mutation_before_receipt",
        "downstream_timeout_after_commit",
        "before_projection",
        "after_projection_before_publish",
    ],
)
def test_worker_faults_recover_once_with_correlated_terminal_state(
    harness, fault_point: str
) -> None:
    command_id = f"cmd-{fault_point}"
    arun(harness.admit(command_id, f"value-{fault_point}"))
    outcome = arun(
        harness.process_one(
            f"fault-worker-{fault_point}",
            fault_point=fault_point,
            timeout_seconds=0.02,
        )
    )
    expected = "timeout" if fault_point == "downstream_timeout_after_commit" else "injected_fault"
    assert outcome.status == expected
    assert outcome.fault_point == fault_point
    assert arun(harness.fault_observation_count(command_id, fault_point)) == 1

    failure = arun(harness.snapshot(command_id))
    if fault_point == "after_downstream_mutation":
        assert failure["effects"] == []
    if fault_point == "after_mutation_before_receipt":
        assert len(failure["effects"]) == 1
        assert failure["receipts"] == []

    recovery_started_at = time.monotonic()
    if expected == "injected_fault":
        time.sleep(harness.controller_interval_seconds + 0.04)
    recovered = arun(harness.process_one(f"recovery-worker-{fault_point}"))
    assert recovered.status == "completed"
    proof = arun(
        harness.assert_terminal_invariants(
            command_id,
            max_recovery_ticks=2,
            recovery_ticks=2,
            recovery_elapsed_seconds=time.monotonic() - recovery_started_at,
        )
    )
    assert all(proof["checks"].values())
    assert proof["counts"]["effects"] == 1


def test_duplicate_conflict_and_concurrent_claim_are_fail_closed(harness) -> None:
    async def admit_both():
        return await asyncio.gather(
            harness.admit(
                "duplicate-command", "same", idempotency_key="duplicate-key"
            ),
            harness.admit(
                "duplicate-command", "same", idempotency_key="duplicate-key"
            ),
        )

    admissions = arun(admit_both())
    assert sorted(item["replayed"] for item in admissions) == [False, True]
    alias = arun(
        harness.admit(
            "duplicate-alias", "same", idempotency_key="duplicate-key"
        )
    )
    assert alias["command_id"] == "duplicate-command"
    assert alias["event_id"] == "event-duplicate-command"
    with pytest.raises(CORE.IdempotencyConflict):
        arun(
            harness.admit(
                "duplicate-command",
                "different",
                idempotency_key="duplicate-key",
            )
        )

    async def claim_both():
        return await asyncio.gather(
            harness.claim_one("worker-a"),
            harness.claim_one("worker-b"),
        )

    claims = arun(claim_both())
    assert sum(claim is not None for claim in claims) == 1


def test_lease_expiry_takeover_fences_stale_owner(harness) -> None:
    recovery_started_at = time.monotonic()
    arun(harness.admit("lease-command", "lease-value"))
    old_claim = arun(harness.claim_one("old-owner"))
    assert old_claim is not None
    assert arun(harness.claim_one("early-contender")) is None

    time.sleep(harness.controller_interval_seconds + 0.04)
    new_claim = arun(harness.claim_one("new-owner"))
    assert new_claim is not None
    with pytest.raises(CORE.LeaseLost):
        arun(harness.apply_effect(old_claim))

    assert arun(harness.complete_claim(new_claim)).status == "completed"
    proof = arun(
        harness.assert_terminal_invariants(
            "lease-command",
            max_recovery_ticks=2,
            recovery_ticks=2,
            recovery_elapsed_seconds=time.monotonic() - recovery_started_at,
        )
    )
    assert proof["checks"]["canonical_apply_once"] is True


def test_timeout_reaches_dlq_then_explicit_replay_converges_once(harness) -> None:
    command_id = "dlq-command"
    arun(harness.admit(command_id, "dlq-value"))
    for attempt in range(2):
        outcome = arun(
            harness.process_one(
                f"timeout-{attempt}",
                fault_point="downstream_timeout_after_commit",
                timeout_seconds=0.01,
            )
        )
        assert outcome.status == "timeout"
    assert arun(harness.dlq_count()) == 1
    recovery_started_at = time.monotonic()
    assert arun(harness.replay_dlq(command_id)) is True
    assert arun(harness.replay_dlq(command_id)) is False
    assert arun(harness.process_one("replay-worker")).status == "completed"
    proof = arun(
        harness.assert_terminal_invariants(
            command_id,
            max_recovery_ticks=2,
            recovery_ticks=1,
            recovery_elapsed_seconds=time.monotonic() - recovery_started_at,
        )
    )
    assert proof["counts"]["effects"] == 1
    assert proof["checks"]["rpo_zero"] is True


def test_expired_final_worker_attempt_is_reaped_to_dlq(harness) -> None:
    command_id = "crash-limit-command"
    arun(harness.admit(command_id, "crash-limit-value"))
    for attempt in range(harness.max_attempts):
        outcome = arun(
            harness.process_one(
                f"crash-limit-{attempt}",
                fault_point="before_downstream_mutation",
            )
        )
        assert outcome.status == "injected_fault"
        time.sleep(harness.controller_interval_seconds + 0.04)
    assert arun(harness.claim_one("reaper")) is None
    snapshot = arun(harness.snapshot(command_id))
    assert snapshot["outbox"][0]["status"] == "dlq"
    assert snapshot["commands"][0]["status"] == "dlq"


def test_terminal_invariant_rejects_missing_authoritative_receipt(harness) -> None:
    command_id = "missing-receipt"
    arun(harness.admit(command_id, "value"))
    claim = arun(harness.claim_one("partial-worker"))
    assert claim is not None
    arun(harness.apply_effect(claim))
    with pytest.raises(CORE.InvariantViolation, match="terminal row counts"):
        arun(
            harness.assert_terminal_invariants(
                command_id,
                max_recovery_ticks=2,
                recovery_ticks=1,
                recovery_elapsed_seconds=0.01,
            )
        )


def _sample_report(run_id: str = "deterministic-run") -> dict:
    checks = {
        "command_completed": True,
        "outbox_published": True,
        "canonical_apply_once": True,
        "command_correlated": True,
        "payload_correlated": True,
        "event_correlated": True,
        "trace_correlated": True,
        "idempotency_correlated": True,
        "effect_correlated": True,
        "controller_correlated": True,
        "rpo_zero": True,
        "recovery_within_two_intervals": True,
    }
    fault_points = [
        "before_outbox_persist",
        "after_outbox_persist",
        "before_downstream_mutation",
        "after_downstream_mutation",
        "after_mutation_before_receipt",
        "downstream_timeout_after_commit",
        "before_projection",
        "after_projection_before_publish",
    ]
    scenarios = [
        {
            "scenario_id": f"F{index:02d}_{point.upper()}",
            "status": "pass",
            "command_id": f"cmd-{index}",
            "expected_fault": point,
            "observed_faults": [point],
            "recovery_ticks": 2,
            "recovery_elapsed_seconds": 0.2,
            "recovery_started_at": "2026-07-14T00:00:00Z",
            "recovered_at": "2026-07-14T00:00:00.200000Z",
            "checks": checks,
            "service_identities": {
                "bff_terminal_readback": {"data": {"loop_id": RUNNER.LOOP_ID}}
            },
            "failure_snapshot": {"audit": [{"outcome": "fault_observed"}]},
            "raw_snapshot": {
                key: [{"proof": key}]
                for key in (
                    "commands",
                    "outbox",
                    "effects",
                    "receipts",
                    "projections",
                    "controller_records",
                )
            },
        }
        for index, point in enumerate(fault_points, 1)
    ]
    scenarios.extend(
        {
            "scenario_id": scenario_id,
            "status": "pass",
            "command_id": scenario_id.lower(),
            "expected_fault": None,
            "observed_faults": [],
            "recovery_ticks": 1,
            "recovery_elapsed_seconds": 0.2,
            "recovery_started_at": "2026-07-14T00:00:00Z",
            "recovered_at": "2026-07-14T00:00:00.200000Z",
            "checks": checks,
            "service_identities": {
                "bff_terminal_readback": {"data": {"loop_id": RUNNER.LOOP_ID}}
            },
            "failure_snapshot": {},
            "raw_snapshot": {
                key: [{"proof": key}]
                for key in (
                    "commands",
                    "outbox",
                    "effects",
                    "receipts",
                    "projections",
                    "controller_records",
                )
            },
        }
        for scenario_id in (
            "DUPLICATE_DELIVERY",
            "LEASE_EXPIRY_FENCING",
            "TIMEOUT_DLQ_REPLAY",
            "WORKER_RESTART",
            "BFF_RESTART",
            "DATABASE_RESTART",
            "FULL_STACK_RESTART",
        )
    )
    return {
        "schema_version": "loop_recovery_matrix_run.v2",
        "task_id": RUNNER.TASK_ID,
        "run_id": run_id,
        "environment": "loop-recovery-deterministic",
        "tenant_id": "tenant-deterministic",
        "database_isolation": "disposable_docker_container",
        "isolation_attestation_sha256": "0" * 64,
        "live_broker_enabled": False,
        "started_at": "2026-07-14T00:00:00Z",
        "completed_at": "2026-07-14T00:00:10Z",
        "controller_interval_seconds": 0.4,
        "max_recovery_ticks": 2,
        "deployment_sha": RUNNER.git_output("rev-parse", "HEAD"),
        "initial_service_identities": {
            "postgres": {"code_identity": "sha256:postgres-image"},
            "bff": {"code_identity": RUNNER.git_output("rev-parse", "HEAD")},
            "bff_readyz": {
                "commit": RUNNER.git_output("rev-parse", "HEAD"),
                "source_commit_sha": RUNNER.git_output("rev-parse", "HEAD"),
            },
        },
        "scenario_count": len(scenarios),
        "overall_status": "pass",
        "adjacent_validation": [
            {
                "command": "-m pytest -q focused adjacent",
                "result": "pass",
                "returncode": 0,
                "summary": "all focused and adjacent tests passed",
            }
        ],
        "scenarios": scenarios,
    }


def test_manifest_is_deterministic_schema_valid_and_does_not_claim_review(
    tmp_path: Path,
) -> None:
    report = _sample_report()
    raw_path = tmp_path / "matrix.json"
    RUNNER.write_json_exclusive(raw_path, report)
    raw_sha = RUNNER.sha256_file(raw_path)
    first = RUNNER.build_evidence(
        report,
        "raw/matrix.json",
        raw_sha,
        raw_path=raw_path,
    )
    second = RUNNER.build_evidence(
        report,
        "raw/matrix.json",
        raw_sha,
        raw_path=raw_path,
    )
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    schema = json.loads((ROOT / "schemas" / "product-evidence.schema.json").read_text())
    jsonschema.validate(first, schema)
    assert first["task"]["owner"] == "Codex"
    assert first["task"]["overall_admission"] == "review_required_evidence_only"
    ac05 = next(item for item in first["acceptance"] if item["id"] == "AC-05")
    assert ac05["status"] == "pending_independent_review_and_merge"
    assert not any(
        item["actor"] == "Claude" and item["status"] in {"pass", "approved"}
        for item in first["record_log"]
    )


def test_evidence_builder_rejects_failed_or_incomplete_raw_report(
    tmp_path: Path,
) -> None:
    report = _sample_report("invalid-report")
    report["scenarios"][0]["checks"]["rpo_zero"] = False
    raw_path = tmp_path / "invalid.json"
    RUNNER.write_json_exclusive(raw_path, report)
    with pytest.raises(RUNNER.MatrixFailure, match="terminal invariant"):
        RUNNER.build_evidence(
            report,
            "raw/invalid.json",
            RUNNER.sha256_file(raw_path),
            raw_path=raw_path,
        )


def test_evidence_finalizer_requires_and_records_real_review_and_merge(
    tmp_path: Path,
) -> None:
    report = _sample_report("finalize-report")
    raw_path = tmp_path / "finalize.json"
    RUNNER.write_json_exclusive(raw_path, report)
    evidence = RUNNER.build_evidence(
        report,
        "raw/finalize.json",
        RUNNER.sha256_file(raw_path),
        raw_path=raw_path,
    )
    pr = {
        "number": RUNNER.PR_NUMBER,
        "url": f"https://github.com/ajoe734/pantheon/pull/{RUNNER.PR_NUMBER}",
        "state": "MERGED",
        "mergedAt": "2026-07-14T01:02:03Z",
        "mergeCommit": {"oid": "a" * 40},
        "headRefOid": RUNNER.git_output("rev-parse", "HEAD"),
        "baseRefName": "dev",
        "statusCheckRollup": [
            {
                "name": "Smoke acceptance",
                "workflowName": "Branch CI Gate",
                "conclusion": "SUCCESS",
                "detailsUrl": "https://github.com/ajoe734/pantheon/actions/runs/123/jobs/456",
            }
        ],
    }
    task = {
        "id": RUNNER.TASK_ID,
        "owner": "Codex",
        "reviewer": "Claude",
        "status": "review_approved",
        "review_file": (
            "docs/deployment/evidence/loop-product-level/"
            f"{RUNNER.TASK_ID}/evidence.json"
        ),
    }
    finalized = RUNNER.finalize_evidence_payload(
        evidence,
        pr=pr,
        task=task,
        observed_at="2026-07-14T01:03:00Z",
    )
    assert finalized["task"]["overall_admission"] == "accepted_contract_evidence"
    assert all(item["status"] == "pass" for item in finalized["acceptance"])
    assert finalized["implementation_delivery"]["pull_request"]["merge_sha"] == "a" * 40
    assert finalized["record_log"][-2]["actor"] == "Claude"


def test_raw_run_artifact_is_exclusive_create(tmp_path: Path) -> None:
    path = tmp_path / "run" / "matrix.json"
    RUNNER.write_json_exclusive(path, {"run_id": "one"})
    with pytest.raises(FileExistsError):
        RUNNER.write_json_exclusive(path, {"run_id": "replacement"})
    assert json.loads(path.read_text()) == {"run_id": "one"}


def test_import_and_unit_paths_do_not_rewrite_tracked_evidence(tmp_path: Path) -> None:
    evidence = RUNNER.EVIDENCE_ROOT / "evidence.json"
    checksum = RUNNER.EVIDENCE_ROOT / "evidence.sha256"
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (evidence, checksum)
        if path.exists()
    }
    RUNNER.write_json_exclusive(tmp_path / "matrix.json", _sample_report("tmp-run"))
    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (evidence, checksum)
        if path.exists()
    }
    assert before == after


def test_correctness_does_not_depend_on_python_assert_statements() -> None:
    for path in (
        ROOT / "scripts" / "run_loop_product_recovery_matrix.py",
        ROOT / "services" / "loop-control" / "recovery_harness.py",
    ):
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec", optimize=2)
        assert "assert " not in source


@pytest.mark.skipif(
    os.environ.get("RUN_LOOP_RECOVERY_DOCKER_INTEGRATION") != "1",
    reason="explicit Docker integration opt-in required",
)
def test_isolated_real_process_matrix(tmp_path: Path) -> None:
    config = RUNNER.MatrixConfig(
        run_id=f"integration-{uuid.uuid4().hex[:12]}",
        environment=f"loop-recovery-integration-{uuid.uuid4().hex[:8]}",
        tenant_id=f"tenant-integration-{uuid.uuid4().hex[:8]}",
        deployment_sha=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        controller_interval_seconds=15.0,
    )
    report = RUNNER.run_integration_matrix(config, tmp_path)
    assert report["overall_status"] == "pass"
    assert report["scenario_count"] == 15
    assert {
        "WORKER_RESTART",
        "BFF_RESTART",
        "DATABASE_RESTART",
        "FULL_STACK_RESTART",
    }.issubset({item["scenario_id"] for item in report["scenarios"]})
