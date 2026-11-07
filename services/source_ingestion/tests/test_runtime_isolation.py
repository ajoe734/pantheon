"""Two Source Ingestion runtimes own their dependencies independently."""

from __future__ import annotations

import inspect

from services.source_ingestion import main as main_module
from services.source_ingestion import runtime as runtime_module
from services.source_ingestion.runtime import create_runtime


def test_runtimes_do_not_share_dependencies_through_main_globals(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", "source-test-secret")
    first = create_runtime(data_dir=tmp_path / "first")
    second = create_runtime(data_dir=tmp_path / "second")
    sentinel = object()
    monkeypatch.setattr(main_module.runtime, "store", sentinel)
    monkeypatch.setattr(main_module.runtime, "DATA_DIR", tmp_path / "other")

    assert first.store is not sentinel and second.store is not sentinel
    assert first.store is not second.store
    assert first.DATA_DIR != second.DATA_DIR
    assert first.DATA_DIR == tmp_path / "first"


def test_no_module_reverse_lookup_remains() -> None:
    source = inspect.getsource(runtime_module)
    assert "_active_attr" not in source
    assert "sys.modules" not in source
    assert "__code__" not in source
    assert not hasattr(main_module, "store")
    assert not hasattr(main_module, "_append_audit_actions")
