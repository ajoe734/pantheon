from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lovable" / "forbidden_path_scanner.py"
spec = importlib.util.spec_from_file_location("forbidden_path_scanner", SCRIPT)
assert spec and spec.loader
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


def _fake_get(mapping: dict[str, str]) -> Callable[[str, float], str]:
    def get(url: str, timeout: float) -> str:
        del timeout
        try:
            return mapping[url]
        except KeyError as exc:
            raise RuntimeError(f"unexpected fetch: {url}") from exc

    return get


def _scan(bundle: str):
    html = """
<html>
  <head>
    <link rel="modulepreload" href="/assets/chunk.js">
    <script type="module" src="/assets/index.js"></script>
  </head>
</html>
"""
    return scanner.scan_deployment(
        "https://pantheon-dev.lovable.app/management",
        http_get=_fake_get(
            {
                "https://pantheon-dev.lovable.app/management": html,
                "https://pantheon-dev.lovable.app/assets/chunk.js": "export const source = 'live';",
                "https://pantheon-dev.lovable.app/assets/index.js": bundle,
            }
        ),
    )


def test_scan_deployment_passes_when_hosted_bundles_are_clean() -> None:
    result = _scan(
        """
const buildEnv = { VITE_BFF_FALLBACK: "strict" };
fetch("/bff/me");
"""
    )

    assert result["passed"] is True
    assert result["errors"] == []
    assert result["forbidden_signals"] == []
    assert result["bundle_urls"] == [
        "https://pantheon-dev.lovable.app/assets/chunk.js",
        "https://pantheon-dev.lovable.app/assets/index.js",
    ]
    assert result["scanned_urls"] == [
        "https://pantheon-dev.lovable.app/assets/chunk.js",
        "https://pantheon-dev.lovable.app/assets/index.js",
        "https://pantheon-dev.lovable.app/management",
    ]


def test_scan_deployment_fails_on_each_forbidden_signal() -> None:
    result = _scan(
        """
fetch("/mocks/current-user.json");
seed.currentUser = true;
const mockSeed = { user: "demo" };
const silentFallbackEnabled = true;
const env = "VITE_BFF_FALLBACK=auto";
const localSeedHydrationEnabled = true;
const nav = "Ask Pathreon Management";
"""
    )

    assert result["passed"] is False
    assert result["errors"] == []
    assert [signal["signal"] for signal in result["forbidden_signals"]] == [
        "/mocks/",
        "seed.",
        "mockSeed",
        "silent fallback marker",
        "VITE_BFF_FALLBACK=auto",
        "local seed hydration",
        "Pathreon brand typo",
    ]
    assert {signal["source_url"] for signal in result["forbidden_signals"]} == {
        "https://pantheon-dev.lovable.app/assets/index.js"
    }


def test_scan_deployment_fails_when_asset_fetch_fails() -> None:
    html = '<script type="module" src="/assets/missing.js"></script>'

    result = scanner.scan_deployment(
        "https://pantheon-dev.lovable.app/management",
        http_get=_fake_get({"https://pantheon-dev.lovable.app/management": html}),
    )

    assert result["passed"] is False
    assert result["bundle_urls"] == ["https://pantheon-dev.lovable.app/assets/missing.js"]
    assert result["errors"] == [
        "fetch failed: https://pantheon-dev.lovable.app/assets/missing.js: unexpected fetch: https://pantheon-dev.lovable.app/assets/missing.js"
    ]


def test_scan_deployment_scans_direct_js_url() -> None:
    result = scanner.scan_deployment(
        "https://pantheon-dev.lovable.app/assets/index.js",
        http_get=_fake_get(
            {
                "https://pantheon-dev.lovable.app/assets/index.js": 'const marker = "local_seed_hydration";',
            }
        ),
    )

    assert result["passed"] is False
    assert result["bundle_urls"] == ["https://pantheon-dev.lovable.app/assets/index.js"]
    assert [signal["signal"] for signal in result["forbidden_signals"]] == ["local seed hydration"]
