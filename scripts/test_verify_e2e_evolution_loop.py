"""Unit tests for the evolution-loop integrity verifier."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_evolution_loop",
    os.path.join(_HERE,"verify_e2e_evolution_loop.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

class TestMalformed(unittest.TestCase):
    def test_untitled_no_runtime_is_malformed(self):
        bad,_=mod._is_malformed_open_incident({"status":"open","id":"i1","title":"Untitled Incident"})
        self.assertTrue(bad)
    def test_well_formed_open_ok(self):
        bad,_=mod._is_malformed_open_incident({"status":"open","id":"i2","runtime_id":"rt-1","title":"Drawdown breach"})
        self.assertFalse(bad)
    def test_closed_incident_ignored(self):
        bad,_=mod._is_malformed_open_incident({"status":"closed","id":"i3","title":"Untitled Incident"})
        self.assertFalse(bad)
    def test_missing_runtime_flagged(self):
        bad,d=mod._is_malformed_open_incident({"status":"open","id":"i4","title":"Real title"})
        self.assertTrue(bad); self.assertIn("no runtime_id",d)

if __name__=="__main__": unittest.main()
