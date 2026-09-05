from __future__ import annotations

import concurrent.futures
import logging
import os
import uuid

import pytest

from services.control_plane.bff.agora.dashboard import store as store_module


def _identity() -> dict:
    return {"recipe_id": "rec-1", "tenant_id": "t", "user_id": "u", "strategy_id": "s",
            "active_version": 1, "created_at": "2026-07-12T00:00:00Z"}


def _version(number: int, previous: int | None = None) -> dict:
    return {"recipe_id": "rec-1", "version": number, "previous_version": previous,
            "status": "active", "recipe_json": {"version": number}, "content_sha256": f"sha-{number}",
            "generated_by": "user", "change_reason": "test", "created_at": "2026-07-12T00:00:00Z"}


def test_memory_store_preserves_versions_and_compare_and_swap() -> None:
    store = store_module.MemoryDashboardRecipeStore()
    store.create_recipe(_identity(), _version(1), "create-key")
    assert store.has_idempotency_key("create-key")
    assert store.append_version("rec-1", 1, _version(2, 1), "patch-key") is True
    assert store.append_version("rec-1", 1, _version(3, 1)) is False
    assert store.get_identity("rec-1")["active_version"] == 2
    assert [row["version"] for row in store.list_versions("rec-1")] == [1, 2]


def test_concurrent_same_key_creates_exactly_one_recipe() -> None:
    store = store_module.MemoryDashboardRecipeStore()

    def create(number: int) -> str:
        identity = {**_identity(), "recipe_id": f"rec-{number}"}
        version = {**_version(1), "recipe_id": f"rec-{number}"}
        return store.create_recipe(identity, version, "shared-create-key")

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        recipe_ids = list(executor.map(create, (1, 2)))

    assert recipe_ids[0] == recipe_ids[1]
    assert len(store.list_identities()) == 1
    assert len(store.list_versions(recipe_ids[0])) == 1


def test_postgres_concurrent_same_key_creates_exactly_one_recipe() -> None:
    dsn = os.getenv("AGORA_DASHBOARD_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("AGORA_DASHBOARD_TEST_POSTGRES_DSN is not configured")
    schema = f"agora_test_{uuid.uuid4().hex}"
    store = store_module.PostgresDashboardRecipeStore(dsn, schema)

    def create(number: int) -> str:
        identity = {**_identity(), "recipe_id": f"rec-{number}"}
        version = {**_version(1), "recipe_id": f"rec-{number}"}
        return store.create_recipe(identity, version, "shared-postgres-key")

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            recipe_ids = list(executor.map(create, (1, 2)))

        assert recipe_ids[0] == recipe_ids[1]
        assert len(store.list_identities()) == 1
        assert len(store.list_versions(recipe_ids[0])) == 1
    finally:
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_factory_defaults_to_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(store_module.BACKEND_ENV, raising=False)
    assert isinstance(store_module.make_dashboard_recipe_store(), store_module.MemoryDashboardRecipeStore)


def test_factory_requires_postgres_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(store_module.DSN_ENV, raising=False)
    with pytest.raises(RuntimeError, match=store_module.DSN_ENV):
        store_module.make_dashboard_recipe_store(backend="postgres")


def test_factory_does_not_log_postgres_credentials(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    class FakePostgresStore:
        def __init__(self, dsn: str, schema: str) -> None:
            self.dsn = dsn

    monkeypatch.setattr(store_module, "PostgresDashboardRecipeStore", FakePostgresStore)
    with caplog.at_level(logging.INFO, logger=store_module.__name__):
        store_module.make_dashboard_recipe_store(
            backend="postgres", dsn="postgresql://secret-user:secret-pass@postgres/pantheon"
        )
    assert "secret-user" not in caplog.text
    assert "secret-pass" not in caplog.text
