from __future__ import annotations

import sys
from pathlib import Path

import pytest


SERVICE_DIR = Path(__file__).resolve().parent
EXECUTION_DIR = SERVICE_DIR.parent / "execution" / "runtime-manager"
for path in (SERVICE_DIR, EXECUTION_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from service import RuntimeManagerError, RuntimeManagerService  # noqa: E402


def _paper_request() -> dict:
    return {
        "plan_id": "plan-paper-001",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-001",
        "artifact_version": "1.0.0",
        "strategy_id": "strategy-001",
        "approval_decision_id": "approval-001",
        "sponsor_persona_id": "persona-001",
        "capital_pool_id": "pool-001",
        "persona_capital_binding_id": "pcb-001",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "canary",
        "loader_checks_passed": True,
        "runtime_id": "runtime-001",
        "metadata": {
            "authoritative_loader_attestation": {
                "status": "passed",
                "authority": "canonical_deployment_registry_governance_capital",
            }
        },
    }


def _canary_request(source_binding_id: str) -> dict:
    return {
        **_paper_request(),
        "current_binding_id": source_binding_id,
        "plan_id": "plan-canary-001",
        "target_stage": "canary",
        "promotion_gate_decision_id": "hgd-canary-001",
        "human_gate_packet_ref": "packet://canary-001",
        "broker_sandbox_smoke_ref": "evidence://broker-smoke",
        "risk_owner_approval_ref": "signature://risk-owner",
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


def test_stage_promotion_atomically_retires_paper_and_activates_canary(tmp_path):
    service = RuntimeManagerService(store_path=tmp_path / "bindings.json")
    paper = service.deploy(_paper_request())

    result = service.promote_stage(_canary_request(paper.binding_id))

    assert result["operation"] == "stage_promotion"
    assert result["source_stage"] == "paper"
    assert result["target_stage"] == "canary"
    assert result["old_binding"]["status"] == "retired"
    assert result["new_binding"]["status"] == "active"
    assert result["new_binding"]["runtime_id"] == paper.runtime_id
    assert result["new_binding"]["artifact_id"] == paper.artifact_id
    assert (
        result["new_binding"]["metadata"]["stage_promotion_parent_binding_id"]
        == paper.binding_id
    )
    active = service.get_active_for_pool("pool-001")
    assert active is not None
    assert active.binding_id == result["new_binding"]["binding_id"]


def test_stage_promotion_replay_returns_existing_child(tmp_path):
    service = RuntimeManagerService(store_path=tmp_path / "bindings.json")
    paper = service.deploy(_paper_request())
    request = _canary_request(paper.binding_id)
    first = service.promote_stage(request)
    replay = service.promote_stage(request)

    assert replay["replayed"] is True
    assert replay["new_binding"]["binding_id"] == first["new_binding"]["binding_id"]
    assert len(service.list_by_pool("pool-001")) == 2


def test_stage_promotion_store_failure_restores_source_and_creates_no_child(
    tmp_path, monkeypatch
):
    service = RuntimeManagerService(store_path=tmp_path / "bindings.json")
    paper = service.deploy(_paper_request())

    def fail_save():
        raise OSError("simulated atomic snapshot failure")

    monkeypatch.setattr(service._store, "_save", fail_save)
    with pytest.raises(OSError, match="snapshot failure"):
        service.promote_stage(_canary_request(paper.binding_id))

    assert service.require(paper.binding_id).status == "active"
    assert len(service.list_by_pool("pool-001")) == 1


def test_stage_promotion_rejects_artifact_change(tmp_path):
    service = RuntimeManagerService(store_path=tmp_path / "bindings.json")
    paper = service.deploy(_paper_request())
    request = _canary_request(paper.binding_id)
    request["artifact_version"] = "2.0.0"

    with pytest.raises(RuntimeManagerError, match="preserve the source artifact"):
        service.promote_stage(request)
