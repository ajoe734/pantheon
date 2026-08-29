from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main
from ports import DefaultResearchKnowledgeSourcePort


OPERATOR_AUTH = "Bearer test-operator:operator"


def _artifact(
    artifact_id: str,
    *,
    version: int,
    parent_artifact_id: str | None,
    status: str,
    sharpe_ratio: float,
    experiment_id: str,
    created_at: str,
) -> dict:
    return {
        "artifact_id": artifact_id,
        "lineage_id": "lin_xyz987",
        "version": version,
        "parent_artifact_id": parent_artifact_id,
        "status": status,
        "name": f"MACD-momentum-v{version}",
        "artifact_type": "strategy_model",
        "produced_by_experiment_id": experiment_id,
        "linked_ticket_id": "tkt_5432",
        "created_at": created_at,
        "metrics": {
            "sharpe_ratio": sharpe_ratio,
            "sortino_ratio": sharpe_ratio + 0.3,
            "max_drawdown": -0.14 + (version * 0.01),
            "annualized_return": 0.10 + (version * 0.02),
            "win_rate": 0.48 + (version * 0.02),
            "avg_trade_duration_days": 5.0 - (version * 0.4),
            "total_trades": 350 + (version * 20),
        },
        "parameters": {
            "fast_period": 9 + version,
            "slow_period": 26,
            "signal_period": 9,
            "position_sizing": "fixed_fractional",
            "risk_per_trade": 0.01,
        },
        "provenance": {
            "linked_experiment": {
                "experiment_id": experiment_id,
                "display_label": f"MACD run v{version}",
            },
            "linked_ticket": {
                "ticket_id": "tkt_5432",
                "title": "Momentum strategy parameter optimization",
            },
            "lineage_refs": [],
        },
    }


_ARTIFACTS = {
    "art_2024_abc121": _artifact(
        "art_2024_abc121",
        version=1,
        parent_artifact_id=None,
        status="superseded",
        sharpe_ratio=0.98,
        experiment_id="exp_9800",
        created_at="2026-04-10T09:00:00Z",
    ),
    "art_2024_abc122": _artifact(
        "art_2024_abc122",
        version=2,
        parent_artifact_id="art_2024_abc121",
        status="superseded",
        sharpe_ratio=1.18,
        experiment_id="exp_9801",
        created_at="2026-04-12T09:00:00Z",
    ),
    "art_2024_abc123": _artifact(
        "art_2024_abc123",
        version=3,
        parent_artifact_id="art_2024_abc122",
        status="sealed",
        sharpe_ratio=1.42,
        experiment_id="exp_9876",
        created_at="2026-04-14T09:00:00Z",
    ),
    "art_2024_pending01": {
        **_artifact(
            "art_2024_pending01",
            version=1,
            parent_artifact_id=None,
            status="pending",
            sharpe_ratio=0.5,
            experiment_id="exp_pending",
            created_at="2026-04-15T09:00:00Z",
        ),
        "lineage_id": "lin_pending",
        "linked_ticket_id": "tkt_pending",
    },
}


class _ArtifactPortDouble(DefaultResearchKnowledgeSourcePort):
    """Typed RW-05 double preserving the route's legacy projection contract."""

    def __init__(self) -> None:
        super().__init__(research_artifacts_store=_ARTIFACTS)

    def list_research_artifacts(
        self,
        *,
        experiment_id: str | None = None,
        ticket_id: str | None = None,
        lineage_id: str | None = None,
        status: str | None = None,
        **_: object,
    ) -> list[dict]:
        records = list(self._artifacts.values())
        if experiment_id:
            records = [record for record in records if record.get("produced_by_experiment_id") == experiment_id]
        if ticket_id:
            records = [record for record in records if record.get("linked_ticket_id") == ticket_id]
        if lineage_id:
            records = [record for record in records if record.get("lineage_id") == lineage_id]
        if status:
            records = [record for record in records if record.get("status") == status]
        records.sort(key=lambda record: str(record.get("created_at") or ""), reverse=True)
        return [self._project_research_artifact_summary(record) for record in records]

    def get_research_artifact(self, artifact_id: str | None) -> dict | None:
        detail = super().get_research_artifact(artifact_id)
        raw = self._artifacts.get(str(artifact_id or ""))
        if detail is None or raw is None:
            return None
        detail["parent_artifact_id"] = raw.get("parent_artifact_id")
        detail["version_chain"] = detail["lineage"]["versions"]
        detail["provenance"] = raw.get("provenance") or {}
        detail["experiment_refs"] = (raw.get("metadata") or {}).get("experiment_refs") or []
        detail["allowedActions"]["canViewDetail"] = True
        return detail

    def set_experiment_refs(self, artifact_id: str, refs: list[dict]) -> None:
        self._artifacts[artifact_id]["metadata"] = {"experiment_refs": refs}

    def compare_research_artifacts(self, artifact_ids: list[str]) -> dict:
        base = super().compare_research_artifacts(artifact_ids)
        field_pairs = []
        for comparison in base["comparisons"]:
            delta = comparison.get("delta")
            field_pairs.append(
                {
                    **comparison,
                    "group": comparison.get("category"),
                    "change_label": "improved" if comparison.get("polarity") == "better" else comparison.get("polarity"),
                    "delta_direction": "up" if isinstance(delta, (int, float)) and delta > 0 else ("down" if isinstance(delta, (int, float)) and delta < 0 else "flat"),
                }
            )
        return {
            "artifacts": base["artifacts"],
            "field_pairs": field_pairs,
            "change_summary": {"total_fields_compared": len(field_pairs)},
            "provenance_pairs": [artifact.get("provenance") or {} for artifact in base["artifacts"]],
        }


