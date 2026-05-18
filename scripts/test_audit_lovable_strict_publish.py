import unittest
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.audit_lovable_strict_publish import verify_strict_publish

class TestAuditStrictPublish(unittest.TestCase):
    def test_verify_strict_publish_pass(self):
        env = {
            "VITE_BFF_MODE": "live",
            "VITE_BFF_FALLBACK": "strict",
            "VITE_BFF_REAL_WRITES": "false"
        }
        
        def mock_env(key):
            return env.get(key)
        
        def mock_url(url):
            return "<html><body>Hello World</body></html>"
            
        result = verify_strict_publish("http://test.com", env_getter=mock_env, url_getter=mock_url)
        self.assertTrue(result["strict_env_confirmed"])
        self.assertFalse(result["probe_failed"])
        self.assertFalse(result["contains_mocks"])

    def test_verify_strict_publish_fail_env(self):
        env = {
            "VITE_BFF_MODE": "mock", # Wrong
            "VITE_BFF_FALLBACK": "strict",
            "VITE_BFF_REAL_WRITES": "false"
        }
        
        def mock_env(key):
            return env.get(key)
        
        def mock_url(url):
            return "<html><body>Hello World</body></html>"
            
        result = verify_strict_publish("http://test.com", env_getter=mock_env, url_getter=mock_url)
        self.assertFalse(result["strict_env_confirmed"])
        self.assertIn("VITE_BFF_MODE", result["missing_flags"])

if __name__ == "__main__":
    unittest.main()
