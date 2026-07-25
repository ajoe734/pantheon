from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import main as bff_main
from command_queue import CommandStore
from read_store import ReadSurfaceStore
from rebalance_authority_test_support import CapitalBffAuthorityHarness


HEADERS = {"Authorization": "Bearer codex2-ppl-alloc:operator,reviewer"}
LIVE_PERSONA_ID = "persona-ppl-alloc-012-live"
LIVE_BINDING_ID = "binding-ppl-alloc-012-live"
LIVE_RUNTIME_ID = "runtime-ppl-alloc-012-live"
LIVE_SLEEVE_ID = "sleeve-ppl-alloc-012-live"
LIVE_ARCHETYPE = "ppl_alloc_live"
PAPER_PERSONA_ID = "persona-ppl-alloc-009-paper"
PAPER_BINDING_ID = "binding-ppl-alloc-009-paper"
PAPER_RUNTIME_ID = "runtime-ppl-alloc-009-paper"
PAPER_POOL_ID = "pool-ppl-alloc-009-paper"


def _browser_json_number_round_trip(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _browser_json_number_round_trip(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_browser_json_number_round_trip(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _client(td: str, *, fallback: bool = True) -> TestClient:
    bff_main.read_store = ReadSurfaceStore(
        os.path.join(td, "read-surfaces.json"),
        allow_local_snapshot_fallback=fallback,
    )
    bff_main._CAPITAL_BFF_IDEMPOTENCY.clear()
    return TestClient(bff_main.app, raise_server_exceptions=False)


def _write_live_binding(store: ReadSurfaceStore, *, current_weight: float) -> None:
    store.create_persona_binding(
        binding_id=LIVE_BINDING_ID,
        persona_id=LIVE_PERSONA_ID,
        capital_pool_id="pool-real",
        actor_id="Codex2",
        role="primary",
        validity="active",
        metadata={
            "allowed_deployment_scope": "live",
            "capital_mode": "live",
            "capital_sleeve_id": LIVE_SLEEVE_ID,
            "current_weight": current_weight,
        },
    )


def _seed_live_persona(store: ReadSurfaceStore) -> None:
    store.create_persona(
        persona_id=LIVE_PERSONA_ID,
        name="PPL Alloc Live",
        actor_id="Codex2",
        archetype=LIVE_ARCHETYPE,
        lifecycle_state="live_running",
        metadata={"capital_mode": "live", "deployment_stage": "live"},
    )
    _write_live_binding(store, current_weight=0.04)
    store.create_runtime_binding(
        runtime_id=LIVE_RUNTIME_ID,
        name="PPL Alloc Live",
        persona_id=LIVE_PERSONA_ID,
        binding_id=LIVE_BINDING_ID,
        deployment_plan_id="plan-ppl-alloc-012-live",
        runtime_kind="live",
        actor_id="Codex2",
        state="running",
        params={
            "capital_pool_id": "pool-real",
            "capital_sleeve_id": LIVE_SLEEVE_ID,
            "current_weight": 0.04,
        },
    )

    original_sessions = store.get_sessions_for_persona
    original_telemetry = store.get_telemetry_summary

    def sessions_for_persona(persona_id: str | None) -> list[dict[str, Any]] | None:
        if persona_id == LIVE_PERSONA_ID:
            return [
                {
                    "id": "session-ppl-alloc-012-live",
                    "status": "active",
                    "runtime_binding_id": LIVE_RUNTIME_ID,
                    "capital_pool_id": "pool-real",
                    "last_heartbeat_at": "2026-07-10T00:00:00Z",
                }
            ]
        return original_sessions(persona_id)

    def telemetry_for_runtime(runtime_id: str) -> dict[str, Any] | None:
        if runtime_id == LIVE_RUNTIME_ID:
            return {
                "runtime_id": LIVE_RUNTIME_ID,
                "pnl": 0.12,
                "drawdown": 0.03,
                "fill_rate": 0.98,
                "avg_slippage_bps": 1.2,
                "total_trades": 48,
                "collected_at": "2026-07-10T00:00:00Z",
            }
        return original_telemetry(runtime_id)

    store.get_sessions_for_persona = sessions_for_persona  # type: ignore[method-assign]
    store.get_telemetry_summary = telemetry_for_runtime  # type: ignore[method-assign]


def _item_by_persona(items: list[dict[str, Any]], persona_id: str) -> dict[str, Any]:
    return next(item for item in items if item.get("persona_id") == persona_id)


def _binding_evidence_ref(
    ref_id: str,
    *,
    binding_id: str,
) -> dict[str, Any]:
    captured_at = "2026-07-11T00:00:00Z"
    return {
        "id": ref_id,
        "ref_id": ref_id,
        "evidence_type": "metric",
        "display_label": ref_id,
        "source_document": {
            "title": ref_id,
            "source_type": "metric_snapshot",
            "source_ref": f"metric://{binding_id}",
            "captured_at": captured_at,
        },
        "linked_object_summary": {
            "entity_type": "persona_binding",
            "entity_ref": binding_id,
            "display_label": binding_id,
        },
        "credibility": {"tier": "primary", "verified": True},
        "created_at": captured_at,
    }


def _install_evidence_records(
    store: ReadSurfaceStore,
    records: list[dict[str, Any]],
) -> None:
    def list_evidence_refs(**_: Any) -> list[dict[str, Any]]:
        return [dict(record) for record in records]

    store.list_evidence_refs = list_evidence_refs  # type: ignore[method-assign]


def _install_runtime_observations(
    store: ReadSurfaceStore,
    *,
    persona_id: str,
    runtime_id: str,
) -> None:
    original_sessions = store.get_sessions_for_persona
    original_telemetry = store.get_telemetry_summary
    original_paper_monitoring = (
        store.list_authoritative_paper_runtime_monitoring_sessions
    )

    def sessions_for_persona(target_persona_id: str | None) -> list[dict[str, Any]] | None:
        if target_persona_id == persona_id:
            return [
                {
                    "id": f"session-{runtime_id}",
                    "status": "active",
                    "runtime_binding_id": runtime_id,
                    "last_heartbeat_at": "2026-07-10T00:00:00Z",
                }
            ]
        return original_sessions(target_persona_id)

    def telemetry_for_runtime(target_runtime_id: str) -> dict[str, Any] | None:
        if target_runtime_id == runtime_id:
            return {
                "runtime_id": runtime_id,
                "pnl": 0.08,
                "drawdown": 0.02,
                "fill_rate": 0.99,
                "avg_slippage_bps": 0.8,
                "total_trades": 32,
                "collected_at": "2026-07-10T00:00:00Z",
            }
        return original_telemetry(target_runtime_id)

    def paper_monitoring_sessions() -> list[dict[str, Any]]:
        return [
            *original_paper_monitoring(),
            {
                "id": f"paper-monitoring-{runtime_id}",
                "session_id": f"paper-monitoring-{runtime_id}",
                "session_type": "paper_runtime_monitoring",
                "status": "running",
                "deployment_stage": "paper",
                "runtime_id": runtime_id,
                "last_heartbeat_at": "2026-07-10T00:00:00Z",
            },
        ]

    store.get_sessions_for_persona = sessions_for_persona  # type: ignore[method-assign]
    store.get_telemetry_summary = telemetry_for_runtime  # type: ignore[method-assign]
    store.list_authoritative_paper_runtime_monitoring_sessions = (  # type: ignore[method-assign]
        paper_monitoring_sessions
    )


def _strict_test_identity(
    authorization: str | None,
    mfa_token: str | None = None,
    session_cookie: str | None = None,
) -> bff_main.OperatorIdentity:
    del session_cookie
    assert authorization and authorization.startswith("Bearer ")
    parts = authorization.removeprefix("Bearer ").split(":")
    roles = parts[1].split(",") if len(parts) > 1 else ["viewer"]
    return bff_main.OperatorIdentity(
        operator_id=parts[0],
        roles=roles,
        mfa_verified=bool(mfa_token) or "mfa" in parts[2:],
        claims={"sub": parts[0], "roles": roles},
        token_kind="structured",
    )


def test_snapshot_id_is_stable_across_semantically_equivalent_item_ordering() -> None:
    items = [
        {
            "persona_id": "persona-snapshot-a",
            "rank": 1,
            "overall_score": 91.0,
            "runtime_ids": ["runtime-a-1", "runtime-a-2"],
            "metrics": {
                "runtime_ids": ["runtime-a-1", "runtime-a-2"],
                "telemetry_evidence_refs": [
                    {"ref_id": "telemetry-a-1", "runtime_id": "runtime-a-1"},
                    {"ref_id": "telemetry-a-2", "runtime_id": "runtime-a-2"},
                ],
                "pnl": 0.12,
            },
            "stage": "live_running",
            "current_weight": 0.08,
            "eligible": True,
        },
        {
            "persona_id": "persona-snapshot-b",
            "rank": 2,
            "overall_score": 84.0,
            "runtime_ids": ["runtime-b-1", "runtime-b-2"],
            "metrics": {
                "runtime_ids": ["runtime-b-1", "runtime-b-2"],
                "telemetry_evidence_refs": [
                    {"ref_id": "telemetry-b-1", "runtime_id": "runtime-b-1"},
                    {"ref_id": "telemetry-b-2", "runtime_id": "runtime-b-2"},
                ],
                "pnl": 0.07,
            },
            "stage": "canary_running",
            "current_weight": 0.03,
            "eligible": True,
        },
    ]
    permuted_items = [
        {
            **item,
            "runtime_ids": list(reversed(item["runtime_ids"])),
            "metrics": {
                **item["metrics"],
                "runtime_ids": list(reversed(item["metrics"]["runtime_ids"])),
                "telemetry_evidence_refs": list(
                    reversed(item["metrics"]["telemetry_evidence_refs"])
                ),
            },
        }
        for item in reversed(items)
    ]

    snapshot_id = bff_main._pm12_ranking_snapshot_id(
        items,
        surface="quarterly",
        period="2026-Q3",
    )
    permuted_snapshot_id = bff_main._pm12_ranking_snapshot_id(
        permuted_items,
        surface="quarterly",
        period="2026-Q3",
    )
    assert permuted_snapshot_id == snapshot_id


def test_ppl_alloc_009_governed_paper_chain_applies_without_two_man(
    tmp_path: Path,
    monkeypatch,
) -> None:
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False) as harness:
        assert harness.client is not None
        assert harness.capital_client is not None
        client = harness.client

        pool = client.post(
            "/bff/capital-pools",
            headers={
                "Authorization": "Bearer op-2:operator",
                "Idempotency-Key": "ppl-alloc-009-paper-pool",
            },
            json={
                "pool_id": PAPER_POOL_ID,
                "name": "PPL-ALLOC-009 isolated paper ledger",
                "owner_id": "tenant-dev",
                "owner_type": "org",
                "status": "active",
                "single_runtime_enforced": True,
                "metadata": {
                    "internal": True,
                    "execution_context": "paper",
                    "tenant_id": "tenant-dev",
                    "persona_id": PAPER_PERSONA_ID,
                },
            },
        )
        assert pool.status_code == 201, pool.text
        binding = client.post(
            "/api/v1/bindings",
            headers={
                "Authorization": "Bearer op-2:operator",
                "Idempotency-Key": "ppl-alloc-009-paper-binding",
            },
            json={
                "binding_id": PAPER_BINDING_ID,
                "persona_id": PAPER_PERSONA_ID,
                "capital_pool_id": PAPER_POOL_ID,
                "capital_sleeve_id": None,
                "role": "paper_owner",
                "allowed_deployment_scope": "paper",
            },
        )
        assert binding.status_code == 201, binding.text
        activated = harness.capital_client.post(
            f"/api/bindings/{PAPER_BINDING_ID}/activate",
                json={
                    "actor_id": "governance-test",
                    "actor_role": "persona.admin",
                    "approval_decision_id": "approval-paper-admission",
                },
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["status"] == "active"

        store = bff_main.read_store
        assert isinstance(store, ReadSurfaceStore)
        store.create_persona(
            persona_id=PAPER_PERSONA_ID,
            name="PPL Alloc 009 Paper",
            actor_id="persona-registry",
            archetype="ppl_alloc_paper",
            lifecycle_state="paper_running",
            risk_level="low",
            metadata={
                "capital_mode": "paper",
                "deployment_stage": "paper",
                "binding_id": PAPER_BINDING_ID,
            },
        )
        store.create_runtime_binding(
            runtime_id=PAPER_RUNTIME_ID,
            name="PPL Alloc 009 Paper",
            persona_id=PAPER_PERSONA_ID,
            binding_id=PAPER_BINDING_ID,
            deployment_plan_id="plan-ppl-alloc-009-paper",
            runtime_kind="paper",
            actor_id="runtime-manager",
            state="running",
            params={"capital_pool_id": PAPER_POOL_ID},
        )
        original_telemetry = store.get_telemetry_summary

        store.list_authoritative_paper_runtime_monitoring_sessions = lambda: [
            {
                "id": "session-ppl-alloc-009-paper",
                "session_id": "session-ppl-alloc-009-paper",
                "session_type": "paper_runtime_monitoring",
                "status": "running",
                "deployment_stage": "paper",
                "runtime_id": PAPER_RUNTIME_ID,
                "runtime_binding_id": PAPER_BINDING_ID,
                "capital_pool_id": PAPER_POOL_ID,
                "last_heartbeat_at": "2026-07-24T00:00:00Z",
            }
        ]  # type: ignore[method-assign]

        def telemetry_for_runtime(runtime_id: str) -> dict[str, Any] | None:
            if runtime_id == PAPER_RUNTIME_ID:
                return {
                    "runtime_id": PAPER_RUNTIME_ID,
                    "pnl": 0.80,
                    "drawdown": 0.01,
                    "fill_rate": 0.99,
                    "avg_slippage_bps": 0.5,
                    "total_trades": 64,
                    "collected_at": "2026-07-24T00:00:00Z",
                }
            return original_telemetry(runtime_id)

        store.get_telemetry_summary = telemetry_for_runtime  # type: ignore[method-assign]

        monkeypatch.setenv("PANTHEON_ENV", "dev")
        monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "strict")
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "false")
        monkeypatch.setenv("PANTHEON_LIVE_BROKER_ENABLED", "false")
        monkeypatch.setenv("PANTHEON_CANARY_EXECUTION_ENABLED", "false")
        monkeypatch.setattr(bff_main, "_extract_identity", _strict_test_identity)
        operator_headers = {
            "Authorization": "Bearer paper-operator:operator:mfa",
        }
        approver_headers = {
            "Authorization": "Bearer paper-approver:approver:mfa",
        }

        quarterly = client.get(
            "/bff/management/quarterly-ranking",
            headers=operator_headers,
            params={"quarter": "2026-Q3", "page_size": 200},
        )
        assert quarterly.status_code == 200, quarterly.text
        quarterly_data = quarterly.json()["data"]
        snapshot_id = quarterly_data["ranking_snapshot_id"]
        row = _item_by_persona(quarterly_data["items"], PAPER_PERSONA_ID)
        assert row["stage"] == "paper_running"
        assert row["capital_scope"] == "paper_ledger"
        assert row["capital_pool_id"] is None
        assert row["capital_sleeve_id"] is None
        assert row["eligible"] is True
        assert row["session_id"] == "session-ppl-alloc-009-paper"
        assert (
            row["session_authority"]
            == "runtime_manager.paper_fleet_monitoring"
        )
        paper_bindings = store.list_bindings(
            persona_id=PAPER_PERSONA_ID,
            role="paper_owner",
        )
        assert len(paper_bindings) == 1, (
            row.get("binding_ids"),
            paper_bindings,
        )
        assert paper_bindings[0]["status"] == "active"
        assert paper_bindings[0]["allowed_deployment_scope"] == "paper"
        assert paper_bindings[0]["capital_sleeve_id"] is None
        assert PAPER_BINDING_ID in row["binding_ids"], row

        recommendations = client.get(
            "/bff/management/quarterly-ranking/recommendations",
            headers=operator_headers,
            params={
                "quarter": "2026-Q3",
                "personaId": PAPER_PERSONA_ID,
                "page_size": 200,
            },
        )
        assert recommendations.status_code == 200, recommendations.text
        recommendation = next(
            item
            for item in recommendations.json()["data"]["items"]
            if item["action_id"] == "promote_to_canary_candidate"
        )
        review_id = recommendation["recommendation_id"]
        submitted = client.post(
            (
                "/bff/management/quarterly-ranking/recommendations/"
                f"{review_id}/submit"
            ),
            headers={
                **operator_headers,
                "Idempotency-Key": "ppl-alloc-009-promotion-submit",
            },
            json={
                "quarter": "2026-Q3",
                "ranking_snapshot_id": snapshot_id,
            },
        )
        assert submitted.status_code == 202, submitted.text
        decided = client.post(
            f"/bff/management/promotion-reviews/{review_id}/decisions",
            headers={
                **approver_headers,
                "Idempotency-Key": "ppl-alloc-009-promotion-decision",
            },
            json={
                "quarter": "2026-Q3",
                "decision": "approve",
                "rationale": "Approve governed paper-only simulation",
            },
        )
        assert decided.status_code == 202, decided.text

        evaluated = client.post(
            "/bff/management/allocation-policy/evaluate",
            headers={**operator_headers, "X-MFA-Token": "mfa-paper-evaluate"},
            json={
                "ranking_snapshot_id": snapshot_id,
                "rows": [row],
                "authority_mode": "governed_paper_simulation",
                "promotion_review_id": review_id,
            },
        )
        assert evaluated.status_code == 200, evaluated.text
        evaluation = evaluated.json()["data"]
        assert evaluation["allocation_policy_version"] == (
            "persona-paper-allocation-simulation-v1"
        )
        line = evaluation["lines"][0]
        assert line["capital_pool_id"] == PAPER_POOL_ID
        assert line["binding_id"] == PAPER_BINDING_ID
        assert line["target_weight"] == 1.0
        assert line["live_capital_side_effects"] is False
        browser_lines = _browser_json_number_round_trip(evaluation["lines"])
        assert browser_lines[0]["current_weight"] == 0
        assert isinstance(browser_lines[0]["current_weight"], int)
        assert browser_lines[0]["target_weight"] == 1
        assert isinstance(browser_lines[0]["target_weight"], int)

        proposal_payload = {
            "capital_pool_id": PAPER_POOL_ID,
            "ranking_snapshot_id": snapshot_id,
            "allocation_evaluation_id": evaluation["allocation_evaluation_id"],
            "allocation_policy_version": evaluation["allocation_policy_version"],
            "reason": "PPL-ALLOC-009 governed paper allocation",
            "lines": browser_lines,
            "simulation": {
                "status": "passed",
                "authority_mode": "governed_paper_simulation",
            },
            "constraints": {
                "paper_only": True,
                "live_capital_enabled": False,
                "canary_execution_enabled": False,
            },
            "rollback_target": {
                "paper_ledger_id": row["paper_ledger_id"],
                "current_weight": 0.0,
            },
            "audit_refs": [
                f"promotion_review:{review_id}",
                f"ranking_snapshot:{snapshot_id}",
            ],
        }
        proposed = client.post(
            "/bff/rebalances",
            headers={
                **operator_headers,
                "Idempotency-Key": "ppl-alloc-009-paper-proposal",
            },
            json=proposal_payload,
        )
        assert proposed.status_code == 202, proposed.text
        rebalance_id = proposed.json()["rebalance_id"]
        approved = client.post(
            f"/bff/rebalances/{rebalance_id}/approve",
            headers={
                **approver_headers,
                "Idempotency-Key": "ppl-alloc-009-paper-approval",
            },
            json={
                "approval_decision_id": "approval-ppl-alloc-009-paper",
                "memo": "Approve paper-only allocation apply",
            },
        )
        assert approved.status_code == 201, approved.text
        approval_id = approved.json()["data"]["approval_decision_id"]

        same_actor_token = client.post(
            "/bff/confirm-tokens",
            headers={
                **approver_headers,
                "Idempotency-Key": "ppl-alloc-009-approver-confirm",
            },
            json={
                "tokenId": "ct-ppl-alloc-009-approver",
                "command": "ApprovedApply",
                "target": {"type": "Rebalance", "id": rebalance_id},
                "operator_id": "paper-approver",
                "reason": "Negative separation test",
            },
        )
        assert same_actor_token.status_code == 201, same_actor_token.text
        same_actor_apply = client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            headers={
                **approver_headers,
                "X-MFA-Token": "mfa-paper-apply",
                "X-Confirm-Token": "ct-ppl-alloc-009-approver",
                "Idempotency-Key": "ppl-alloc-009-same-actor-apply",
            },
            json={"approval_decision_id": approval_id},
        )
        assert same_actor_apply.status_code == 409, same_actor_apply.text
        assert (
            same_actor_apply.json()["error"]["details"]["reason"]
            == "PAPER_SIMULATION_APPROVAL_APPLY_NOT_DISTINCT"
        )

        confirmed = client.post(
            "/bff/confirm-tokens",
            headers={
                **operator_headers,
                "Idempotency-Key": "ppl-alloc-009-operator-confirm",
            },
            json={
                "tokenId": "ct-ppl-alloc-009-operator",
                "command": "ApprovedApply",
                "target": {"type": "Rebalance", "id": rebalance_id},
                "operator_id": "paper-operator",
                "reason": "Confirm governed paper allocation",
            },
        )
        assert confirmed.status_code == 201, confirmed.text
        applied = client.post(
            f"/bff/rebalances/{rebalance_id}/apply",
            headers={
                **operator_headers,
                "X-MFA-Token": "mfa-paper-apply",
                "X-Confirm-Token": "ct-ppl-alloc-009-operator",
                "Idempotency-Key": "ppl-alloc-009-paper-apply",
            },
            json={"approval_decision_id": approval_id},
        )
        assert applied.status_code == 202, applied.text
        apply_command_id = applied.json()["data"]["command_id"]
        receipt = client.get(
            f"/api/v1/operator/commands/{apply_command_id}",
            headers=operator_headers,
        )
        assert receipt.status_code == 200, receipt.text
        assert receipt.json()["status"] == "executed"
        result = receipt.json()["result"]
        assert result["authoritative_capital_readback"] is True
        assert result["live_capital_side_effects"] is False
        assert result["allocation_readback"][0]["current_weight"] == 1.0
        assert result["allocation_readback"][0]["binding_id"] == PAPER_BINDING_ID

        stored = bff_main.command_store.get_command(apply_command_id)
        assert stored is not None
        preconditions = stored["audit"]["precondition_evidence"]
        assert preconditions["approval_decision_id"] == approval_id
        assert preconditions["paper_simulation_authority"] == (
            "governed_paper_simulation"
        )
        assert "two_man_signature_id" not in preconditions


