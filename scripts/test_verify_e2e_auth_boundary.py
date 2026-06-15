"""Unit tests for the auth-boundary verifier constants/logic."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_auth_boundary",
    os.path.join(_HERE,"verify_e2e_auth_boundary.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

class TestAuthBoundary(unittest.TestCase):
    def test_auth_reject_set(self):
        self.assertEqual(mod.AUTH_REJECT, {401,403})
    def test_protected_set_nonempty(self):
        self.assertGreaterEqual(len(mod.PROTECTED), 8)
        self.assertIn("/bff/runtimes", mod.PROTECTED)
    def test_200_is_not_a_reject(self):
        self.assertNotIn(200, mod.AUTH_REJECT)

if __name__=="__main__": unittest.main()
