"""Unit tests for the promotion/governance integrity verifier's logic."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_promotion_governance",
    os.path.join(_HERE,"verify_e2e_promotion_governance.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

class TestResolves(unittest.TestCase):
    def test_plain_200(self): self.assertTrue(mod._resolves(200,{"data":{"id":"a"}}))
    def test_404(self): self.assertFalse(mod._resolves(404,None))
    def test_degraded(self): self.assertFalse(mod._resolves(200,{"data":{"status":"degraded"}}))
    def test_unavailable_surface(self): self.assertFalse(mod._resolves(200,{"data":{"readSurface":{"status":"unavailable"}}}))

class TestApprovalId(unittest.TestCase):
    def test_top_level(self): self.assertEqual(mod._approval_id({"approval_decision_id":"ap1"}),"ap1")
    def test_from_ref(self): self.assertEqual(mod._approval_id({"approval_ref":{"approval_decision_id":"ap2"}}),"ap2")
    def test_none(self): self.assertIsNone(mod._approval_id({}))

class TestStages(unittest.TestCase):
    def test_promoted_stages(self): self.assertEqual(mod.PROMOTED_STAGES,{"canary","live"})

if __name__=="__main__": unittest.main()
