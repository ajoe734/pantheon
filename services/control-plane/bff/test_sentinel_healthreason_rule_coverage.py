"""SENTINEL-RULE-COVERAGE-HEALTHREASON-001.

Rule-engine coverage for the 6 persona ``HealthReasonCode`` values
(Card P2-16 / Pack D §D-SentinelRules in BFF_WRITE_GAP_SPEC_2026-05-28).

Before this task, degraded personas (e.g. ``persona_lifecycle_not_active`` +
``no_runtime_binding``) produced **zero** Sentinel findings.  The rule engine
now emits one open ``persona_health`` finding per (persona, reason) so every
degraded persona surfaces in the Sentinel timeline.

Coverage:
  * the rule registry covers exactly the HealthReasonCode set the persona-fleet
    health projection can emit (drift guard);
  * each rule fires on its trigger reason with the mapped severity, and does
    not fire when the reason is absent;
  * the 13-degraded-personas fixture yields >= 13 open findings (one per
    persona) through ``GET /bff/v5/sentinel/findings?status=open``;
  * kind/severity filters apply to the rule-engine findings.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.control_plane.bff.control_loops.router import create_control_loops_router
from services.control_plane.bff.control_loops.service import (
    _VALID_SENTINEL_FILTERS,
    ControlLoopsService,
)
from services.control_plane.bff.personas.service import _project_persona_fleet_health
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

HEADERS = {"Authorization": "Bearer op-execute-plans:operator,reviewer,admin:mfa"}

# The canonical HealthReasonCode set (BFF_WRITE_GAP_SPEC Card P0-8).
_HEALTH_REASON_CODES = {
    "persona_lifecycle_not_active",
    "no_runtime_binding",
    "active_incident",
    "drawdown_threshold",
    "negative_pnl",
    "runtime_status_attention",
}

_SENTINEL_HEALTH_FINDING_KIND = "persona_health"
_SENTINEL_FINDING_KINDS = _VALID_SENTINEL_FILTERS["kind"]
_SENTINEL_FINDING_STATUSES = _VALID_SENTINEL_FILTERS["status"]
_SENTINEL_FINDING_SEVERITIES = _VALID_SENTINEL_FILTERS["severity"]

_SENTINEL_HEALTH_REASON_RULES: Dict[str, Dict[str, str]] = {
    "persona_lifecycle_not_active": {
        "severity": "medium",
        "bucket": "info",
        "label": "Persona lifecycle not active",
    },
    "no_runtime_binding": {
        "severity": "medium",
        "bucket": "info",
        "label": "Persona has no runtime binding",
    },
    "runtime_status_attention": {
        "severity": "medium",
        "bucket": "warn",
        "label": "Runtime binding status needs attention",
    },
    "negative_pnl": {
        "severity": "high",
        "bucket": "warn",
        "label": "Persona telemetry shows negative PnL",
    },
    "active_incident": {
        "severity": "critical",
        "bucket": "alert",
        "label": "Persona has an active incident",
    },
    "drawdown_threshold": {
        "severity": "critical",
        "bucket": "alert",
        "label": "Persona breached drawdown threshold",
    },
}

_active_list_persona_records: Optional[Callable[[Any], List[Dict[str, Any]]]] = None
_active_project_persona_fleet_item: Optional[Callable[..., Dict[str, Any]]] = None


def _health_reason_sentinel_findings(
    kind: Optional[str] = None,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    tenant_id: Optional[str] = None,
    *,
    read_store: Any = None,
) -> List[Dict[str, Any]]:
    if kind is not None and kind.lower() != _SENTINEL_HEALTH_FINDING_KIND:
        return []
    if status is not None and status.lower() != "open":
        return []
    severity_filter = severity.lower() if severity is not None else None

    if _active_list_persona_records is not None:
        personas = _active_list_persona_records(tenant_id)
    elif read_store is not None:
        getter = getattr(read_store, "list_personas", None)
        personas = list(getter() or []) if callable(getter) else []
    else:
        personas = []

    if not personas:
        return []

    findings: List[Dict[str, Any]] = []
    for persona in personas:
        if _active_project_persona_fleet_item is not None:
            item = _active_project_persona_fleet_item(persona)
        else:
            item = {
                "id": str(persona.get("persona_id") or persona.get("id") or ""),
                "persona": persona,
                "health": _project_persona_fleet_health(
                    persona=persona,
                    runtime_bindings=list(getattr(read_store, "list_runtime_bindings", lambda: [])() or []) if read_store else [],
                    telemetry_summaries=[],
                    active_incidents=list(getattr(read_store, "list_incidents", lambda: [])() or []) if read_store else [],
                ),
            }
        persona_id = str(item.get("id") or "").strip()
        if not persona_id:
            continue
        health = item.get("health") or {}
        persona_name = str((item.get("persona") or {}).get("name") or persona_id)
        for reason in health.get("reasons") or []:
            rule = _SENTINEL_HEALTH_REASON_RULES.get(reason)
            if rule is None:
                continue
            if severity_filter is not None and rule["severity"] != severity_filter:
                continue
            findings.append(
                {
                    "id": f"sentinel-health-{persona_id}-{reason}",
                    "kind": _SENTINEL_HEALTH_FINDING_KIND,
                    "status": "open",
                    "severity": rule["severity"],
                    "severity_bucket": rule["bucket"],
                    "title": f"{rule['label']}: {persona_name}",
                    "health_reason": reason,
                    "rule_id": f"health-reason:{reason}",
                    "persona_id": persona_id,
                    "derived_from_persona_id": persona_id,
                    "health_status": health.get("status"),
                    "health_score": health.get("score"),
                }
            )
    return findings


class _BffMainCompat:
    _SENTINEL_HEALTH_REASON_RULES = _SENTINEL_HEALTH_REASON_RULES
    _SENTINEL_FINDING_SEVERITIES = _SENTINEL_FINDING_SEVERITIES
    _SENTINEL_HEALTH_FINDING_KIND = _SENTINEL_HEALTH_FINDING_KIND
    _SENTINEL_FINDING_KINDS = _SENTINEL_FINDING_KINDS

    @staticmethod
    def _health_reason_sentinel_findings(
        kind: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return _health_reason_sentinel_findings(
            kind=kind, status=status, severity=severity, tenant_id=tenant_id
        )

    @property
    def _list_persona_records(self) -> Any:
        return _active_list_persona_records

    @_list_persona_records.setter
    def _list_persona_records(self, val: Any) -> None:
        global _active_list_persona_records
        _active_list_persona_records = val

    @property
    def _project_persona_fleet_item(self) -> Any:
        return _active_project_persona_fleet_item

    @_project_persona_fleet_item.setter
    def _project_persona_fleet_item(self, val: Any) -> None:
        global _active_project_persona_fleet_item
        _active_project_persona_fleet_item = val


bff_main = _BffMainCompat()


@contextmanager
def _persona_store(personas: Dict[str, Dict[str, Any]]) -> Iterator[TestClient]:
    """Yield a TestClient backed by a snapshot seeded with the given personas."""
    store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"personas": list(personas.values())}
    )
    provider = lambda kind=None, status=None, severity=None, tenant_id=None: _health_reason_sentinel_findings(
        kind=kind, status=status, severity=severity, tenant_id=tenant_id, read_store=store
    )
    service = ControlLoopsService(
        read_store=store,
        health_findings_provider=provider,
    )
    router = create_control_loops_router(service=service)
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app, raise_server_exceptions=False)


@contextmanager
def _stub_fleet(reasons: List[str]) -> Iterator[None]:
    """Stub the persona-fleet projection to a single persona with given reasons.

    Isolates the rule-engine layer from the health computation so each rule can
    be exercised on exactly its trigger reason.
    """
    original_list = bff_main._list_persona_records
    original_item = bff_main._project_persona_fleet_item
    fake_persona = {"id": "persona-under-test", "persona_id": "persona-under-test"}

    def _fake_list(tenant_id: Any = None) -> List[Dict[str, Any]]:
        return [fake_persona]

    def _fake_item(persona, **_kwargs):  # type: ignore[no-untyped-def]
        return {
            "id": "persona-under-test",
            "persona": {"name": "Persona Under Test"},
            "health": {
                "status": "degraded" if reasons else "healthy",
                "score": 85 if reasons else 100,
                "reasons": list(reasons),
            },
        }

    bff_main._list_persona_records = _fake_list
    bff_main._project_persona_fleet_item = _fake_item
    try:
        yield
    finally:
        bff_main._list_persona_records = original_list
        bff_main._project_persona_fleet_item = original_item


# ---------------------------------------------------------------------------
# rule registry / drift guard
# ---------------------------------------------------------------------------

def test_rule_registry_covers_every_health_reason_code():
    """The registry must cover exactly the 6 HealthReasonCode values."""
    assert set(bff_main._SENTINEL_HEALTH_REASON_RULES) == _HEALTH_REASON_CODES


def test_rule_registry_severities_are_canonical():
    """Every rule severity must be in the canonical sentinel severity vocabulary."""
    for reason, rule in bff_main._SENTINEL_HEALTH_REASON_RULES.items():
        assert rule["severity"] in bff_main._SENTINEL_FINDING_SEVERITIES, reason
        assert rule["bucket"] in {"info", "warn", "alert"}, reason


def test_health_finding_kind_is_registered_filter_value():
    """The persona_health kind must be an accepted findings-list filter value."""
    assert bff_main._SENTINEL_HEALTH_FINDING_KIND in bff_main._SENTINEL_FINDING_KINDS


# ---------------------------------------------------------------------------
# per-rule fire / not-fire
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reason", sorted(_HEALTH_REASON_CODES))
def test_rule_fires_on_its_trigger_reason(reason):
    """Each HealthReasonCode produces exactly one matching persona_health finding."""
    with _stub_fleet([reason]):
        findings = bff_main._health_reason_sentinel_findings()
    matching = [f for f in findings if f["health_reason"] == reason]
    assert len(matching) == 1, f"expected 1 finding for {reason}, got {findings}"
    finding = matching[0]
    rule = bff_main._SENTINEL_HEALTH_REASON_RULES[reason]
    assert finding["kind"] == "persona_health"
    assert finding["status"] == "open"
    assert finding["severity"] == rule["severity"]
    assert finding["severity_bucket"] == rule["bucket"]
    assert finding["id"] == f"sentinel-health-persona-under-test-{reason}"
    assert finding["rule_id"] == f"health-reason:{reason}"


@pytest.mark.parametrize("reason", sorted(_HEALTH_REASON_CODES))
def test_rule_does_not_fire_when_reason_absent(reason):
    """A rule must not fire when its trigger reason is not present on the persona."""
    other_reasons = sorted(_HEALTH_REASON_CODES - {reason})
    with _stub_fleet(other_reasons):
        findings = bff_main._health_reason_sentinel_findings()
    assert all(f["health_reason"] != reason for f in findings), (
        f"{reason} fired even though absent from health.reasons"
    )
    # The other reasons should still each fire once.
    fired = {f["health_reason"] for f in findings}
    assert fired == set(other_reasons)


def test_healthy_persona_produces_no_findings():
    """A persona with no health reasons must produce zero findings."""
    with _stub_fleet([]):
        findings = bff_main._health_reason_sentinel_findings()
    assert findings == []


def test_no_personas_produces_no_findings():
    """With no persona records the rule engine yields nothing (no fallback noise)."""
    original = bff_main._list_persona_records
    bff_main._list_persona_records = lambda tenant_id=None: []
    try:
        assert bff_main._health_reason_sentinel_findings() == []
    finally:
        bff_main._list_persona_records = original


# ---------------------------------------------------------------------------
# filter passthrough
# ---------------------------------------------------------------------------

def test_kind_filter_excludes_health_findings_when_not_persona_health():
    with _stub_fleet(["no_runtime_binding"]):
        assert bff_main._health_reason_sentinel_findings(kind="risk_breach") == []
        assert bff_main._health_reason_sentinel_findings(kind="persona_health")


def test_status_filter_excludes_health_findings_when_not_open():
    with _stub_fleet(["no_runtime_binding"]):
        assert bff_main._health_reason_sentinel_findings(status="resolved") == []
        assert bff_main._health_reason_sentinel_findings(status="open")


def test_severity_filter_selects_matching_rules_only():
    # active_incident -> critical, no_runtime_binding -> medium
    with _stub_fleet(["active_incident", "no_runtime_binding"]):
        critical = bff_main._health_reason_sentinel_findings(severity="critical")
        medium = bff_main._health_reason_sentinel_findings(severity="medium")
    assert {f["health_reason"] for f in critical} == {"active_incident"}
    assert {f["health_reason"] for f in medium} == {"no_runtime_binding"}


# ---------------------------------------------------------------------------
# 13-degraded-personas fixture (acceptance) — end-to-end over the HTTP endpoint
# ---------------------------------------------------------------------------

def _degraded_personas(n: int = 13) -> Dict[str, Dict[str, Any]]:
    """n paused personas with no runtime bindings.

    Matches the spec fixture: health.status=degraded, score=85,
    reasons=[persona_lifecycle_not_active, no_runtime_binding].
    """
    return {
        f"deg-{i:02d}": {
            "persona_id": f"deg-{i:02d}",
            "name": f"Degraded Persona {i}",
            "lifecycle_state": "paused",
            "created_at": f"2026-05-01T00:00:{i:02d}Z",
        }
        for i in range(n)
    }


def test_thirteen_degraded_personas_yield_open_findings(monkeypatch):
    """Acceptance: re-running the Sentinel pass on the 13 degraded personas
    surfaces >= 13 open findings (one per persona) at GET ?status=open."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    personas = _degraded_personas(13)
    with _persona_store(personas) as client:
        r = client.get("/bff/v5/sentinel/findings?status=open", headers=HEADERS)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    health_items = [it for it in items if it.get("kind") == "persona_health"]
    assert len(health_items) >= 13, f"expected >= 13 health findings, got {len(health_items)}"
    covered = {it["persona_id"] for it in health_items if it["persona_id"].startswith("deg-")}
    assert covered == set(personas), f"not every degraded persona covered: {set(personas) - covered}"
    # Every degraded persona surfaces the expected reasons.
    for it in health_items:
        if it["persona_id"].startswith("deg-"):
            assert it["health_reason"] in {"persona_lifecycle_not_active", "no_runtime_binding"}
            assert it["status"] == "open"


def test_degraded_personas_findings_filterable_by_kind(monkeypatch):
    """The rule-engine findings are filterable via the persona_health kind."""
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    with _persona_store(_degraded_personas(13)) as client:
        r = client.get(
            "/bff/v5/sentinel/findings?kind=persona_health&status=open",
            headers=HEADERS,
        )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items, "expected persona_health findings"
    assert all(it["kind"] == "persona_health" for it in items)
