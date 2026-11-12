import copy
import json
import unittest
from pathlib import Path

from services.evolution.approved_baselines import (
    PERIODS_PER_YEAR, RETURN_BASIS, compare_member_book, resolve_member_baseline,
)

FIXTURES = Path(__file__).resolve().parents[1] / "research" / "replication" / "fixtures"


EVALUATIONS = {}  # run id -> the producer evaluation the genuine gate path scored


def genuine_entry(fixture="recorded_backtest_tw_2330", symbols=("2330.TW",), tenant="t1"):
    """Registry entry built from the real gate on recorded Taiwan backtest output.

    Mirrors what the existing path persists: ``create_promotion_request``'s
    ``evaluation_summary`` and lineage plus the embedded strategy artifact.
    """
    from services.registry.strategy_artifact import strategy_artifact_checksum
    from services.research.replication.gate import ReplicationGate, create_promotion_request
    from services.research.replication.gate_schema import ReplicationRequest
    from services.research.vectorbt.adapter.vectorbt_adapter import build_canonical_tw_strategy_artifact

    evaluation = json.loads((FIXTURES / f"{fixture}.json").read_text(encoding="utf-8"))
    artifact = build_canonical_tw_strategy_artifact(strategy_id="strat-tw-replication-001", symbols=symbols)
    checksum = strategy_artifact_checksum(artifact)
    assert checksum == evaluation["strategy_artifact"]["checksum"]
    request = ReplicationRequest(
        candidate_id="cand-" + fixture, source_task_id="RS-002",
        research_handoff={
            "source_metadata": {"api_endpoint": "https://api.openalex.org/works/W123",
                                "retrieved_at": "2026-11-10T00:00:00Z",
                                "governance_context": "Approved structured source"},
            "normalized_findings": {"strategy_spec": {"name": "tw_momentum"},
                                    "replication_notes": "Real TW backtest",
                                    "evaluation_hypotheses": "H1: OOS Sharpe > deflated threshold"}},
        proposed_strategy_spec={"name": "tw_momentum", "description": "Taiwan momentum",
                                "signals": ["momentum"], "parameters": {"lookback": 2},
                                "governance": {"approval_required": True,
                                               "policy_id": "oc-001-research-normalization"}},
        backtest_evaluation=evaluation,
        metadata={"strategy_artifact_checksum": checksum, "dataset_version_id": evaluation["dataset_id"]})
    gate = ReplicationGate()
    gate.backtest_run_store.record(evaluation)
    response = gate.evaluate_candidate(request)
    assert response.passed, response
    promotion = create_promotion_request(response, request.proposed_strategy_spec)
    summary = promotion.registry_entry["evaluation_summary"]
    from services.registry.promotion.gate import PromotionGate
    EVALUATIONS[summary["backtest_run_id"]] = evaluation
    # The registry owner's own verification mints the persisted proof + binding.
    verified = PromotionGate.verify_admitted_replication_proof(
        promotion.replication_proof, summary, promotion.lineage,
        backtest_evaluation=evaluation, artifact_checksum=checksum)
    # Set by the registry service only after the Research owner readback confirms the run.
    verified["owner_verified_binding"]["producer_verified"] = True
    return {"registry_id": "reg-" + fixture, "artifact_state": "approved", "checksum": checksum,
            "owner_tenant": tenant, "approval_decision_id": "dec-" + fixture,
            "evaluation_summary": summary, "lineage": promotion.lineage,
            "producer_run_id": summary["backtest_run_id"],
            "replication_success": promotion.registry_entry["replication_success"],
            "replication_proof": verified,
            "metadata": {"strategy_artifact": artifact}}


def resolve(e, universe=("2330.TW",), checksum=None, ref=None, tenant="t1"):
    return resolve_member_baseline(
        tenant_id=tenant, persona_id="p1", artifact_ref=ref or (e or {}).get("registry_id", "x"),
        checksum=checksum or (e or {}).get("checksum", ""), entry=e, universe=universe)


def book(sharpe=0.4, drawdown=0.05, **kw):
    return {"basis": RETURN_BASIS, "periods_per_year": PERIODS_PER_YEAR,
            "sharpe_ratio": sharpe, "max_drawdown": drawdown, **kw}


class ApprovedBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.one = genuine_entry()
        cls.two = genuine_entry("recorded_backtest_tw_2330_2317", ("2330.TW", "2317.TW"))

    def test_genuine_gate_proof_resolves_with_source_refs(self):
        b = resolve(self.one)
        self.assertEqual(b["status"], "approved")
        summary = self.one["evaluation_summary"]
        self.assertEqual(b["oos_sharpe"], summary["out_of_sample_sharpe_ratio"])
        self.assertEqual(b["oos_max_drawdown"], abs(summary["max_drawdown"]))
        self.assertEqual(b["baseline_key"], f"t1/p1/{self.one['registry_id']}/{self.one['checksum']}")
        self.assertEqual(b["source_refs"]["backtest_run_id"], summary["backtest_run_id"])
        self.assertEqual((b["return_basis"], b["periods_per_year"]), (RETURN_BASIS, 365))

    def test_real_registry_owner_round_trip_carries_the_gate_verdict(self):
        """Genuine RegistryEntry durable round trip keeps the admitted proof; legacy rows stay unavailable."""
        from services.registry.models import RegistryEntry
        e = self.one
        payload = {
            "registry_id": e["registry_id"], "artifact_type": "execution_bundle",
            "strategy_id": "strat-tw-replication-001", "version": "1.0.0",
            "artifact_state": "approved", "lineage": e["lineage"],
            "storage_ref": {"backend": "inline", "path": "$.entry.metadata.strategy_artifact"},
            "checksum": e["checksum"], "evaluation_summary": e["evaluation_summary"],
            "approval_decision_id": e["approval_decision_id"], "metadata": e["metadata"],
            "owner_tenant": "t1", "replication_success": True,
            "replication_proof": e["replication_proof"]}
        owner = RegistryEntry.from_dict(payload).to_dict()
        self.assertIs(owner["replication_success"], True)
        self.assertEqual(owner["replication_proof"], e["replication_proof"])
        self.assertEqual(resolve(owner)["status"], "approved")
        legacy = RegistryEntry.from_dict({k: v for k, v in payload.items()
                                          if not k.startswith("replication_")}).to_dict()
        b = resolve(legacy)
        self.assertEqual((b["status"], b["reason"]),
                         ("no_approved_baseline", "gate_verdict_not_recorded"))

    def test_two_members_resolve_distinct_baselines_from_their_own_proof(self):
        a, b = resolve(self.one), resolve(self.two, universe=("2330.TW", "2317.TW"))
        self.assertEqual((a["status"], b["status"]), ("approved", "approved"))
        self.assertNotEqual(a["checksum"], b["checksum"])
        self.assertNotEqual(a["backtest_run_id"], b["backtest_run_id"])
        self.assertNotEqual(a["validated_universe"], b["validated_universe"])

    def test_missing_unapproved_or_foreign_proof_is_explicit_no_baseline(self):
        def mutate(fn):
            e = copy.deepcopy(self.one); fn(e); return e
        cases = {
            "registry_entry_missing": None,
            "artifact_not_approved": mutate(lambda e: e.update(artifact_state="candidate")),
            "wrong_tenant": mutate(lambda e: e.update(owner_tenant="t2")),
            "strategy_artifact_missing": mutate(lambda e: e["metadata"].clear()),
            "artifact_checksum_mismatch": mutate(lambda e: e["metadata"]["strategy_artifact"]["parameters"].update(lookback_bars=9)),
            "backtest_run_missing": mutate(lambda e: e["evaluation_summary"].update(backtest_run_id="")),
            "backtest_run_not_in_lineage": mutate(lambda e: (e["lineage"].update(source_run_ids=["other-run"]), e.pop("producer_run_id"))),
            "out_of_sample_metrics_missing": mutate(lambda e: e["evaluation_summary"].update(out_of_sample_sharpe_ratio=None)),
            "gate_proof_differs_from_summary": mutate(lambda e: e["evaluation_summary"].update(out_of_sample_sharpe_ratio=-1.0)),
            "gate_not_passed": mutate(lambda e: e.update(replication_success=False)),
            "gate_proof_missing": mutate(lambda e: e.pop("replication_proof")),
        }
        for reason, e in cases.items():
            with self.subTest(reason):
                b = resolve(e, ref=self.one["registry_id"], checksum=self.one["checksum"])
                self.assertEqual((b["status"], b["reason"]), ("no_approved_baseline", reason))
                self.assertNotIn("oos_sharpe", b)  # no synthetic/default value

    def test_summary_numbers_and_a_run_id_are_not_a_gate_verdict(self):
        # Reviewer counterexample: graft the two-symbol entry's summary and lineage
        # onto the single-symbol member; its own checksum/universe must not approve.
        grafted = copy.deepcopy(self.one)
        grafted["evaluation_summary"] = copy.deepcopy(self.two["evaluation_summary"])
        grafted["lineage"] = copy.deepcopy(self.two["lineage"])
        self.assertNotEqual(resolve(grafted)["status"], "approved")
        grafted["replication_proof"] = copy.deepcopy(self.two["replication_proof"])
        b = resolve(grafted)  # even a fully grafted proof is bound to another artifact's run
        self.assertEqual((b["status"], b["reason"]), ("no_approved_baseline", "gate_binding_differs_from_summary"))

        def mutate(fn):
            e = copy.deepcopy(self.one); fn(e); return e

        def fail_gate(e):
            verdict = next(r for r in e["replication_proof"]["replication_results"]
                           if r["criterion_id"] == "real_backtest_validation")
            verdict["passed"] = False
        cases = {
            "gate_not_passed/rejected": mutate(lambda e: e["replication_proof"].update(admission_status="rejected")),
            "gate_not_passed/verdict": mutate(fail_gate),
            "gate_binding_not_owner_verified": mutate(lambda e: e["replication_proof"].pop("owner_verified_binding")),
            "gate_binding_not_producer_verified/absent": mutate(
                lambda e: e["replication_proof"]["owner_verified_binding"].pop("producer_verified")),
            "gate_binding_not_producer_verified/false": mutate(
                lambda e: e["replication_proof"]["owner_verified_binding"].update(producer_verified=False)),
        }
        for name, e in cases.items():
            with self.subTest(name):
                # keep proof and summary consistent so only the named defect is under test
                e["replication_proof"]["evaluation_summary"] = e["evaluation_summary"]
                b = resolve(e)
                self.assertEqual(b["status"], "no_approved_baseline")
                self.assertEqual(b["reason"], name.split("/")[0])

    def test_forged_metrics_with_unchanged_owner_binding_are_not_approved(self):
        # Reviewer counterexample: Sharpe 999 / drawdown ~0 in summary, proof and details.
        forged = copy.deepcopy(self.one)
        forged["evaluation_summary"].update(out_of_sample_sharpe_ratio=999.0, max_drawdown=0.000001)
        proof = forged["replication_proof"]
        proof["evaluation_summary"] = forged["evaluation_summary"]
        details = next(r for r in proof["replication_results"]
                       if r["criterion_id"] == "real_backtest_validation")["details"]
        details.update(out_of_sample_sharpe_ratio=999.0, out_of_sample_max_drawdown=0.000001)
        b = resolve(forged)
        self.assertEqual((b["status"], b["reason"]), ("no_approved_baseline", "gate_binding_differs_from_summary"))

    def test_registry_owner_refuses_altered_fabricated_or_foreign_proof(self):
        from services.registry.promotion.gate import PromotionError, PromotionGate
        entry = self.one
        run_id = entry["evaluation_summary"]["backtest_run_id"]
        evaluation = EVALUATIONS[run_id]

        def verify(proof=None, summary=None, ev=None, checksum=None):
            return PromotionGate.verify_admitted_replication_proof(
                proof if proof is not None else entry["replication_proof"],
                summary if summary is not None else entry["evaluation_summary"], entry["lineage"],
                backtest_evaluation=ev if ev is not None else evaluation,
                artifact_checksum=checksum or entry["checksum"])
        self.assertEqual(verify()["owner_verified_binding"]["backtest_run_id"], run_id)
        # altered metrics everywhere, evaluation untouched
        summary = {**entry["evaluation_summary"], "out_of_sample_sharpe_ratio": 999.0, "max_drawdown": 1e-6}
        proof = copy.deepcopy(entry["replication_proof"])
        proof["evaluation_summary"] = summary
        details = next(r for r in proof["replication_results"]
                       if r["criterion_id"] == "real_backtest_validation")["details"]
        details.update(out_of_sample_sharpe_ratio=999.0, out_of_sample_max_drawdown=1e-6)
        with self.assertRaises(PromotionError):
            verify(proof, summary)
        # wholly fabricated all-true checks over a failing evaluation
        weak = copy.deepcopy(evaluation)
        weak["out_of_sample"]["sharpe_ratio"] = -1.0
        with self.assertRaises(PromotionError):
            verify(ev=weak)
        # a required criterion that did not pass (or is absent) is refused despite genuine metrics
        for mode in ("fail", "drop"):
            tampered = copy.deepcopy(entry["replication_proof"])
            if mode == "fail":
                next(r for r in tampered["replication_results"] if r["criterion_id"] == "schema_validity")["passed"] = False
            else:
                tampered["replication_results"] = [r for r in tampered["replication_results"]
                                                   if r["criterion_id"] != "lineage_complete"]
            with self.subTest(mode), self.assertRaises(PromotionError):
                verify(tampered)
        # caller-controlled policy context may not loosen the gate
        loose = copy.deepcopy(entry["replication_proof"])
        next(r for r in loose["replication_results"]
             if r["criterion_id"] == "real_backtest_validation")["details"]["mandate_max_drawdown"] = 0.99
        with self.assertRaises(PromotionError):
            verify(loose)
        # evaluation for another artifact / another run, and missing evaluation
        with self.assertRaises(PromotionError):
            verify(checksum=self.two["checksum"])
        with self.assertRaises(PromotionError):
            verify(ev=EVALUATIONS[self.two["evaluation_summary"]["backtest_run_id"]])
        with self.assertRaises(PromotionError):
            PromotionGate.verify_admitted_replication_proof(
                entry["replication_proof"], entry["evaluation_summary"], entry["lineage"],
                backtest_evaluation=None, artifact_checksum=entry["checksum"])

    def test_wrong_child_scope_or_universe_never_inherits_proof(self):
        # A child with a different checksum/ref cannot use this entry's proof.
        self.assertEqual(resolve(self.one, checksum=self.two["checksum"])["reason"], "checksum_mismatch")
        self.assertEqual(resolve(self.one, ref="child-artifact")["reason"], "registry_entry_is_not_member_artifact")
        # A member trading outside the gate-validated universe is not covered.
        self.assertEqual(resolve(self.one, universe=("2330.TW", "2317.TW"))["reason"], "universe_not_validated")
        self.assertEqual(resolve(self.two, universe=("2330.TW",))["reason"], "universe_not_validated")
        self.assertEqual(resolve(self.one, tenant="t2")["reason"], "wrong_tenant")

    def test_comparison_uses_member_book_and_rejects_incomparable_basis(self):
        base = resolve(self.one)
        low = compare_member_book(base, book(0.4, 0.12))
        self.assertTrue(low["below_baseline_sharpe"] and low["exceeds_baseline_drawdown"])
        self.assertFalse(compare_member_book(base, book(9.0, 0.0001))["below_baseline_sharpe"])
        gross = book(basis="underlying_gross_virtual")
        self.assertEqual(compare_member_book(base, gross)["status"], "incomparable_return_basis")
        self.assertEqual(compare_member_book(base, book(periods_per_year=12))["status"], "incomparable_return_basis")
        # member net block is chosen over the gross headline block when its basis matches
        both = {**gross, "net_of_costs": book(0.4, 0.12)}
        self.assertEqual(compare_member_book(base, both)["status"], "compared")
        # the exposure-fraction book is not the gate's share-sized basis
        fraction = {**gross, "net_of_costs": book(0.4, 0.12, basis="underlying_exposure_fraction_net_taiwan_costs_equal_notional")}
        self.assertEqual(compare_member_book(base, fraction)["status"], "incomparable_return_basis")
        undefined = compare_member_book(base, book(None, sharpe_undefined_reason="insufficient_or_constant_returns"))
        self.assertEqual(undefined["status"], "member_metric_missing")
        missing = compare_member_book(resolve(None, ref="x"), book())
        self.assertEqual((missing["status"], missing["reason"]), ("no_approved_baseline", "registry_entry_missing"))


if __name__ == "__main__":
    unittest.main()
