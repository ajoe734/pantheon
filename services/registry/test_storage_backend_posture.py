"""Registry storage backend selection must fail closed in production posture
rather than silently returning the in-memory test double.

architecture-resumption-sa-sd.md §3.1: an unset/empty REGISTRY_STORE_BACKEND
in a staging/production persistence posture (PANTHEON_ENV / PANTHEON_PERSISTENCE_POSTURE)
must never resolve to the in-memory RegistryStore. Dev/test posture keeps the
explicit, documented memory default so the existing unit-test suite (which
never sets a production posture) is unaffected — see services/registry/conftest.py.
This mirrors the existing repo-wide pattern in
services/foundation/persistence_posture.py used by other owner services.
"""
from __future__ import annotations

import os

import pytest

from .storage import RegistryStore, build_registry_store


@pytest.fixture
def clean_env(monkeypatch):
    for key in (
        "REGISTRY_STORE_BACKEND",
        "PANTHEON_ENV",
        "PANTHEON_PERSISTENCE_POSTURE",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)
    yield monkeypatch


def test_unset_backend_in_dev_posture_defaults_to_memory(clean_env):
    """Dev posture (the default when no PANTHEON_ENV is set) keeps the
    explicit, documented memory test-double default."""
    store = build_registry_store()
    assert isinstance(store, RegistryStore)


def test_unset_backend_in_prod_posture_fails_closed_not_memory(clean_env):
    """An unset/empty backend in an enforced (staging/prod) persistence
    posture must raise, never silently return the in-memory store."""
    clean_env.setenv("PANTHEON_ENV", "prod")
    with pytest.raises(RuntimeError):
        build_registry_store()


def test_explicit_memory_backend_in_prod_posture_still_fails_closed(clean_env):
    """An explicit REGISTRY_STORE_BACKEND=memory is a documented test-only
    opt-in; it must not be usable to bypass the production posture gate."""
    clean_env.setenv("PANTHEON_ENV", "staging")
    clean_env.setenv("REGISTRY_STORE_BACKEND", "memory")
    with pytest.raises(RuntimeError):
        build_registry_store()


def test_postgres_backend_in_prod_posture_with_dsn_is_allowed(clean_env, monkeypatch):
    """Selecting postgres explicitly with a DSN configured must pass the
    posture gate (actual connection is exercised elsewhere, gated on
    TEST_DATABASE_URL)."""
    clean_env.setenv("PANTHEON_ENV", "prod")
    clean_env.setenv("REGISTRY_STORE_BACKEND", "postgres")
    clean_env.setenv("DATABASE_URL", "postgresql://example/registry")

    calls = {}

    def _fake_build_postgres_registry_store():
        calls["built"] = True
        return object()

    monkeypatch.setattr(
        "services.registry.pg_store.build_postgres_registry_store",
        _fake_build_postgres_registry_store,
    )
    build_registry_store()
    assert calls.get("built") is True
