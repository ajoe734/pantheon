"""Unit tests for the capital-integrity verifier's helpers + invariant logic."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_capital_integrity",
    os.path.join(_HERE,"verify_e2e_capital_integrity.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

class TestBudget(unittest.TestCase):
    def test_scalar(self): self.assertEqual(mod._budget_value({"budget":10000.0}),10000.0)
    def test_dict_amount(self): self.assertEqual(mod._budget_value({"budget":{"amount":500}}),500.0)
    def test_missing_zero(self): self.assertEqual(mod._budget_value({}),0.0)
    def test_bad_zero(self): self.assertEqual(mod._budget_value({"budget":"x"}),0.0)

class TestItems(unittest.TestCase):
    def test_items(self): self.assertEqual(mod._items({"items":[1]}),[1])
    def test_data(self): self.assertEqual(mod._items({"data":[2]}),[2])
    def test_empty(self): self.assertEqual(mod._items({}),[])

if __name__=="__main__": unittest.main()
