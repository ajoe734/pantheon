"""Pytest configuration for BFF tests.

Contract and behavior tests use colon-format stub tokens by default so they
can focus on route logic rather than JWT setup.  Auth-specific tests
(test_bff_auth_facade.py) explicitly set PANTHEON_BFF_AUTH_STUB="" to test
the production JWT path.
"""
from __future__ import annotations

import os
import pytest


@pytest.fixture(autouse=True)
def _bff_stub_auth_default(monkeypatch):
    """Enable stub auth mode unless the test overrides it explicitly."""
    if not os.environ.get("PANTHEON_BFF_AUTH_STUB"):
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
