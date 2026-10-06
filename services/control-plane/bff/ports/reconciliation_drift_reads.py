"""Port and client for authoritative reconciliation-drift reads."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

_DEFAULT_RECONCILIATION_TIMEOUT = 10
_STATUS_MAP = {
    "critical": "breached", "breached": "breached", "fail": "breached", "failed": "breached",
    "warning": "watch", "degraded": "watch", "warn": "watch", "watch": "watch",
    "ok": "ok", "pass": "ok", "passed": "ok",
}


def map_reconciliation_record_to_drift_report(
    record: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Map a reconciliation-drift owner record into the paper/live drift report DTO."""
    if not isinstance(record, dict):
        return None

    delta = record.get("delta_summary")
    delta_summary = delta if isinstance(delta, dict) else {}
    gen_at = record.get("generated_at")

    def _metric_state(metrics: Any, stage: Any, ts_key: str) -> Optional[Dict[str, Any]]:
        if not isinstance(metrics, dict):
            return None
        res: Dict[str, Any] = {"deployment_stage": stage, "metrics": dict(metrics)}
        if gen_at is not None:
            res[ts_key] = gen_at
        return res

    paper_baseline = _metric_state(delta_summary.get("baseline_metrics"), "paper", "captured_at")
    observed_state = _metric_state(delta_summary.get("observed_metrics"), record.get("deployment_stage"), "observed_at")

    drift_groups = None
    threshold_evaluation = None
    drift_checks = delta_summary.get("drift_checks")
    if isinstance(drift_checks, list):
        metrics, breached_ids, has_watch, has_breach = [], [], False, False
        for c in drift_checks:
            if not isinstance(c, dict):
                continue
            mid = str(c.get("metric") or "")
            st = str(c.get("status") or "").lower()
            mapped_status = _STATUS_MAP.get(st, st or "unknown")
            if mapped_status == "breached":
                has_breach = True
                if mid:
                    breached_ids.append(mid)
            elif mapped_status == "watch":
                has_watch = True
            metrics.append({
                "metric_id": mid,
                "label": mid.replace("_", " ").title() if mid else "",
                "baseline_value": c.get("baseline"),
                "observed_value": c.get("observed"),
                "delta": c.get("relative_delta"),
                "status": mapped_status,
            })
        overall = "breached" if has_breach else ("watch" if has_watch else "ok")
        drift_groups = [{
            "group_id": "reconciliation_drift",
            "label": "Reconciliation Drift",
            "status": overall,
            "metrics": metrics,
        }]
        threshold_evaluation = {
            "overall_status": overall,
            "summary": f"Observed metrics evaluation: {overall}.",
            "breached_metric_ids": breached_ids,
        }

    report: Dict[str, Any] = {
        "runtime_id": record.get("runtime_id"),
        "binding_id": record.get("binding_id") or record.get("runtime_binding_id") or record.get("scope_ref"),
        "deployment_stage": record.get("deployment_stage"),
        "generated_at": gen_at,
        "paper_baseline": paper_baseline,
        "observed_state": observed_state,
        "drift_groups": drift_groups or [],
        "threshold_evaluation": threshold_evaluation or {
            "overall_status": "ok" if record.get("status") == "resolved" else "unavailable",
            "summary": "No drift checks evaluated.",
            "breached_metric_ids": [],
        },
    }
    for key in ("artifact_id", "artifact_version", "evidence_refs", "recommended_actions"):
        if record.get(key) is not None:
            report[key] = record[key]
    plan_id = record.get("deployment_plan_id") or record.get("plan_id")
    if plan_id is not None:
        report["plan_id"] = plan_id

    return report


