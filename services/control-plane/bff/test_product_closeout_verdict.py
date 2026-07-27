from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


MODULE_PATH = Path(__file__).with_name("product_closeout_verdict.py")
SPEC = importlib.util.spec_from_file_location("l12_product_closeout_bff", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bff_verdict = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bff_verdict
SPEC.loader.exec_module(bff_verdict)
gov = bff_verdict._governance


class FixedClock:
    def __call__(self) -> datetime:
        return datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def _request() -> dict:
    return {
        "program_id": "pantheon-twelve-loop-gap-2026-07-26",
        "catalog_sha256": "a" * 64,
        "task_id": "L12-CLOSE-001",
        "closeout_manifest_sha256": "b" * 64,
        "target_environment": "pantheon-lupin-dev",
        "frontend_sha": "c" * 40,
        "bff_sha": "d" * 40,
        "decision": "approved",
        "ttl_seconds": 180,
    }


def _identity(
    *,
    operator_id: str = "human-ops-001",
    roles: list[str] | None = None,
    mfa_verified: object = True,
    token_kind: str = "jwt",
    principal_type: str = "human",
    claims: object = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        operator_id=operator_id,
        roles=roles or ["admin"],
        mfa_verified=mfa_verified,
        token_kind=token_kind,
        claims={"principal_type": principal_type} if claims is None else claims,
    )


@pytest.fixture
def bff(tmp_path: Path) -> tuple[TestClient, dict, object]:
    private_key = gov.generate_private_key()
    policy = gov.VerdictPolicy(
        policy_version="product-closeout-v1",
        public_keys={"ops-key": gov.public_key_bytes(private_key)},
        max_verdict_age_seconds=300,
        max_ttl_seconds=300,
    )
    ledger = gov.ProtectedVerdictLedger(tmp_path / "protected" / "ledger.jsonl")
    service = gov.ProductCloseoutVerdictService(
        ledger=ledger,
        policy=policy,
        private_key=private_key,
        key_id="ops-key",
        clock=FixedClock(),
    )
    state = {"identity": _identity()}

    def resolve_identity() -> object:
        return state["identity"]

    app = FastAPI()
    app.include_router(
        bff_verdict.create_product_closeout_verdict_router(
            service=service,
            identity_dependency=resolve_identity,
        )
    )
    return TestClient(app), state, service


def test_authorized_mfa_human_ops_can_issue_and_inspect(bff: tuple) -> None:
    client, _, service = bff
    response = client.post(
        "/bff/governance/product-closeout-verdicts",
        json=_request(),
    )

    assert response.status_code == 201, response.text
    payload = response.json()
    verdict = payload["data"]
    assert verdict["actor_id"] == "human-ops-001"
    assert verdict["actor_role"] == "ops"
    assert verdict["signature_algorithm"] == "ed25519"
    assert payload["meta"]["protected_authority"] == "Human/Ops"
    assert service.inspect(verdict["verdict_id"])["consumption_count"] == 0

    readback = client.get(
        f"/bff/governance/product-closeout-verdicts/{verdict['verdict_id']}"
    )
    assert readback.status_code == 200
    assert readback.json()["data"]["verdict"] == verdict


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        (_identity(roles=["viewer"]), "approver or admin"),
        (_identity(mfa_verified=False), "MFA"),
        (_identity(mfa_verified="true"), "MFA"),
        (_identity(token_kind="stub"), "verified JWT"),
        (_identity(token_kind="test"), "verified JWT"),
        (_identity(token_kind="structured"), "verified JWT"),
        (_identity(token_kind="bearer"), "verified JWT"),
        (_identity(principal_type="service"), "automation"),
        (_identity(operator_id="Codex"), "fleet actors"),
        (_identity(operator_id="codex"), "fleet actors"),
        (_identity(operator_id="codex2_3"), "fleet actors"),
    ],
)
def test_wrong_role_stub_automation_and_fleet_identity_are_forbidden(
    bff: tuple,
    identity: object,
    expected: str,
) -> None:
    client, state, service = bff
    state["identity"] = identity

    response = client.post(
        "/bff/governance/product-closeout-verdicts",
        json=_request(),
    )

    assert response.status_code == 403
    assert expected in response.json()["detail"]["message"]
    assert service.ledger.records() == []


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        (_identity(claims={}), "explicit human principal"),
        (_identity(claims={"sub": "human-ops-001", "amr": ["mfa"]}), "explicit human"),
        (_identity(claims={"principal_type": ""}), "explicit human"),
        (_identity(claims={"principal_type": "   "}), "explicit human"),
        (
            SimpleNamespace(
                operator_id="human-ops-001",
                roles=["admin"],
                mfa_verified=True,
                token_kind="jwt",
            ),
            "explicit human",
        ),
        (_identity(claims="not-a-claims-object"), "explicit human"),
        (_identity(principal_type="robot"), "not a trusted human principal"),
        (_identity(principal_type="bot"), "not a trusted human principal"),
        (_identity(principal_type="workload"), "automation"),
        (_identity(principal_type="service_account"), "automation"),
        (_identity(principal_type="fleet"), "automation"),
        (
            _identity(claims={"principal_type": "human", "actor_type": "service"}),
            "conflicting principal classification",
        ),
        (_identity(claims={"actorType": "service"}), "automation"),
        (_identity(claims={"actorType": "human"}), None),
        (_identity(claims={"principalType": "user"}), None),
    ],
)
def test_principal_classification_must_be_explicit_and_human(
    bff: tuple,
    identity: object,
    expected: str | None,
) -> None:
    client, state, service = bff
    state["identity"] = identity

    response = client.post(
        "/bff/governance/product-closeout-verdicts",
        json=_request(),
    )

    if expected is None:
        assert response.status_code == 201, response.text
        assert len(service.ledger.records()) == 1
        return
    assert response.status_code == 403, response.text
    assert expected in response.json()["detail"]["message"]
    assert service.ledger.records() == []


