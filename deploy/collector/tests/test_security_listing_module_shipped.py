"""The collector image ships the shared security listing module; the loader fails with a config error, not IndexError."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from collector_stack import runner as runner_mod
from collector_stack.config import CollectorConfigError

COLLECTOR_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = COLLECTOR_DIR.parent.parent
SHARED = REPO_ROOT / "services" / "source_ingestion" / "security_listing.py"


def _dockerfile() -> str:
    return (COLLECTOR_DIR / "Dockerfile").read_text(encoding="utf-8")


VENDORED = COLLECTOR_DIR / "vendor" / "security_listing.py"


def test_vendored_module_byte_identical_to_shared_module() -> None:
    assert VENDORED.is_file() and SHARED.is_file()
    assert VENDORED.read_bytes() == SHARED.read_bytes(), (
        "drift: cp services/source_ingestion/security_listing.py deploy/collector/vendor/"
    )


def test_dockerfile_copies_vendored_module_from_collector_context() -> None:
    text = _dockerfile()
    m = re.search(r"^COPY (\S+security_listing\.py) (\S+)$", text, re.M)
    assert m, "Dockerfile must COPY security_listing.py"
    src, dest = m.groups()
    assert (COLLECTOR_DIR / src) == VENDORED
    assert dest == "/app/services/source_ingestion/security_listing.py"
    compose = (COLLECTOR_DIR / "docker-compose.yml").read_text(encoding="utf-8")
    assert "context: ." in compose and "dockerfile: Dockerfile" in compose


def _image_layout(tmp_path: Path, *, with_module: bool) -> Path:
    app = tmp_path / "app"
    shutil.copytree(COLLECTOR_DIR / "collector_stack", app / "collector_stack")
    if with_module:  # exactly what the Dockerfile COPY produces
        dest = app / "services" / "source_ingestion" / "security_listing.py"
        dest.parent.mkdir(parents=True)
        shutil.copyfile(VENDORED, dest)
    return app


def test_image_module_is_byte_identical_to_repo_module(tmp_path: Path) -> None:
    app = _image_layout(tmp_path, with_module=True)
    shipped = app / "services" / "source_ingestion" / "security_listing.py"
    assert shipped.read_bytes() == SHARED.read_bytes()


def test_image_layout_candidates_find_module_with_and_without(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PANTHEON_SECURITY_LISTING_MODULE", raising=False)
    app = _image_layout(tmp_path, with_module=True)
    cands = runner_mod._security_listing_candidates(app / "collector_stack" / "runner.py")
    assert cands[0] == app / "services" / "source_ingestion" / "security_listing.py" and cands[0].is_file()


def test_missing_module_raises_config_error_not_index_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    monkeypatch.delenv("PANTHEON_SECURITY_LISTING_MODULE", raising=False)
    app = _image_layout(tmp_path, with_module=False)
    fake_runner = app / "collector_stack" / "runner.py"
    monkeypatch.setattr(runner_mod, "__file__", str(fake_runner))
    real_import = importlib.import_module

    def _no_shared(name: str, *a: object, **k: object):
        if name == "services.source_ingestion.security_listing":
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", _no_shared)
    with pytest.raises(CollectorConfigError) as exc:
        runner_mod.load_security_listing_module()
    assert str(app / "services" / "source_ingestion" / "security_listing.py") in str(exc.value)


def test_shallow_runner_path_does_not_index_error() -> None:
    assert runner_mod._security_listing_candidates(Path("/app/collector_stack/runner.py")) == [
        Path("/app/services/source_ingestion/security_listing.py")
    ]
    assert runner_mod._security_listing_candidates(Path("/runner.py")) == []
