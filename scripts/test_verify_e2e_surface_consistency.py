"""Unit tests for the operator read-surface cross-consistency verifier."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_surface_consistency",
    os.path.join(_HERE,"verify_e2e_surface_consistency.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

class TestHelpers(unittest.TestCase):
    def test_items_runtimes_key(self): self.assertEqual(mod._items({"runtimes":[1]}),[1])
    def test_items_items_key(self): self.assertEqual(mod._items({"items":[2]}),[2])
    def test_persona_top(self): self.assertEqual(mod._persona_of({"persona_id":"p1"}),"p1")
    def test_persona_meta(self): self.assertEqual(mod._persona_of({"metadata":{"persona_id":"p2"}}),"p2")
    def test_persona_none(self): self.assertIsNone(mod._persona_of({}))

if __name__=="__main__": unittest.main()
