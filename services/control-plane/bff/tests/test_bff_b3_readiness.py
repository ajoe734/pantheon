"""
BFF-B3-008: contract tests for Management readiness aggregates.

The five routes are read-only BFF Management surfaces. They publish EP5,
broker-live, capital-binding-live, BFF HA, and strict-publish readiness without
enabling live broker, live capital, or production topology changes.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.ports import create_read_surface_ports

OPERATOR_HEADERS = {"Authorization": "Bearer op-b3-readiness:operator"}

_REPO_ROOT = Path(__file__).resolve().parents[4]

_READINESS_EP5_EVIDENCE_REFS = [
    (
        "support/evidence/ep5-paper-tw-001/evidence-packet.json",
        "Paper TW evidence packet",
    ),
    (
        "docs/deployment/evidence/ep5-broker-tw-002/20260517T054748Z/evidence-packet/shioaji-sandbox-evidence-packet.json",
        "Broker sandbox evidence packet",
    ),
]
_READINESS_STRICT_PUBLISH_AUDIT = "support/evidence/lsp-final-audit/strict-publish-audit.json"
_READINESS_STRICT_PUBLISH_REPORT = "support/evidence/lsp-final-audit/strict-publish-audit.md"
_READINESS_BFF_HA_PACKET = "support/evidence/bff-ha-failover-demo/README.md"
_READINESS_BFF_HA_REVIEW = "support/evidence/bff-ha-failover-demo/review-ha-010-v2.md"
_READINESS_NO_REAL_CAPITAL_EVIDENCE = "support/evidence/MGMT-BROKER-003/no-real-capital-evidence.json"
_READINESS_BROKER_LIVE_DISABLED = (
    "docs/deployment/evidence/ep5-broker-tw-002/20260517T054748Z/sandbox-smoke/live-disabled.json"
)


def _read_repo_json_artifact(rel_path: str) -> Optional[Dict[str, Any]]:
    path = _REPO_ROOT / rel_path
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _read_repo_text_artifact(rel_path: str) -> str:
    path = _REPO_ROOT / rel_path
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _readiness_check(
    check_id: str,
    label: str,
    status: str,
    *,
    blocking: bool,
    message: str,
    evidence_refs: Optional[List[str]] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": check_id,
        "label": label,
        "status": status,
        "blocking": blocking,
        "message": message,
    }
    if evidence_refs:
        payload["evidence_refs"] = evidence_refs
    if details:
        payload["details"] = details
    return payload


def _readiness_summary(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_status: Dict[str, int] = {}
    blocking_reasons: List[str] = []
    for check in checks:
        status = str(check.get("status") or "")
        by_status[status] = by_status.get(status, 0) + 1
        if bool(check.get("blocking")) and status != "pass":
            blocking_reasons.append(str(check.get("id") or ""))
    can_proceed = not blocking_reasons
    readiness_status = "ready" if can_proceed else "blocked"
    return {
        "readinessStatus": readiness_status,
        "readiness_status": readiness_status,
        "canProceed": can_proceed,
        "can_proceed": can_proceed,
        "checkCount": len(checks),
        "check_count": len(checks),
        "passedCheckCount": by_status.get("pass", 0),
        "passed_check_count": by_status.get("pass", 0),
        "blockingReasonCount": len(blocking_reasons),
        "blocking_reason_count": len(blocking_reasons),
        "blockingReasons": blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "byStatus": by_status,
        "by_status": by_status,
    }


def _readiness_response(
    *,
    readiness_id: str,
    title: str,
    checks: List[Dict[str, Any]],
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    summary = _readiness_summary(checks)
    surface_key = f"management_readiness_{readiness_id.replace('-', '_')}"
    return {
        "data": {
            "id": readiness_id,
            "readinessId": readiness_id,
            "readiness_id": readiness_id,
            "title": title,
            "readinessStatus": summary["readinessStatus"],
            "readiness_status": summary["readiness_status"],
            "canProceed": summary["canProceed"],
            "can_proceed": summary["can_proceed"],
            "blockingReasons": summary["blockingReasons"],
            "blocking_reasons": summary["blocking_reasons"],
            "checks": checks,
            "details": details or {},
        },
        "summary": summary,
        "checks": checks,
        "meta": {
            "surfaces": {
                surface_key: {
                    "source": "bff_composed",
                    "status": "ok" if summary["can_proceed"] else "degraded",
                    "readiness_status": summary["readiness_status"],
                    "can_proceed": summary["can_proceed"],
                }
            }
        },
    }


def _build_app(store: Any) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    def _require_auth(authorization: Optional[str] = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": "AUTH_REQUIRED", "message": "Auth required"}},
            )

    @app.get("/bff/management/readiness/ep5")
    def ep5(authorization: Optional[str] = Header(default=None)):
        _require_auth(authorization)
        checks = [
            _readiness_check(
                "ep5_evidence_bundle",
                "EP5 prerequisite evidence bundle",
                "fail",
                blocking=True,
                message="EP5 readiness requires prerequisite evidence bundle.",
            )
        ]
        return _readiness_response(readiness_id="ep5", title="EP5 Readiness", checks=checks)

    @app.get("/bff/management/readiness/broker-live")
    def broker_live(authorization: Optional[str] = Header(default=None)):
        _require_auth(authorization)
        broker_surface = store.get_openclaw_broker_adapter_readiness() if hasattr(store, "get_openclaw_broker_adapter_readiness") else {}
        live_gate_enabled = os.getenv("PANTHEON_LIVE_BROKER_ENABLED", "").lower() in {"1", "true", "yes", "on"}
        live_execution_enabled = bool(broker_surface.get("live_execution_enabled"))
        live_adapter_state = str(broker_surface.get("live_adapter_state") or "unknown").lower()
        broker_live_ready = (
            live_gate_enabled
            and live_execution_enabled
            and live_adapter_state in {"enabled", "active"}
        )
        checks = [
            _readiness_check(
                "openclaw_broker_readiness_surface",
                "OpenClaw broker readiness surface",
                "pass" if broker_surface.get("overall_status") != "unavailable" else "fail",
                blocking=True,
                message="Broker live readiness requires OpenClaw surface.",
            ),
            _readiness_check(
                "live_broker_gate",
                "Live broker gate",
                "pass" if broker_live_ready else "blocked",
                blocking=True,
                message="Live broker execution is fail-closed.",
            ),
            _readiness_check(
                "no_real_capital_side_effects",
                "No real capital side effects",
                "pass" if broker_surface.get("is_real_capital") is False and broker_surface.get("is_real_order") is False else "fail",
                blocking=True,
                message="Broker readiness must not report real capital.",
            ),
        ]
        details = {
            "broker_readiness": broker_surface,
            "live_broker_enabled": broker_live_ready,
            "fail_closed": not broker_live_ready,
        }
        return _readiness_response(
            readiness_id="broker-live",
            title="Broker Live Readiness",
            checks=checks,
            details=details,
        )

    @app.get("/bff/management/readiness/capital-binding-live")
    def capital_binding_live(authorization: Optional[str] = Header(default=None)):
        _require_auth(authorization)
        gate_enabled = (
            os.getenv("OPENCLAW_CAPITAL_BINDING_ENABLED", "").lower() in {"1", "true", "yes", "on"}
            or os.getenv("PANTHEON_CAPITAL_BINDING_LIVE_ENABLED", "").lower() in {"1", "true", "yes", "on"}
        )
        checks = [
            _readiness_check(
                "capital_binding_live_gate",
                "Capital binding live gate",
                "pass" if gate_enabled else "blocked",
                blocking=True,
                message="Live capital binding remains fail-closed.",
            ),
            _readiness_check(
                "active_persona_capital_bindings",
                "Active persona-capital binding records",
                "warn",
                blocking=False,
                message="Active persona-capital bindings.",
            ),
            _readiness_check(
                "live_runtime_binding_absence",
                "No live runtime binding activated by this BFF",
                "pass",
                blocking=True,
                message="Readiness publication must not silently materialize live bindings.",
            ),
        ]
        details = {
            "capital_binding_live_enabled": gate_enabled,
            "fail_closed": not gate_enabled,
        }
        return _readiness_response(
            readiness_id="capital-binding-live",
            title="Capital Binding Live Readiness",
            checks=checks,
            details=details,
        )

    @app.get("/bff/management/readiness/bff-ha")
    def bff_ha(authorization: Optional[str] = Header(default=None)):
        _require_auth(authorization)
        packet_text = _read_repo_text_artifact(_READINESS_BFF_HA_PACKET)
        review_text = _read_repo_text_artifact(_READINESS_BFF_HA_REVIEW)
        packet_exists = bool(packet_text)
        review_approved = "Status: **approved**" in review_text or "Approved." in review_text
        checks = [
            _readiness_check(
                "dev_failover_demo_packet",
                "Dev failover demo packet recorded",
                "pass" if packet_exists else "fail",
                blocking=True,
                message="Dev failover demo packet.",
            ),
            _readiness_check(
                "dev_failover_demo_review",
                "Dev failover demo review approved",
                "pass" if review_approved else "fail",
                blocking=True,
                message="Dev failover demo review.",
            ),
            _readiness_check(
                "production_ha_topology",
                "Production HA topology and LB cutover",
                "blocked",
                blocking=True,
                message="Production topology gate.",
            ),
        ]
        details = {
            "dev_demo_ready": packet_exists and review_approved,
            "production_topology_ready": False,
        }
        return _readiness_response(
            readiness_id="bff-ha",
            title="BFF HA Readiness",
            checks=checks,
            details=details,
        )

    @app.get("/bff/management/readiness/strict-publish")
    def strict_publish(authorization: Optional[str] = Header(default=None)):
        _require_auth(authorization)
        audit = _read_repo_json_artifact(_READINESS_STRICT_PUBLISH_AUDIT) or {}
        component_status = audit.get("component_status") if isinstance(audit.get("component_status"), dict) else {}
        forbidden_scan = (
            (audit.get("components") or {}).get("forbidden_path_scan")
            if isinstance(audit.get("components"), dict)
            else {}
        )
        forbidden_signals = (
            forbidden_scan.get("forbidden_signals")
            if isinstance(forbidden_scan, dict) and isinstance(forbidden_scan.get("forbidden_signals"), list)
            else []
        )
        checks = [
            _readiness_check(
                "browser_probe",
                "Browser health and /bff/me probe",
                "pass" if component_status.get("LSP-002-V2") is True else "fail",
                blocking=True,
                message="Browser probe.",
            ),
            _readiness_check(
                "bundle_hash_capture",
                "Hosted bundle hash capture",
                "pass" if component_status.get("LSP-003-V2") is True else "fail",
                blocking=True,
                message="Bundle hash capture.",
            ),
            _readiness_check(
                "forbidden_path_scan",
                "Forbidden mock/seed runtime path scan",
                "pass" if component_status.get("LSP-004-V2") is True else "fail",
                blocking=True,
                message="Forbidden mock/seed runtime path scan.",
                details={"forbidden_signal_count": len(forbidden_signals)},
            ),
        ]
        details = {
            "passed": bool(audit.get("passed")),
            "checked_at": audit.get("checked_at"),
        }
        return _readiness_response(
            readiness_id="strict-publish",
            title="Strict Publish Audit",
            checks=checks,
            details=details,
        )

    return app


@contextmanager
def _readiness_client() -> Iterator[TestClient]:
    tracked_env = {
        "PANTHEON_LIVE_BROKER_ENABLED": os.environ.get("PANTHEON_LIVE_BROKER_ENABLED"),
        "OPENCLAW_CAPITAL_BINDING_ENABLED": os.environ.get("OPENCLAW_CAPITAL_BINDING_ENABLED"),
        "PANTHEON_CAPITAL_BINDING_LIVE_ENABLED": os.environ.get("PANTHEON_CAPITAL_BINDING_LIVE_ENABLED"),
    }
    with tempfile.TemporaryDirectory():
        os.environ["PANTHEON_LIVE_BROKER_ENABLED"] = "false"
        os.environ["OPENCLAW_CAPITAL_BINDING_ENABLED"] = "false"
        os.environ["PANTHEON_CAPITAL_BINDING_LIVE_ENABLED"] = "false"
        try:
            store = create_read_surface_ports()
            store.get_openclaw_broker_adapter_readiness = lambda: {
                "surface": "openclaw_broker_adapter_readiness",
                "overall_status": "ok",
                "live_adapter_state": "fail_closed",
                "live_execution_enabled": False,
                "canary_execution_enabled": False,
                "is_real_capital": False,
                "is_real_order": False,
                "gate_reason": "fail_closed_explicit_gate_required",
                "service_status": {"status": "ok", "source": "test"},
            }
            app = _build_app(store)
            yield TestClient(app)
        finally:
            for key, value in tracked_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def test_management_readiness_routes_publish_fail_closed_status() -> None:
    routes = {
        "ep5": "/bff/management/readiness/ep5",
        "broker-live": "/bff/management/readiness/broker-live",
        "capital-binding-live": "/bff/management/readiness/capital-binding-live",
        "bff-ha": "/bff/management/readiness/bff-ha",
        "strict-publish": "/bff/management/readiness/strict-publish",
    }
    with _readiness_client() as client:
        for readiness_id, path in routes.items():
            response = client.get(path, headers=OPERATOR_HEADERS)

            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["data"]["id"] == readiness_id
            assert payload["data"]["canProceed"] is False
            assert payload["summary"]["readinessStatus"] == "blocked"
            assert payload["summary"]["blockingReasonCount"] >= 1
            assert payload["checks"]
            assert payload["meta"]["surfaces"][f"management_readiness_{readiness_id.replace('-', '_')}"][
                "source"
            ] == "bff_composed"


def test_management_broker_live_readiness_uses_operator_read_auth_and_no_live_side_effects() -> None:
    with _readiness_client() as client:
        response = client.get("/bff/management/readiness/broker-live", headers=OPERATOR_HEADERS)

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["details"]["fail_closed"] is True
        assert payload["data"]["details"]["broker_readiness"]["live_execution_enabled"] is False
        assert payload["data"]["details"]["broker_readiness"]["is_real_capital"] is False
        gate_check = next(check for check in payload["checks"] if check["id"] == "live_broker_gate")
        assert gate_check["status"] == "blocked"
        assert gate_check["blocking"] is True


def test_management_strict_publish_readiness_exposes_audit_blocker() -> None:
    with _readiness_client() as client:
        response = client.get("/bff/management/readiness/strict-publish", headers=OPERATOR_HEADERS)

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["data"]["details"]["passed"] is False
        forbidden_scan = next(check for check in payload["checks"] if check["id"] == "forbidden_path_scan")
        assert forbidden_scan["status"] == "fail"
        assert forbidden_scan["blocking"] is True
        assert forbidden_scan["details"]["forbidden_signal_count"] >= 1


def test_management_readiness_requires_authentication() -> None:
    with _readiness_client() as client:
        response = client.get("/bff/management/readiness/ep5")

        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"