def test_request_cannot_self_assert_actor_signature_or_ledger_fields(bff: tuple) -> None:
    client, _, service = bff
    candidate = {
        **_request(),
        "actor_id": "Codex",
        "actor_role": "ops",
        "signature": "candidate-self-signature",
        "ledger_entry_id": "candidate-ledger-entry",
        "verdict_id": "candidate-verdict",
        "nonce": "candidate-nonce-value",
    }

    response = client.post(
        "/bff/governance/product-closeout-verdicts",
        json=candidate,
    )

    assert response.status_code == 422
    assert service.ledger.records() == []


def test_revoke_route_uses_authenticated_identity_not_request_actor(bff: tuple) -> None:
    client, state, service = bff
    issued = client.post(
        "/bff/governance/product-closeout-verdicts",
        json=_request(),
    ).json()["data"]

    revoked = client.post(
        f"/bff/governance/product-closeout-verdicts/{issued['verdict_id']}/revoke",
        json={"reason": "hosted identity changed"},
    )

    assert revoked.status_code == 200
    record = revoked.json()["data"]
    assert record["actor_id"] == "human-ops-001"
    assert record["record_type"] == "revoked"
    assert service.inspect(issued["verdict_id"])["revoked"] is True

    state["identity"] = _identity(roles=["viewer"])
    denied = client.get(
        f"/bff/governance/product-closeout-verdicts/{issued['verdict_id']}"
    )
    assert denied.status_code == 403


def test_verification_only_service_cannot_be_abused_as_bff_issuer(
    tmp_path: Path,
) -> None:
    private_key = gov.generate_private_key()
    policy = gov.VerdictPolicy(
        policy_version="product-closeout-v1",
        public_keys={"ops-key": gov.public_key_bytes(private_key)},
    )
    service = gov.ProductCloseoutVerdictService(
        ledger=gov.ProtectedVerdictLedger(tmp_path / "protected" / "ledger.jsonl"),
        policy=policy,
        clock=FixedClock(),
    )

    def identity() -> object:
        return _identity()

    app = FastAPI()
    app.include_router(
        bff_verdict.create_product_closeout_verdict_router(
            service=service,
            identity_dependency=identity,
        )
    )
    response = TestClient(app).post(
        "/bff/governance/product-closeout-verdicts",
        json=_request(),
    )

    assert response.status_code == 403
    assert "no protected issuer capability" in response.json()["detail"]["message"]
