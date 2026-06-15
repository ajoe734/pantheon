"""Unit tests for the FE static-serving verifier's asset-extraction logic."""
import importlib.util, os, unittest
_HERE=os.path.dirname(__file__)
_spec=importlib.util.spec_from_file_location("verify_e2e_fe_serving",
    os.path.join(_HERE,"verify_e2e_fe_serving.py"))
mod=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mod)

class TestAssetExtraction(unittest.TestCase):
    def test_extracts_js_and_css(self):
        html='<script type="module" src="/assets/index-AbC.js"></script><link href="/assets/index-XyZ.css">'
        self.assertEqual(set(mod.ASSET_RE.findall(html)), {"/assets/index-AbC.js","/assets/index-XyZ.css"})
    def test_ignores_non_assets(self):
        self.assertEqual(mod.ASSET_RE.findall('<img src="/placeholder.svg">'), [])
    def test_empty_index_no_assets(self):
        self.assertEqual(mod.ASSET_RE.findall("<html></html>"), [])

if __name__=="__main__": unittest.main()
