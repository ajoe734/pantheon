from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest


STORE_PATH = Path(__file__).resolve().parents[1] / "agora/research/store.py"


def _module():
    spec = importlib.util.spec_from_file_location("agora_research_store_backend_test", STORE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_factory_keeps_memory_backend_as_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    monkeypatch.delenv("AGORA_RESEARCH_STORE_BACKEND", raising=False)
    monkeypatch.delenv("AGORA_RESEARCH_PLAN_STORE_BACKEND", raising=False)
    assert isinstance(module.make_research_plan_store(), module.MemoryResearchPlanStore)


def test_factory_selects_postgres_with_dedicated_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    captured = {}

    class StubPostgresStore:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(module, "PostgresResearchPlanStore", StubPostgresStore)
    monkeypatch.setenv("AGORA_RESEARCH_STORE_BACKEND", "postgres")
    monkeypatch.setenv("AGORA_RESEARCH_STORE_DSN", "postgresql://research@example/pantheon")
    monkeypatch.setenv("AGORA_RESEARCH_STORE_SCHEMA", "tenant_research")

    assert isinstance(module.make_research_plan_store(), StubPostgresStore)
    assert captured == {
        "dsn": "postgresql://research@example/pantheon",
        "schema": "tenant_research",
    }


def test_factory_fails_closed_without_postgres_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    monkeypatch.setenv("AGORA_RESEARCH_STORE_BACKEND", "postgres")
    monkeypatch.delenv("AGORA_RESEARCH_STORE_DSN", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DSN"):
        module.make_research_plan_store()


@pytest.fixture
def postgres_store():
    dsn = os.environ.get("AGORA_RESEARCH_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set AGORA_RESEARCH_TEST_POSTGRES_DSN for real Postgres coverage")
    module = _module()
    schema = f"test_agora_research_{uuid.uuid4().hex}"
    store = module.PostgresResearchPlanStore(dsn=dsn, schema=schema)
    try:
        yield module, store
    finally:
        with store._connect() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_postgres_reviews_are_isolated_and_survive_store_restart(postgres_store) -> None:
    module, store = postgres_store
    pool_id = "pool-1"
    first = {"review_id": "review-a", "artifact_id": "artifact-a", "decision": "accept"}
    second = {"review_id": "review-b", "artifact_id": "artifact-b", "decision": "park"}
    store.add_candidate_review(pool_id, "artifact-a", first)
    store.add_candidate_review(pool_id, "artifact-b", second)

    restarted = module.PostgresResearchPlanStore(dsn=store.dsn, schema=store.schema)
    assert restarted.list_candidate_reviews(pool_id, "artifact-a") == [first]
    assert restarted.list_candidate_reviews(pool_id, "artifact-b") == [second]
    assert restarted.list_candidate_reviews(pool_id, "artifact-missing") == []


def test_postgres_score_replacement_is_durable_and_exact(postgres_store) -> None:
    module, store = postgres_store
    store.replace_candidate_scores("pool-1", {
        "artifact-a": {"candidate_id": "artifact-a", "score": 1},
        "artifact-b": {"candidate_id": "artifact-b", "score": 2},
    })
    store.replace_candidate_scores("pool-1", {
        "artifact-b": {"candidate_id": "artifact-b", "score": 3},
    })

    restarted = module.PostgresResearchPlanStore(dsn=store.dsn, schema=store.schema)
    assert restarted.list_candidate_scores("pool-1") == [
        {"candidate_id": "artifact-b", "score": 3},
    ]
