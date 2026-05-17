from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable


SCRIPT = Path(__file__).with_name("audit_lovable_strict_publish.py")
spec = importlib.util.spec_from_file_location("audit_lovable_strict_publish", SCRIPT)
assert spec and spec.loader
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


REQUIRED_ENV = {
    "VITE_BFF_MODE": "live",
    "VITE_BFF_FALLBACK": "strict",
    "VITE_BFF_REAL_WRITES": "false",
}


def _fake_get(mapping: dict[str, str]) -> Callable[[str, float], str]:
    def get(url: str, timeout: float) -> str:
        del timeout
        return mapping[url]

    return get


def test_verify_strict_publish_passes_with_strict_flags_and_clean_bundle() -> None:
    html = '<script type="module" src="/assets/index-good.js"></script>'
    bundle = """
const buildEnv = {
  "VITE_BFF_MODE": "live",
  "VITE_BFF_FALLBACK": "strict",
  "VITE_BFF_REAL_WRITES": "false"
};
fetch("/bff/me");
"""
    result = audit_module.verify_strict_publish(
        "https://pantheon-dev.lovable.app/management",
        required_env=REQUIRED_ENV,
        http_get=_fake_get(
            {
                "https://pantheon-dev.lovable.app/management": html,
                "https://pantheon-dev.lovable.app/assets/index-good.js": bundle,
            }
        ),
    )

    assert result["passed"] is True
    assert result["strict_env_confirmed"] is True
    assert result["missing_flags"] == []
    assert result["forbidden_runtime_paths"] == []
    assert result["bundle_urls"] == ["https://pantheon-dev.lovable.app/assets/index-good.js"]
    assert result["bundle_hashes"]["https://pantheon-dev.lovable.app/assets/index-good.js"]["sha256"]


def test_verify_strict_publish_fails_relaxed_flags_and_forbidden_seed_path() -> None:
    html = '<script type="module" src="/assets/index-relaxed.js"></script>'
    bundle = """
const buildEnv = {
  "VITE_BFF_MODE": "live",
  "VITE_BFF_FALLBACK": "auto",
  "VITE_BFF_REAL_WRITES": "true"
};
fetch("/mocks/current-user.json");
fetch("/assets/seed-fallback.json");
"""
    result = audit_module.verify_strict_publish(
        "https://pantheon-dev.lovable.app/management",
        required_env=REQUIRED_ENV,
        http_get=_fake_get(
            {
                "https://pantheon-dev.lovable.app/management": html,
                "https://pantheon-dev.lovable.app/assets/index-relaxed.js": bundle,
            }
        ),
    )

    assert result["passed"] is False
    assert result["strict_env_confirmed"] is False
    assert result["missing_flags"] == ["VITE_BFF_FALLBACK", "VITE_BFF_REAL_WRITES"]
    assert result["forbidden_runtime_paths"] == [
        "/assets/seed-fallback.json",
        "/mocks/current-user.json",
    ]
