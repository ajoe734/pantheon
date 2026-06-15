"""Regression: the Caddy edge templates must declare baseline security headers
and stay syntactically valid.

Verification campaign 2026-06-14, round 25, finding F15. The dev FE (a
browser-facing SPA) and both edges previously emitted no security headers and
leaked the upstream Server banner.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

CADDY_DIR = Path(__file__).resolve().parent
DEV = CADDY_DIR / "dev.Caddyfile.tmpl"
STAGING = CADDY_DIR / "staging.Caddyfile.tmpl"


def test_dev_fe_site_has_security_headers():
    text = DEV.read_text()
    # The FE site block must carry the browser-facing headers.
    for needle in ("X-Content-Type-Options nosniff", "X-Frame-Options DENY",
                   "Referrer-Policy no-referrer", "Strict-Transport-Security", "-Server"):
        assert needle in text, f"dev FE template missing: {needle}"


def test_dev_bff_site_has_hsts_and_no_server_banner():
    text = DEV.read_text()
    # Split on the FE-site comment marker (not the placeholder, which also
    # appears in the header comment block).
    bff_block = text.split("# Static execute-plans")[0]
    assert "Strict-Transport-Security" in bff_block
    assert "-Server" in bff_block


def test_staging_bff_has_hsts():
    text = STAGING.read_text()
    assert "Strict-Transport-Security" in text
    assert "-Server" in text


@pytest.mark.skipif(shutil.which("caddy") is None, reason="caddy binary not available")
@pytest.mark.parametrize("template", [DEV, STAGING])
def test_template_validates(tmp_path, template):
    rendered = (
        template.read_text()
        .replace("__BFF_HOST__", "bff.example.sslip.io")
        .replace("__FE_HOST__", "fe.example.sslip.io")
        .replace("__FE_ROOT__", str(tmp_path))
    )
    cfg = tmp_path / "Caddyfile"
    cfg.write_text(rendered)
    out = subprocess.run(
        ["caddy", "validate", "--config", str(cfg), "--adapter", "caddyfile"],
        capture_output=True, text=True,
    )
    assert "Valid configuration" in (out.stdout + out.stderr), out.stdout + out.stderr
