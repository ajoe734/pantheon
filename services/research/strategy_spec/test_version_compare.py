"""Tests for version_compare module.

Covers: field diffs, metric diffs, readiness diffs, build_version_compare,
        build_recommendation, candidate count limits, and evidence class validation.
"""
import unittest

from .version_compare import (
    MAX_CANDIDATE_VERSIONS,
    VALID_EVIDENCE_CLASSES,
    VALID_GATES,
    VALID_READINESS_STATES,
    VersionCompareError,
    VersionCompareLimitExceededError,
    build_recommendation,
    build_version_compare,
    compute_field_diffs,
    compute_metric_diffs,
    compute_readiness_diffs,
)


def _base_spec(title: str = "Base Strategy") -> dict:
    return {
        "spec_version": "1.0",
        "strategy_id": "strat-cmp-001",
        "title": title,
        "hypothesis": "Price momentum persists.",
        "objective": "Prototype research.",
        "lifecycle_state": "draft",
        "market_scope": {"symbols": ["UNIVERSE"], "frequency": "1d"},
        "data_dependencies": [{"ref": "dataset:tw-v1", "kind": "dataset"}],
        "execution_profile": {
            "signal_schema_version": "1.0",
            "quantity_type": "PERCENT_PORTFOLIO",
            "execution_mode_hint": "research",
        },
        "evaluation_plan": {"metrics": ["sharpe_ratio", "max_drawdown"]},
        "governance": {"approval_required": True, "policy_id": "research-v1"},
        "provenance": {"source_kind": "manual", "created_at": "2026-06-21T00:00:00Z"},
        "metadata": {},
    }


def _make_version_ref(workshop_version_id: str, registry_id: str) -> dict:
    return {
        "workshop_version_id": workshop_version_id,
        "strategy_spec_registry_id": registry_id,
        "label": f"v-{workshop_version_id}",
    }


# ---------------------------------------------------------------------------
# compute_field_diffs
# ---------------------------------------------------------------------------

class TestComputeFieldDiffs(unittest.TestCase):
    def test_identical_docs_no_diffs(self):
        doc = _base_spec()
        diffs = compute_field_diffs(doc, doc, "cand-v1")
        self.assertEqual(diffs, [])

    def test_identical_docs_include_unchanged(self):
        doc = _base_spec()
        diffs = compute_field_diffs(doc, doc, "cand-v1", include_unchanged=True)
        self.assertTrue(len(diffs) > 0)
        for d in diffs:
            self.assertEqual(d["change_kind"], "unchanged")

    def test_title_changed(self):
        base = _base_spec("Original")
        candidate = _base_spec("Updated")
        diffs = compute_field_diffs(base, candidate, "cand-v2")
        title_diff = next((d for d in diffs if d["path"] == "/title"), None)
        self.assertIsNotNone(title_diff)
        self.assertEqual(title_diff["change_kind"], "changed")
        self.assertEqual(title_diff["before"], "Original")
        self.assertEqual(title_diff["after"], "Updated")
        self.assertEqual(title_diff["candidate_version_id"], "cand-v2")

    def test_field_added_in_candidate(self):
        base = _base_spec()
        candidate = _base_spec()
        base.pop("evidence_refs", None)
        candidate["evidence_refs"] = [{"ref": "paper:001", "kind": "paper"}]
        diffs = compute_field_diffs(base, candidate, "cand-v3")
        added = [d for d in diffs if d["change_kind"] == "added" and d["path"] == "/evidence_refs"]
        self.assertEqual(len(added), 1)
        self.assertIn("after", added[0])
        self.assertNotIn("before", added[0])

    def test_field_removed_in_candidate(self):
        base = _base_spec()
        base["evidence_refs"] = [{"ref": "paper:001", "kind": "paper"}]
        candidate = _base_spec()
        diffs = compute_field_diffs(base, candidate, "cand-v4")
        removed = [d for d in diffs if d["change_kind"] == "removed" and d["path"] == "/evidence_refs"]
        self.assertEqual(len(removed), 1)
        self.assertIn("before", removed[0])
        self.assertNotIn("after", removed[0])

    def test_immutable_keys_not_diffed(self):
        base = _base_spec()
        candidate = dict(_base_spec())
        candidate["strategy_id"] = "should-not-appear"
        diffs = compute_field_diffs(base, candidate, "cand-v5")
        paths = [d["path"] for d in diffs]
        self.assertNotIn("/strategy_id", paths)
        self.assertNotIn("/spec_version", paths)
        self.assertNotIn("/lifecycle_state", paths)
        self.assertNotIn("/provenance", paths)

    def test_market_scope_change(self):
        base = _base_spec()
        candidate = _base_spec()
        candidate["market_scope"] = {"symbols": ["BTC", "ETH"], "frequency": "1h"}
        diffs = compute_field_diffs(base, candidate, "cand-v6")
        scope_diffs = [d for d in diffs if d["path"] == "/market_scope"]
        self.assertEqual(len(scope_diffs), 1)
        self.assertEqual(scope_diffs[0]["change_kind"], "changed")


