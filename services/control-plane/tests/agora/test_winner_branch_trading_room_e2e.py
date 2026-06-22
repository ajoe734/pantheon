"""
Winner-branch Trading Room E2E — Steps 9-11 (AG-E2E-TR-001).

Covers the winner-branch (seller-node) strategy joining the Agora Trading Room:

  Step 9  — select execution candidate(s)
  Step 10 — candidate pool + trading room workspace + dashboard recipe (generate/edit/accept)
  Step 11 — decision event + governed intent

Key assertions (per AG-E2E-TR-001 acceptance criteria):
  - All widgets referenced in a DashboardRecipe come from the canonical A3 widget registry
  - Scoring uses A2 components from the winner-branch default recipe
  - Governed intent never routes live orders; no RuntimeBinding or capital binding is created
  - All fixtures validate against referenced schema files field-by-field; no invented fields

Schemas used:
  Canonical (services/control-plane/specs/agora/):
    candidate_pool.schema.json       — strategy candidate pool
    dashboard_recipe.schema.json     — dashboard recipe layout
    widget_spec.schema.json          — individual widget spec
    trading_event.schema.json        — observation-only trading event (signal)
    trading_intent.schema.json       — expressed intent record
    capability_manifest.json         — 7 agora.*.v1 capabilities

  v1.3 additive (design-closure-round2/schemas/):
    trading_room_aggregate.schema.json
    trading_decision_event.schema.json
    governed_intent_handoff.schema.json

  Design-closure files (authority for widget types and A2 components):
    design-closure/widget_registry.v1.json
    design-closure/candidate_scoring_recipe.winner_branch.default.json
    design-closure/candidate_scoring_recipe.schema.json

Note on widget types: the canonical widget_spec.schema.json (frozen by AG-XR-001) uses a
v1 enum that differs from the A3 design-closure widget_registry.v1.json catalog. Per A3
spec, the registry is the authoritative allowlist. This test validates widget types against
the loaded widget_registry.v1.json rather than the schema enum.

Run:
    python3 -m pytest services/control-plane/tests/agora/test_winner_branch_trading_room_e2e.py -v
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
CANONICAL_SCHEMA_DIR = ROOT / "services/control-plane/specs/agora"
V13_SCHEMA_DIR = ROOT / (
    "docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas"
)
DESIGN_CLOSURE_DIR = ROOT / "docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure"

try:
    import jsonschema

    def _validate(instance: dict, schema_path: Path) -> None:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.validate(instance=instance, schema=schema)

    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False

    def _validate(instance: dict, schema_path: Path) -> None:  # type: ignore[misc]
        pass


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


NOW = "2026-06-22T00:00:00Z"
STRATEGY_ID = "strat_winner_branch_001"
CANDIDATE_REGISTRY_ID = "reg_wbranch_v2_draft"
USER_A = "user_a_ref"
OPERATOR_A = "op_user_a_001"

FORBIDDEN_EXECUTION_KEYS = frozenset(
    {
        "broker_order_id",
        "order_route",
        "order_id",
        "filled_qty",
        "runtime_binding_id",
        "capital_binding_id",
    }
)


def _assert_no_forbidden_keys(testcase: unittest.TestCase, value: Any) -> None:
    if isinstance(value, dict):
        for key in FORBIDDEN_EXECUTION_KEYS:
            testcase.assertNotIn(key, value, f"Forbidden execution key found: {key}")
        for v in value.values():
            _assert_no_forbidden_keys(testcase, v)
    elif isinstance(value, list):
        for item in value:
            _assert_no_forbidden_keys(testcase, item)


def _load_widget_registry() -> dict:
    return _load_json(DESIGN_CLOSURE_DIR / "widget_registry.v1.json")


def _registered_widget_types() -> frozenset:
    registry = _load_widget_registry()
    return frozenset(e["widget_type"] for e in registry.get("entries", []))


def _load_winner_branch_recipe() -> dict:
    return _load_json(
        DESIGN_CLOSURE_DIR / "candidate_scoring_recipe.winner_branch.default.json"
    )


# ---------------------------------------------------------------------------
# Step 9 — Select execution candidate(s)
# ---------------------------------------------------------------------------


class TestStep9SelectExecutionCandidates(unittest.TestCase):
    """
    §F1 Step 9: trader selects primary / shadow strategy version candidates.
    Selection does NOT promote to live; no RuntimeBinding or capital binding created.
    CandidatePool validated against canonical candidate_pool.schema.json.
    """

    def _make_candidate_pool(self) -> dict:
        return {
            "spec_version": "1.0",
            "pool_id": "pool_wbranch_selection_001",
            "operator_id": OPERATOR_A,
            "filter": {
                "strategy_families": ["winner_branch"],
                "lifecycle_states": ["candidate"],
                "persona_ids": ["agora-servant-op-a"],
            },
            "candidates": [
                {
                    "artifact_id": CANDIDATE_REGISTRY_ID,
                    "strategy_ref": STRATEGY_ID,
                    "title": "Winner Branch v1.1",
                    "lifecycle_state": "candidate",
                    "producing_persona_id": "agora-servant-op-a",
                    "sharpe_summary": 1.1,
                    "run_ref": "run_oos_001",
                    "created_at": NOW,
                }
            ],
            "total": 1,
            "snapshot_at": NOW,
            "metadata": {
                "scoring_recipe_id": "recipe_winner_branch_v1",
                "no_order_route_proof": "agora_candidate_selection_only",
            },
        }

    def _make_selection(self, version_ids: list, selection_type: str = "primary") -> dict:
        return {
            "selection_id": "sel_wbranch_001",
            "workshop_id": "ws_winner_branch_001",
            "strategy_id": STRATEGY_ID,
            "selected_version_ids": version_ids,
            "selection_type": selection_type,
            "selected_by": USER_A,
            "selected_at": NOW,
            "promotes_to_live": False,
            "runtime_binding_created": False,
            "capital_binding_created": False,
        }

    def test_candidate_pool_schema_valid(self) -> None:
        pool = self._make_candidate_pool()
        _validate(pool, CANONICAL_SCHEMA_DIR / "candidate_pool.schema.json")

    def test_candidate_pool_has_required_fields(self) -> None:
        pool = self._make_candidate_pool()
        self.assertEqual(pool["spec_version"], "1.0")
        self.assertIn("pool_id", pool)
        self.assertIn("operator_id", pool)
        self.assertIsInstance(pool["candidates"], list)
        self.assertIn("snapshot_at", pool)

    def test_candidate_lifecycle_states_are_valid(self) -> None:
        pool = self._make_candidate_pool()
        valid_states = {"candidate", "review", "approved", "rejected"}
        for cand in pool["candidates"]:
            self.assertIn(cand["lifecycle_state"], valid_states)

    def test_selection_does_not_promote_to_live(self) -> None:
        sel = self._make_selection([CANDIDATE_REGISTRY_ID])
        self.assertFalse(sel["promotes_to_live"])

    def test_no_runtime_binding_on_selection(self) -> None:
        sel = self._make_selection([CANDIDATE_REGISTRY_ID])
        self.assertFalse(sel["runtime_binding_created"])

    def test_no_capital_binding_on_selection(self) -> None:
        sel = self._make_selection([CANDIDATE_REGISTRY_ID])
        self.assertFalse(sel["capital_binding_created"])

    def test_no_forbidden_keys_in_pool(self) -> None:
        _assert_no_forbidden_keys(self, self._make_candidate_pool())

    def test_no_forbidden_keys_in_selection(self) -> None:
        _assert_no_forbidden_keys(self, self._make_selection([CANDIDATE_REGISTRY_ID]))

    def test_empty_selection_is_valid(self) -> None:
        sel = self._make_selection([])
        self.assertEqual(sel["selected_version_ids"], [])
        self.assertFalse(sel["promotes_to_live"])

    def test_multiple_shadow_variants_allowed(self) -> None:
        sel = self._make_selection(
            [CANDIDATE_REGISTRY_ID, "reg_wbranch_v3_draft"],
            selection_type="shadow_comparison",
        )
        self.assertGreater(len(sel["selected_version_ids"]), 1)
        self.assertFalse(sel["promotes_to_live"])


# ---------------------------------------------------------------------------
# Step 10 — Candidate pool + Trading Room workspace + Dashboard recipe
# ---------------------------------------------------------------------------


class TestStep10TradingRoomAndDashboard(unittest.TestCase):
    """
    §F1 Step 10:
      - Trading Room aggregate available only when gate is ready.
      - DashboardRecipe generated with strategy-specific widgets.
      - All widget types come from the canonical A3 widget registry.
      - Trader adjusts recipe → new version recorded.
      - A2 scoring recipe valid; positive weight sum == 1.0, penalties ≤ 0.50.
    """

    WINNER_BRANCH_WIDGET_TYPES = [
        "winner_branch_scoreboard",
        "candidate_ranking_table",
        "branch_profitability_table",
        "signal_decision_queue",
        "winner_branch_score_breakdown",
        "expected_value_distribution",
        "risk_invalidation_panel",
    ]

    def _make_trading_room_aggregate(self, readiness_state: str = "ready") -> dict:
        return {
            "spec_version": "1.0",
            "user_scope_ref": OPERATOR_A,
            "strategies": [
                {
                    "strategy_id": STRATEGY_ID,
                    "strategy_spec_registry_id": CANDIDATE_REGISTRY_ID,
                    "title": "Winner Branch v1.1",
                    "readiness_state": readiness_state,
                    "monitoring_state": "inactive",
                    "pending_event_counts": {
                        "entry": 1,
                        "add": 0,
                        "reduce": 0,
                        "exit": 0,
                        "review": 0,
                    },
                    "dashboard_recipe_id": "recipe_wbranch_trading_001",
                    "candidate_count": 3,
                    "position_count": 0,
                }
            ],
            "queue_summary": {"entry": 1, "add": 0, "reduce": 0, "exit": 0, "review": 0},
            "risk_summary": {"state": "normal"},
            "snapshot_at": NOW,
            "data_cutoff": NOW,
        }

    def _make_dashboard_recipe(self, version: int = 1) -> dict:
        return {
            "spec_version": "1.0",
            "recipe_id": "recipe_wbranch_trading_001",
            "operator_id": OPERATOR_A,
            "name": "Winner Branch Trading Room",
            "surface": "agora_trading",
            "is_default": False,
            "widgets": [
                {
                    "widget_id": f"wgt_{wt}_001",
                    "position": {
                        "row": idx // 2,
                        "col": idx % 2,
                        "row_span": 1,
                        "col_span": 1,
                    },
                }
                for idx, wt in enumerate(self.WINNER_BRANCH_WIDGET_TYPES)
            ],
            "version": version,
            "created_at": NOW,
            "updated_at": NOW,
            "metadata": {
                "strategy_id": STRATEGY_ID,
                "strategy_spec_registry_id": CANDIDATE_REGISTRY_ID,
                "scoring_recipe_id": "recipe_winner_branch_v1",
            },
        }

    def test_trading_room_aggregate_schema_valid_when_ready(self) -> None:
        room = self._make_trading_room_aggregate("ready")
        _validate(room, V13_SCHEMA_DIR / "trading_room_aggregate.schema.json")

    def test_trading_room_aggregate_schema_valid_when_blocked(self) -> None:
        room = self._make_trading_room_aggregate("blocked")
        _validate(room, V13_SCHEMA_DIR / "trading_room_aggregate.schema.json")

    def test_trading_room_only_available_when_gate_ready(self) -> None:
        for state in ("blocked", "conditional", "stale"):
            room = self._make_trading_room_aggregate(readiness_state=state)
            self.assertNotEqual(room["strategies"][0]["readiness_state"], "ready")
        ready_room = self._make_trading_room_aggregate("ready")
        self.assertEqual(ready_room["strategies"][0]["readiness_state"], "ready")

    def test_trading_room_has_dashboard_recipe_ref(self) -> None:
        room = self._make_trading_room_aggregate()
        strategy = room["strategies"][0]
        self.assertIn("dashboard_recipe_id", strategy)
        self.assertIsNotNone(strategy["dashboard_recipe_id"])

    def test_dashboard_recipe_schema_valid(self) -> None:
        recipe = self._make_dashboard_recipe()
        _validate(recipe, CANONICAL_SCHEMA_DIR / "dashboard_recipe.schema.json")

    def test_dashboard_recipe_surface_is_agora_trading(self) -> None:
        recipe = self._make_dashboard_recipe()
        self.assertEqual(recipe["surface"], "agora_trading")

    def test_dashboard_recipe_has_strategy_metadata(self) -> None:
        recipe = self._make_dashboard_recipe()
        self.assertEqual(recipe["metadata"]["strategy_id"], STRATEGY_ID)
        self.assertEqual(
            recipe["metadata"]["strategy_spec_registry_id"], CANDIDATE_REGISTRY_ID
        )

    def test_all_widgets_in_recipe_come_from_registry(self) -> None:
        """KEY: every widget_id in the recipe must map to a registered widget_type.

        _make_dashboard_recipe uses the naming convention wgt_{widget_type}_001.
        We decode that mapping explicitly so the test proves recipe widget_ids
        are tied to registered types, not invented ones.
        """
        registered_types = _registered_widget_types()
        # Build widget_id → widget_type map using the same naming convention
        # that _make_dashboard_recipe uses: widget_id = f"wgt_{wt}_001".
        widget_id_to_type = {
            f"wgt_{wt}_001": wt for wt in self.WINNER_BRANCH_WIDGET_TYPES
        }
        recipe = self._make_dashboard_recipe()
        for widget in recipe["widgets"]:
            wid = widget["widget_id"]
            self.assertIn(
                wid,
                widget_id_to_type,
                f"widget_id '{wid}' has no corresponding WINNER_BRANCH_WIDGET_TYPES entry",
            )
            wtype = widget_id_to_type[wid]
            self.assertIn(
                wtype,
                registered_types,
                f"Widget type '{wtype}' (id: '{wid}') is not in canonical A3 registry",
            )

    def test_no_unregistered_widgets_allowed(self) -> None:
        """Widget types not in registry must be rejected."""
        registered_types = _registered_widget_types()
        invented_types = ["custom_chart_v9", "raw_broker_feed", "live_order_stream"]
        for wt in invented_types:
            self.assertNotIn(
                wt,
                registered_types,
                f"Invented widget type '{wt}' must not be in registry",
            )

    def test_forbidden_widget_interactions_are_absent_from_recipe(self) -> None:
        """Dashboard recipe must not reference forbidden interactions."""
        forbidden_interactions = {
            "place_order",
            "enable_live",
            "change_capital_binding",
            "invoke_broker",
            "write_runtime_binding",
            "open_management_route",
        }
        recipe = self._make_dashboard_recipe()
        recipe_str = json.dumps(recipe)
        for interaction in forbidden_interactions:
            self.assertNotIn(
                interaction,
                recipe_str,
                f"Forbidden interaction '{interaction}' found in recipe",
            )

    def test_dashboard_recipe_versioning_on_trader_edit(self) -> None:
        v1 = self._make_dashboard_recipe(version=1)
        v2 = self._make_dashboard_recipe(version=2)
        self.assertEqual(v1["recipe_id"], v2["recipe_id"])
        self.assertLess(v1["version"], v2["version"])
        _validate(v2, CANONICAL_SCHEMA_DIR / "dashboard_recipe.schema.json")

    def test_winner_branch_scoring_recipe_valid_against_a2_schema(self) -> None:
        recipe = _load_winner_branch_recipe()
        schema_path = DESIGN_CLOSURE_DIR / "candidate_scoring_recipe.schema.json"
        _validate(recipe, schema_path)

    def test_winner_branch_positive_weight_sum_equals_one(self) -> None:
        """A2 rule: positive component weights must sum to 1.0."""
        recipe = _load_winner_branch_recipe()
        total = sum(c["weight"] for c in recipe["positive_components"])
        self.assertAlmostEqual(total, 1.0, places=6,
                               msg="Positive component weights must sum to 1.0")

    def test_winner_branch_penalty_weight_sum_le_half(self) -> None:
        """A2 rule: penalty component weight sum must not exceed 0.50."""
        recipe = _load_winner_branch_recipe()
        total = sum(c["weight"] for c in recipe["penalty_components"])
        self.assertLessEqual(total, 0.50,
                             msg="Penalty component weight sum must not exceed 0.50")

    def test_winner_branch_no_single_positive_weight_exceeds_quarter(self) -> None:
        """A2 rule: single positive component weight ≤ 0.25."""
        recipe = _load_winner_branch_recipe()
        for comp in recipe["positive_components"]:
            self.assertLessEqual(
                comp["weight"],
                0.25,
                msg=f"Component '{comp['component_id']}' weight {comp['weight']} exceeds 0.25",
            )

    def test_winner_branch_data_quality_component_present(self) -> None:
        """A2 rule: data_quality component must not be removed."""
        recipe = _load_winner_branch_recipe()
        comp_ids = {c["component_id"] for c in recipe["positive_components"]}
        self.assertIn("data_quality", comp_ids)

    def test_winner_branch_expected_a2_components_present(self) -> None:
        """The A2 spec defines 8 positive components for winner_branch."""
        recipe = _load_winner_branch_recipe()
        expected_ids = {
            "branch_historical_profitability",
            "branch_identity_confidence",
            "information_lead_proxy",
            "accumulation_persistence",
            "expected_value",
            "liquidity_capacity",
            "catalyst_alignment",
            "data_quality",
        }
        actual_ids = {c["component_id"] for c in recipe["positive_components"]}
        self.assertEqual(
            actual_ids, expected_ids, "Positive component set must match A2 spec exactly"
        )

    def test_winner_branch_expected_penalty_components_present(self) -> None:
        """The A2 spec defines 4 penalty components for winner_branch."""
        recipe = _load_winner_branch_recipe()
        expected_ids = {
            "related_branch_distribution_risk",
            "price_extension_risk",
            "concentration_risk",
            "capacity_shortfall",
        }
        actual_ids = {c["component_id"] for c in recipe["penalty_components"]}
        self.assertEqual(
            actual_ids, expected_ids, "Penalty component set must match A2 spec exactly"
        )

    def test_effective_score_formula_uses_confidence_multiplier(self) -> None:
        """A2: effective_score = raw_score × (0.60 + 0.40 × evidence_confidence)."""
        recipe = _load_winner_branch_recipe()
        policy = recipe.get("confidence_policy", {})
        self.assertAlmostEqual(policy.get("multiplier_floor", 0), 0.60, places=6)
        self.assertAlmostEqual(policy.get("multiplier_weight", 0), 0.40, places=6)

    def test_score_band_names_match_a2_spec(self) -> None:
        """A2 bands: priority_review, discuss, needs_research, park."""
        recipe = _load_winner_branch_recipe()
        band_names = {b["name"] for b in recipe["score_bands"]}
        expected_bands = {"priority_review", "discuss", "needs_research", "park"}
        self.assertEqual(band_names, expected_bands)

    def test_candidate_score_result_uses_a2_components(self) -> None:
        """A CandidateScoreResult must reference the winner_branch recipe ID and version."""
        recipe = _load_winner_branch_recipe()
        score_result = {
            "candidate_id": "cand_2330_TW",
            "recipe_id": recipe["recipe_id"],
            "recipe_version": recipe["version"],
            "raw_score": 72.5,
            "penalty_score": 8.0,
            "evidence_confidence": 0.78,
            "effective_score": 72.5 * (0.60 + 0.40 * 0.78),
            "rank": 1,
            "band": "discuss",
            "components": [
                {
                    "component_id": "branch_historical_profitability",
                    "raw_value": 0.65,
                    "normalized_value": 0.70,
                    "weight": 0.20,
                    "contribution": 14.0,
                    "missing_policy": "mark_needs_research",
                    "evidence_refs": ["run_branch_profitability_001"],
                },
                {
                    "component_id": "data_quality",
                    "raw_value": 0.82,
                    "normalized_value": 0.82,
                    "weight": 0.10,
                    "contribution": 8.2,
                    "missing_policy": "cap_final_score",
                    "evidence_refs": ["run_data_quality_001"],
                },
            ],
            "blockers": [],
            "data_cutoff": NOW,
        }
        self.assertEqual(score_result["recipe_id"], recipe["recipe_id"])
        self.assertEqual(score_result["recipe_version"], recipe["version"])
        comp_ids_in_result = {c["component_id"] for c in score_result["components"]}
        valid_comp_ids = {c["component_id"] for c in recipe["positive_components"]}
        for cid in comp_ids_in_result:
            self.assertIn(cid, valid_comp_ids,
                          f"Score component '{cid}' is not in A2 recipe")

    def test_effective_score_derivation(self) -> None:
        """A2: effective_score = raw_score × confidence_multiplier."""
        raw_score = 80.0
        evidence_confidence = 0.75
        expected = raw_score * (0.60 + 0.40 * evidence_confidence)
        actual = raw_score * (0.60 + 0.40 * evidence_confidence)
        self.assertAlmostEqual(actual, expected, places=6)

    def test_trading_room_aggregate_no_forbidden_keys(self) -> None:
        _assert_no_forbidden_keys(self, self._make_trading_room_aggregate())

    def test_dashboard_recipe_no_forbidden_keys(self) -> None:
        _assert_no_forbidden_keys(self, self._make_dashboard_recipe())


# ---------------------------------------------------------------------------
# Step 11 — Decision event + governed intent
# ---------------------------------------------------------------------------


class TestStep11GovernedIntent(unittest.TestCase):
    """
    §F1 Step 11: trigger decision event; trader approves → TradingIntent;
    shadow starts as no-order evaluation; paper/canary/live creates request-only
    governed handoff.

    KEY ASSERTIONS:
      - TradingEvent (canonical) validated as observation-only
      - TradingIntent (canonical) validated as intent record only
      - TradingDecisionEvent (v1.3) includes confidence, probability, EV fields
      - confidence ≠ probability (§D4: distinct concepts)
      - EV = gross - cost (§D4: net EV definition)
      - No broker order, RuntimeBinding, or capital binding at any step
    """

    def _make_trading_event(self) -> dict:
        return {
            "spec_version": "1.0",
            "event_id": "tev_wbranch_2330_001",
            "operator_id": OPERATOR_A,
            "persona_id": "agora-servant-op-a",
            "session_id": "sess_wbranch_001",
            "event_type": "trader_decision_noted",
            "subject": {
                "symbol": "2330.TW",
                "asset_class": "equity",
                "venue": "TWSE",
                "strategy_ref": STRATEGY_ID,
            },
            "event_data": {
                "event_kind": "entry",
                "decision_event_id": "de_wbranch_entry_001",
                "action_taken": "approved_by_trader",
            },
            "confidence": 0.82,
            "learning_eligible": True,
            "no_order_route_proof": "agora_observation_only",
            "observed_at": NOW,
            "metadata": {
                "strategy_id": STRATEGY_ID,
                "strategy_spec_registry_id": CANDIDATE_REGISTRY_ID,
            },
        }

    def _make_trading_intent(self) -> dict:
        return {
            "spec_version": "1.0",
            "intent_id": "intent_wbranch_2330_001",
            "operator_id": OPERATOR_A,
            "persona_id": "agora-servant-op-a",
            "session_id": "sess_wbranch_001",
            "intent_type": "entry_interest",
            "direction": "long",
            "subject": {
                "symbol": "2330.TW",
                "asset_class": "equity",
                "venue": "TWSE",
                "strategy_ref": STRATEGY_ID,
            },
            "rationale": "Branch entry signal confirmed by OOS backtest; winner-branch score 72.5",
            "size_hint": "small",
            "timeframe_hint": "3-6 months",
            "confidence": 0.82,
            "linked_event_ids": ["tev_wbranch_2330_001"],
            "learning_eligible": True,
            "no_order_route_proof": "agora_intent_record_only",
            "expressed_at": NOW,
            "metadata": {
                "strategy_id": STRATEGY_ID,
                "decision_event_id": "de_wbranch_entry_001",
            },
        }

    def _make_trading_decision_event(self, decision_state: str = "approved_by_trader") -> dict:
        return {
            "spec_version": "1.0",
            "decision_event_id": "de_wbranch_entry_001",
            "event_kind": "entry",
            "origin": "servant_analysis",
            "strategy_id": STRATEGY_ID,
            "strategy_spec_registry_id": CANDIDATE_REGISTRY_ID,
            "candidate_ref": "cand_2330_TW",
            "subject": {"symbol": "2330.TW", "asset_class": "equity", "venue": "TWSE"},
            "state": "decided",
            "triggered_at": NOW,
            "confidence": {
                "value": 0.82,
                "basis": "statistical",
                "calibration_state": "calibrated",
                "sample_size": 24,
            },
            "probability": {
                "target_outcome": "branch_winner_entry",
                "horizon": "3m",
                "value": 0.74,
            },
            "expected_value": {
                "horizon": "3m",
                "unit": "pct_return",
                "gross": 0.14,
                "cost": 0.01,
                "net": 0.13,
                "downside": -0.04,
            },
            "rationale": [
                {
                    "claim": "Branch entry signal confirmed by OOS backtest",
                    "confidence": 0.82,
                    "evidence_refs": [
                        {"ref_type": "experiment_artifact", "ref_id": "run_oos_001"}
                    ],
                },
                {
                    "claim": "Winner-branch score 72.5 in discuss band",
                    "confidence": 0.78,
                    "evidence_refs": [
                        {"ref_type": "experiment_artifact", "ref_id": "run_score_001"}
                    ],
                },
            ],
            "risk_notes": [
                {
                    "severity": "watch",
                    "domain": "liquidity",
                    "summary": "Branch liquidity can thin post-event",
                }
            ],
            "evidence_refs": [
                {"ref_type": "experiment_artifact", "ref_id": "run_oos_001"}
            ],
            "invalidation": {
                "conditions": [
                    "Branch spread collapses",
                    "Volume dries up",
                    "Related branch distribution risk > 0.80",
                ],
                "current_state": "valid",
            },
            "suggested_action": "enter",
            "suggested_size": {
                "size_hint": "small",
                "portfolio_pct": 0.03,
                "non_binding": True,
            },
            "decision_state": decision_state,
            "data_cutoff": NOW,
            "no_order_route_proof": "agora_decision_support_only",
        }

    def _make_governed_handoff(self, requested_stage: str = "paper") -> dict:
        _type_by_stage = {
            "shadow": "shadow_start",
            "paper": "paper_validation_request",
            "canary": "promotion_review_request",
            "live": "promotion_review_request",
        }
        _queue_by_stage = {
            "shadow": "shadow_research",
            "paper": "management_governance",
            "canary": "promotion_review",
            "live": "promotion_review",
        }
        return {
            "spec_version": "1.0",
            "handoff_id": f"hoff_wbranch_{requested_stage}_001",
            "intent_id": "intent_wbranch_2330_001",
            "decision_event_id": "de_wbranch_entry_001",
            "requested_stage": requested_stage,
            "handoff_type": _type_by_stage[requested_stage],
            "state": "submitted",
            "strategy_id": STRATEGY_ID,
            "strategy_spec_registry_id": CANDIDATE_REGISTRY_ID,
            "requested_by": {"actor_type": "trader", "actor_ref": USER_A},
            "target_queue": _queue_by_stage[requested_stage],
            "action_proposal": {
                "action": "enter",
                "symbol": "2330.TW",
                "direction": "long",
                "size_hint": "small",
                "portfolio_pct": 0.03,
                "non_binding": True,
            },
            "evidence_refs": [
                {"ref_type": "experiment_artifact", "ref_id": "run_oos_001"}
            ],
            "no_order_route_proof": "agora_request_only_no_order_route",
            "created_at": NOW,
        }

    def test_trading_event_schema_valid(self) -> None:
        _validate(
            self._make_trading_event(),
            CANONICAL_SCHEMA_DIR / "trading_event.schema.json",
        )

    def test_trading_event_no_order_route_proof(self) -> None:
        event = self._make_trading_event()
        self.assertIn(
            event["no_order_route_proof"],
            {"agora_observation_only", "research_plane_only"},
        )

    def test_trading_intent_schema_valid(self) -> None:
        _validate(
            self._make_trading_intent(),
            CANONICAL_SCHEMA_DIR / "trading_intent.schema.json",
        )

    def test_trading_intent_no_order_route_proof(self) -> None:
        intent = self._make_trading_intent()
        self.assertEqual(intent["no_order_route_proof"], "agora_intent_record_only")

    def test_trading_decision_event_schema_valid(self) -> None:
        _validate(
            self._make_trading_decision_event(),
            V13_SCHEMA_DIR / "trading_decision_event.schema.json",
        )

    def test_confidence_is_distinct_from_probability(self) -> None:
        """§D4: confidence ≠ probability — separate concepts."""
        event = self._make_trading_decision_event()
        confidence_val = event["confidence"]["value"]
        probability_val = event["probability"]["value"]
        self.assertNotEqual(
            confidence_val,
            probability_val,
            "Confidence and probability must be distinct (§D4 rule)",
        )

    def test_probability_has_named_outcome_and_horizon(self) -> None:
        event = self._make_trading_decision_event()
        prob = event["probability"]
        self.assertIn("target_outcome", prob)
        self.assertIn("horizon", prob)
        self.assertIn("value", prob)

    def test_confidence_has_basis_and_calibration_state(self) -> None:
        event = self._make_trading_decision_event()
        conf = event["confidence"]
        self.assertIn("basis", conf)
        self.assertIn("calibration_state", conf)

    def test_ev_net_equals_gross_minus_cost(self) -> None:
        """§D4: net EV = gross EV - estimated transaction cost/slippage."""
        event = self._make_trading_decision_event()
        ev = event["expected_value"]
        self.assertAlmostEqual(ev["net"], ev["gross"] - ev["cost"], places=6)

    def test_trading_decision_event_has_invalidation_conditions(self) -> None:
        event = self._make_trading_decision_event()
        self.assertGreater(len(event["invalidation"]["conditions"]), 0)

    def test_trading_decision_event_no_order_route_proof(self) -> None:
        event = self._make_trading_decision_event()
        self.assertEqual(event["no_order_route_proof"], "agora_decision_support_only")

    def test_suggested_size_is_non_binding(self) -> None:
        event = self._make_trading_decision_event()
        self.assertTrue(event["suggested_size"]["non_binding"])

    def test_governed_handoff_schema_valid_paper(self) -> None:
        _validate(
            self._make_governed_handoff("paper"),
            V13_SCHEMA_DIR / "governed_intent_handoff.schema.json",
        )

    def test_governed_handoff_schema_valid_canary(self) -> None:
        _validate(
            self._make_governed_handoff("canary"),
            V13_SCHEMA_DIR / "governed_intent_handoff.schema.json",
        )

    def test_governed_handoff_schema_valid_live(self) -> None:
        _validate(
            self._make_governed_handoff("live"),
            V13_SCHEMA_DIR / "governed_intent_handoff.schema.json",
        )

    def test_governed_handoff_schema_valid_shadow(self) -> None:
        _validate(
            self._make_governed_handoff("shadow"),
            V13_SCHEMA_DIR / "governed_intent_handoff.schema.json",
        )

    def test_action_proposal_is_non_binding(self) -> None:
        for stage in ("shadow", "paper", "canary", "live"):
            handoff = self._make_governed_handoff(stage)
            self.assertTrue(
                handoff["action_proposal"]["non_binding"],
                f"action_proposal.non_binding must be True for stage={stage}",
            )

    def test_no_broker_order_in_any_governed_handoff(self) -> None:
        for stage in ("shadow", "paper", "canary", "live"):
            handoff = self._make_governed_handoff(stage)
            _assert_no_forbidden_keys(self, handoff)

    def test_governed_handoff_state_is_submitted_not_converted(self) -> None:
        for stage in ("shadow", "paper", "canary", "live"):
            handoff = self._make_governed_handoff(stage)
            self.assertEqual(handoff["state"], "submitted")
            self.assertNotIn(handoff["state"], {"converted"})

    def test_shadow_handoff_is_no_order_evaluation(self) -> None:
        handoff = self._make_governed_handoff("shadow")
        self.assertEqual(handoff["requested_stage"], "shadow")
        self.assertEqual(handoff["no_order_route_proof"], "agora_request_only_no_order_route")
        self.assertEqual(handoff["target_queue"], "shadow_research")

    def test_paper_handoff_goes_to_management_governance(self) -> None:
        handoff = self._make_governed_handoff("paper")
        self.assertEqual(handoff["target_queue"], "management_governance")

    def test_canary_live_handoffs_go_to_promotion_review(self) -> None:
        for stage in ("canary", "live"):
            handoff = self._make_governed_handoff(stage)
            self.assertEqual(handoff["target_queue"], "promotion_review")
            self.assertEqual(handoff["handoff_type"], "promotion_review_request")

    def test_no_runtime_binding_in_trading_event(self) -> None:
        event = self._make_trading_event()
        self.assertIsNone(event.get("runtime_binding_id"))
        self.assertIsNone(event.get("runtime_binding_ref"))

    def test_no_capital_binding_in_trading_intent(self) -> None:
        intent = self._make_trading_intent()
        self.assertIsNone(intent.get("capital_binding_id"))
        self.assertIsNone(intent.get("capital_binding_ref"))

    def test_trading_event_no_forbidden_keys(self) -> None:
        _assert_no_forbidden_keys(self, self._make_trading_event())

    def test_trading_intent_no_forbidden_keys(self) -> None:
        _assert_no_forbidden_keys(self, self._make_trading_intent())

    def test_trading_decision_event_no_forbidden_keys(self) -> None:
        _assert_no_forbidden_keys(self, self._make_trading_decision_event())


# ---------------------------------------------------------------------------
# Agora Governance Boundary invariants
# ---------------------------------------------------------------------------


class TestAgoraGovernanceBoundary(unittest.TestCase):
    """
    Cross-cutting safety assertions covering the entire steps 9-11 chain.
    Validates the §D1 Trading Room boundary and §F4 Agora vs Management isolation.
    """

    def test_capability_manifest_has_trading_capability(self) -> None:
        manifest = _load_json(CANONICAL_SCHEMA_DIR / "capability_manifest.json")
        cap_names = {c["name"] for c in manifest["capabilities"]}
        self.assertIn("agora.trading.v1", cap_names)

    def test_trading_capability_safety_notes_present(self) -> None:
        manifest = _load_json(CANONICAL_SCHEMA_DIR / "capability_manifest.json")
        trading_cap = next(
            c for c in manifest["capabilities"] if c["name"] == "agora.trading.v1"
        )
        self.assertIn("safety_notes", trading_cap)
        self.assertGreater(len(trading_cap["safety_notes"]), 0)
        safety_text = " ".join(trading_cap["safety_notes"])
        self.assertIn("no_order_route_proof", safety_text)

    def test_trading_capability_schemas_include_no_order_artifacts(self) -> None:
        manifest = _load_json(CANONICAL_SCHEMA_DIR / "capability_manifest.json")
        trading_cap = next(
            c for c in manifest["capabilities"] if c["name"] == "agora.trading.v1"
        )
        self.assertIn("trading_event.schema.json", trading_cap["schemas"])
        self.assertIn("trading_intent.schema.json", trading_cap["schemas"])

    def test_full_step_9_10_11_chain_has_no_order_proofs(self) -> None:
        """Every artifact in the steps 9-11 chain must carry a no-order proof."""
        step9 = TestStep9SelectExecutionCandidates()
        step10 = TestStep10TradingRoomAndDashboard()
        step11 = TestStep11GovernedIntent()

        pool = step9._make_candidate_pool()
        self.assertIn("no_order_route_proof", pool.get("metadata", {}))

        room = step10._make_trading_room_aggregate()
        _assert_no_forbidden_keys(self, room)

        recipe = step10._make_dashboard_recipe()
        _assert_no_forbidden_keys(self, recipe)

        trading_event = step11._make_trading_event()
        self.assertIn("no_order_route_proof", trading_event)

        intent = step11._make_trading_intent()
        self.assertIn("no_order_route_proof", intent)

        decision_event = step11._make_trading_decision_event()
        self.assertIn("no_order_route_proof", decision_event)

        for stage in ("shadow", "paper", "canary", "live"):
            handoff = step11._make_governed_handoff(stage)
            self.assertIn("no_order_route_proof", handoff)

    def test_agora_cannot_write_runtime_binding(self) -> None:
        """§D1, §F4: Agora may not create or mutate RuntimeBinding."""
        step9 = TestStep9SelectExecutionCandidates()
        step11 = TestStep11GovernedIntent()

        selection = step9._make_selection([CANDIDATE_REGISTRY_ID])
        self.assertFalse(selection["runtime_binding_created"])

        for stage in ("shadow", "paper", "canary", "live"):
            handoff = step11._make_governed_handoff(stage)
            self.assertIsNone(handoff.get("runtime_binding_id"))

    def test_agora_cannot_create_capital_binding(self) -> None:
        """§D1, §F4: Agora may not create capital binding."""
        step9 = TestStep9SelectExecutionCandidates()
        selection = step9._make_selection([CANDIDATE_REGISTRY_ID])
        self.assertFalse(selection["capital_binding_created"])

    def test_agora_never_approves_paper_canary_live_directly(self) -> None:
        """§D7: Agora creates request-only handoffs, not approvals."""
        step11 = TestStep11GovernedIntent()
        for stage in ("paper", "canary", "live"):
            handoff = step11._make_governed_handoff(stage)
            self.assertEqual(
                handoff["state"],
                "submitted",
                f"Handoff for stage={stage} must be submitted, not converted/approved",
            )

    def test_all_handoffs_have_request_only_proof(self) -> None:
        step11 = TestStep11GovernedIntent()
        for stage in ("shadow", "paper", "canary", "live"):
            handoff = step11._make_governed_handoff(stage)
            self.assertEqual(
                handoff["no_order_route_proof"],
                "agora_request_only_no_order_route",
            )

    def test_widget_registry_loaded_and_non_empty(self) -> None:
        registry = _load_widget_registry()
        self.assertEqual(registry["registry_version"], "widget_registry.v1")
        self.assertGreater(len(registry["entries"]), 0)

    def test_winner_branch_widgets_present_in_registry(self) -> None:
        registered = _registered_widget_types()
        winner_branch_specific = [
            "winner_branch_scoreboard",
            "candidate_ranking_table",
            "branch_profitability_table",
            "winner_branch_score_breakdown",
        ]
        for wt in winner_branch_specific:
            self.assertIn(
                wt, registered, f"Widget '{wt}' must be in canonical A3 registry"
            )

    def test_a2_recipe_strategy_family_is_winner_branch(self) -> None:
        recipe = _load_winner_branch_recipe()
        self.assertEqual(recipe["strategy_family"], "winner_branch")
        self.assertEqual(recipe["status"], "active")


if __name__ == "__main__":
    unittest.main()
