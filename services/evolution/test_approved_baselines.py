import copy
import unittest

from services.evolution.approved_baselines import (
    PERIODS_PER_YEAR, RETURN_BASIS, compare_member_book, resolve_member_baseline,
)

CHECKSUM = "sha256:" + "a" * 64


def entry(ref="art-1", checksum=CHECKSUM, **verdict):
    gate = {"status": "passed", "artifact_id": ref, "artifact_checksum": checksum,
            "tenant_id": "t1", "backtest_run_id": "bt-1", "out_of_sample_sharpe_ratio": 1.2,
            "out_of_sample_max_drawdown": -0.08, "validated_universe": ["2330.TW", "2317.TW"],
            "return_basis": RETURN_BASIS, "periods_per_year": PERIODS_PER_YEAR}
    gate.update(verdict)
    return {"registry_id": ref, "artifact_state": "approved", "checksum": checksum,
            "owner_tenant": "t1", "approval_decision_id": "dec-1",
            "metadata": {"replication_gate_verdict": gate}}


def resolve(e, ref="art-1", checksum=CHECKSUM, universe=("2330.TW",)):
    return resolve_member_baseline(tenant_id="t1", persona_id="p1", artifact_ref=ref,
                                   checksum=checksum, entry=e, universe=universe)


def book(sharpe=0.4, drawdown=0.05, **kw):
    return {"basis": RETURN_BASIS, "periods_per_year": PERIODS_PER_YEAR,
            "sharpe_ratio": sharpe, "max_drawdown": drawdown, **kw}


class ApprovedBaselineTests(unittest.TestCase):
    def test_exact_proof_resolves_with_source_refs(self):
        b = resolve(entry())
        self.assertEqual(b["status"], "approved")
        self.assertEqual((b["oos_sharpe"], b["oos_max_drawdown"]), (1.2, 0.08))
        self.assertEqual(b["baseline_key"], f"t1/p1/art-1/{CHECKSUM}")
        self.assertEqual(b["source_refs"]["backtest_run_id"], "bt-1")

    def test_missing_unapproved_or_foreign_proof_is_explicit_no_baseline(self):
        def mutate(fn):
            e = copy.deepcopy(entry()); fn(e); return e
        cases = {
            "registry_entry_missing": None,
            "artifact_not_approved": mutate(lambda e: e.update(artifact_state="candidate")),
            "checksum_mismatch": mutate(lambda e: e.update(checksum="sha256:" + "b" * 64)),
            "wrong_tenant": mutate(lambda e: e.update(owner_tenant="t2")),
            "gate_verdict_missing": mutate(lambda e: e["metadata"].clear()),
            "gate_not_passed": entry(status="failed"),
            "gate_verdict_for_other_artifact": entry(artifact_id="parent"),  # child never inherits parent proof
            "backtest_run_missing": entry(backtest_run_id=""),
            "out_of_sample_metrics_missing": entry(out_of_sample_sharpe_ratio=None),
            "validated_universe_missing": entry(validated_universe=[]),
        }
        for reason, e in cases.items():
            with self.subTest(reason):
                b = resolve(e)
                self.assertEqual((b["status"], b["reason"]), ("no_approved_baseline", reason))
                self.assertNotIn("oos_sharpe", b)  # no synthetic/default value
        self.assertEqual(resolve(entry(), universe=("9999.TW",))["reason"], "universe_not_validated")
        self.assertEqual(resolve(entry(), checksum="sha256:" + "c" * 64)["reason"], "checksum_mismatch")

    def test_comparison_uses_member_book_and_rejects_incomparable_basis(self):
        base = resolve(entry())
        low = compare_member_book(base, book(0.4, 0.12))
        self.assertTrue(low["below_baseline_sharpe"] and low["exceeds_baseline_drawdown"])
        self.assertAlmostEqual(low["sharpe_delta"], -0.8)
        self.assertFalse(compare_member_book(base, book(1.5, 0.01))["below_baseline_sharpe"])
        self.assertEqual(compare_member_book(base, book(basis="account_levered"))["status"],
                         "incomparable_return_basis")
        self.assertEqual(compare_member_book(base, book(periods_per_year=12))["status"],
                         "incomparable_return_basis")
        undefined = compare_member_book(base, book(None, sharpe_undefined_reason="insufficient_or_constant_returns"))
        self.assertEqual(undefined["status"], "member_metric_missing")
        missing = compare_member_book(resolve(None), book())
        self.assertEqual((missing["status"], missing["reason"]), ("no_approved_baseline", "registry_entry_missing"))


if __name__ == "__main__":
    unittest.main()