# ---------------------------------------------------------------------------
# compute_metric_diffs
# ---------------------------------------------------------------------------

class TestComputeMetricDiffs(unittest.TestCase):
    def test_no_metric_values_returns_entries_with_none(self):
        base = _base_spec()
        candidate = _base_spec()
        diffs = compute_metric_diffs(base, candidate, "cand-v1")
        self.assertEqual(len(diffs), 2)  # sharpe_ratio + max_drawdown
        for d in diffs:
            self.assertIsNone(d["base_value"])
            self.assertIsNone(d["candidate_value"])
            self.assertEqual(d["evidence_class"], "predicted")
            self.assertNotIn("absolute_delta", d)

    def test_metric_values_compute_delta(self):
        base = _base_spec()
        candidate = _base_spec()
        diffs = compute_metric_diffs(
            base, candidate, "cand-v1",
            base_metric_values={"sharpe_ratio": 1.2, "max_drawdown": -0.15},
            candidate_metric_values={"sharpe_ratio": 1.5, "max_drawdown": -0.10},
        )
        sharpe = next(d for d in diffs if d["metric"] == "sharpe_ratio")
        self.assertAlmostEqual(sharpe["absolute_delta"], 0.3)
        self.assertAlmostEqual(sharpe["relative_delta_pct"], 25.0, places=2)

        dd = next(d for d in diffs if d["metric"] == "max_drawdown")
        self.assertAlmostEqual(dd["absolute_delta"], 0.05)

    def test_valid_evidence_classes(self):
        base = _base_spec()
        candidate = _base_spec()
        for ec in VALID_EVIDENCE_CLASSES:
            diffs = compute_metric_diffs(base, candidate, "cand-v1", evidence_class=ec)
            self.assertTrue(all(d["evidence_class"] == ec for d in diffs))

    def test_invalid_evidence_class_raises(self):
        base = _base_spec()
        candidate = _base_spec()
        with self.assertRaises(VersionCompareError):
            compute_metric_diffs(base, candidate, "cand-v1", evidence_class="made_up")

    def test_metric_added_in_candidate(self):
        base = _base_spec()
        candidate = _base_spec()
        candidate["evaluation_plan"]["metrics"].append("calmar_ratio")
        diffs = compute_metric_diffs(base, candidate, "cand-v2")
        metrics = {d["metric"] for d in diffs}
        self.assertIn("calmar_ratio", metrics)

    def test_metric_results_sorted(self):
        base = _base_spec()
        candidate = _base_spec()
        diffs = compute_metric_diffs(base, candidate, "cand-v1")
        names = [d["metric"] for d in diffs]
        self.assertEqual(names, sorted(names))

    def test_zero_base_value_no_relative_delta(self):
        base = _base_spec()
        candidate = _base_spec()
        candidate["evaluation_plan"]["metrics"] = ["sharpe_ratio"]
        base["evaluation_plan"]["metrics"] = ["sharpe_ratio"]
        diffs = compute_metric_diffs(
            base, candidate, "cand-v1",
            base_metric_values={"sharpe_ratio": 0.0},
            candidate_metric_values={"sharpe_ratio": 1.0},
        )
        sharpe = diffs[0]
        self.assertAlmostEqual(sharpe["absolute_delta"], 1.0)
        self.assertNotIn("relative_delta_pct", sharpe)


# ---------------------------------------------------------------------------
# compute_readiness_diffs
# ---------------------------------------------------------------------------

