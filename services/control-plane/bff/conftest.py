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


@pytest.fixture(autouse=True)
def _saved_evaluator_result_stub(monkeypatch, request):
    """The BFF only projects the persona evaluator's saved result; stub that boundary."""
    from services.control_plane.bff.pm12 import evaluator_results
    from services.control_plane.bff.personas import service as personas_service
    from services.control_plane.bff.tests.evaluator_saved_result_stub import SavedEvaluator

    if getattr(request.module, "REAL_RANKING_OWNER", False):
        return None
    stub = SavedEvaluator()
    from services.rankings.snapshots import snapshot_record
    stub.get_ranking_snapshot = stub.snapshots.get
    attach = personas_service._pm12_attach_ranking_snapshot

    def recording_attach(items, **kwargs):
        result = attach(items, **kwargs)
        stub.record(snapshot_record(items, **kwargs).to_canonical_dict())
        monkeypatch.setattr(personas_service, "_get_ranking_write_owner", lambda: stub)
        return result

    monkeypatch.setattr(personas_service, "_pm12_attach_ranking_snapshot", recording_attach)
    monkeypatch.setattr(evaluator_results, "saved_evaluator_result", stub.result)
    monkeypatch.setattr(evaluator_results, "saved_recommendation", stub.recommendation)
    return stub