def test_allocation_line_assertion_hash_is_numeric_semantic_and_fail_closed() -> None:
    admitted = {
        "current_weight": 0.0,
        "target_weight": 1.0,
        "delta": 1.0,
        "nested": {
            "score": Decimal("86.500"),
            "values": [0.0, 1.0, -0.0],
        },
        "requires_human_approval": True,
    }
    browser_round_trip = _browser_json_number_round_trip(admitted)
    assert (
        bff_main._pm12_allocation_line_assertion_hash(browser_round_trip)
        == bff_main._pm12_allocation_line_assertion_hash(admitted)
    )

    rejected = (
        {**browser_round_trip, "target_weight": 2},
        {**browser_round_trip, "requires_human_approval": 1},
        {**browser_round_trip, "target_weight": float("nan")},
        {**browser_round_trip, "target_weight": float("inf")},
        {**browser_round_trip, "target_weight": float("-inf")},
    )
    for forged in rejected:
        try:
            forged_hash = bff_main._pm12_allocation_line_assertion_hash(forged)
        except ValueError:
            continue
        assert forged_hash != bff_main._pm12_allocation_line_assertion_hash(
            admitted
        )


def test_ranking_tuple_and_snapshot_round_trip_into_rebalance_proposal() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        harness: CapitalBffAuthorityHarness | None = None
        try:
            harness = CapitalBffAuthorityHarness(Path(td))
            harness.__enter__()
            assert harness.client is not None
            client = harness.client
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            owner_binding = client.post(
                "/api/v1/bindings",
                headers={
                    "Authorization": "Bearer codex2-ppl-alloc:operator",
                    "Idempotency-Key": "ppl-alloc-012-owner-binding",
                },
                json={
                    "binding_id": LIVE_BINDING_ID,
                    "persona_id": LIVE_PERSONA_ID,
                    "capital_pool_id": "pool-real",
                    "capital_sleeve_id": LIVE_SLEEVE_ID,
                    "role": "live_owner",
                    "allowed_deployment_scope": "live",
                },
            )
            assert owner_binding.status_code == 201, owner_binding.text
            _seed_live_persona(store)

            quarterly = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert quarterly.status_code == 200, quarterly.text
            quarterly_body = quarterly.json()
            snapshot_id = quarterly_body["data"]["ranking_snapshot_id"]
            assert snapshot_id.startswith("ranking-quarterly-2026-q3-")
            assert quarterly_body["data"]["summary"]["ranking_snapshot_id"] == snapshot_id
            assert quarterly_body["meta"]["ranking_snapshot_id"] == snapshot_id
            assert {
                item["ranking_snapshot_id"]
                for item in quarterly_body["data"]["items"]
            } == {snapshot_id}

            live_row = _item_by_persona(
                quarterly_body["data"]["items"],
                LIVE_PERSONA_ID,
            )
            assert live_row["stage"] == "live_running"
            assert live_row["capital_scope"] == "live_sleeve"
            assert live_row["capital_scope_id"] == LIVE_SLEEVE_ID
            assert live_row["capital_pool_id"] == "pool-real"
            assert live_row["capital_sleeve_id"] == LIVE_SLEEVE_ID
            assert live_row["current_weight"] == 0.04
            assert live_row["current_weight_source"] == "persona_binding"
            assert live_row["eligible"] is True
            assert live_row["exclusion_reasons"] == []
            assert "telemetry-summary:runtime-ppl-alloc-012-live" in {
                ref["ref_id"] for ref in live_row["evidence_refs"]
            }

            filtered = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "q": "PPL Alloc Live",
                    "page_size": 1,
                },
            )
            repeated = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert filtered.status_code == repeated.status_code == 200
            filtered_body = filtered.json()
            assert filtered_body["data"]["ranking_snapshot_id"] == snapshot_id
            assert filtered_body["page_info"]["total"] == 1
            assert len(filtered_body["data"]["items"]) == 1
            assert filtered_body["data"]["items"][0]["persona_id"] == LIVE_PERSONA_ID
            filtered_summary = filtered_body["data"]["summary"]
            assert filtered_summary["top_persona_id"] == LIVE_PERSONA_ID
            assert filtered_summary["persona_count"] == filtered_body["page_info"]["total"]
            assert filtered_summary["ranked_count"] == filtered_body["page_info"]["total"]
            assert filtered_summary["ranking_universe_count"] == len(
                quarterly_body["data"]["items"]
            )
            assert repeated.json()["data"]["ranking_snapshot_id"] == snapshot_id
            admin_view = client.get(
                "/bff/management/quarterly-ranking",
                headers={"Authorization": "Bearer ppl-alloc-admin:admin"},
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert admin_view.status_code == 200, admin_view.text
            assert admin_view.json()["data"]["ranking_snapshot_id"] == snapshot_id

            drilldown = client.get(
                "/bff/management/quarterly-ranking/drilldown",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "persona_id": LIVE_PERSONA_ID},
            )
            assert drilldown.status_code == 200, drilldown.text
            drilldown_body = drilldown.json()
            assert drilldown_body["data"]["ranking_snapshot_id"] == snapshot_id
            assert drilldown_body["data"]["ranking_item"]["ranking_snapshot_id"] == snapshot_id
            assert drilldown_body["summary"]["ranking_snapshot_id"] == snapshot_id
            assert drilldown_body["meta"]["ranking_snapshot_id"] == snapshot_id

            recommendations = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert recommendations.status_code == 200, recommendations.text
            recommendation_body = recommendations.json()
            assert recommendation_body["data"]["ranking_snapshot_id"] == snapshot_id
            assert recommendation_body["meta"]["ranking_snapshot_id"] == snapshot_id
            assert recommendation_body["data"]["items"]
            for recommendation in recommendation_body["data"]["items"]:
                assert recommendation["ranking_snapshot_id"] == snapshot_id
                assert recommendation["stage"] == live_row["stage"]
                assert recommendation["current_weight"] == live_row["current_weight"]
                assert recommendation["capital_scope"] == live_row["capital_scope"]
                assert recommendation["evidence_refs"] == live_row["evidence_refs"][:5]

            rolling = client.get(
                "/bff/management/persona-league",
                headers=HEADERS,
                params={"page_size": 200},
            )
            rolling_rankings = client.get(
                "/bff/management/persona-league/rankings",
                headers=HEADERS,
                params={"criteria": "overall", "limit": 200},
            )
            assert rolling.status_code == rolling_rankings.status_code == 200
            rolling_snapshot_id = rolling.json()["data"]["ranking_snapshot_id"]
            assert rolling_snapshot_id.startswith("ranking-rolling-short-cycle-")
            assert rolling_rankings.json()["data"]["ranking_snapshot_id"] == rolling_snapshot_id
            assert rolling_snapshot_id != snapshot_id
            rolling_row = _item_by_persona(rolling.json()["data"]["items"], LIVE_PERSONA_ID)
            rolling_rank = _item_by_persona(
                rolling_rankings.json()["data"]["items"][0]["items"],
                LIVE_PERSONA_ID,
            )
            assert rolling_row["stage"] == rolling_rank["stage"] == "live_running"
            assert rolling_row["current_weight"] == rolling_rank["current_weight"] == 0.04
            assert rolling_rank["ranking_snapshot_id"] == rolling_snapshot_id

            evaluation = client.post(
                "/bff/management/allocation-policy/evaluate",
                headers=HEADERS,
                json={"ranking_snapshot_id": snapshot_id, "rows": [live_row]},
            )
            assert evaluation.status_code == 200, evaluation.text
            evaluation_body = evaluation.json()
            line = evaluation_body["data"]["lines"][0]
            assert evaluation_body["meta"]["ranking_snapshot_id"] == snapshot_id
            evaluation_id = evaluation_body["data"]["allocation_evaluation_id"]
            policy_version = evaluation_body["data"]["allocation_policy_version"]
            for field in (
                "ranking_snapshot_id",
                "persona_id",
                "stage",
                "capital_scope",
                "capital_pool_id",
                "capital_sleeve_id",
                "current_weight",
            ):
                assert line[field] == live_row[field]
            assert line["evidence_refs"] == live_row["evidence_ref_ids"]

            row_tamper_cases = {
                "stage": "paper_running",
                "current_weight": 0.99,
                "target_weight": 0.99,
                "delta": 0.99,
                "allocation_policy_input": {},
                "evidence_ref_ids": [*live_row["evidence_ref_ids"], "forged-evidence"],
                "evidence_refs": [
                    *live_row["evidence_refs"],
                    {"ref_id": "forged-evidence"},
                ],
            }
            for field, forged_value in row_tamper_cases.items():
                tampered = client.post(
                    "/bff/management/allocation-policy/evaluate",
                    headers=HEADERS,
                    json={
                        "ranking_snapshot_id": snapshot_id,
                        "rows": [{**live_row, field: forged_value}],
                    },
                )
                assert tampered.status_code == 422, (field, tampered.text)

            reloaded = ReadSurfaceStore(
                str(harness.read_path),
                allow_local_snapshot_fallback=False,
            )
            assert reloaded.get_ranking_snapshot(snapshot_id) is not None
            assert reloaded.get_allocation_evaluation(evaluation_id) is not None
            harness.restart()
            assert harness.client is not None
            client = harness.client

            proposal = client.post(
                "/bff/rebalances",
                headers={**HEADERS, "Idempotency-Key": "ppl-alloc-012-proposal"},
                json={
                    "capital_pool_id": "pool-real",
                    "ranking_snapshot_id": snapshot_id,
                    "allocation_evaluation_id": evaluation_id,
                    "allocation_policy_version": policy_version,
                    "reason": "PPL-ALLOC-012 round trip",
                    "lines": evaluation_body["data"]["lines"],
                    "simulation": {"status": "passed"},
                    "constraints": {"pool_total_max": 1},
                    "rollback_target": {"snapshot_id": "allocation-before-ppl-alloc-012"},
                },
            )
            assert proposal.status_code == 202, proposal.text
            assert proposal.json()["ranking_snapshot_id"] == snapshot_id
            detail = client.get(
                f"/bff/rebalances/{proposal.json()['rebalance_id']}",
                headers=HEADERS,
            )
            assert detail.status_code == 200, detail.text
            detail_data = detail.json()["data"]
            assert detail_data["ranking_snapshot_id"] == snapshot_id
            assert detail_data["allocation_evaluation_id"] == evaluation_id
            assert detail_data["allocation_policy_version"] == policy_version
            assert detail_data["lines"][0]["ranking_snapshot_id"] == snapshot_id
            assert detail_data["lines"][0]["evidence_refs"] == live_row["evidence_ref_ids"]
            for field in (
                "current_weight",
                "target_weight",
                "delta",
                "cap_reasons",
                "allocation_line_digest",
            ):
                assert detail_data["lines"][0][field] == line[field]

            proposal_tamper_cases = (
                ("current_weight", 0.03, False),
                ("target_weight", 0.08, True),
                ("delta", 0.04, False),
                ("cap_reasons", ["forged-cap"], True),
                (
                    "evidence_refs",
                    [*line["evidence_refs"], "forged-evidence"],
                    False,
                ),
                ("requires_human_approval", 1, False),
                ("allocation_line_digest", "0" * 64, False),
            )
            for index, (field, forged_value, recompute_digest) in enumerate(
                proposal_tamper_cases
            ):
                forged_line = {**line, field: forged_value}
                if recompute_digest:
                    forged_line.pop("allocation_line_digest", None)
                    forged_line["allocation_line_digest"] = bff_main._pm12_allocation_line_digest(
                        forged_line
                    )
                tampered = client.post(
                    "/bff/rebalances",
                    headers={
                        **HEADERS,
                        "Idempotency-Key": f"ppl-alloc-012-line-tamper-{index}",
                    },
                    json={
                        "capital_pool_id": "pool-real",
                        "ranking_snapshot_id": snapshot_id,
                        "allocation_evaluation_id": evaluation_id,
                        "allocation_policy_version": policy_version,
                        "reason": "PPL-ALLOC-012 tamper rejection",
                        "lines": [forged_line],
                        "simulation": {"status": "passed"},
                        "constraints": {"pool_total_max": 1},
                        "rollback_target": {
                            "snapshot_id": "allocation-before-ppl-alloc-012"
                        },
                    },
                )
                assert tampered.status_code == 422, (field, tampered.text)

            missing_snapshot = client.post(
                "/bff/management/allocation-policy/evaluate",
                headers=HEADERS,
                json={"rows": [live_row]},
            )
            unknown_snapshot = client.post(
                "/bff/management/allocation-policy/evaluate",
                headers=HEADERS,
                json={
                    "ranking_snapshot_id": "ranking-quarterly-forged",
                    "rows": [
                        {
                            **live_row,
                            "ranking_snapshot_id": "ranking-quarterly-forged",
                        }
                    ],
                },
            )
            mixed_snapshot = client.post(
                "/bff/management/allocation-policy/evaluate",
                headers=HEADERS,
                json={
                    "ranking_snapshot_id": snapshot_id,
                    "rows": [{**live_row, "ranking_snapshot_id": "ranking-quarterly-other"}],
                },
            )
            missing_row_snapshot = client.post(
                "/bff/management/allocation-policy/evaluate",
                headers=HEADERS,
                json={
                    "ranking_snapshot_id": snapshot_id,
                    "rows": [
                        {
                            key: value
                            for key, value in live_row.items()
                            if key != "ranking_snapshot_id"
                        }
                    ],
                },
            )
            mismatched_proposal = client.post(
                "/bff/rebalances",
                headers={**HEADERS, "Idempotency-Key": "ppl-alloc-012-mismatch"},
                json={
                    "capital_pool_id": "pool-real",
                    "ranking_snapshot_id": snapshot_id,
                    "allocation_evaluation_id": evaluation_id,
                    "allocation_policy_version": policy_version,
                    "lines": [{**line, "ranking_snapshot_id": "ranking-quarterly-other"}],
                    "simulation": {"status": "passed"},
                    "constraints": {"pool_total_max": 1},
                    "rollback_target": {"snapshot_id": "allocation-before-ppl-alloc-012"},
                },
            )
            missing_line_snapshot = client.post(
                "/bff/rebalances",
                headers={
                    **HEADERS,
                    "Idempotency-Key": "ppl-alloc-012-missing-line-snapshot",
                },
                json={
                    "capital_pool_id": "pool-real",
                    "ranking_snapshot_id": snapshot_id,
                    "allocation_evaluation_id": evaluation_id,
                    "allocation_policy_version": policy_version,
                    "lines": [
                        {
                            key: value
                            for key, value in line.items()
                            if key != "ranking_snapshot_id"
                        }
                    ],
                    "simulation": {"status": "passed"},
                    "constraints": {"pool_total_max": 1},
                    "rollback_target": {"snapshot_id": "allocation-before-ppl-alloc-012"},
                },
            )
            assert missing_snapshot.status_code == 422
            assert unknown_snapshot.status_code == 422
            assert mixed_snapshot.status_code == 422
            assert missing_row_snapshot.status_code == 422
            assert mismatched_proposal.status_code == 422
            assert missing_line_snapshot.status_code == 422
        finally:
            if harness is not None:
                harness.__exit__(None, None, None)
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store


