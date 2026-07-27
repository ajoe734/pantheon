from __future__ import annotations

import argparse
import importlib

import pytest


cleanup = importlib.import_module("scripts.cleanup_loop_control_test_rows")


def test_contamination_catalog_is_exact_and_preserves_legitimate_loops():
    cleanup._validate_catalog()
    exact_keys = {
        (
            signature.loop_id,
            signature.tenant_id,
            signature.environment,
            signature.controller_ids,
        )
        for signature in cleanup.CONTAMINATION_SIGNATURES
    }
    assert exact_keys == {
        ("test-loop-1", "default", "test", ("ctrl-1",)),
        ("test-writer-loop", "default", "test", ("ctrl-writer",)),
        ("test-lease-loop", "default", "test", ("ctrl-lease-1",)),
        (
            "test-loop-concurrent",
            "tenant-concurrency",
            "test",
            ("ctrl-concurrent",),
        ),
        (
            "test-loop-isolation",
            "tenant-a",
            "dev",
            ("controller-tenant-a-dev",),
        ),
        (
            "test-loop-isolation",
            "tenant-b",
            "dev",
            ("controller-tenant-b-dev",),
        ),
        (
            "test-loop-isolation",
            "tenant-a",
            "prod",
            ("controller-tenant-a-prod",),
        ),
        (
            "test-loop-fenced-generation",
            "tenant-fence",
            "test",
            ("stable-controller-id",),
        ),
    }
    assert cleanup.PRESERVED_CANONICAL_LOOP_IDS == {
        "source_ingestion",
        "strategy_distillation",
    }
    assert not (
        {key[0] for key in exact_keys}
        & cleanup.PRESERVED_CANONICAL_LOOP_IDS
    )


def test_candidate_predicate_has_no_prefix_or_environment_wildcard():
    predicate, parameters = cleanup.build_candidate_predicate()
    assert "LIKE" not in predicate.upper()
    assert "environment =" in predicate
    assert "controller_id = ANY" in predicate
    assert len(parameters) == 4 * len(cleanup.CONTAMINATION_SIGNATURES)
    assert "source_ingestion" not in parameters
    assert "strategy_distillation" not in parameters


def test_plan_digest_is_stable_and_binds_target_and_rows():
    plan = {
        "task_id": cleanup.TASK_ID,
        "target": {"database_name": "test", "schema": "public"},
        "candidate_rows": [{"loop_id": "test-loop-1", "row_sha256": "a"}],
    }
    assert cleanup.cleanup_plan_sha256(plan) == cleanup.cleanup_plan_sha256(
        {
            "candidate_rows": [
                {"row_sha256": "a", "loop_id": "test-loop-1"}
            ],
            "target": {"schema": "public", "database_name": "test"},
            "task_id": cleanup.TASK_ID,
        }
    )
    changed = dict(plan)
    changed["target"] = {"database_name": "dev", "schema": "public"}
    assert cleanup.cleanup_plan_sha256(changed) != cleanup.cleanup_plan_sha256(
        plan
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"actor": "Codex2"}, "actor Human/Ops"),
        ({"approved": False}, "actor Human/Ops"),
        ({"task_id": "OTHER"}, "wrong task"),
        ({"cleanup_plan_sha256": "stale"}, "fresh approval"),
    ],
)
def test_apply_evidence_fails_closed(change, message):
    evidence = {
        "schema_version": 1,
        "task_id": cleanup.TASK_ID,
        "actor": "Human/Ops",
        "approved": True,
        "cleanup_plan_sha256": "current",
    }
    evidence.update(change)
    with pytest.raises(cleanup.CleanupRefused, match=message):
        cleanup.validate_human_ops_evidence(
            evidence,
            expected_plan_sha256="current",
        )


def test_apply_evidence_accepts_exact_human_ops_plan_binding():
    cleanup.validate_human_ops_evidence(
        {
            "schema_version": 1,
            "task_id": cleanup.TASK_ID,
            "actor": "Human/Ops",
            "approved": True,
            "cleanup_plan_sha256": "current",
            "note": "Approved after reviewing the dry-run row digests.",
        },
        expected_plan_sha256="current",
    )