class TestComputeReadinessDiffs(unittest.TestCase):
    def test_returns_all_three_gates(self):
        diffs = compute_readiness_diffs({}, {}, "cand-v1")
        gates = {d["gate"] for d in diffs}
        self.assertEqual(gates, {"preliminary_research", "full_validation", "trading_room"})

    def test_default_state_not_assessed(self):
        diffs = compute_readiness_diffs({}, {}, "cand-v1")
        for d in diffs:
            self.assertEqual(d["base_state"], "not_assessed")
            self.assertEqual(d["candidate_state"], "not_assessed")

    def test_state_changes_reflected(self):
        base_gates = {"preliminary_research": "ready", "full_validation": "conditional"}
        cand_gates = {"preliminary_research": "ready", "full_validation": "ready", "trading_room": "blocked"}
        diffs = compute_readiness_diffs(base_gates, cand_gates, "cand-v2")
        by_gate = {d["gate"]: d for d in diffs}

        pr = by_gate["preliminary_research"]
        self.assertEqual(pr["base_state"], "ready")
        self.assertEqual(pr["candidate_state"], "ready")

        fv = by_gate["full_validation"]
        self.assertEqual(fv["base_state"], "conditional")
        self.assertEqual(fv["candidate_state"], "ready")

        tr = by_gate["trading_room"]
        self.assertEqual(tr["base_state"], "not_assessed")
        self.assertEqual(tr["candidate_state"], "blocked")

    def test_candidate_version_id_set(self):
        diffs = compute_readiness_diffs({}, {}, "my-candidate-id")
        for d in diffs:
            self.assertEqual(d["candidate_version_id"], "my-candidate-id")


# ---------------------------------------------------------------------------
# build_version_compare
# ---------------------------------------------------------------------------

class TestBuildVersionCompare(unittest.TestCase):
    def _make_compare(self, n_candidates=1, **kwargs):
        base_doc = _base_spec()
        candidate_docs = [_base_spec(f"Candidate {i+1}") for i in range(n_candidates)]
        base_version = _make_version_ref("wv-base", "reg-base")
        candidate_versions = [_make_version_ref(f"wv-cand-{i+1}", f"reg-cand-{i+1}") for i in range(n_candidates)]
        return build_version_compare(
            comparison_id=f"vcmp_test_{n_candidates}",
            workshop_id="ws-001",
            strategy_id="strat-cmp-001",
            base_version=base_version,
            candidate_versions=candidate_versions,
            base_doc=base_doc,
            candidate_docs=candidate_docs,
            created_at="2026-06-21T00:00:00Z",
            **kwargs,
        )

    def test_basic_structure(self):
        result = self._make_compare()
        self.assertEqual(result["spec_version"], "1.0")
        self.assertEqual(result["comparison_id"], "vcmp_test_1")
        self.assertEqual(result["workshop_id"], "ws-001")
        self.assertEqual(result["strategy_id"], "strat-cmp-001")
        self.assertIn("base_version", result)
        self.assertIn("candidate_versions", result)
        self.assertIn("field_diffs", result)
        self.assertIn("metric_diffs", result)
        self.assertIn("readiness_diffs", result)
        self.assertEqual(result["created_at"], "2026-06-21T00:00:00Z")

    def test_base_version_gets_sha256(self):
        result = self._make_compare()
        self.assertIn("document_sha256", result["base_version"])
        self.assertEqual(len(result["base_version"]["document_sha256"]), 64)

    def test_candidate_versions_get_sha256(self):
        result = self._make_compare(n_candidates=2)
        for cv in result["candidate_versions"]:
            self.assertIn("document_sha256", cv)
            self.assertEqual(len(cv["document_sha256"]), 64)

    def test_max_candidates(self):
        result = self._make_compare(n_candidates=MAX_CANDIDATE_VERSIONS)
        self.assertEqual(len(result["candidate_versions"]), MAX_CANDIDATE_VERSIONS)

    def test_exceeds_max_candidates_raises(self):
        with self.assertRaises(VersionCompareLimitExceededError):
            self._make_compare(n_candidates=MAX_CANDIDATE_VERSIONS + 1)

    def test_zero_candidates_raises(self):
        with self.assertRaises(VersionCompareLimitExceededError):
            build_version_compare(
                comparison_id="vcmp_zero",
                workshop_id="ws-001",
                strategy_id="strat-cmp-001",
                base_version=_make_version_ref("wv-base", "reg-base"),
                candidate_versions=[],
                base_doc=_base_spec(),
                candidate_docs=[],
                created_at="2026-06-21T00:00:00Z",
            )

    def test_mismatched_docs_raises(self):
        with self.assertRaises(VersionCompareError):
            build_version_compare(
                comparison_id="vcmp_mismatch",
                workshop_id="ws-001",
                strategy_id="strat-cmp-001",
                base_version=_make_version_ref("wv-base", "reg-base"),
                candidate_versions=[_make_version_ref("wv-c1", "reg-c1"), _make_version_ref("wv-c2", "reg-c2")],
                base_doc=_base_spec(),
                candidate_docs=[_base_spec()],  # only 1 doc for 2 candidates
                created_at="2026-06-21T00:00:00Z",
            )

    def test_readiness_diffs_all_three_gates_per_candidate(self):
        result = self._make_compare(n_candidates=2)
        # 2 candidates × 3 gates = 6 entries
        self.assertEqual(len(result["readiness_diffs"]), 6)
        gates = {d["gate"] for d in result["readiness_diffs"]}
        self.assertEqual(gates, {"preliminary_research", "full_validation", "trading_room"})

    def test_field_diffs_attributed_to_correct_candidate(self):
        base_doc = _base_spec("Base")
        cand_doc = _base_spec("Changed")
        result = build_version_compare(
            comparison_id="vcmp_attr",
            workshop_id="ws-001",
            strategy_id="strat-cmp-001",
            base_version=_make_version_ref("wv-base", "reg-base"),
            candidate_versions=[_make_version_ref("wv-cand-X", "reg-cand-X")],
            base_doc=base_doc,
            candidate_docs=[cand_doc],
            created_at="2026-06-21T00:00:00Z",
        )
        title_diffs = [d for d in result["field_diffs"] if d["path"] == "/title"]
        self.assertEqual(len(title_diffs), 1)
        self.assertEqual(title_diffs[0]["candidate_version_id"], "wv-cand-X")

    def test_created_at_auto_set_when_none(self):
        result = build_version_compare(
            comparison_id="vcmp_auto_ts",
            workshop_id="ws-002",
            strategy_id="strat-cmp-001",
            base_version=_make_version_ref("wv-base", "reg-base"),
            candidate_versions=[_make_version_ref("wv-c1", "reg-c1")],
            base_doc=_base_spec(),
            candidate_docs=[_base_spec()],
            # created_at omitted
        )
        self.assertIn("created_at", result)
        self.assertIsNotNone(result["created_at"])

    def test_with_readiness_and_metric_values(self):
        base_doc = _base_spec()
        cand_doc = _base_spec("With Metrics")
        result = build_version_compare(
            comparison_id="vcmp_full",
            workshop_id="ws-003",
            strategy_id="strat-cmp-001",
            base_version=_make_version_ref("wv-base", "reg-base"),
            candidate_versions=[_make_version_ref("wv-cand-m1", "reg-cand-m1")],
            base_doc=base_doc,
            candidate_docs=[cand_doc],
            base_gate_states={"preliminary_research": "ready"},
            candidate_gate_states_list=[{"preliminary_research": "ready", "full_validation": "conditional"}],
            base_metric_values={"sharpe_ratio": 1.1, "max_drawdown": -0.20},
            candidate_metric_values_list=[{"sharpe_ratio": 1.4, "max_drawdown": -0.12}],
            evidence_class="backtested_oos",
            created_at="2026-06-21T00:00:00Z",
        )
        sharpe = next(d for d in result["metric_diffs"] if d["metric"] == "sharpe_ratio")
        self.assertEqual(sharpe["evidence_class"], "backtested_oos")
        self.assertAlmostEqual(sharpe["absolute_delta"], 0.3)

        fv_diff = next(d for d in result["readiness_diffs"] if d["gate"] == "full_validation")
        self.assertEqual(fv_diff["base_state"], "not_assessed")
        self.assertEqual(fv_diff["candidate_state"], "conditional")


