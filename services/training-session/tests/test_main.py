from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_main_module():
    name = "training_session_main_test_module"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, SERVICE_DIR / "main.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_persona_authority_token_fail_closed_and_file_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = _load_main_module()

    # Case 1: Neither variable set -> raises PersonaTargetError
    monkeypatch.delenv("TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN", raising=False)
    monkeypatch.delenv("TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN_FILE", raising=False)
    monkeypatch.delenv("PANTHEON_PERSONA_SERVICE_TOKEN", raising=False)
    with pytest.raises(
        main.PersonaTargetError,
        match="TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN is required",
    ):
        main._persona_authority_token()

    # Case 2: PANTHEON_PERSONA_SERVICE_TOKEN alone does NOT satisfy persona authority (no plain token fallback)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", "plain-fallback-token")
    with pytest.raises(
        main.PersonaTargetError,
        match="TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN is required",
    ):
        main._persona_authority_token()
    monkeypatch.delenv("PANTHEON_PERSONA_SERVICE_TOKEN", raising=False)

    # Case 3: Explicit direct TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN set -> returns token
    monkeypatch.setenv("TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN", "direct-authority-token")
    assert main._persona_authority_token() == "direct-authority-token"

    # Case 4: Token file configured via configured_service_token with mode 0600
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as handle:
        os.chmod(handle.name, 0o600)
        handle.write("file-based-authority-token\n")
        handle.flush()
        monkeypatch.setenv("TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN_FILE", handle.name)
        assert main._persona_authority_token() == "file-based-authority-token"

    # Case 5: Missing configured token file -> fails closed (no fallback to env)
    monkeypatch.setenv(
        "TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN_FILE",
        "/tmp/nonexistent-persona-authority-token-file",
    )
    with pytest.raises(
        main.PersonaTargetError,
        match="TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN credential unavailable",
    ):
        main._persona_authority_token()

    # Case 6: Unsafe permissions on token file -> fails closed
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as handle:
        os.chmod(handle.name, 0o666)
        handle.write("unsafe-perm-token\n")
        handle.flush()
        monkeypatch.setenv("TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN_FILE", handle.name)
        with pytest.raises(
            main.PersonaTargetError,
            match="TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN credential unavailable",
        ):
            main._persona_authority_token()

    # Case 7: Invalid/empty token file -> fails closed
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as handle:
        os.chmod(handle.name, 0o600)
        handle.write("   \n")
        handle.flush()
        monkeypatch.setenv("TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN_FILE", handle.name)
        with pytest.raises(
            main.PersonaTargetError,
            match="TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN credential unavailable",
        ):
            main._persona_authority_token()


def test_persona_target_url_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    main = _load_main_module()

    monkeypatch.setenv(
        "TEST_READBACK_TEMPLATE",
        "http://persona:8002/api/personas/{persona_id}/training-target?tenant={tenant_id}&session={session_id}",
    )
    url = main._persona_target_url(
        "TEST_READBACK_TEMPLATE",
        persona_id="persona-1",
        tenant_id="tenant-dev",
        session_id="session-1",
    )
    assert (
        url
        == "http://persona:8002/api/personas/persona-1/training-target?tenant=tenant-dev&session=session-1"
    )

    # Unset template raises PersonaTargetError
    with pytest.raises(main.PersonaTargetError, match="UNSET_TEMPLATE is required"):
        main._persona_target_url(
            "UNSET_TEMPLATE",
            persona_id="p",
            session_id="s",
        )


def test_compose_wires_persona_target_templates() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    training = compose["services"]["training-session-svc"]
    env = training["environment"]

    assert env["TRAINING_SESSION_PERSONA_READBACK_URL_TEMPLATE"] == (
        "${TRAINING_SESSION_PERSONA_READBACK_URL_TEMPLATE:-http://persona:8002/api/personas/{persona_id}/training-target}"
    )
    assert env["TRAINING_SESSION_PERSONA_TARGET_WRITE_URL_TEMPLATE"] == (
        "${TRAINING_SESSION_PERSONA_TARGET_WRITE_URL_TEMPLATE:-http://persona:8002/api/personas/{persona_id}/training-target}"
    )
    assert env["TRAINING_SESSION_PERSONA_TARGET_READBACK_URL_TEMPLATE"] == (
        "${TRAINING_SESSION_PERSONA_TARGET_READBACK_URL_TEMPLATE:-http://persona:8002/api/personas/{persona_id}/training-target}"
    )
    assert env["TRAINING_SESSION_APPROVAL_READBACK_URL_TEMPLATE"] == (
        "${TRAINING_SESSION_APPROVAL_READBACK_URL_TEMPLATE:-http://governance:8082/api/governance/approvals/{approval_decision_ref}}"
    )
    assert env["TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN"] == (
        "${TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN:-}"
    )
    assert env["TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN_FILE"] == (
        "${TRAINING_SESSION_PERSONA_AUTHORITY_TOKEN_FILE:-}"
    )