@pytest.mark.asyncio
async def test_run_defaults_to_dry_run_and_never_calls_delete(
    monkeypatch,
):
    class FakeConnection:
        async def close(self):
            return None

    class FakeAsyncpg:
        async def connect(self, _dsn):
            return FakeConnection()

    plan = {
        "task_id": cleanup.TASK_ID,
        "target": {"database_name": "test", "schema": "public"},
        "candidate_rows": [],
    }

    async def fake_collect(_conn, *, schema, lock_rows=False):
        assert schema == "public"
        assert lock_rows is False
        return plan

    async def forbidden_delete(*_args, **_kwargs):
        raise AssertionError("dry-run must not call DELETE")

    monkeypatch.setenv(
        cleanup.CLEANUP_DATABASE_URL_ENV,
        "postgresql://tester:secret@127.0.0.1/test",
    )
    monkeypatch.setitem(__import__("sys").modules, "asyncpg", FakeAsyncpg())
    monkeypatch.setattr(cleanup, "collect_cleanup_plan", fake_collect)
    monkeypatch.setattr(cleanup, "_delete_candidates", forbidden_delete)

    result = await cleanup._run(
        argparse.Namespace(
            schema="public",
            apply=False,
            human_ops_evidence=None,
        )
    )
    assert result["mode"] == "dry_run"
    assert result["plan"] == plan


@pytest.mark.asyncio
async def test_apply_rolls_back_when_deleted_count_differs_from_plan(
    monkeypatch,
):
    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, _exc, _tb):
            self.exc_type = exc_type
            return False

    class FakeConnection:
        def transaction(self):
            return Transaction()

    plan = {
        "table_exists": True,
        "candidate_row_count": 2,
        "candidate_rows": [
            {"loop_id": "test-loop-1", "row_sha256": "a"},
            {"loop_id": "test-writer-loop", "row_sha256": "b"},
        ],
    }
    plan_sha = cleanup.cleanup_plan_sha256(plan)

    async def fake_collect(_conn, *, schema, lock_rows):
        assert schema == "public"
        assert lock_rows is True
        return plan

    async def fake_delete(_conn, *, schema):
        assert schema == "public"
        return 1

    monkeypatch.setattr(cleanup, "collect_cleanup_plan", fake_collect)
    monkeypatch.setattr(cleanup, "_delete_candidates", fake_delete)
    with pytest.raises(cleanup.CleanupRefused, match="transaction will roll back"):
        await cleanup.apply_cleanup(
            FakeConnection(),
            schema="public",
            evidence={
                "schema_version": 1,
                "task_id": cleanup.TASK_ID,
                "actor": "Human/Ops",
                "approved": True,
                "cleanup_plan_sha256": plan_sha,
            },
        )


@pytest.mark.asyncio
async def test_apply_refuses_before_delete_when_plan_binding_is_stale(
    monkeypatch,
):
    class Transaction:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

    class FakeConnection:
        def transaction(self):
            return Transaction()

    plan = {
        "table_exists": True,
        "candidate_row_count": 1,
        "candidate_rows": [
            {"loop_id": "test-loop-1", "row_sha256": "current"}
        ],
    }

    async def fake_collect(_conn, *, schema, lock_rows):
        assert schema == "public"
        assert lock_rows is True
        return plan

    async def forbidden_delete(*_args, **_kwargs):
        raise AssertionError("stale evidence must be refused before DELETE")

    monkeypatch.setattr(cleanup, "collect_cleanup_plan", fake_collect)
    monkeypatch.setattr(cleanup, "_delete_candidates", forbidden_delete)
    with pytest.raises(cleanup.CleanupRefused, match="fresh approval"):
        await cleanup.apply_cleanup(
            FakeConnection(),
            schema="public",
            evidence={
                "schema_version": 1,
                "task_id": cleanup.TASK_ID,
                "actor": "Human/Ops",
                "approved": True,
                "cleanup_plan_sha256": "stale",
            },
        )


@pytest.mark.asyncio
async def test_run_requires_dedicated_cleanup_dsn(monkeypatch):
    monkeypatch.delenv(cleanup.CLEANUP_DATABASE_URL_ENV, raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://operator:secret@dev-db.example/pantheon",
    )
    with pytest.raises(cleanup.CleanupRefused, match="DATABASE_URL is ignored"):
        await cleanup._run(
            argparse.Namespace(
                schema="public",
                apply=False,
                human_ops_evidence=None,
            )
        )