# ---------------------------------------------------------------------------
# build_recommendation
# ---------------------------------------------------------------------------

class TestBuildRecommendation(unittest.TestCase):
    def test_decision_authority_always_trader(self):
        rec = build_recommendation("wv-cand-1", "Better risk profile", 0.8)
        self.assertEqual(rec["decision_authority"], "trader")

    def test_confidence_clamped(self):
        rec_high = build_recommendation("wv-cand-1", "Test", 1.5)
        self.assertEqual(rec_high["confidence"], 1.0)
        rec_low = build_recommendation("wv-cand-1", "Test", -0.5)
        self.assertEqual(rec_low["confidence"], 0.0)

    def test_normal_confidence_preserved(self):
        rec = build_recommendation("wv-cand-1", "Solid improvement", 0.75)
        self.assertAlmostEqual(rec["confidence"], 0.75)

    def test_limitations_defaults_to_empty_list(self):
        rec = build_recommendation("wv-cand-1", "Good", 0.9)
        self.assertEqual(rec["limitations"], [])

    def test_limitations_passed_through(self):
        lims = ["Limited OOS data", "Single regime tested"]
        rec = build_recommendation("wv-cand-1", "Promising", 0.6, limitations=lims)
        self.assertEqual(rec["limitations"], lims)

    def test_required_fields_present(self):
        rec = build_recommendation("wv-abc", "rationale text", 0.5)
        for key in ("recommended_version_id", "rationale", "confidence", "limitations", "decision_authority"):
            self.assertIn(key, rec)


if __name__ == "__main__":
    unittest.main()