@contextmanager
def _seeded_client():
    with tempfile.TemporaryDirectory() as td:
        original_store = bff_main.read_store
        bff_main.read_store = _ArtifactPortDouble()
        client = TestClient(bff_main.app)
        try:
            yield client
        finally:
            bff_main.read_store = original_store


def test_rw05_list_contract_returns_artifact_registry_projection() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/artifacts?ticket_id=tkt_5432&page_size=20",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["total_count"] == 3
        assert [item["artifact_id"] for item in payload["artifacts"]] == [
            "art_2024_abc123",
            "art_2024_abc122",
            "art_2024_abc121",
        ]
        assert payload["artifacts"][0]["is_current_version"] is True
        assert payload["artifacts"][1]["is_current_version"] is False
        assert payload["artifacts"][0]["allowedActions"] == {"canCompare": True}
        assert payload["artifacts"][1]["allowedActions"] == {"canCompare": True}
        assert payload["meta"]["surfaces"]["artifact_list"] in {"ok", "degraded"}


def test_rw05_list_contract_returns_non_comparable_authority_for_pending_artifacts() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/artifacts?status=pending&page_size=20",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["total_count"] == 1
        assert payload["artifacts"][0]["artifact_id"] == "art_2024_pending01"
        assert payload["artifacts"][0]["allowedActions"] == {"canCompare": False}


def test_rw05_detail_contract_returns_version_chain_and_allowed_actions() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/artifacts/art_2024_abc123",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["artifact_id"] == "art_2024_abc123"
        assert payload["parent_artifact_id"] == "art_2024_abc122"
        assert [item["version"] for item in payload["version_chain"]] == [1, 2, 3]
        assert payload["provenance"]["linked_ticket"]["ticket_id"] == "tkt_5432"
        assert payload["allowedActions"] == {
            "canCompare": True,
            "canViewDetail": True,
        }
        assert payload["meta"]["surfaces"]["artifact_detail"] in {"ok", "degraded"}


def test_rw05_detail_exposes_wandb_experiment_refs_from_registry_metadata() -> None:
    with _seeded_client() as client:
        bff_main.read_store.set_experiment_refs(
            "art_2024_abc123",
            [
                {
                    "backend": "wandb",
                    "run_id": "fake-run-001",
                    "run_uri": "https://wandb.ai/pantheon-ci/pantheon-test/runs/fake-run-001",
                    "artifact_uri": "wandb://pantheon-ci/pantheon-test/pantheon-artifact:approved",
                    "sync_status": "online_synced",
                    "readback_refs": {
                        "verified": True,
                        "run_path": "pantheon-ci/pantheon-test/fake-run-001",
                        "artifact_path": "pantheon-ci/pantheon-test/pantheon-artifact:approved",
                    },
                }
            ],
        )

        response = client.get(
            "/api/v1/artifacts/art_2024_abc123",
            headers={"Authorization": OPERATOR_AUTH},
        )

        assert response.status_code == 200, response.text
        refs = response.json()["experiment_refs"]
        assert refs[0]["backend"] == "wandb"
        assert refs[0]["sync_status"] == "online_synced"
        assert refs[0]["readback_refs"]["verified"] is True
        assert refs[0]["readback_refs"]["run_path"] == "pantheon-ci/pantheon-test/fake-run-001"


def test_rw05_compare_contract_returns_backend_composed_diff() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/artifacts/compare?artifact_ids=art_2024_abc121,art_2024_abc123",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        assert len(payload["artifacts"]) == 2
        sharpe_pair = next(
            pair for pair in payload["field_pairs"]
            if pair["field_key"] == "metrics.sharpe_ratio"
        )
        assert sharpe_pair["group"] == "performance"
        assert sharpe_pair["change_label"] == "improved"
        assert sharpe_pair["delta_direction"] == "up"
        assert payload["change_summary"]["total_fields_compared"] >= 10
        assert payload["provenance_pairs"][1]["linked_experiment"]["experiment_id"] == "exp_9876"
        assert payload["meta"]["surfaces"]["artifact_compare"] in {"ok", "degraded"}


def test_rw05_compare_rejects_non_comparable_artifacts() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/artifacts/compare?artifact_ids=art_2024_abc123,art_2024_pending01",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 422, response.text

        payload = response.json()
        assert payload["error"]["code"] == "OPERATION_NOT_ALLOWED"
        assert payload["error"]["details"]["non_comparable_artifacts"] == [
            {
                "artifact_id": "art_2024_pending01",
                "status": "pending",
                "reason": "Only sealed and superseded artifacts may be compared.",
            }
        ]


def test_rw05_compare_rejects_invalid_cardinality() -> None:
    with _seeded_client() as client:
        response = client.get(
            "/api/v1/artifacts/compare?artifact_ids=art_2024_abc123",
            headers={"Authorization": OPERATOR_AUTH},
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"
