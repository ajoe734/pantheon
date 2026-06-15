"""Unit tests for the telemetry-coverage verifier's has_summary logic."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_telemetry_coverage",
    os.path.join(_HERE,"verify_e2e_telemetry_coverage.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

class TestHasSummary(unittest.TestCase):
    def test_trades_count(self): self.assertTrue(mod.has_summary(200,{"data":{"total_trades":0}}))
    def test_heartbeat(self): self.assertTrue(mod.has_summary(200,{"data":{"last_heartbeat_at":"2026-06-15T00:00:00Z"}}))
    def test_artifact(self): self.assertTrue(mod.has_summary(200,{"data":{"artifact_id":"a1"}}))
    def test_404_no(self): self.assertFalse(mod.has_summary(404,None))
    def test_empty_data_no(self): self.assertFalse(mod.has_summary(200,{"data":{}}))

if __name__=="__main__": unittest.main()