def test_durable_lineage_integrity_fails_closed_after_same_id_store_tamper() -> None:
    for tamper_target in ("ranking_snapshot", "allocation_evaluation"):
        with tempfile.TemporaryDirectory() as td:
            original_store = bff_main.read_store
            try:
                client = _client(td, fallback=False)
                store = bff_main.read_store
                assert isinstance(store, ReadSurfaceStore)
                _seed_live_persona(store)
                ranking = client.get(
                    "/bff/management/quarterly-ranking",
                    headers=HEADERS,
                    params={"quarter": "2026-Q3", "page_size": 200},
                )
                assert ranking.status_code == 200, ranking.text
                snapshot_id = ranking.json()["data"]["ranking_snapshot_id"]
                live_row = _item_by_persona(
                    ranking.json()["data"]["items"],
                    LIVE_PERSONA_ID,
                )
                evaluated = client.post(
                    "/bff/management/allocation-policy/evaluate",
                    headers=HEADERS,
                    json={"ranking_snapshot_id": snapshot_id, "rows": [live_row]},
                )
                assert evaluated.status_code == 200, evaluated.text
                evaluation = evaluated.json()["data"]

                if tamper_target == "ranking_snapshot":
                    stored_items = store._data["ranking_snapshots"][snapshot_id][
                        "items"
                    ]
                    stored_row = _item_by_persona(stored_items, LIVE_PERSONA_ID)
                    stored_row["stage"] = "paper_running"
                else:
                    evaluation_id = evaluation["allocation_evaluation_id"]
                    store._data["allocation_evaluations"][evaluation_id]["lines"][
                        0
                    ]["target_weight"] = 0.99
                store._save()
                bff_main.read_store = ReadSurfaceStore(
                    os.path.join(td, "read-surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )

                if tamper_target == "ranking_snapshot":
                    rejected = client.post(
                        "/bff/management/allocation-policy/evaluate",
                        headers=HEADERS,
                        json={
                            "ranking_snapshot_id": snapshot_id,
                            "rows": [live_row],
                        },
                    )
                else:
                    rejected = client.post(
                        "/bff/rebalances",
                        headers={
                            **HEADERS,
                            "Idempotency-Key": "ppl-alloc-012-corrupt-evaluation",
                        },
                        json={
                            "capital_pool_id": "pool-real",
                            "ranking_snapshot_id": snapshot_id,
                            "allocation_evaluation_id": evaluation[
                                "allocation_evaluation_id"
                            ],
                            "allocation_policy_version": evaluation[
                                "allocation_policy_version"
                            ],
                            "lines": evaluation["lines"],
                            "simulation": {"status": "passed"},
                            "constraints": {"pool_total_max": 1},
                            "rollback_target": {"snapshot_id": "before-corruption"},
                        },
                    )
                assert rejected.status_code == 422, rejected.text
                assert "integrity" in rejected.text.lower()
            finally:
                bff_main.read_store = original_store


def test_binding_weight_mutation_changes_snapshot_and_quarterly_surfaces_converge() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            _seed_live_persona(store)

            initial = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert initial.status_code == 200, initial.text
            initial_snapshot_id = initial.json()["data"]["ranking_snapshot_id"]
            assert _item_by_persona(
                initial.json()["data"]["items"],
                LIVE_PERSONA_ID,
            )["current_weight"] == 0.04

            _write_live_binding(store, current_weight=0.07)

            ranking = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            drilldown = client.get(
                "/bff/management/quarterly-ranking/drilldown",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "persona_id": LIVE_PERSONA_ID},
            )
            recommendations = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert ranking.status_code == drilldown.status_code == recommendations.status_code == 200

            ranking_body = ranking.json()
            mutated_snapshot_id = ranking_body["data"]["ranking_snapshot_id"]
            assert mutated_snapshot_id != initial_snapshot_id
            mutated_row = _item_by_persona(
                ranking_body["data"]["items"],
                LIVE_PERSONA_ID,
            )
            assert mutated_row["current_weight"] == 0.07
            assert mutated_row["ranking_snapshot_id"] == mutated_snapshot_id

            drilldown_body = drilldown.json()
            assert drilldown_body["data"]["ranking_snapshot_id"] == mutated_snapshot_id
            assert drilldown_body["data"]["ranking_item"]["current_weight"] == 0.07
            assert drilldown_body["meta"]["ranking_snapshot_id"] == mutated_snapshot_id

            recommendation_body = recommendations.json()
            assert recommendation_body["data"]["ranking_snapshot_id"] == mutated_snapshot_id
            assert recommendation_body["meta"]["ranking_snapshot_id"] == mutated_snapshot_id
            assert recommendation_body["data"]["items"]
            assert {
                item["ranking_snapshot_id"]
                for item in recommendation_body["data"]["items"]
            } == {mutated_snapshot_id}
            assert {
                item["current_weight"]
                for item in recommendation_body["data"]["items"]
            } == {0.07}
        finally:
            bff_main.read_store = original_store


def test_binding_evidence_is_persona_scoped_and_rbac_keeps_snapshot_stable() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            _seed_live_persona(store)
            live_ref_id = "evidence-ppl-alloc-012-live-binding"
            alpha_ref_id = "evidence-ppl-alloc-012-alpha-binding"
            _install_evidence_records(
                store,
                [
                    _binding_evidence_ref(live_ref_id, binding_id=LIVE_BINDING_ID),
                    _binding_evidence_ref(alpha_ref_id, binding_id="binding-042"),
                ],
            )

            operator_response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            admin_response = client.get(
                "/bff/management/quarterly-ranking",
                headers={"Authorization": "Bearer ppl-alloc-rbac:admin"},
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert operator_response.status_code == admin_response.status_code == 200
            operator_body = operator_response.json()
            admin_body = admin_response.json()
            assert (
                operator_body["data"]["ranking_snapshot_id"]
                == admin_body["data"]["ranking_snapshot_id"]
            )

            operator_live = _item_by_persona(
                operator_body["data"]["items"],
                LIVE_PERSONA_ID,
            )
            operator_alpha = _item_by_persona(
                operator_body["data"]["items"],
                "persona-alpha",
            )
            admin_live = _item_by_persona(
                admin_body["data"]["items"],
                LIVE_PERSONA_ID,
            )
            operator_live_by_id = {
                ref["ref_id"]: ref for ref in operator_live["evidence_refs"]
            }
            operator_alpha_ref_ids = {
                ref["ref_id"] for ref in operator_alpha["evidence_refs"]
            }
            admin_live_by_id = {
                ref["ref_id"]: ref for ref in admin_live["evidence_refs"]
            }

            assert live_ref_id in operator_live_by_id
            assert alpha_ref_id not in operator_live_by_id
            assert alpha_ref_id in operator_alpha_ref_ids
            assert live_ref_id not in operator_alpha_ref_ids
            assert operator_live_by_id[live_ref_id]["redacted"] is True
            assert admin_live_by_id[live_ref_id]["redacted"] is False
            assert operator_live["evidence_ref_ids"] == admin_live["evidence_ref_ids"]
        finally:
            bff_main.read_store = original_store


def test_recommendations_preserve_archetype_filter_projection() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            _seed_live_persona(store)

            response = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "archetype": LIVE_ARCHETYPE,
                    "page_size": 200,
                },
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["page_info"]["total"] >= 1
            assert body["data"]["items"]
            assert {
                item["persona_id"] for item in body["data"]["items"]
            } == {LIVE_PERSONA_ID}
            assert {
                item["archetype"] for item in body["data"]["items"]
            } == {LIVE_ARCHETYPE}
            filtered_action_counts = {
                action_id: len(
                    [
                        item
                        for item in body["data"]["items"]
                        if item["action_id"] == action_id
                    ]
                )
                for action_id in body["data"]["summary"]["by_action"]
            }
            assert body["data"]["summary"]["by_action"] == filtered_action_counts
            assert sum(filtered_action_counts.values()) == body["page_info"]["total"]
            assert body["data"]["summary"]["recommendation_count"] == body["page_info"]["total"]
        finally:
            bff_main.read_store = original_store


def test_live_runtime_without_active_persona_binding_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td, fallback=False)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            persona_id = "persona-ppl-alloc-012-runtime-only"
            runtime_id = "runtime-ppl-alloc-012-runtime-only"
            store.create_persona(
                persona_id=persona_id,
                name="PPL Alloc Runtime Only",
                actor_id="Codex2",
                lifecycle_state="live_running",
                metadata={"capital_mode": "live", "deployment_stage": "live"},
            )
            store.create_runtime_binding(
                runtime_id=runtime_id,
                name="PPL Alloc Runtime Only",
                persona_id=persona_id,
                binding_id="",
                deployment_plan_id="plan-ppl-alloc-012-runtime-only",
                runtime_kind="live",
                actor_id="Codex2",
                state="running",
                params={
                    "capital_pool_id": "pool-runtime-only",
                    "capital_sleeve_id": "sleeve-runtime-only",
                    "current_weight": 0.06,
                },
            )
            _install_runtime_observations(
                store,
                persona_id=persona_id,
                runtime_id=runtime_id,
            )

            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert response.status_code == 200, response.text
            row = _item_by_persona(response.json()["data"]["items"], persona_id)
            assert row["stage"] == "live_running"
            assert row["binding_resolution"] == "missing"
            assert row["eligible"] is False
            assert "missing_capital_binding" in row["exclusion_codes"]
        finally:
            bff_main.read_store = original_store


