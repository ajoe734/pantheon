"""Tests for scripts/backfill_hosted_chain_persona_scope.py (PERSONA-CHAIN-TEST-SCOPE-20261104)."""

from __future__ import annotations

import ast
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from scripts import backfill_hosted_chain_persona_scope as backfill
from services.control_plane.bff.personas import fleet_classification as fc
from services.control_plane.bff.ports.persona_write_owner import (
    PersonaRegistryHttpWritePort,
)
from services.persona.write_owner import (
    CreatePersonaRequest,
    PersistentCapabilitySnapshotOwner,
    PersistentPersonaOwner,
    PersistentPersonaTrainingTargetOwner,
    create_app,
)

CHAIN_PERSONA_CLAUDE = (
    "persona-strat-hosted-all12-h12-claude-20261027-4923-src-106b4057d07a"
)
CHAIN_PERSONA_01A11 = (
    "persona-strat-hosted-all12-h12-01a11-20261023-9aeb-src-e018ff6d0eb8"
)
REQUESTED_BY_OPERATOR = {
    "actor_id": "pantheon-dev-operator-a",
    "actor_type": "operator",
}
SERVICE_TOKEN = "svc-secret-do-not-print-token-998877"


class _FakeHttpResponse(io.BytesIO):
    def __init__(self, status: int, body: bytes) -> None:
        super().__init__(body)
        self.status = status

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _seed_chain_persona(
    owner: PersistentPersonaOwner,
    persona_id: str,
    *,
    tenant_id: str = "pantheon-dev",
    name: str | None = None,
    mandate: str = "Hosted L12 chain verification persona",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = owner.create(
        CreatePersonaRequest(
            persona_id=persona_id,
            tenant_id=tenant_id,
            name=name or f"Chain {persona_id[-12:]}",
            mandate=mandate,
            status="active",
            lifecycle_state="draft",
            owner="pantheon-dev-operator-a",
            actor_id="operator-bff",
            metadata=(
                {"requested_by": dict(REQUESTED_BY_OPERATOR)}
                if metadata is None
                else metadata
            ),
        )
    )
    return body.model_dump(mode="json")


@pytest.fixture()
def owner_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setenv("PANTHEON_BFF_DEFAULT_TENANT_ID", "pantheon-dev")
    monkeypatch.setenv("PERSONA_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("PANTHEON_PERSONA_SERVICE_ACTOR_ID", "operator-bff")

    persona_owner = PersistentPersonaOwner.from_json_path(tmp_path / "personas.json")
    capability_owner = PersistentCapabilitySnapshotOwner.from_json_path(
        tmp_path / "capabilities.json"
    )
    training_owner = PersistentPersonaTrainingTargetOwner.from_json_path(
        tmp_path / "training_targets.json",
        persona_owner=persona_owner,
    )
    app = create_app(
        persona_owner,
        capability_owner=capability_owner,
        training_target_owner=training_owner,
        governance_decision_verifier=None,
    )
    client = TestClient(app)
    recorded_requests: list[dict[str, Any]] = []

    def _urlopen(req: urllib.request.Request, timeout: float = 5.0):
        raw_body = req.data.decode("utf-8") if req.data else None
        parsed_body = json.loads(raw_body) if raw_body else None
        headers = {k: v for k, v in req.header_items()}
        path = urllib.request.urlsplit(req.full_url).path
        recorded_requests.append(
            {
                "method": req.get_method(),
                "path": path,
                "headers": headers,
                "body": parsed_body,
            }
        )
        resp = client.request(
            req.get_method(),
            path,
            headers=headers,
            content=req.data,
        )
        if resp.status_code >= 400:
            raise urllib.error.HTTPError(
                req.full_url,
                resp.status_code,
                resp.reason_phrase,
                hdrs=None,
                fp=io.BytesIO(resp.content),
            )
        return _FakeHttpResponse(resp.status_code, resp.content)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    port = PersonaRegistryHttpWritePort(
        base_url="http://persona-service:8000",
        timeout_seconds=5.0,
        service_token=SERVICE_TOKEN,
        service_actor_id="operator-bff",
    )
    monkeypatch.setattr(
        backfill,
        "_build_runtime",
        lambda: (port, backfill.configured_dev_tenant()),
    )
    return {
        "persona_owner": persona_owner,
        "port": port,
        "requests": recorded_requests,
    }


def test_cli_requires_explicit_persona_id_and_defaults_to_dry_run() -> None:
    with pytest.raises(SystemExit) as exc_info:
        backfill.parse_args([])
    assert exc_info.value.code == 2

    args = backfill.parse_args(
        [
            "--persona-id",
            CHAIN_PERSONA_CLAUDE,
            "--persona-id",
            CHAIN_PERSONA_01A11,
        ]
    )
    assert args.apply is False
    assert args.persona_ids == [CHAIN_PERSONA_CLAUDE, CHAIN_PERSONA_01A11]

    with pytest.raises(SystemExit) as bad_id:
        backfill.parse_args(["--persona-id", "../bad"])
    assert bad_id.value.code == 2


def test_dry_run_prints_before_and_after_without_patching(
    owner_harness: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    persona_owner: PersistentPersonaOwner = owner_harness["persona_owner"]
    _seed_chain_persona(persona_owner, CHAIN_PERSONA_CLAUDE)
    _seed_chain_persona(persona_owner, CHAIN_PERSONA_01A11)

    rc = backfill.main(
        [
            "--persona-id",
            CHAIN_PERSONA_CLAUDE,
            "--persona-id",
            CHAIN_PERSONA_01A11,
        ]
    )
    assert rc == 0
    stdout = capsys.readouterr().out
    assert SERVICE_TOKEN not in stdout
    payload = json.loads(stdout)

    assert payload["status"] == "dry_run"
    assert payload["dry_run"] is True
    assert payload["apply"] is False
    assert [r["method"] for r in owner_harness["requests"]] == ["GET", "GET"]

    for item, pid in zip(
        payload["records"],
        (CHAIN_PERSONA_CLAUDE, CHAIN_PERSONA_01A11),
        strict=True,
    ):
        assert item["persona_id"] == pid
        assert item["changed"] is True
        assert item["before"]["metadata"] == {"requested_by": REQUESTED_BY_OPERATOR}
        assert item["before"]["classification"] == {
            "persona_purpose": "unknown",
            "data_scope": "unknown",
            "classification_source": {
                "purpose": "unclassified",
                "data_scope": "unclassified",
            },
            "classification_confirmed": False,
        }
        assert item["after"]["metadata"] == {
            "requested_by": REQUESTED_BY_OPERATOR,
            "data_scope": "test",
            "persona_class": "trading",
        }
        assert item["after"]["classification"] == {
            "persona_purpose": "trading",
            "data_scope": "test",
            "classification_source": {
                "purpose": "persona_class",
                "data_scope": "data_scope",
            },
            "classification_confirmed": True,
        }
        # Durable owner store was not mutated by dry-run
        persisted = persona_owner.get(pid).model_dump(mode="json")
        assert persisted["metadata"] == {"requested_by": REQUESTED_BY_OPERATOR}


def test_apply_sends_exact_patch_body_and_preserves_fields_and_is_idempotent(
    owner_harness: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    persona_owner: PersistentPersonaOwner = owner_harness["persona_owner"]
    seeded_claude = _seed_chain_persona(
        persona_owner,
        CHAIN_PERSONA_CLAUDE,
        name="Claude Chain Persona",
        mandate="All-12 Claude hosted chain verification",
    )
    seeded_01a11 = _seed_chain_persona(
        persona_owner,
        CHAIN_PERSONA_01A11,
        name="01a11 Chain Persona",
        mandate="All-12 01a11 hosted chain verification",
    )

    rc = backfill.main(
        [
            "--persona-id",
            CHAIN_PERSONA_CLAUDE,
            "--persona-id",
            CHAIN_PERSONA_01A11,
            "--apply",
        ]
    )
    assert rc == 0
    stdout = capsys.readouterr().out
    assert SERVICE_TOKEN not in stdout
    payload = json.loads(stdout)
    assert payload["status"] == "applied"
    assert payload["dry_run"] is False
    assert payload["apply"] is True

    patch_calls = [r for r in owner_harness["requests"] if r["method"] == "PATCH"]
    assert len(patch_calls) == 2
    assert [c["path"] for c in patch_calls] == [
        f"/api/personas/{CHAIN_PERSONA_CLAUDE}",
        f"/api/personas/{CHAIN_PERSONA_01A11}",
    ]
    for call in patch_calls:
        assert call["body"] == {
            "actor_id": "operator-bff",
            "metadata": {
                "data_scope": "test",
                "persona_class": "trading",
            },
        }
        assert set(call["body"].keys()) == {"actor_id", "metadata"}
        assert set(call["body"]["metadata"].keys()) == {"data_scope", "persona_class"}

    for before_seed, pid in (
        (seeded_claude, CHAIN_PERSONA_CLAUDE),
        (seeded_01a11, CHAIN_PERSONA_01A11),
    ):
        readback = persona_owner.get(pid).model_dump(mode="json")
        assert readback["name"] == before_seed["name"]
        assert readback["mandate"] == before_seed["mandate"]
        assert readback["status"] == "active"
        assert readback["lifecycle_state"] == "draft"
        assert readback["owner"] == "pantheon-dev-operator-a"
        assert readback["tenant_id"] == "pantheon-dev"
        assert readback["metadata"] == {
            "requested_by": REQUESTED_BY_OPERATOR,
            "data_scope": "test",
            "persona_class": "trading",
        }
        assert fc.persona_classification(readback) == {
            "persona_purpose": "trading",
            "data_scope": "test",
            "classification_source": {
                "purpose": "persona_class",
                "data_scope": "data_scope",
            },
            "classification_confirmed": True,
        }

    # Second --apply run is idempotent and sends zero new PATCH calls
    owner_harness["requests"].clear()
    rc_second = backfill.main(
        [
            "--persona-id",
            CHAIN_PERSONA_CLAUDE,
            "--persona-id",
            CHAIN_PERSONA_01A11,
            "--apply",
        ]
    )
    assert rc_second == 0
    second_payload = json.loads(capsys.readouterr().out)
    assert [r["changed"] for r in second_payload["records"]] == [False, False]
    assert [r["method"] for r in owner_harness["requests"]] == ["GET", "GET"]


def test_fails_closed_on_unknown_id_or_wrong_tenant_or_conflicting_metadata(
    owner_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    persona_owner: PersistentPersonaOwner = owner_harness["persona_owner"]
    _seed_chain_persona(persona_owner, CHAIN_PERSONA_CLAUDE)

    # 1. Unknown ID alongside a known ID fails closed before any PATCH is sent
    rc_unknown = backfill.main(
        [
            "--persona-id",
            CHAIN_PERSONA_CLAUDE,
            "--persona-id",
            "persona-does-not-exist",
            "--apply",
        ]
    )
    assert rc_unknown == 1
    err_unknown = json.loads(capsys.readouterr().out)
    assert err_unknown["status"] == "error"
    assert "unknown persona_id" in err_unknown["error"]
    assert [r for r in owner_harness["requests"] if r["method"] == "PATCH"] == []

    # 2. Persona belonging to a tenant other than the configured dev tenant fails closed
    _seed_chain_persona(
        persona_owner,
        CHAIN_PERSONA_01A11,
        tenant_id="tenant-other",
    )
    owner_harness["requests"].clear()
    rc_tenant = backfill.main(
        [
            "--persona-id",
            CHAIN_PERSONA_CLAUDE,
            "--persona-id",
            CHAIN_PERSONA_01A11,
            "--apply",
        ]
    )
    assert rc_tenant == 1
    err_tenant = json.loads(capsys.readouterr().out)
    assert err_tenant["status"] == "error"
    assert "expected configured dev tenant 'pantheon-dev'" in err_tenant["error"]
    assert [r for r in owner_harness["requests"] if r["method"] == "PATCH"] == []

    # 3. Conflicting existing classification metadata fails closed
    _seed_chain_persona(
        persona_owner,
        "persona-regular-conflict",
        metadata={
            "requested_by": dict(REQUESTED_BY_OPERATOR),
            "data_scope": "regular",
        },
    )
    owner_harness["requests"].clear()
    rc_conflict = backfill.main(
        ["--persona-id", "persona-regular-conflict", "--apply"]
    )
    assert rc_conflict == 1
    err_conflict = json.loads(capsys.readouterr().out)
    assert "already declares metadata.data_scope='regular'" in err_conflict["error"]
    assert [r for r in owner_harness["requests"] if r["method"] == "PATCH"] == []

    # 4. Non-dev environment fails closed before any write
    monkeypatch.setenv("PANTHEON_ENV", "prod")
    owner_harness["requests"].clear()
    rc_env = backfill.main(["--persona-id", CHAIN_PERSONA_CLAUDE, "--apply"])
    assert rc_env == 1
    err_env = json.loads(capsys.readouterr().out)
    assert "requires PANTHEON_ENV=dev" in err_env["error"]
    assert owner_harness["requests"] == []


def test_never_writes_database_or_calls_update_persona_or_prints_credentials(
    owner_harness: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = Path(backfill.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_modules = {"psycopg", "psycopg2", "asyncpg", "sqlite3", "sqlalchemy"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden_modules
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] not in forbidden_modules
        elif isinstance(node, ast.Attribute):
            assert node.attr != "update_persona"

    persona_owner: PersistentPersonaOwner = owner_harness["persona_owner"]
    _seed_chain_persona(
        persona_owner,
        CHAIN_PERSONA_CLAUDE,
        metadata={
            "requested_by": dict(REQUESTED_BY_OPERATOR),
            "service_token": "leak-me-not-secret-value",
        },
    )
    rc = backfill.main(["--persona-id", CHAIN_PERSONA_CLAUDE])
    assert rc == 0
    out = capsys.readouterr().out
    assert SERVICE_TOKEN not in out
    assert "leak-me-not-secret-value" not in out
    parsed = json.loads(out)
    assert (
        parsed["records"][0]["before"]["metadata"]["service_token"] == "[REDACTED]"
    )
