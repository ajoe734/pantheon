from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_build_context_excludes_support_evidence_artifacts() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "support/evidence" in dockerignore
    assert "support/evidence/**" in dockerignore
