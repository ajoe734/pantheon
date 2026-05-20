from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "docs" / "bff" / "bff_ha_observability_spec.md"

REQUIRED_METRICS = {
    "bff_http_request_duration_seconds",
    "bff_http_requests_total",
    "bff_sse_active_connections",
    "bff_idempotency_requests_total",
    "bff_audit_writes_total",
    "bff_degraded_mode_active",
}

REQUIRED_DEGRADED_KEYS = {
    "AUTH_UNAVAILABLE",
    "IDEMPOTENCY_UNAVAILABLE",
    "AUDIT_UNAVAILABLE",
    "REGISTRY_GOVERNANCE_UNAVAILABLE",
    "RUNTIME_MANAGER_UNAVAILABLE",
    "TELEMETRY_UNAVAILABLE",
    "SSE_FANOUT_UNAVAILABLE",
}


def _spec_text() -> str:
    return SPEC_PATH.read_text(encoding="utf-8")


def test_observability_spec_covers_required_dashboard_metrics() -> None:
    spec = _spec_text()

    missing = sorted(metric for metric in REQUIRED_METRICS if metric not in spec)

    assert missing == []
    assert "Route latency histograms" in spec
    assert "Route and dependency error rates" in spec
    assert "Idempotency cache hit ratio" in spec
    assert "Audit write rate" in spec
    assert "Degraded-mode count" in spec


def test_observability_spec_covers_dashboard_audiences_and_sla_source() -> None:
    spec = _spec_text()

    assert "services/bff/ha/sla_targets.json" in spec
    assert "Operator Dashboard" in spec
    assert "Risk Owner Dashboard" in spec
    assert "Developer Dashboard" in spec


def test_observability_spec_preserves_fail_closed_alerts() -> None:
    spec = _spec_text()

    for degraded_key in REQUIRED_DEGRADED_KEYS:
        assert degraded_key in spec

    assert "BFFIdempotencyUnavailable" in spec
    assert "BFFAuditUnavailable" in spec
    assert "BFFCommandAcceptedWithoutSafetyProof" in spec
    assert "stopped before backend dispatch" in spec
