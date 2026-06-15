"""Unit tests for the deployment lifecycle coherence verifier helpers."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_deployment_lifecycle",
    os.path.join(_HERE,"verify_e2e_deployment_lifecycle.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

class TestHelpers(unittest.TestCase):
    def test_not_deployed_set(self):
        self.assertIn("none", mod.NOT_DEPLOYED); self.assertIn("", mod.NOT_DEPLOYED)
    def test_items(self):
        self.assertEqual(mod._items({"items":[1]}),[1])
        self.assertEqual(mod._items({"data":[2]}),[2])
        self.assertEqual(mod._items({}),[])

if __name__=="__main__": unittest.main()
