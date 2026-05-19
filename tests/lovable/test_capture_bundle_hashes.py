from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Callable


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lovable" / "capture_bundle_hashes.py"
spec = importlib.util.spec_from_file_location("capture_bundle_hashes", SCRIPT)
assert spec and spec.loader
capture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture_module)


def _fake_get(mapping: dict[str, str]) -> Callable[[str, float], str]:
    def get(url: str, timeout: float) -> str:
        del timeout
        return mapping[url]

    return get


def test_capture_bundle_hashes_fetches_index_and_recursive_js_chunks() -> None:
    html = """
<html>
  <head>
    <link rel="modulepreload" href="/assets/vendor-222.js">
  </head>
  <body>
    <script type="module" src="/assets/index-111.js"></script>
  </body>
</html>
"""
    index_js = """import "/assets/chunk-333.js"; console.log("index");"""
    vendor_js = """console.log("vendor");"""
    chunk_js = """console.log("chunk");"""

    result = capture_module.capture_bundle_hashes(
        "https://pantheon-dev.lovable.app/management",
        checked_at="2026-05-19T15:00:00Z",
        http_get=_fake_get(
            {
                "https://pantheon-dev.lovable.app/management": html,
                "https://pantheon-dev.lovable.app/assets/index-111.js": index_js,
                "https://pantheon-dev.lovable.app/assets/vendor-222.js": vendor_js,
                "https://pantheon-dev.lovable.app/assets/chunk-333.js": chunk_js,
            }
        ),
    )

    assert result["passed"] is True
    assert result["errors"] == []
    assert result["checked_at"] == "2026-05-19T15:00:00Z"
    assert result["index"]["sha256"] == hashlib.sha256(html.encode()).hexdigest()
    assert result["js_bundle_urls"] == [
        "https://pantheon-dev.lovable.app/assets/chunk-333.js",
        "https://pantheon-dev.lovable.app/assets/index-111.js",
        "https://pantheon-dev.lovable.app/assets/vendor-222.js",
    ]
    assert result["js_bundles"]["https://pantheon-dev.lovable.app/assets/index-111.js"]["sha256"] == hashlib.sha256(
        index_js.encode()
    ).hexdigest()
    assert [asset["kind"] for asset in result["assets"]] == [
        "index_html",
        "js_bundle",
        "js_bundle",
        "js_bundle",
    ]
    assert result["asset_count"] == 4


def test_write_audit_packet_merges_bundle_capture(tmp_path: Path) -> None:
    audit_packet = tmp_path / "audit_packet.json"
    audit_packet.write_text(json.dumps({"task_id": "PUBLISH-AUDIT", "notes": ["keep"]}), encoding="utf-8")
    result = capture_module.capture_bundle_hashes(
        "https://pantheon-dev.lovable.app/management",
        checked_at="2026-05-19T15:00:00Z",
        http_get=_fake_get(
            {
                "https://pantheon-dev.lovable.app/management": '<script src="/assets/index.js"></script>',
                "https://pantheon-dev.lovable.app/assets/index.js": "console.log('ok');",
            }
        ),
    )

    merged = capture_module.write_audit_packet(result, audit_packet)
    written = json.loads(audit_packet.read_text(encoding="utf-8"))

    assert merged == written
    assert written["task_id"] == "PUBLISH-AUDIT"
    assert written["notes"] == ["keep"]
    assert written["bundle_hash_capture"]["task_id"] == "LSP-003-V2"
    assert written["bundle_hash_capture"]["asset_count"] == 2
    assert written["bundle_hash_capture"]["passed"] is True


def test_capture_bundle_hashes_records_js_fetch_failure() -> None:
    result = capture_module.capture_bundle_hashes(
        "https://pantheon-dev.lovable.app/management",
        checked_at="2026-05-19T15:00:00Z",
        http_get=_fake_get(
            {
                "https://pantheon-dev.lovable.app/management": '<script src="/assets/missing.js"></script>',
            }
        ),
    )

    assert result["passed"] is False
    assert result["index"] is not None
    assert result["js_bundle_urls"] == []
    assert result["asset_count"] == 1
    assert result["errors"] == [
        "fetch js failed: https://pantheon-dev.lovable.app/assets/missing.js: "
        "'https://pantheon-dev.lovable.app/assets/missing.js'"
    ]
