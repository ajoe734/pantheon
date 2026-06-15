"""Unit tests for the e2e binding-provenance verifier's classification logic.

The live verifier (verify_e2e_binding_provenance.py) probes a deployed BFF; these
tests gate its pure decision logic — crucially that a 200 graceful-degradation
envelope is NOT counted as a resolved provenance reference (the bug that made the
first run report artifacts as healthy when their read-model source was down).
"""
import importlib.util
import os
import unittest

_HERE = os.path.dirname(__file__)
_spec = importlib.util.spec_from_file_location(
    "verify_e2e_binding_provenance", os.path.join(_HERE, "verify_e2e_binding_provenance.py")
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


class TestIsResolved(unittest.TestCase):
    def test_plain_200_resolves(self):
        self.assertTrue(mod._is_resolved(200, {"data": {"id": "x", "name": "real"}}))

    def test_404_not_resolved(self):
        self.assertFalse(mod._is_resolved(404, None))

    def test_5xx_not_resolved(self):
        self.assertFalse(mod._is_resolved(500, None))

    def test_degraded_envelope_not_resolved(self):
        # 200 but body advertises degradation -> the read-model source is down.
        payload = {"data": {"id": "x", "status": "degraded"}}
        self.assertFalse(mod._is_resolved(200, payload))

    def test_unavailable_read_surface_not_resolved(self):
        payload = {"data": {"id": "x", "readSurface": {"status": "unavailable"}}}
        self.assertFalse(mod._is_resolved(200, payload))

    def test_non_dict_body_not_resolved(self):
        self.assertFalse(mod._is_resolved(200, "oops"))


class TestHelpers(unittest.TestCase):
    def test_items_prefers_items_then_data(self):
        self.assertEqual(mod._items({"items": [1, 2]}), [1, 2])
        self.assertEqual(mod._items({"data": [3]}), [3])
        self.assertEqual(mod._items({}), [])

    def test_ref_reads_top_level_then_metadata(self):
        self.assertEqual(mod._ref({"artifact_id": "a1"}, "artifact_id"), "a1")
        self.assertEqual(mod._ref({"metadata": {"strategy_id": "s1"}}, "strategy_id"), "s1")
        self.assertIsNone(mod._ref({}, "plan_id"))

    def test_ref_endpoints_cover_provenance_chain(self):
        self.assertEqual(
            set(mod.REF_ENDPOINTS), {"artifact", "strategy", "deployment", "capital_pool"}
        )


if __name__ == "__main__":
    unittest.main()
