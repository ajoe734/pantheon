"""Unit tests for the sentinel finding integrity verifier helpers."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_sentinel_integrity",
    os.path.join(_HERE,"verify_e2e_sentinel_integrity.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

class TestItems(unittest.TestCase):
    def test_items(self): self.assertEqual(mod._items({"items":[1]}),[1])
    def test_data(self): self.assertEqual(mod._items({"data":[2]}),[2])
    def test_empty(self): self.assertEqual(mod._items({"x":1}),[])
    def test_nonn_dict(self): self.assertEqual(mod._items(None),[])

if __name__=="__main__": unittest.main()