def test_runtime_binding_mode_is_actual_stage_not_binding_ceiling() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td, fallback=False)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            persona_id = "persona-ppl-alloc-012-paper-under-live-ceiling"
            binding_id = "binding-ppl-alloc-012-paper-under-live-ceiling"
            runtime_id = "runtime-ppl-alloc-012-paper-under-live-ceiling"
            store.create_persona(
                persona_id=persona_id,
                name="PPL Paper Under Live Ceiling",
                actor_id="Codex2",
                lifecycle_state="live_running",
                metadata={"capital_mode": "live", "deployment_stage": "live"},
            )
            store.create_persona_binding(
                binding_id=binding_id,
                persona_id=persona_id,
                capital_pool_id="pool-paper-under-live-ceiling",
                actor_id="Codex2",
                validity="active",
                metadata={
                    "allowed_deployment_scope": "live",
                    "capital_mode": "live",
                    "current_weight": 0.04,
                },
            )
            store.create_runtime_binding(
                runtime_id=runtime_id,
                name="PPL Paper Under Live Ceiling",
                persona_id=persona_id,
                binding_id=binding_id,
                deployment_plan_id="plan-ppl-alloc-012-paper-under-live-ceiling",
                runtime_kind="paper",
                actor_id="Codex2",
                state="running",
                params={"capital_pool_id": "pool-paper-under-live-ceiling"},
            )
            _install_runtime_observations(
                store,
                persona_id=persona_id,
                runtime_id=runtime_id,
            )

            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert response.status_code == 200, response.text
            row = _item_by_persona(response.json()["data"]["items"], persona_id)
            assert row["deployment_stage"] == "paper"
            assert row["stage"] == "paper_running"
            assert row["runtime_resolution"] == "active"
            assert row["session_resolution"] == "active"
            assert row["telemetry_resolution"] == "fresh"
            assert row["current_weight"] is None
            assert row["current_weight_source"] == "not_applicable_paper_ledger"
            assert row["eligible"] is True
        finally:
            bff_main.read_store = original_store


