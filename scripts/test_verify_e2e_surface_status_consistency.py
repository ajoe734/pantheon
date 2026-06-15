"""Unit tests for the surface-status consistency verifier's contradiction logic."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_surface_status_consistency",
    os.path.join(_HERE,"verify_e2e_surface_status_consistency.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

class TestContradiction(unittest.TestCase):
    def test_items_with_unavailable_is_contradiction(self):
        p={"items":[1,2,3],"meta":{"surfaces":{"x":{"status":"unavailable","source":"missing"}}}}
        self.assertEqual(len(mod.find_contradictions(p,"/p")),1)
    def test_items_with_ok_surface_fine(self):
        p={"items":[1],"meta":{"surfaces":{"x":{"status":"ok","source":"service_client"}}}}
        self.assertEqual(mod.find_contradictions(p,"/p"),[])
    def test_empty_items_unavailable_fine(self):
        p={"items":[],"meta":{"surfaces":{"x":{"status":"unavailable","source":"missing"}}}}
        self.assertEqual(mod.find_contradictions(p,"/p"),[])
    def test_source_missing_with_items_is_contradiction(self):
        p={"data":[1],"meta":{"surfaces":{"y":{"status":"ok","source":"missing"}}}}
        self.assertEqual(len(mod.find_contradictions(p,"/p")),1)

if __name__=="__main__": unittest.main()
