"""Live-route proof that allocation lineage is enforced by the Capital owner, not the BFF.

Operator decision 2026-10-07 (CAPITAL-ALLOCATION-LINEAGE-OWNER-20261007): PPL-ALLOC-009/012 lineage, deleted from the
BFF with the 9/1 main.py prune (c1894c3b0), is restored in the Capital owner.  The BFF routes only forward.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


from services.rankings.snapshots import snapshot_content_digest
from services.control_plane.bff.tests.rebalance_authority_test_support import (
    HEADERS,
    CapitalBffAuthorityHarness,
    rebalance_payload,
)

VIEWER = {"Authorization": "Bearer viewer-1:viewer"}
SNAPSHOT_ID = "ranking-quarterly-2026-q3-admitted"
ITEM = {
    "persona_id": "p-live", "stage": "live_running", "tier": "a", "pnl_score": 0.8, "sharpe_score": 0.7,
    "evidence_ref_ids": ["ev-1"],
}


class _RankingReader:
    """Stands in for the Rankings read store: only the admitted snapshot exists."""

    def get_ranking_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        if snapshot_id != SNAPSHOT_ID:
            return None
        return {
            "ranking_snapshot_id": SNAPSHOT_ID, "surface": "quarterly", "period": "2026-Q3", "items": [ITEM],
            "content_digest": snapshot_content_digest([ITEM], surface="quarterly", period="2026-Q3"),
        }


def _evaluate_body(**overrides: Any) -> Dict[str, Any]:
    row = {
        "persona_id": "p-live", "ranking_snapshot_id": SNAPSHOT_ID, "stage": "live_running", "tier": "a",
        "current_weight": 0.10, "capital_pool_id": "pool-real", "capital_sleeve_id": "sleeve-live",
    }
    body = {"ranking_snapshot_id": SNAPSHOT_ID, "allocation_policy_version": "persona-real-allocation-v1", "rows": [row]}
    body.update(overrides)
    return body


def _proposal(evaluation: Dict[str, Any]) -> Dict[str, Any]:
    payload = rebalance_payload()
    payload.update(
        ranking_snapshot_id=evaluation["ranking_snapshot_id"],
        allocation_evaluation_id=evaluation["allocation_evaluation_id"],
        allocation_policy_version=evaluation["allocation_policy_version"],
        lines=evaluation["lines"],
    )
    return payload


def _evaluate(harness: CapitalBffAuthorityHarness, body: Dict[str, Any], headers: Dict[str, str] = VIEWER):
    return harness.client.post("/bff/management/allocation-policy/evaluate", json=body, headers=headers)


def _propose(harness: CapitalBffAuthorityHarness, payload: Dict[str, Any], key: str):
    return harness.client.post("/bff/rebalances", json=payload, headers={**HEADERS, "Idempotency-Key": key})


def test_viewer_cannot_obtain_an_evaluation_for_a_forged_snapshot(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False, ranking_reader=_RankingReader()) as harness:
        forged = _evaluate(harness, _evaluate_body(ranking_snapshot_id="forged-snapshot"))
        assert forged.status_code == 422, forged.text
        assert "allocation_evaluation_id" not in forged.text

        row = _evaluate_body()["rows"][0]
        for tampered in ({**row, "persona_id": "p-unknown"}, {**row, "pnl_score": 5.0}, {**row, "ranking_snapshot_id": "other"}):
            assert _evaluate(harness, _evaluate_body(rows=[tampered])).status_code == 422
        assert _evaluate(harness, _evaluate_body(allocation_policy_version="any")).status_code == 422
        assert _evaluate(harness, _evaluate_body(rows=[row, row])).status_code == 422  # duplicate persona


def test_owner_admits_only_the_evaluation_it_can_reproduce_from_the_snapshot(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False, ranking_reader=_RankingReader()) as harness:
        evaluated = _evaluate(harness, _evaluate_body())
        assert evaluated.status_code == 200, evaluated.text
        evaluation = evaluated.json()["data"]
        honest = _proposal(evaluation)

        forged_snapshot = {**honest, "ranking_snapshot_id": "forged-snapshot"}
        assert _propose(harness, forged_snapshot, "rb-forged-snapshot").status_code in (409, 422)

        forged_evaluation = {**honest, "allocation_evaluation_id": "allocation-evaluation-forged"}
        assert _propose(harness, forged_evaluation, "rb-forged-evaluation").status_code in (409, 422)

        line = dict(evaluation["lines"][0])
        line.update(target_weight=0.5, delta=0.4)
        from services.capital.allocation_store import allocation_line_digest

        line["allocation_line_digest"] = allocation_line_digest(line)  # self-consistent, but not the snapshot's weight
        rejected = _propose(harness, {**honest, "lines": [line]}, "rb-forged-weight")
        assert rejected.status_code in (409, 422), rejected.text
        assert harness.capital_client.get("/api/rebalances").json() == []

        accepted = _propose(harness, honest, "rb-honest")
        assert accepted.status_code == 201, accepted.text


def test_owner_without_a_rankings_store_is_unavailable_not_permissive(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path, seed_allocation=False, ranking_reader=_RankingReader()) as harness:
        from services.capital import allocation_lineage

        allocation_lineage.create_ranking_reader = lambda: (_ for _ in ()).throw(ValueError("RANKING_STORE_DSN is required"))
        response = _evaluate(harness, _evaluate_body())
        assert response.status_code == 503, response.text


def test_pool_create_with_a_foreign_metadata_tenant_is_rejected_before_the_owner_commits(tmp_path: Path) -> None:
    with CapitalBffAuthorityHarness(tmp_path) as harness:
        body = {"pool_id": "pool-tenant-clash", "name": "Clash", "owner_id": "fund-1", "owner_type": "fund",
                "metadata": {"tenant_id": "another-tenant"}}
        headers = {**HEADERS, "Idempotency-Key": "create-pool-tenant-clash"}
        first = harness.client.post("/bff/capital-pools", json=body, headers=headers)
        assert first.status_code < 500, first.text  # never a 502 after the owner committed
        assert first.status_code in (403, 422)
        assert harness.capital_client.get("/api/capital-pools/pool-tenant-clash").status_code == 404
        retry = harness.client.post("/bff/capital-pools", json=body, headers=headers)
        assert retry.status_code == first.status_code