def test_paper_runtime_session_requires_runtime_manager_monitoring_owner() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            _client(td, fallback=False)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            store.get_sessions_for_persona = lambda _persona_id: [  # type: ignore[method-assign]
                {
                    "id": "local-persona-session",
                    "status": "active",
                    "runtime_id": PAPER_RUNTIME_ID,
                }
            ]
            store.list_authoritative_paper_runtime_monitoring_sessions = (  # type: ignore[method-assign]
                lambda: []
            )
            session, resolution = bff_main._pm12_runtime_session_resolution(
                PAPER_PERSONA_ID,
                {
                    "runtime_id": PAPER_RUNTIME_ID,
                    "deployment_mode": "paper",
                    "state": "running",
                },
            )
            assert session is None
            assert resolution == "missing"
        finally:
            bff_main.read_store = original_store


def test_missing_runtime_fails_closed_even_with_live_binding_and_observations() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td, fallback=False)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            persona_id = "persona-ppl-alloc-012-missing-runtime"
            runtime_id = "runtime-ppl-alloc-012-phantom"
            store.create_persona(
                persona_id=persona_id,
                name="PPL Missing Runtime",
                actor_id="Codex2",
                lifecycle_state="live_running",
                metadata={"capital_mode": "live", "deployment_stage": "live"},
            )
            store.create_persona_binding(
                binding_id="binding-ppl-alloc-012-missing-runtime",
                persona_id=persona_id,
                capital_pool_id="pool-missing-runtime",
                actor_id="Codex2",
                validity="active",
                metadata={
                    "allowed_deployment_scope": "live",
                    "capital_mode": "live",
                    "current_weight": 0.04,
                },
            )
            _install_runtime_observations(
                store,
                persona_id=persona_id,
                runtime_id=runtime_id,
            )

            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert response.status_code == 200, response.text
            row = _item_by_persona(response.json()["data"]["items"], persona_id)
            assert row["stage"] == "not_running"
            assert row["deployment_stage"] == "none"
            assert row["runtime_resolution"] == "missing"
            assert row["current_weight"] is None
            assert row["eligible"] is False
            assert "missing_runtime" in row["exclusion_codes"]
        finally:
            bff_main.read_store = original_store


def test_runtime_binding_requires_fresh_explicit_deployment_mode() -> None:
    for runtime_patch, removed_fields, expected_resolution, expected_code in (
        ({}, {"deployment_mode"}, "invalid_deployment_mode", "inactive_runtime"),
        ({"stale": True}, set(), "stale", "stale_runtime"),
    ):
        with tempfile.TemporaryDirectory() as td:
            original_store = bff_main.read_store
            try:
                client = _client(td, fallback=False)
                store = bff_main.read_store
                assert isinstance(store, ReadSurfaceStore)
                _seed_live_persona(store)
                original_runtimes = store.list_runtime_bindings

                def runtime_bindings(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
                    records = original_runtimes(*args, **kwargs)
                    for record in records:
                        if record.get("runtime_id") != LIVE_RUNTIME_ID:
                            continue
                        for field in removed_fields:
                            record.pop(field, None)
                        record.update(runtime_patch)
                    return records

                store.list_runtime_bindings = runtime_bindings  # type: ignore[method-assign]
                response = client.get(
                    "/bff/management/quarterly-ranking",
                    headers=HEADERS,
                    params={"quarter": "2026-Q3", "page_size": 200},
                )
                assert response.status_code == 200, response.text
                row = _item_by_persona(
                    response.json()["data"]["items"],
                    LIVE_PERSONA_ID,
                )
                assert row["stage"] == "not_running"
                assert row["deployment_stage"] == "none"
                assert row["runtime_resolution"] == expected_resolution
                assert row["current_weight"] is None
                assert row["eligible"] is False
                assert expected_code in row["exclusion_codes"]
            finally:
                bff_main.read_store = original_store


def test_ended_or_stale_runtime_session_fails_closed() -> None:
    for session_patch, expected_resolution, expected_code in (
        ({"ended_at": "2026-07-10T00:05:00Z"}, "ended", "ended_session"),
        (
            {"staleness": {"status": "stale", "reason": "stale_heartbeat"}},
            "stale",
            "stale_session",
        ),
    ):
        with tempfile.TemporaryDirectory() as td:
            original_store = bff_main.read_store
            try:
                client = _client(td, fallback=False)
                store = bff_main.read_store
                assert isinstance(store, ReadSurfaceStore)
                _seed_live_persona(store)
                original_sessions = store.get_sessions_for_persona

                def sessions_for_persona(persona_id: str | None) -> list[dict[str, Any]] | None:
                    if persona_id == LIVE_PERSONA_ID:
                        return [{
                            "id": "session-ppl-alloc-012-live",
                            "status": "active",
                            "runtime_binding_id": LIVE_RUNTIME_ID,
                            "last_heartbeat_at": "2026-07-10T00:00:00Z",
                            **session_patch,
                        }]
                    return original_sessions(persona_id)

                store.get_sessions_for_persona = sessions_for_persona  # type: ignore[method-assign]
                response = client.get(
                    "/bff/management/quarterly-ranking",
                    headers=HEADERS,
                    params={"quarter": "2026-Q3", "page_size": 200},
                )
                assert response.status_code == 200, response.text
                row = _item_by_persona(response.json()["data"]["items"], LIVE_PERSONA_ID)
                assert row["stage"] == "live_running"
                assert row["session_resolution"] == expected_resolution
                assert row["eligible"] is False
                assert expected_code in row["exclusion_codes"]
            finally:
                bff_main.read_store = original_store


def test_stale_or_mismatched_telemetry_cannot_supply_ranking_coverage() -> None:
    for telemetry_patch, expected_resolution in (
        ({"stale": True}, "stale"),
        ({"state": "degraded"}, "degraded"),
        ({"connectivity_status": "disconnected"}, "degraded"),
        ({"runtime_id": "runtime-ppl-alloc-012-other"}, "identity_mismatch"),
    ):
        with tempfile.TemporaryDirectory() as td:
            original_store = bff_main.read_store
            try:
                client = _client(td, fallback=False)
                store = bff_main.read_store
                assert isinstance(store, ReadSurfaceStore)
                _seed_live_persona(store)
                original_telemetry = store.get_telemetry_summary

                def telemetry_for_runtime(runtime_id: str) -> dict[str, Any] | None:
                    if runtime_id == LIVE_RUNTIME_ID:
                        return {
                            "runtime_id": LIVE_RUNTIME_ID,
                            "pnl": 99.0,
                            "drawdown": 0.0,
                            "fill_rate": 1.0,
                            "total_trades": 999,
                            "collected_at": "2026-07-10T00:00:00Z",
                            **telemetry_patch,
                        }
                    return original_telemetry(runtime_id)

                store.get_telemetry_summary = telemetry_for_runtime  # type: ignore[method-assign]
                response = client.get(
                    "/bff/management/quarterly-ranking",
                    headers=HEADERS,
                    params={"quarter": "2026-Q3", "page_size": 200},
                )
                assert response.status_code == 200, response.text
                row = _item_by_persona(response.json()["data"]["items"], LIVE_PERSONA_ID)
                assert row["telemetry_resolution"] == expected_resolution
                assert row["metrics"]["telemetry_coverage_count"] == 0
                assert row["evidence_refs"] == []
                assert row["source_confidence"] == "unavailable"
                assert row["eligible"] is False
                expected_code = {
                    "stale": "stale_telemetry",
                    "degraded": "degraded_telemetry",
                    "identity_mismatch": "runtime_identity_mismatch",
                }[expected_resolution]
                assert expected_code in row["exclusion_codes"]
            finally:
                bff_main.read_store = original_store


def test_declared_stopped_runtime_cannot_be_authoritative_despite_active_status() -> None:
    persona_id = "persona-ppl-alloc-012-stopped-runtime"
    binding_id = "binding-ppl-alloc-012-stopped-runtime"
    runtime_id = "runtime-ppl-alloc-012-stopped-runtime"
    binding = {
        "id": binding_id,
        "binding_id": binding_id,
        "persona_id": persona_id,
        "status": "active",
        "validity": "active",
        "metadata": {"capital_mode": "live", "current_weight": 0.05},
    }
    runtime = {
        "id": runtime_id,
        "runtime_id": runtime_id,
        "persona_id": persona_id,
        "binding_id": binding_id,
        "status": "active",
        "state": "stopped",
        "runtime_kind": "live",
    }
    selected_binding, selected_runtime, resolution = bff_main._pm12_binding_runtime_context(
        persona_id=persona_id,
        item={"binding_id": binding_id, "runtime_ids": [runtime_id]},
        bindings=[binding],
        runtimes=[runtime],
    )
    assert selected_binding["binding_id"] == binding_id
    assert selected_runtime == {}
    assert "inactive" in resolution

    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td, fallback=False)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            store.create_persona(
                persona_id=persona_id,
                name="PPL Stopped Runtime",
                actor_id="Codex2",
                lifecycle_state="live_running",
                metadata={"capital_mode": "live", "deployment_stage": "live"},
            )
            store.create_persona_binding(
                binding_id=binding_id,
                persona_id=persona_id,
                capital_pool_id="pool-stopped-runtime",
                actor_id="Codex2",
                validity="active",
                metadata={
                    "capital_mode": "live",
                    "capital_sleeve_id": "sleeve-stopped-runtime",
                    "current_weight": 0.05,
                },
            )
            store.create_runtime_binding(
                runtime_id=runtime_id,
                name="PPL Stopped Runtime",
                persona_id=persona_id,
                binding_id=binding_id,
                deployment_plan_id="plan-ppl-alloc-012-stopped-runtime",
                runtime_kind="live",
                actor_id="Codex2",
                state="stopped",
                params={
                    "capital_pool_id": "pool-stopped-runtime",
                    "capital_sleeve_id": "sleeve-stopped-runtime",
                    "current_weight": 0.05,
                },
            )
            original_list_runtime_bindings = store.list_runtime_bindings

            def conflicting_runtime_bindings(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
                records = original_list_runtime_bindings(*args, **kwargs)
                for record in records:
                    if record.get("runtime_id") == runtime_id:
                        record["status"] = "active"
                        record["state"] = "stopped"
                return records

            store.list_runtime_bindings = conflicting_runtime_bindings  # type: ignore[method-assign]
            _install_runtime_observations(
                store,
                persona_id=persona_id,
                runtime_id=runtime_id,
            )

            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert response.status_code == 200, response.text
            row = _item_by_persona(response.json()["data"]["items"], persona_id)
            assert row["stage"] == "not_running"
            assert row["runtime_resolution"] == "inactive"
            assert row["current_weight"] is None
            assert row["current_weight_source"] == "unavailable"
            assert row["eligible"] is False
            assert "inactive_runtime" in row["exclusion_codes"]
        finally:
            bff_main.read_store = original_store


def test_invalid_binding_weights_never_serialize_or_become_eligible() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td, fallback=False)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            invalid_weights = {
                "nan": float("nan"),
                "positive-inf": float("inf"),
                "negative-inf": float("-inf"),
                "negative": -0.01,
                "above-one": 1.01,
            }
            persona_ids: list[str] = []
            for label, invalid_weight in invalid_weights.items():
                persona_id = f"persona-ppl-alloc-012-invalid-{label}"
                runtime_id = f"runtime-ppl-alloc-012-invalid-{label}"
                persona_ids.append(persona_id)
                store.create_persona(
                    persona_id=persona_id,
                    name=f"PPL Invalid Weight {label}",
                    actor_id="Codex2",
                    lifecycle_state="live_running",
                    metadata={"capital_mode": "live", "deployment_stage": "live"},
                )
                store.create_persona_binding(
                    binding_id=f"binding-ppl-alloc-012-invalid-{label}",
                    persona_id=persona_id,
                    capital_pool_id=f"pool-invalid-{label}",
                    actor_id="Codex2",
                    validity="active",
                    metadata={
                        "allowed_deployment_scope": "live",
                        "capital_mode": "live",
                        "capital_sleeve_id": f"sleeve-invalid-{label}",
                        "current_weight": invalid_weight,
                        "target_weight": invalid_weight,
                    },
                )
                store.create_runtime_binding(
                    runtime_id=runtime_id,
                    name=f"PPL Invalid Weight {label}",
                    persona_id=persona_id,
                    binding_id=f"binding-ppl-alloc-012-invalid-{label}",
                    deployment_plan_id=f"plan-ppl-alloc-012-invalid-{label}",
                    runtime_kind="live",
                    actor_id="Codex2",
                    state="running",
                    params={"capital_pool_id": f"pool-invalid-{label}"},
                )
                _install_runtime_observations(
                    store,
                    persona_id=persona_id,
                    runtime_id=runtime_id,
                )

            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert response.status_code == 200, response.text
            assert "NaN" not in response.text
            assert "Infinity" not in response.text
            rows = response.json()["data"]["items"]
            for persona_id in persona_ids:
                row = _item_by_persona(rows, persona_id)
                assert row["stage"] == "live_running"
                assert row["current_weight"] is None
                assert row["target_weight"] is None
                assert row["current_weight_source"] == "persona_binding_invalid"
                assert row["eligible"] is False
                assert "missing_current_weight" in row["exclusion_codes"]
        finally:
            bff_main.read_store = original_store


