from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest import mock

from services.runtime_auth_inbound import encode_jwt_hs256


SERVICE_DIR = Path(__file__).resolve().parent
EXECUTION_DIR = SERVICE_DIR.parent / "execution" / "runtime-manager"
for path in (SERVICE_DIR, EXECUTION_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _load_main(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "PANTHEON_RUNTIME_BINDING_STORE_PATH", str(tmp_path / "bindings.json")
    )
    monkeypatch.setenv("PANTHEON_SINGLE_RUNTIME_ENFORCED", "true")
    sys.modules.pop("main", None)
    module = importlib.import_module("main")
    module._svc = None
    return module


def _paper_request() -> dict:
    return {
        "plan_id": "plan-paper-http",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-http",
        "artifact_version": "1.0.0",
        "strategy_id": "strategy-http",
        "approval_decision_id": "approval-http",
        "sponsor_persona_id": "persona-http",
        "capital_pool_id": "pool-http",
        "persona_capital_binding_id": "pcb-http",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "canary",
        "loader_checks_passed": True,
        "runtime_id": "runtime-http",
        "metadata": {
            "authoritative_loader_attestation": {
                "status": "passed",
                "authority": "canonical_deployment_registry_governance_capital",
            }
        },
    }


def _promotion_request(binding_id: str) -> dict:
    return {
        **_paper_request(),
        "current_binding_id": binding_id,
        "plan_id": "plan-canary-http",
        "target_stage": "canary",
        "human_gate_decision_id": "hgd-canary-http",
        "environment": "dev",
        "promotion_gate_decision_id": "hgd-canary-http",
        "human_gate_packet_ref": "packet://http",
        "broker_sandbox_smoke_ref": "evidence://broker-smoke",
        "risk_owner_approval_ref": "signature://risk",
        "operator_approval_ref": "signature://operator-a",
        "capital_scale_pct": 5.0,
        "gross_scale_pct": 25.0,
        "metadata": {
            "authoritative_promotion_attestation": {
                "status": "passed",
                "authority": "canonical_stage_promotion",
                "source_stage": "paper",
                "target_stage": "canary",
            }
        },
    }


def test_http_promotion_requires_mfa_from_verified_jwt_claim(tmp_path, monkeypatch):
    main = _load_main(tmp_path, monkeypatch)
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_CANARY_EXECUTION_ENABLED", "true")
    response = main.app.test_client().post(
        "/api/runtime-bindings/rb-paper-http/promote",
        headers={
            "Authorization": "Bearer operator-b:operator",
            "X-MFA-Token": "123456",
        },
        json=_promotion_request("rb-paper-http"),
    )

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "CLAIM_BOUND_MFA_REQUIRED"


def test_http_promotion_remains_disabled_until_stage_execution_is_enabled(
    tmp_path, monkeypatch
):
    main = _load_main(tmp_path, monkeypatch)
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", "promotion-http-secret")
    monkeypatch.delenv("PANTHEON_CANARY_EXECUTION_ENABLED", raising=False)
    token = encode_jwt_hs256(
        {"sub": "operator-b", "roles": ["operator"], "amr": ["pwd", "mfa"]},
        secret="promotion-http-secret",
    )
    response = main.app.test_client().post(
        "/api/runtime-bindings/rb-paper-http/promote",
        headers={"Authorization": f"Bearer {token}"},
        json=_promotion_request("rb-paper-http"),
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "STAGE_EXECUTION_DISABLED"


def test_http_promotion_calls_atomic_service_after_authority_passes(
    tmp_path, monkeypatch
):
    main = _load_main(tmp_path, monkeypatch)
    monkeypatch.setenv("PANTHEON_RUNTIME_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", "promotion-http-secret")
    monkeypatch.setenv("PANTHEON_CANARY_EXECUTION_ENABLED", "true")
    paper = main._get_service().deploy(_paper_request())
    body = _promotion_request(paper.binding_id)
    token = encode_jwt_hs256(
        {"sub": "operator-b", "roles": ["operator"], "amr": ["mfa"]},
        secret="promotion-http-secret",
    )

    with mock.patch.object(
        main, "_canonicalize_promotion_body", return_value=body
    ) as canonicalize:
        response = main.app.test_client().post(
            f"/api/runtime-bindings/{paper.binding_id}/promote",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )

    assert response.status_code == 201, response.get_data(as_text=True)
    result = response.get_json()
    assert result["operation"] == "stage_promotion"
    assert result["old_binding"]["status"] == "retired"
    assert result["new_binding"]["deployment_mode"] == "canary"
    canonicalize.assert_called_once()
