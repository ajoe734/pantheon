"""Pytest configuration for BFF tests.

Contract and behavior tests use colon-format stub tokens by default so they
can focus on route logic rather than JWT setup.  Auth-specific tests
(test_bff_auth_facade.py) explicitly set PANTHEON_BFF_AUTH_STUB="" to test
the production JWT path.
"""
from __future__ import annotations

import os
import pytest

if not os.environ.get("RANKING_STORE_DSN") and not os.environ.get("DATABASE_URL"):
    os.environ.setdefault("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
    os.environ.setdefault("RANKING_STORE_BOOTSTRAP", "0")


@pytest.fixture(autouse=True)
def _bff_stub_auth_default(monkeypatch):
    """Enable stub auth mode unless the test overrides it explicitly."""
    if not os.environ.get("PANTHEON_BFF_AUTH_STUB"):
        monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    if not os.environ.get("PANTHEON_BFF_AUTH_MODE"):
        monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    if not os.environ.get("PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS"):
        monkeypatch.setenv(
            "PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS",
            ",".join(
                [
                    "fake-auth",
                    "ignored",
                    "operator_001",
                    "repair-smoke",
                    "test",
                    "test-token",
                ]
            ),
        )
