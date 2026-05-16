"""Tests for the canonical StrategySpec model and schema."""

import unittest

from .models import (
    CodeRef,
    DataDependency,
    EvidenceRef,
    ExecutionProfile,
    Governance,
    MarketScope,
    Provenance,
    StrategyExecutionModeHint,
    StrategyQuantityType,
    StrategySpec,
    StrategySpecValidationError,
    validate_strategy_spec_payload,
)


def _strategy_spec_payload() -> dict:
    return {
        "spec_version": "1.0",
        "strategy_id": "strat-momentum-v1",
        "title": "Momentum Strategy",
        "hypothesis": "Liquid equities with persistent momentum continue trending over weekly windows.",
        "objective": "Replicate the signal under governed research before paper or live promotion.",
        "lifecycle_state": "candidate",
        "market_scope": {
            "symbols": ["RESEARCH_UNIVERSE"],
            "asset_classes": ["equities"],
            "venues": ["research"],
            "frequency": "1d",
        },
        "data_dependencies": [
            {"ref": "source-record:openalex/W123", "kind": "source_record"},
            {"ref": "dataset:tw-equities-daily-v1", "kind": "dataset"},
        ],
        "execution_profile": {
            "signal_schema_version": "1.0",
            "quantity_type": "PERCENT_PORTFOLIO",
            "rebalance_cadence": "1w",
            "execution_mode_hint": "research",
        },
        "evaluation_plan": {
            "metrics": ["sharpe_ratio", "max_drawdown"],
            "candidate_gate": "Pass source lineage and replication checks.",
            "paper_gate": "Require explicit paper approval.",
            "live_gate": "Require paper evidence and explicit live approval.",
        },
        "governance": {
            "approval_required": True,
            "policy_id": "research-strategy-spec-v1",
            "risk_profile": "research_only",
        },
        "provenance": {
            "source_kind": "paper",
            "created_at": "2026-05-16T07:00:00Z",
            "source_refs": ["source-record:openalex/W123"],
            "created_by": "Codex",
        },
        "evidence_refs": [
            {
                "ref_type": "evidence_bundle",
                "ref_id": "evbundle-alpha-001",
                "association": "source_evidence",
            },
            {
                "ref_type": "evidence_item",
                "ref_id": "evi-alpha-001",
                "source_id": "source-record:openalex/W123",
                "evidence_item_id": "evi-alpha-001",
                "citation_ref": "alpha-paper#abstract",
                "association": "supporting_evidence",
            },
        ],
        "code_refs": [
            {
                "repo_ref": "https://github.com/example/alpha",
                "path": "examples/alpha.py",
                "source_id": "source-record:github/example-alpha",
                "commit": "abc123",
                "symbol": "build_alpha",
                "line_start": 10,
                "line_end": 24,
                "association": "strategy_code_reference",
            }
        ],
        "metadata": {
            "task_id": "STRAT-001",
        },
    }


class TestStrategySpecModels(unittest.TestCase):
    def test_strategy_spec_payload_round_trips_through_model_and_schema(self):
        payload = _strategy_spec_payload()

        validate_strategy_spec_payload(payload)
        spec = StrategySpec.from_dict(payload)

        self.assertEqual(spec.strategy_id, "strat-momentum-v1")
        self.assertEqual(spec.market_scope.symbols, ("RESEARCH_UNIVERSE",))
        self.assertEqual(spec.execution_profile.execution_mode_hint, "research")
        self.assertEqual(spec.evidence_refs[0].ref_id, "evbundle-alpha-001")
        self.assertEqual(spec.code_refs[0].path, "examples/alpha.py")
        self.assertEqual(spec.to_dict(), payload)
        validate_strategy_spec_payload(spec.to_dict())

    def test_model_construction_accepts_typed_subobjects(self):
        spec = StrategySpec(
            spec_version="1.0",
            strategy_id="strat-typed-v1",
            title="Typed Strategy",
            hypothesis="A typed payload should serialize to the canonical contract.",
            objective="Prove typed construction before registry usage.",
            market_scope=MarketScope(symbols=["RESEARCH_UNIVERSE"], frequency="1d"),
            data_dependencies=[DataDependency(ref="note:typed", kind="note")],
            execution_profile=ExecutionProfile(
                signal_schema_version="1.0",
                quantity_type=StrategyQuantityType.PERCENT_PORTFOLIO,
                execution_mode_hint=StrategyExecutionModeHint.RESEARCH,
            ),
            evaluation_plan={"metrics": ["sharpe_ratio"]},
            governance=Governance(approval_required=True),
            provenance=Provenance(
                source_kind="note",
                created_at="2026-05-16T07:00:00Z",
                source_refs=["note:typed"],
            ),
            evidence_refs=[
                EvidenceRef(
                    ref_type="evidence_bundle",
                    ref_id="evbundle-typed",
                    association="source_evidence",
                )
            ],
            code_refs=[
                CodeRef(
                    repo_ref="https://github.com/example/typed",
                    path="typed_strategy.py",
                    line_start=1,
                    line_end=8,
                )
            ],
        )

        payload = spec.to_dict()

        self.assertEqual(payload["execution_profile"]["quantity_type"], "PERCENT_PORTFOLIO")
        self.assertEqual(payload["evidence_refs"][0]["ref_id"], "evbundle-typed")
        self.assertEqual(payload["code_refs"][0]["path"], "typed_strategy.py")
        self.assertNotIn("metadata", payload)
        validate_strategy_spec_payload(payload)

    def test_schema_rejects_unknown_top_level_fields(self):
        payload = _strategy_spec_payload()
        payload["unexpected"] = True

        with self.assertRaisesRegex(StrategySpecValidationError, "Additional properties"):
            StrategySpec.from_dict(payload)

    def test_model_rejects_duplicate_data_dependencies(self):
        payload = _strategy_spec_payload()
        payload["data_dependencies"].append({"ref": "source-record:openalex/W123", "kind": "source_record"})

        with self.assertRaisesRegex(StrategySpecValidationError, "duplicate kind/ref pairs"):
            StrategySpec.from_dict(payload)

    def test_model_rejects_duplicate_evidence_refs(self):
        payload = _strategy_spec_payload()
        payload["evidence_refs"].append(dict(payload["evidence_refs"][0]))

        with self.assertRaisesRegex(StrategySpecValidationError, "duplicate ref_type/ref_id pairs"):
            StrategySpec.from_dict(payload)

    def test_model_rejects_invalid_code_line_range(self):
        payload = _strategy_spec_payload()
        payload["code_refs"][0]["line_end"] = 9

        with self.assertRaisesRegex(StrategySpecValidationError, "line_end"):
            StrategySpec.from_dict(payload)

    def test_model_rejects_non_manual_provenance_without_source_refs(self):
        payload = _strategy_spec_payload()
        payload["provenance"].pop("source_refs")

        with self.assertRaisesRegex(StrategySpecValidationError, "provenance.source_refs"):
            StrategySpec.from_dict(payload)

    def test_model_requires_approval_for_execution_stage_hints(self):
        payload = _strategy_spec_payload()
        payload["execution_profile"]["execution_mode_hint"] = "live"
        payload["governance"]["approval_required"] = False

        with self.assertRaisesRegex(StrategySpecValidationError, "approval_required=true"):
            StrategySpec.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
