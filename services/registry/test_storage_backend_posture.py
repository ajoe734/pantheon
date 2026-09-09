"""Registry storage backend selection must fail closed rather than silently
returning the in-memory test double.

architecture-resumption-sa-sd.md §3.1: REGISTRY_STORE_BACKEND must always be
explicitly set to ``memory`` or ``postgres`` — "memory is explicitly injected
test-only, never missing-config/connection/schema fallback".

Reviewer finding 7 (gen-8 review): an earlier revision of this module
documented and tested a "dev posture keeps an implicit memory default"
carve-out. That carve-out itself was the defect: an entirely unconfigured
mounted app (no PANTHEON_ENV, no REGISTRY_STORE_BACKEND) silently served real
writes against the in-memory store, which is indistinguishable from a
working deployment until data vanishes on process exit. There is no
posture-based carve-out anymore — an unset backend fails closed in every
posture; dev/test callers opt into memory the same explicit way
services/registry/conftest.py already does for this package's whole unit
test run.

Backend selection also fails closed the same way
services/foundation/persistence_posture.py already does for every other
service: in an enforced posture (PANTHEON_ENV / PANTHEON_PERSISTENCE_POSTURE
in {stage, staging, prod, production, ...}), REGISTRY_STORE_BACKEND must
resolve to postgres or this raises RuntimeError.
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


def test_unset_backend_always_fails_closed_even_in_dev_posture(clean_env):
    """Reviewer finding 7 (gen-8 review): an unset REGISTRY_STORE_BACKEND
    must raise even in dev posture (no PANTHEON_ENV set) — the mounted app
    never silently serves real writes against the in-memory test double just
    because nothing was configured at all. Explicit opt-in only (see
    test_explicit_memory_backend_in_dev_posture_is_allowed below)."""
    with pytest.raises(RuntimeError):
        build_registry_store()


def test_explicit_memory_backend_in_dev_posture_is_allowed(clean_env):
    """An explicit REGISTRY_STORE_BACKEND=memory (as services/registry/conftest.py
    sets for this whole package's unit test run) is the documented test-only
    opt-in into the in-memory test double."""
    clean_env.setenv("REGISTRY_STORE_BACKEND", "memory")
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
