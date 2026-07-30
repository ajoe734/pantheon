"""Unit tests for verify_twelve_loop_observability.py."""

import importlib.util
import copy
import os
import unittest

_HERE = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location("verify_twelve_loop_observability", os.path.join(_HERE, "verify_twelve_loop_observability.py"))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


class TestObservabilityChainVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = mod.generate_observability_proof_record()

    def test_generate_and_verify_record(self):
        record = copy.deepcopy(self.record)
        ok, errors = mod.verify_observability_chain(record)
        self.assertTrue(ok, f"Verification failed with errors: {errors}")
        self.assertEqual(len(errors), 0)

    def test_fabricated_identity_rejection(self):
        record = copy.deepcopy(self.record)
        record["identity_reconciliation"]["fabricated_identity_count"] = 1
        ok, errors = mod.verify_observability_chain(record)
        self.assertFalse(ok)
        self.assertIn("Identity reconciliation failed: fabricated or unattributed records found.", errors)

    def test_unapproved_decision_rejection(self):
        record = copy.deepcopy(self.record)
        record["evolution_decision"]["approval_status"] = "pending"
        ok, errors = mod.verify_observability_chain(record)
        self.assertFalse(ok)
        self.assertTrue(any("EvolutionDecision" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