def test_paper_ledger_without_persona_binding_remains_ranking_eligible() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td, fallback=False)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            persona_id = "persona-ppl-alloc-012-paper-unbound"
            runtime_id = "runtime-ppl-alloc-012-paper-unbound"
            paper_ledger_id = "paper-ledger-ppl-alloc-012-unbound"
            store.create_persona(
                persona_id=persona_id,
                name="PPL Paper Ledger Unbound",
                actor_id="Codex2",
                lifecycle_state="paper_running",
                metadata={
                    "capital_mode": "paper",
                    "deployment_stage": "paper",
                    "paper_ledger_id": paper_ledger_id,
                },
            )
            store.create_runtime_binding(
                runtime_id=runtime_id,
                name="PPL Paper Ledger Unbound",
                persona_id=persona_id,
                binding_id="",
                deployment_plan_id="plan-ppl-alloc-012-paper-unbound",
                runtime_kind="paper",
                actor_id="Codex2",
                state="running",
                params={"paper_ledger_id": paper_ledger_id},
            )
            _install_runtime_observations(
                store,
                persona_id=persona_id,
                runtime_id=runtime_id,
            )

            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert response.status_code == 200, response.text
            row = _item_by_persona(response.json()["data"]["items"], persona_id)
            assert row["stage"] == "paper_running"
            assert row["paper_ledger_id"] == paper_ledger_id
            assert row["capital_scope"] == "paper_ledger"
            assert row["capital_scope_id"] == paper_ledger_id
            assert row["binding_resolution"] == "missing"
            assert row["current_weight"] is None
            assert row["current_weight_source"] == "not_applicable_paper_ledger"
            assert row["eligible"] is True
            assert "missing_capital_binding" not in row["exclusion_codes"]
        finally:
            bff_main.read_store = original_store


