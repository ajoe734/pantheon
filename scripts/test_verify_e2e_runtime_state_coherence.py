"""Unit tests for the runtime-state coherence verifier's mismatch logic."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_runtime_state_coherence",
    os.path.join(_HERE,"verify_e2e_runtime_state_coherence.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

class TestCoherence(unittest.TestCase):
    def test_coherent_no_mismatch(self):
        R=[{"runtime_id":"rt1","deployment_stage":"paper","status":"active","binding_id":"b1"}]
        S=[{"runtime_id":"rt1","deployment_stage":"paper","status":"active","runtime_binding_id":"b1"}]
        self.assertEqual(mod.find_mismatches(R,S),[])
    def test_stage_mismatch(self):
        R=[{"runtime_id":"rt1","deployment_stage":"live","status":"active","binding_id":"b1"}]
        S=[{"runtime_id":"rt1","deployment_stage":"paper","status":"active","runtime_binding_id":"b1"}]
        self.assertTrue(any("stage" in m for m in mod.find_mismatches(R,S)))
    def test_status_mismatch(self):
        R=[{"runtime_id":"rt1","deployment_stage":"paper","status":"paused","binding_id":"b1"}]
        S=[{"runtime_id":"rt1","deployment_stage":"paper","status":"active","runtime_binding_id":"b1"}]
        self.assertTrue(any("status" in m for m in mod.find_mismatches(R,S)))
    def test_disjoint_runtimes_ignored(self):
        self.assertEqual(mod.find_mismatches([{"runtime_id":"a"}],[{"runtime_id":"b"}]),[])

if __name__=="__main__": unittest.main()