class ReconciliationDriftReadsPort:
    """Reads authoritative reconciliation and drift comparison records."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        records_provider: Optional[Callable[..., List[Dict[str, Any]]]] = None,
        default_tenant_id: Optional[str] = None,
    ) -> None:
        self._base_url = (base_url or os.getenv("RECONCILIATION_DRIFT_URL") or os.getenv("PANTHEON_RECONCILIATION_DRIFT_URL") or "").rstrip("/")
        self._auth_token = auth_token or os.getenv("RECONCILIATION_DRIFT_AUTH_TOKEN") or os.getenv("PANTHEON_RECONCILIATION_DRIFT_AUTH_TOKEN") or ""
        self._records_provider = records_provider
        self._default_tenant_id = default_tenant_id

    def _resolve_tenant_id(self) -> str:
        try:
            from ..core.owner_reads import selected_tenant
            current = selected_tenant.get()
            if current:
                return current
        except Exception:
            pass
        return self._default_tenant_id or os.getenv("PANTHEON_BFF_TENANT_ID") or "default"

    def _fetch_records(
        self,
        *,
        binding_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self._records_provider is not None:
            records = self._records_provider(binding_id=binding_id, runtime_id=runtime_id)
            if not isinstance(records, list):
                raise ValueError("records_provider must return a list")
            return records

        if not self._base_url:
            raise RuntimeError("RECONCILIATION_DRIFT_URL is not configured")

        params: Dict[str, str] = {}
        if binding_id:
            params["binding_id"] = binding_id
        if runtime_id:
            params["runtime_id"] = runtime_id

        query_str = f"?{urllib.parse.urlencode(params)}" if params else ""
        url = f"{self._base_url}/api/reconciliation-drift/reconciliation-records{query_str}"
        headers = {
            "Accept": "application/json",
            "X-Tenant-Id": self._resolve_tenant_id(),
        }
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"

        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=_DEFAULT_RECONCILIATION_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not isinstance(data, list):
                raise ValueError("Expected list from reconciliation-records")
            return data

    def get_surface_status(self) -> str:
        """Report availability of the reconciliation owner."""
        if self._records_provider is None and not self._base_url:
            return "unavailable"
        try:
            self._fetch_records()
            return "service"
        except Exception as exc:
            log.warning("Reconciliation drift read surface unavailable: %s", exc)
            return "unavailable"

    def get_paper_live_drift_report(
        self,
        *,
        binding_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the mapped paper/live comparison for the specified binding or runtime."""
        try:
            records = self._fetch_records(binding_id=binding_id, runtime_id=runtime_id)
        except Exception as exc:
            log.warning("Failed to fetch reconciliation records for %s/%s: %s", binding_id, runtime_id, exc)
            return None

        matching = []
        for r in records:
            if not isinstance(r, dict):
                continue
            stage = str(r.get("deployment_stage") or r.get("recon_type") or "").lower()
            if stage not in {"live", "live_run"}:
                continue
            r_rid = str(r.get("runtime_id") or "").strip()
            r_bid = str(r.get("binding_id") or r.get("runtime_binding_id") or r.get("scope_ref") or "").strip()
            match_rt = runtime_id and r_rid == str(runtime_id).strip()
            match_bd = binding_id and r_bid == str(binding_id).strip()
            if match_rt or match_bd or (not runtime_id and not binding_id):
                matching.append(r)

        if not matching:
            return None

        latest = sorted(matching, key=lambda r: str(r.get("generated_at") or ""))[-1]
        return map_reconciliation_record_to_drift_report(latest)

    def list_paper_live_drift_reports(self) -> List[Dict[str, Any]]:
        """List all mapped live drift reports across visible records."""
        try:
            records = self._fetch_records()
        except Exception:
            return []

        by_key: Dict[str, Dict[str, Any]] = {}
        for r in records:
            if not isinstance(r, dict):
                continue
            if str(r.get("deployment_stage") or r.get("recon_type") or "").lower() not in {"live", "live_run"}:
                continue
            key = str(r.get("runtime_id") or r.get("binding_id") or "")
            if key and (key not in by_key or str(r.get("generated_at") or "") > str(by_key[key].get("generated_at") or "")):
                by_key[key] = r

        return [
            mapped for mapped in (
                map_reconciliation_record_to_drift_report(r) for r in by_key.values()
            ) if mapped is not None
        ]
