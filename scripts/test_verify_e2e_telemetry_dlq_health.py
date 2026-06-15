"""Unit tests for the telemetry DLQ health verifier's evaluate logic."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_telemetry_dlq_health",
    os.path.join(_HERE,"verify_e2e_telemetry_dlq_health.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

def _entry(reason): return {"event":{}, "reason":reason, "tags":[reason]}

class TestUnreplayable(unittest.TestCase):
    def test_binding_mismatch_is_unreplayable(self):
        self.assertTrue(mod._is_unreplayable(_entry("binding_id 'rb-x' not found in RuntimeBinding store")))
    def test_evidence_contract_is_unreplayable(self):
        self.assertTrue(mod._is_unreplayable(_entry("Evidence contract violation E-1")))
    def test_write_error_is_replayable(self):
        self.assertFalse(mod._is_unreplayable(_entry("writer error: timeout")))

class TestEvaluate(unittest.TestCase):
    def test_pinned_with_unreplayable_fails(self):
        stats={"count":100,"entries":[_entry("not found in RuntimeBinding store")]*100}
        ok,summ=mod.evaluate(stats,100)
        self.assertFalse(ok); self.assertTrue(summ["pinned_at_threshold"])
    def test_below_threshold_ok(self):
        stats={"count":5,"entries":[_entry("not found in RuntimeBinding store")]*5}
        ok,_=mod.evaluate(stats,100); self.assertTrue(ok)
    def test_pinned_but_all_replayable_ok(self):
        stats={"count":100,"entries":[_entry("writer error")]*100}
        ok,summ=mod.evaluate(stats,100)
        self.assertTrue(ok); self.assertEqual(summ["unreplayable"],0)

if __name__=="__main__": unittest.main()