def test_stable_promotion_submit_replays_original_snapshot_after_ranking_mutation() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_final_idempotency = dict(bff_main._FINAL_CONTRACT_IDEMPOTENCY)
        try:
            client = _client(td)
            bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            _seed_live_persona(store)

            recommendation_response = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert recommendation_response.status_code == 200, recommendation_response.text
            recommendation = recommendation_response.json()["data"]["items"][0]
            recommendation_id = recommendation["recommendation_id"]
            original_snapshot_id = recommendation["ranking_snapshot_id"]
            original_current_weight = recommendation["current_weight"]

            submit = client.post(
                f"/bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit",
                headers={**HEADERS, "Idempotency-Key": "ppl-alloc-012-submit-original"},
                json={
                    "quarter": "2026-Q3",
                    "ranking_snapshot_id": original_snapshot_id,
                },
            )
            assert submit.status_code == 202, submit.text
            original_review_id = submit.json()["data"]["review_id"]
            assert original_review_id != recommendation_id
            assert submit.json()["data"]["ranking_snapshot_id"] == original_snapshot_id
            assert bff_main.command_store._get_all_commands()[0]["params"][
                "ranking_snapshot_id"
            ] == original_snapshot_id
            original_decision = client.post(
                f"/bff/management/promotion-reviews/{original_review_id}/decisions",
                headers={
                    "Authorization": "Bearer ppl-alloc-012-approver:approver",
                    "Idempotency-Key": "ppl-alloc-012-promotion-decision",
                },
                json={
                    "decision": "approve",
                    "quarter": "2026-Q3",
                    "rationale": "Approve only this immutable ranking revision.",
                },
            )
            assert original_decision.status_code == 202, original_decision.text

            _write_live_binding(store, current_weight=0.09)
            mutated_response = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert mutated_response.status_code == 200, mutated_response.text
            mutated_recommendation = next(
                item
                for item in mutated_response.json()["data"]["items"]
                if item["recommendation_id"] == recommendation_id
            )
            assert mutated_recommendation["ranking_snapshot_id"] != original_snapshot_id
            assert mutated_recommendation["current_weight"] != original_current_weight

            review_list = client.get(
                "/bff/management/promotion-reviews",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            review_detail = client.get(
                f"/bff/management/promotion-reviews/{recommendation_id}",
                headers=HEADERS,
                params={"quarter": "2026-Q3"},
            )
            assert review_list.status_code == 200, review_list.text
            assert review_detail.status_code == 200, review_detail.text
            stored_list_review = next(
                item
                for item in review_list.json()["data"]["items"]
                if item["recommendation_id"] == recommendation_id
            )
            for current_review in (stored_list_review, review_detail.json()["data"]):
                assert current_review["ranking_snapshot_id"] == (
                    mutated_recommendation["ranking_snapshot_id"]
                )
                assert current_review["current_weight"] == (
                    mutated_recommendation["current_weight"]
                )
                assert current_review["submitted"] is False
                assert current_review["status"] == "recommended_not_submitted"
                assert current_review["decision_status"] == "pending"

            historical_detail = client.get(
                f"/bff/management/promotion-reviews/{original_review_id}",
                headers=HEADERS,
                params={"quarter": "2026-Q3"},
            )
            assert historical_detail.status_code == 200, historical_detail.text
            historical = historical_detail.json()["data"]
            assert historical["review_id"] == original_review_id
            assert historical["ranking_snapshot_id"] == original_snapshot_id
            assert historical["current_weight"] == original_current_weight
            assert historical["submitted"] is True
            assert historical["decision_status"] == "accepted"
            historical_mutation = client.post(
                f"/bff/management/promotion-reviews/{original_review_id}/decisions",
                headers={
                    "Authorization": "Bearer ppl-alloc-012-approver:approver",
                    "Idempotency-Key": "ppl-alloc-012-old-revision-mutation",
                },
                json={
                    "decision": "approve",
                    "quarter": "2026-Q3",
                    "rationale": "Historical revisions are read-only.",
                },
            )
            assert historical_mutation.status_code == 404, historical_mutation.text

            stale_alias = client.post(
                f"/bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit",
                headers={**HEADERS, "Idempotency-Key": "ppl-alloc-012-submit-replay"},
                json={
                    "quarter": "2026-Q3",
                    "ranking_snapshot_id": original_snapshot_id,
                },
            )
            assert stale_alias.status_code == 200, stale_alias.text
            stale_alias_body = stale_alias.json()
            assert stale_alias_body["meta"]["idempotency"]["replayed"] is True
            assert stale_alias_body["data"]["review_id"] == original_review_id
            assert stale_alias_body["data"]["ranking_snapshot_id"] == (
                original_snapshot_id
            )

            replay = client.post(
                f"/bff/management/quarterly-ranking/recommendations/{original_review_id}/submit",
                headers={**HEADERS, "Idempotency-Key": "ppl-alloc-012-submit-replay"},
                json={
                    "quarter": "2026-Q3",
                    "ranking_snapshot_id": original_snapshot_id,
                },
            )
            assert replay.status_code == 200, replay.text
            replay_body = replay.json()
            assert replay_body["meta"]["idempotency"]["replayed"] is True
            assert replay_body["data"]["ranking_snapshot_id"] == original_snapshot_id
            assert replay_body["meta"]["ranking_snapshot_id"] == original_snapshot_id
            assert replay_body["data"]["review"]["ranking_snapshot_id"] == original_snapshot_id
            assert len(bff_main.command_store._get_all_commands()) == 2
            assert bff_main.command_store._get_all_commands()[0]["params"][
                "ranking_snapshot_id"
            ] == original_snapshot_id

            superseding = client.post(
                f"/bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit",
                headers={
                    **HEADERS,
                    # Same client namespace is safe because the server scopes it
                    # to the immutable review revision.
                    "Idempotency-Key": "ppl-alloc-012-submit-original",
                },
                json={
                    "quarter": "2026-Q3",
                    "ranking_snapshot_id": mutated_recommendation[
                        "ranking_snapshot_id"
                    ],
                },
            )
            assert superseding.status_code == 202, superseding.text
            superseding_body = superseding.json()
            assert superseding_body["data"]["review_id"] != original_review_id
            assert superseding_body["data"]["recommendation_id"] == recommendation_id
            assert superseding_body["data"]["status"] == "pending_human_gate"
            assert superseding_body["data"]["review"]["decision_status"] == "pending"
            superseding_review_id = superseding_body["data"]["review_id"]
            assert len(bff_main.command_store._get_all_commands()) == 3
            inbox = client.get(
                "/bff/management/human-inbox",
                headers=HEADERS,
                params={"source_type": "promotion_review", "page_size": 200},
            )
            assert inbox.status_code == 200, inbox.text
            review_ids = [
                item["promotion_review_id"]
                for item in inbox.json()["data"]["items"]
                if item["recommendation_id"] == recommendation_id
            ]
            assert set(review_ids) == {
                original_review_id,
                superseding_review_id,
            }
            assert len(review_ids) == len(set(review_ids))

            superseding_decision = client.post(
                f"/bff/management/promotion-reviews/{superseding_review_id}/decisions",
                headers={
                    "Authorization": "Bearer ppl-alloc-012-approver:approver",
                    # The same client retry key is independently scoped to the
                    # new immutable revision.
                    "Idempotency-Key": "ppl-alloc-012-promotion-decision",
                },
                json={
                    "decision": "approve",
                    "quarter": "2026-Q3",
                    "rationale": "Approve the superseding ranking revision.",
                },
            )
            assert superseding_decision.status_code == 202, superseding_decision.text
            assert superseding_decision.json()["data"]["review_id"] == (
                superseding_review_id
            )
            assert len(bff_main.command_store._get_all_commands()) == 4
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.update(original_final_idempotency)


def test_stable_promotion_submit_uses_each_admitted_snapshot_after_mutation_and_restart(
    monkeypatch,
) -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_final_idempotency = dict(bff_main._FINAL_CONTRACT_IDEMPOTENCY)
        client: TestClient | None = None
        try:
            clock = {"now": datetime(2026, 7, 24, 23, 0, tzinfo=timezone.utc)}
            monkeypatch.setattr(
                bff_main,
                "utc_now",
                lambda: clock["now"].isoformat().replace("+00:00", "Z"),
            )
            client = _client(td)
            command_path = os.path.join(td, "commands.jsonl")
            bff_main.command_store = CommandStore(command_path)
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            _seed_live_persona(store)

            original_response = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert original_response.status_code == 200, original_response.text
            original = original_response.json()["data"]["items"][0]
            recommendation_id = original["recommendation_id"]
            original_snapshot_id = original["ranking_snapshot_id"]
            original_weight = original["current_weight"]

            clock["now"] += timedelta(seconds=30)
            _write_live_binding(store, current_weight=0.09)
            mutated_response = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert mutated_response.status_code == 200, mutated_response.text
            mutated = next(
                item
                for item in mutated_response.json()["data"]["items"]
                if item["recommendation_id"] == recommendation_id
            )
            assert mutated["ranking_snapshot_id"] != original_snapshot_id
            assert mutated["current_weight"] != original_weight

            client.close()
            client = None
            bff_main.read_store = ReadSurfaceStore(
                os.path.join(td, "read-surfaces.json"),
                allow_local_snapshot_fallback=True,
            )
            bff_main.command_store = CommandStore(command_path)
            client = TestClient(bff_main.app, raise_server_exceptions=False)

            restarted_response = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert restarted_response.status_code == 200, restarted_response.text
            restarted = next(
                item
                for item in restarted_response.json()["data"]["items"]
                if item["recommendation_id"] == recommendation_id
            )
            assert restarted["current_weight"] == mutated["current_weight"]

            historical_exact_submit = client.post(
                (
                    "/bff/management/quarterly-ranking/recommendations/"
                    f"{original['review_id']}/submit"
                ),
                headers={
                    **HEADERS,
                    "Idempotency-Key": "ppl-alloc-012-exact-historical-new-submit",
                },
                json={
                    "quarter": "2026-Q3",
                    "ranking_snapshot_id": original_snapshot_id,
                },
            )
            assert historical_exact_submit.status_code == 409, (
                historical_exact_submit.text
            )
            assert bff_main.command_store._get_all_commands() == []

            original_submit = client.post(
                f"/bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit",
                headers={
                    **HEADERS,
                    "Idempotency-Key": "ppl-alloc-012-submit-original-after-restart",
                },
                json={
                    "quarter": "2026-Q3",
                    "ranking_snapshot_id": original_snapshot_id,
                },
            )
            assert original_submit.status_code == 202, original_submit.text
            original_body = original_submit.json()
            original_review_id = original_body["data"]["review_id"]
            assert original_body["data"]["ranking_snapshot_id"] == (
                original_snapshot_id
            )
            assert original_body["data"]["review"]["current_weight"] == (
                original_weight
            )

            submit = client.post(
                (
                    "/bff/management/quarterly-ranking/recommendations/"
                    f"{restarted['review_id']}/submit"
                ),
                headers={
                    **HEADERS,
                    "Idempotency-Key": "ppl-alloc-012-submit-current-after-restart",
                },
                json={
                    "quarter": "2026-Q3",
                    "ranking_snapshot_id": restarted["ranking_snapshot_id"],
                },
            )
            assert submit.status_code == 202, submit.text
            body = submit.json()
            assert body["data"]["ranking_snapshot_id"] == restarted[
                "ranking_snapshot_id"
            ]
            assert body["data"]["review"]["current_weight"] == restarted[
                "current_weight"
            ]
            restarted_review_id = body["data"]["review_id"]
            assert restarted_review_id != original_review_id
            assert body["meta"]["live_capital_mutation"] is False
            commands = bff_main.command_store._get_all_commands()
            assert len(commands) == 2
            original_params = commands[0]["params"]
            restarted_params = commands[1]["params"]
            assert original_params["ranking_snapshot_id"] == original_snapshot_id
            assert original_params["source_recommendation"]["current_weight"] == (
                original_weight
            )
            assert restarted_params["ranking_snapshot_id"] == restarted[
                "ranking_snapshot_id"
            ]
            assert restarted_params["source_recommendation"]["current_weight"] == restarted[
                "current_weight"
            ]
            assert restarted_params["live_capital_mutation"] is False
            assert restarted_params["direct_live_capital_mutation"] is False
            assert restarted_params["runtime_mutation"] is False

            original_decision = client.post(
                f"/bff/management/promotion-reviews/{original_review_id}/decisions",
                headers={
                    "Authorization": "Bearer ppl-alloc-012-approver:approver",
                    "Idempotency-Key": "ppl-alloc-012-original-revision-decision",
                },
                json={
                    "decision": "approve",
                    "quarter": "2026-Q3",
                    "rationale": "Approve only the original immutable revision.",
                },
            )
            assert original_decision.status_code == 202, original_decision.text
            current_recommendations = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert current_recommendations.status_code == 200, (
                current_recommendations.text
            )
            current_recommendation = next(
                item
                for item in current_recommendations.json()["data"]["items"]
                if item["recommendation_id"] == recommendation_id
            )
            assert current_recommendation["review_id"] == restarted_review_id
            assert current_recommendation["ranking_snapshot_id"] == restarted[
                "ranking_snapshot_id"
            ]
            assert current_recommendation["human_review_state"][
                "decision_status"
            ] == "pending"
            restarted_detail = client.get(
                f"/bff/management/promotion-reviews/{restarted_review_id}",
                headers=HEADERS,
                params={"quarter": "2026-Q3"},
            )
            assert restarted_detail.status_code == 200, restarted_detail.text
            assert restarted_detail.json()["data"]["ranking_snapshot_id"] == (
                restarted["ranking_snapshot_id"]
            )
            assert restarted_detail.json()["data"]["decision_status"] == "pending"
        finally:
            if client is not None:
                client.close()
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.update(original_final_idempotency)


def test_promotion_first_submit_rejects_expired_snapshot(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_final_idempotency = dict(bff_main._FINAL_CONTRACT_IDEMPOTENCY)
        try:
            clock = {"now": datetime(2026, 7, 24, 23, 0, tzinfo=timezone.utc)}
            monkeypatch.setattr(
                bff_main,
                "utc_now",
                lambda: clock["now"].isoformat().replace("+00:00", "Z"),
            )
            monkeypatch.setenv("PANTHEON_PM12_RANKING_SNAPSHOT_TTL_SECONDS", "60")
            client = _client(td)
            bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            _seed_live_persona(store)

            response = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert response.status_code == 200, response.text
            recommendation = response.json()["data"]["items"][0]
            clock["now"] += timedelta(seconds=61)

            submit = client.post(
                (
                    "/bff/management/quarterly-ranking/recommendations/"
                    f"{recommendation['recommendation_id']}/submit"
                ),
                headers={
                    **HEADERS,
                    "Idempotency-Key": "ppl-alloc-012-submit-expired",
                },
                json={
                    "quarter": "2026-Q3",
                    "ranking_snapshot_id": recommendation["ranking_snapshot_id"],
                },
            )
            assert submit.status_code == 409, submit.text
            assert submit.json()["error"]["details"]["precondition_failed"] == (
                "ranking_snapshot_id"
            )
            assert bff_main.command_store._get_all_commands() == []
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.update(original_final_idempotency)


def test_promotion_first_submit_rejects_unknown_forged_or_mutated_snapshot_tuple() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_final_idempotency = dict(bff_main._FINAL_CONTRACT_IDEMPOTENCY)
        try:
            client = _client(td)
            bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            _seed_live_persona(store)

            response = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert response.status_code == 200, response.text
            recommendation = response.json()["data"]["items"][0]
            recommendation_id = recommendation["recommendation_id"]
            snapshot_id = recommendation["ranking_snapshot_id"]

            cases = (
                (recommendation_id, f"{snapshot_id}-unknown", "2026-Q3"),
                (
                    recommendation_id.replace(
                        LIVE_PERSONA_ID,
                        "persona-ppl-alloc-012-forged",
                    ),
                    snapshot_id,
                    "2026-Q3",
                ),
                (
                    recommendation_id.rsplit("-", 1)[0] + "-forged_action",
                    snapshot_id,
                    "2026-Q3",
                ),
                (recommendation_id, snapshot_id, "2026-Q2"),
            )
            for index, (route_id, asserted_snapshot_id, quarter) in enumerate(cases):
                submit = client.post(
                    (
                        "/bff/management/quarterly-ranking/recommendations/"
                        f"{route_id}/submit"
                    ),
                    headers={
                        **HEADERS,
                        "Idempotency-Key": f"ppl-alloc-012-submit-forged-{index}",
                    },
                    json={
                        "quarter": quarter,
                        "ranking_snapshot_id": asserted_snapshot_id,
                    },
                )
                assert submit.status_code == 422, submit.text

            snapshots = store._ensure_local_overlay_records("ranking_snapshots")
            snapshots[snapshot_id]["items"][0]["score"] = 0
            store._save()
            mutated = client.post(
                f"/bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit",
                headers={
                    **HEADERS,
                    "Idempotency-Key": "ppl-alloc-012-submit-mutated-digest",
                },
                json={
                    "quarter": "2026-Q3",
                    "ranking_snapshot_id": snapshot_id,
                },
            )
            assert mutated.status_code == 422, mutated.text
            assert mutated.json()["error"]["details"]["precondition_failed"] == (
                "ranking_snapshot_id"
            )
            assert bff_main.command_store._get_all_commands() == []
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.update(original_final_idempotency)


def test_legacy_promotion_submit_remains_read_only_when_current_revision_is_submitted() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_final_idempotency = dict(bff_main._FINAL_CONTRACT_IDEMPOTENCY)
        try:
            client = _client(td)
            bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            _seed_live_persona(store)

            recommendation_response = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert recommendation_response.status_code == 200, recommendation_response.text
            recommendation = recommendation_response.json()["data"]["items"][0]
            recommendation_id = recommendation["recommendation_id"]
            caller_snapshot_id = recommendation["ranking_snapshot_id"]

            legacy_params = {
                "quarter": "2026-Q3",
                "recommendation_id": recommendation_id,
                "recommendation_action_id": recommendation["action_id"],
                "persona_id": LIVE_PERSONA_ID,
                "live_capital_mutation": False,
            }
            bff_main.command_store.submit_command(
                command_id="cmd-ppl-alloc-012-legacy-submit",
                command_type=bff_main.CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
                target=bff_main.TargetObject(
                    type=bff_main.ObjectType.RANKING,
                    id=recommendation_id,
                ),
                submitted_at="2026-07-10T00:00:00Z",
                params=legacy_params,
                audit_context={
                    "operator_id": "legacy-admin",
                    "roles_at_submission": ["admin"],
                    "timestamp": "2026-07-10T00:00:00Z",
                },
            )
            commands_before = bff_main.command_store._get_all_commands()
            assert len(commands_before) == 1
            assert "ranking_snapshot_id" not in commands_before[0]["params"]
            assert "source_type" not in commands_before[0]["params"]
            assert "source_record_id" not in commands_before[0]["params"]
            assert "source_recommendation" not in commands_before[0]["params"]

            current_detail = client.get(
                f"/bff/management/promotion-reviews/{recommendation_id}",
                headers=HEADERS,
                params={"quarter": "2026-Q3"},
            )
            assert current_detail.status_code == 200, current_detail.text
            assert current_detail.json()["data"]["ranking_snapshot_id"] == (
                caller_snapshot_id
            )
            assert current_detail.json()["data"]["submitted"] is False

            legacy_decision = client.post(
                f"/bff/management/promotion-reviews/{recommendation_id}/decisions",
                headers={
                    "Authorization": "Bearer legacy-approver:approver",
                    "Idempotency-Key": "ppl-alloc-012-legacy-decision",
                },
                json={
                    "decision": "approve",
                    "quarter": "2026-Q3",
                    "rationale": "Legacy snapshotless rows cannot authorize current.",
                },
            )
            assert legacy_decision.status_code == 409, legacy_decision.text

            submit = client.post(
                f"/bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit",
                headers={
                    **HEADERS,
                    "Idempotency-Key": "ppl-alloc-012-legacy-replay",
                },
                json={
                    "quarter": "2026-Q3",
                    "ranking_snapshot_id": caller_snapshot_id,
                },
            )
            assert submit.status_code == 202, submit.text
            new_review_id = submit.json()["data"]["review_id"]
            assert new_review_id != recommendation_id

            commands_after = bff_main.command_store._get_all_commands()
            assert len(commands_after) == 2
            assert "ranking_snapshot_id" not in commands_after[0]["params"]
            assert "source_type" not in commands_after[0]["params"]
            assert "source_record_id" not in commands_after[0]["params"]
            assert "source_recommendation" not in commands_after[0]["params"]
            assert commands_after[1]["target"]["id"] == new_review_id
            assert commands_after[1]["params"]["ranking_snapshot_id"] == (
                caller_snapshot_id
            )
            assert commands_after[1]["params"]["promotion_review_id"] == (
                new_review_id
            )
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.update(original_final_idempotency)


def test_promotion_submit_cross_role_replay_redacts_admin_only_evidence() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        original_command_store = bff_main.command_store
        original_final_idempotency = dict(bff_main._FINAL_CONTRACT_IDEMPOTENCY)
        try:
            client = _client(td)
            bff_main.command_store = CommandStore(os.path.join(td, "commands.jsonl"))
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            _seed_live_persona(store)
            restricted_ref_id = "evidence-ppl-alloc-012-admin-only-binding"
            _install_evidence_records(
                store,
                [
                    _binding_evidence_ref(
                        restricted_ref_id,
                        binding_id=LIVE_BINDING_ID,
                    )
                ],
            )
            admin_headers = {"Authorization": "Bearer ppl-alloc-submit-admin:admin"}

            admin_recommendations = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=admin_headers,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert admin_recommendations.status_code == 200, admin_recommendations.text
            recommendation = admin_recommendations.json()["data"]["items"][0]
            admin_evidence_by_id = {
                ref["ref_id"]: ref for ref in recommendation["evidence_refs"]
            }
            assert admin_evidence_by_id[restricted_ref_id]["redacted"] is False
            recommendation_id = recommendation["recommendation_id"]
            stored_snapshot_id = recommendation["ranking_snapshot_id"]

            submit = client.post(
                f"/bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit",
                headers={
                    **admin_headers,
                    "Idempotency-Key": "ppl-alloc-012-admin-submit",
                },
                json={
                    "quarter": "2026-Q3",
                    "ranking_snapshot_id": stored_snapshot_id,
                },
            )
            assert submit.status_code == 202, submit.text
            assert submit.json()["data"]["ranking_snapshot_id"] == stored_snapshot_id

            operator_recommendations = client.get(
                "/bff/management/quarterly-ranking/recommendations",
                headers=HEADERS,
                params={
                    "quarter": "2026-Q3",
                    "personaId": LIVE_PERSONA_ID,
                    "page_size": 200,
                },
            )
            assert operator_recommendations.status_code == 200, operator_recommendations.text
            operator_recommendation = next(
                item
                for item in operator_recommendations.json()["data"]["items"]
                if item["recommendation_id"] == recommendation_id
            )
            operator_evidence_by_id = {
                ref["ref_id"]: ref
                for ref in operator_recommendation["evidence_refs"]
            }
            assert operator_evidence_by_id[restricted_ref_id]["redacted"] is True

            replay = client.post(
                f"/bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit",
                headers={
                    **HEADERS,
                    "Idempotency-Key": "ppl-alloc-012-operator-replay",
                },
                json={
                    "quarter": "2026-Q3",
                    "ranking_snapshot_id": stored_snapshot_id,
                },
            )
            assert replay.status_code == 200, replay.text
            replay_body = replay.json()
            assert replay_body["meta"]["idempotency"]["replayed"] is True
            assert replay_body["data"]["ranking_snapshot_id"] == stored_snapshot_id
            assert replay_body["meta"]["ranking_snapshot_id"] == stored_snapshot_id
            assert len(bff_main.command_store._get_all_commands()) == 1

            operator_review = replay_body["data"]["review"]
            operator_review_evidence = {
                ref["ref_id"]: ref for ref in operator_review["evidence_refs"]
            }
            source_recommendation = operator_review["source_recommendation"]
            source_recommendation_evidence = {
                ref["ref_id"]: ref
                for ref in source_recommendation["evidence_refs"]
            }
            for evidence_by_id in (
                operator_review_evidence,
                source_recommendation_evidence,
            ):
                restricted_ref = evidence_by_id.get(restricted_ref_id)
                if restricted_ref is not None:
                    assert restricted_ref["redacted"] is True
                    assert {
                        "source_document",
                        "source_ref",
                        "linked_object_summary",
                        "credibility",
                        "resolved_link",
                    }.isdisjoint(restricted_ref)

            def contains_source_document(value: Any) -> bool:
                if isinstance(value, dict):
                    return "source_document" in value or any(
                        contains_source_document(item) for item in value.values()
                    )
                if isinstance(value, list):
                    return any(contains_source_document(item) for item in value)
                return False

            assert contains_source_document(operator_review) is False
            assert contains_source_document(source_recommendation) is False
        finally:
            bff_main.read_store = original_store
            bff_main.command_store = original_command_store
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.clear()
            bff_main._FINAL_CONTRACT_IDEMPOTENCY.update(original_final_idempotency)


def test_multiple_active_bindings_fail_closed_without_seed_weight() -> None:
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td, fallback=False)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            persona_id = "persona-ppl-alloc-012-ambiguous"
            store.create_persona(
                persona_id=persona_id,
                name="PPL Alloc Ambiguous",
                actor_id="Codex2",
                lifecycle_state="live_running",
                metadata={"capital_mode": "live", "deployment_stage": "live"},
            )
            for suffix, weight in (("a", 0.03), ("b", 0.07)):
                store.create_persona_binding(
                    binding_id=f"binding-ppl-alloc-012-{suffix}",
                    persona_id=persona_id,
                    capital_pool_id=f"pool-{suffix}",
                    actor_id="Codex2",
                    validity="active",
                    metadata={
                        "capital_mode": "live",
                        "capital_sleeve_id": f"sleeve-{suffix}",
                        "current_weight": weight,
                    },
                )
            runtime_id = "runtime-ppl-alloc-012-ambiguous"
            store.create_runtime_binding(
                runtime_id=runtime_id,
                name="PPL Alloc Ambiguous",
                persona_id=persona_id,
                binding_id="binding-ppl-alloc-012-a",
                deployment_plan_id="plan-ppl-alloc-012-ambiguous",
                runtime_kind="live",
                actor_id="Codex2",
                state="running",
                params={"capital_pool_id": "pool-a"},
            )
            _install_runtime_observations(
                store,
                persona_id=persona_id,
                runtime_id=runtime_id,
            )

            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert response.status_code == 200, response.text
            row = _item_by_persona(response.json()["data"]["items"], persona_id)
            assert row["stage"] == "live_running"
            assert row["current_weight"] is None
            assert row["current_weight_source"] == "unavailable"
            assert row["capital_pool_id"] is None
            assert row["capital_sleeve_id"] is None
            assert row["eligible"] is False
            assert {
                "binding_mismatch",
                "missing_current_weight",
                "missing_capital_binding",
            }.issubset(set(row["exclusion_codes"]))
        finally:
            bff_main.read_store = original_store


def test_binding_runtime_and_stage_mismatches_fail_closed() -> None:
    binding = {
        "id": "binding-current-live",
        "binding_id": "binding-current-live",
        "persona_id": "persona-mismatch",
        "status": "active",
        "metadata": {"capital_mode": "live", "current_weight": 0.08},
    }
    mismatched_runtime = {
        "id": "runtime-old-paper",
        "runtime_id": "runtime-old-paper",
        "persona_id": "persona-mismatch",
        "binding_id": "binding-old-paper",
        "state": "running",
        "runtime_kind": "paper",
    }
    selected_binding, selected_runtime, resolution = bff_main._pm12_binding_runtime_context(
        persona_id="persona-mismatch",
        item={
            "binding_id": "binding-current-live",
            "runtime_ids": ["runtime-old-paper"],
        },
        bindings=[binding],
        runtimes=[mismatched_runtime],
    )
    assert selected_binding["binding_id"] == "binding-current-live"
    assert selected_runtime == {}
    assert resolution == "binding_mismatch"

    conflicting_binding = {
        "id": "binding-expired-despite-status",
        "binding_id": "binding-expired-despite-status",
        "persona_id": "persona-conflicting-lifecycle",
        "status": "active",
        "validity": "expired",
        "metadata": {"capital_mode": "live", "current_weight": 0.14},
    }
    conflicting_runtime = {
        "id": "runtime-stopped-despite-status",
        "runtime_id": "runtime-stopped-despite-status",
        "persona_id": "persona-conflicting-lifecycle",
        "binding_id": "binding-expired-despite-status",
        "status": "active",
        "state": "stopped",
        "runtime_kind": "live",
    }
    selected_binding, selected_runtime, resolution = bff_main._pm12_binding_runtime_context(
        persona_id="persona-conflicting-lifecycle",
        item={
            "binding_id": "binding-expired-despite-status",
            "runtime_ids": ["runtime-stopped-despite-status"],
        },
        bindings=[conflicting_binding],
        runtimes=[conflicting_runtime],
    )
    assert selected_binding == {}
    assert selected_runtime == {}
    assert resolution == "inactive"

    declared_expired_binding = {
        "id": "binding-b-expired",
        "binding_id": "binding-b-expired",
        "persona_id": "persona-stale-declaration",
        "validity": "expired",
        "metadata": {"capital_mode": "live", "current_weight": 0.13},
    }
    unrelated_active_binding = {
        "id": "binding-a-active",
        "binding_id": "binding-a-active",
        "persona_id": "persona-stale-declaration",
        "validity": "active",
        "metadata": {"capital_mode": "live", "current_weight": 0.05},
    }
    selected_binding, selected_runtime, resolution = bff_main._pm12_binding_runtime_context(
        persona_id="persona-stale-declaration",
        item={"binding_id": "binding-b-expired"},
        bindings=[unrelated_active_binding, declared_expired_binding],
        runtimes=[],
    )
    assert selected_binding == {}
    assert selected_runtime == {}
    assert resolution == "inactive"

    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        try:
            client = _client(td, fallback=False)
            store = bff_main.read_store
            assert isinstance(store, ReadSurfaceStore)
            store.create_persona(
                persona_id="persona-stage-mismatch",
                name="PPL Stage Mismatch",
                actor_id="Codex2",
                lifecycle_state="live_running",
                metadata={"capital_mode": "live", "deployment_stage": "live"},
            )
            store.create_persona_binding(
                binding_id="binding-stage-mismatch",
                persona_id="persona-stage-mismatch",
                capital_pool_id="pool-paper-wrong",
                actor_id="Codex2",
                validity="active",
                metadata={"capital_mode": "paper", "current_weight": 0.09},
            )
            store.create_persona(
                persona_id="persona-inactive-binding",
                name="PPL Inactive Binding",
                actor_id="Codex2",
                lifecycle_state="live_running",
                metadata={"capital_mode": "live", "deployment_stage": "live"},
            )
            store.create_persona_binding(
                binding_id="binding-inactive",
                persona_id="persona-inactive-binding",
                capital_pool_id="pool-expired",
                actor_id="Codex2",
                validity="expired",
                metadata={"capital_mode": "live", "current_weight": 0.11},
            )

            response = client.get(
                "/bff/management/quarterly-ranking",
                headers=HEADERS,
                params={"quarter": "2026-Q3", "page_size": 200},
            )
            assert response.status_code == 200, response.text
            rows = response.json()["data"]["items"]
            stage_mismatch = _item_by_persona(rows, "persona-stage-mismatch")
            assert stage_mismatch["stage"] == "not_running"
            assert stage_mismatch["current_weight"] is None
            assert stage_mismatch["capital_scope"] == "unbound"
            assert "missing_runtime" in stage_mismatch["exclusion_codes"]

            inactive = _item_by_persona(rows, "persona-inactive-binding")
            assert inactive["stage"] == "not_running"
            assert inactive["current_weight"] is None
            assert inactive["capital_scope"] == "unbound"
            assert inactive["binding_resolution"] == "inactive"
            assert inactive["eligible"] is False
        finally:
            bff_main.read_store = original_store


def test_pm12_quarterly_rows_allocation_policy_compatibility() -> None:
    from persona_allocation_policy import calculate_target_allocations
    row = {
        "persona_id": "persona-ppl-alloc-012-compat",
        "stage": "live_running",
        "tier": "s",
        "overall_score": 85.0,
        "current_weight": 0.04,
    }
    lines = calculate_target_allocations([row])
    assert len(lines) == 1
    line = lines[0]
    assert line["rank_score"] == 85.0
    assert line["target_weight"] == 0.05
    assert line["delta"] == 0.01
    assert "quarterly_increase_cap_25pct" in line["cap_reasons"]
