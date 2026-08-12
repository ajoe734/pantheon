from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_build_context_excludes_support_evidence_artifacts() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "support/evidence" in dockerignore
    assert "support/evidence/**" in dockerignore


def test_docker_build_context_excludes_lean_test_artifacts() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "lean/Tests" in dockerignore
    assert "lean/Tests/**" in dockerignore


def test_bff_build_context_includes_only_the_shared_canonical_assertion_module() -> None:
    """The BFF imports this verifier at process start; runtime state stays excluded."""

    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".orchestrator" not in dockerignore
    assert ".orchestrator/*" in dockerignore
    assert "!.orchestrator/canonical_mutation_assertion.py" in dockerignore
