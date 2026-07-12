from __future__ import annotations

import importlib.util
import sys
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
