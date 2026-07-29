from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from services.background_worker_health import (
    healthcheck as check_worker_health,
    write_health,
)
from services.trade_journey.correlation_envelope import propagate_envelope


DEFAULT_WARNING_RELATIVE_DELTA = 0.2
DEFAULT_CRITICAL_RELATIVE_DELTA = 0.5
DRIFT_STATUSES = {"warning", "critical"}
SUMMARY_NUMERIC_FIELDS = (
    "pnl",
    "drawdown",
    "sharpe_ratio",
    "fill_rate",
    "avg_slippage_bps",
    "total_trades",
    "queue_lag_ms",
    "event_delivery_lag_ms",
)
CONSUMER_STATE_VERSION = 2
_WORKER_NAME = "reconciliation-drift-consumer"


class ConsumerStateError(RuntimeError):
    """Raised when durable consumer state is corrupt or cannot be checkpointed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    safe = safe.strip("-._")
    return safe or "unknown"


def _next_id(prefix: str, timestamp: str, existing: set[str]) -> str:
    date_prefix = timestamp[:10].replace("-", "")
    index = len(existing) + 1
    candidate = f"{prefix}-{date_prefix}-{index:03d}"
    while candidate in existing:
        index += 1
        candidate = f"{prefix}-{date_prefix}-{index:03d}"
    return candidate


def _status_rank(status: str) -> int:
    return {"ok": 0, "warning": 1, "critical": 2}.get(status, 0)


def _threshold_for(metric: str, thresholds: dict[str, Any]) -> tuple[float, float]:
    raw = thresholds.get(metric)
    if isinstance(raw, dict):
        warning = _numeric(raw.get("warning_relative_delta"))
        critical = _numeric(raw.get("critical_relative_delta"))
        return (
            DEFAULT_WARNING_RELATIVE_DELTA if warning is None else warning,
            DEFAULT_CRITICAL_RELATIVE_DELTA if critical is None else critical,
        )
    return DEFAULT_WARNING_RELATIVE_DELTA, DEFAULT_CRITICAL_RELATIVE_DELTA


def _drift_type_for_metric(metric: str) -> str:
    normalized = metric.lower()
    if "slippage" in normalized:
        return "slippage"
    if normalized in {"pnl", "profit", "profit_loss", "return", "returns"} or "drawdown" in normalized:
        return "pnl"
    if any(token in normalized for token in ("fee", "cost", "commission")):
        return "cost"
    if any(token in normalized for token in ("health", "heartbeat", "latency")):
        return "runtime_health"
    if "policy" in normalized:
        return "policy"
    return "execution"


def _severity_for_status(status: str) -> str:
    if status == "critical":
        return "critical"
    if status == "warning":
        return "medium"
    return "low"


def _recommended_action(severity: str, deployment_stage: str | None) -> str:
    if severity == "critical":
        if deployment_stage in {"live", "canary"}:
            return "open_incident"
        return "rerun_research"
    if severity in {"high", "medium"}:
        return "rerun_research"
    return "observe"


def _event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event = payload.get("event")
    if isinstance(event, dict) and not payload.get("metrics"):
        merged = dict(event)
        if payload.get("status") and "source_status" not in merged:
            merged["source_status"] = payload["status"]
        return merged
    return payload


def load_telemetry_events_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        events: list[dict[str, Any]] = []
        for item in payload:
            events.extend(load_telemetry_events_from_payload(item))
        return events
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("events"), list):
        return load_telemetry_events_from_payload(payload["events"])
    if isinstance(payload.get("telemetry_events"), list):
        return load_telemetry_events_from_payload(payload["telemetry_events"])
    return [_event_payload(payload)]


def load_telemetry_events_from_path(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if source.is_dir():
        events: list[dict[str, Any]] = []
        for child in sorted(source.iterdir()):
            if child.suffix.lower() in {".json", ".jsonl"}:
                events.extend(load_telemetry_events_from_path(child))
        return events
    if not source.exists():
        raise FileNotFoundError(f"telemetry fixture path not found: {source}")
    if source.suffix.lower() == ".jsonl":
        events = []
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.extend(load_telemetry_events_from_payload(json.loads(line)))
        return events
    return load_telemetry_events_from_payload(json.loads(source.read_text(encoding="utf-8")))


def _event_metrics(event: dict[str, Any]) -> dict[str, float]:
    raw_metrics = event.get("metrics") or event.get("observed_metrics") or {}
    if not isinstance(raw_metrics, dict):
        return {}
    metrics: dict[str, float] = {}
    for key, raw_value in raw_metrics.items():
        number = _numeric(raw_value)
        if number is not None:
            metrics[str(key)] = number
    return metrics


def _event_baseline_metrics(event: dict[str, Any], fallback: dict[str, Any] | None) -> dict[str, Any]:
    for key in ("baseline_metrics", "baseline"):
        raw = event.get(key)
        if isinstance(raw, dict):
            metrics = raw.get("metrics") if key == "baseline" else raw
            if isinstance(metrics, dict):
                return metrics
    return fallback or {}


def _event_thresholds(event: dict[str, Any], fallback: dict[str, Any] | None) -> dict[str, Any]:
    raw = event.get("thresholds")
    if isinstance(raw, dict):
        return raw
    return fallback or {}


def _build_drift_checks(
    *,
    baseline_metrics: dict[str, Any],
    observed_metrics: dict[str, float],
    thresholds: dict[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for metric, baseline_raw in sorted(baseline_metrics.items()):
        baseline = _numeric(baseline_raw)
        if baseline is None:
            continue
        observed = observed_metrics.get(metric)
        if observed is None:
            continue
        denominator = abs(baseline) if baseline != 0 else 1.0
        relative_delta = abs(observed - baseline) / denominator
        warning_threshold, critical_threshold = _threshold_for(metric, thresholds)
        if relative_delta >= critical_threshold:
            status = "critical"
        elif relative_delta >= warning_threshold:
            status = "warning"
        else:
            status = "ok"
        checks.append(
            {
                "metric": metric,
                "status": status,
                "baseline": baseline,
                "observed": observed,
                "relative_delta": relative_delta,
                "warning_relative_delta": warning_threshold,
                "critical_relative_delta": critical_threshold,
            }
        )
    return checks


def build_drift_report_from_event(
    event: dict[str, Any],
    *,
    baseline_metrics: dict[str, Any] | None = None,
    thresholds: dict[str, Any] | None = None,
    generated_at: str | None = None,
    existing_report_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    normalized = _event_payload(event)
    timestamp = generated_at or str(normalized.get("generated_at") or normalized.get("event_time") or utc_now())
    observed_metrics = _event_metrics(normalized)
    baseline = _event_baseline_metrics(normalized, baseline_metrics)
    event_thresholds = _event_thresholds(normalized, thresholds)
    checks = _build_drift_checks(
        baseline_metrics=baseline,
        observed_metrics=observed_metrics,
        thresholds=event_thresholds,
    )
    breached = [check for check in checks if check["status"] in DRIFT_STATUSES]
    if not breached:
        return None

    worst_check = max(breached, key=lambda check: _status_rank(str(check["status"])))
    severity = _severity_for_status(str(worst_check["status"]))
    event_id = str(normalized.get("event_id") or normalized.get("id") or "").strip()
    existing = existing_report_ids or set()
    report_id = str(normalized.get("drift_report_id") or "").strip()
    if not report_id and event_id:
        report_id = f"drift-{_safe_id(event_id)}"
    if not report_id:
        report_id = _next_id("drift", timestamp, existing)

    recon_run_id = str(normalized.get("recon_run_id") or "").strip()
    if not recon_run_id and event_id:
        recon_run_id = f"recon-{_safe_id(event_id)}"
    if not recon_run_id:
        recon_run_id = _next_id("recon", timestamp, existing)

    binding_id = str(
        normalized.get("binding_id")
        or normalized.get("runtime_binding_id")
        or normalized.get("scope_ref")
        or ""
    ).strip()
    runtime_id = str(normalized.get("runtime_id") or "").strip()
    scope_ref = binding_id or runtime_id or event_id or report_id
    deployment_stage = str(normalized.get("deployment_stage") or normalized.get("mode") or "").strip().lower() or None
    deployment_plan_id = str(normalized.get("deployment_plan_id") or normalized.get("plan_id") or "").strip()
    capital_pool_id = str(normalized.get("capital_pool_id") or "").strip()
    persona_capital_binding_id = str(
        normalized.get("persona_capital_binding_id") or normalized.get("persona_binding_id") or ""
    ).strip()
    trace_id = str(normalized.get("trace_id") or "").strip()
    correlation_envelope = normalized.get("correlation_envelope")
    tenant_id = str(
        normalized.get("tenant_id")
        or (
            correlation_envelope.get("tenant_id")
            if isinstance(correlation_envelope, dict)
            else ""
        )
        or ""
    ).strip()
    baseline_ref = str(
        normalized.get("baseline_ref")
        or normalized.get("paper_baseline_ref")
        or "governed-runtime-baseline"
    )
    current_ref = str(normalized.get("current_ref") or normalized.get("actual_ref") or event_id or report_id)

    evidence_refs: list[str] = []
    if event_id:
        evidence_refs.append(f"telemetry_event:{event_id}")
    if binding_id:
        evidence_refs.append(f"runtime_binding:{binding_id}")
    artifact_id = str(normalized.get("artifact_id") or "").strip()
    artifact_version = str(normalized.get("artifact_version") or "").strip()
    if artifact_id:
        artifact_ref = artifact_id if not artifact_version else f"{artifact_id}@{artifact_version}"
        evidence_refs.append(f"artifact:{artifact_ref}")
    evidence_refs.append(f"drift_report:{report_id}")
    evidence_refs.append(f"reconciliation_record:{recon_run_id}")
    for ref in normalized.get("evidence_refs") or []:
        if isinstance(ref, str):
            evidence_refs.append(ref)

    cluster_id = str(normalized.get("incident_cluster_id") or "").strip()
    if not cluster_id:
        cluster_id = f"drift:{_safe_id(str(worst_check['metric']))}"

    result = {
        "id": report_id,
        "drift_report_id": report_id,
        "tenant_id": tenant_id or None,
        "recon_run_id": recon_run_id,
        "drift_type": _drift_type_for_metric(str(worst_check["metric"])),
        "incident_cluster_id": cluster_id,
        "scope_ref": scope_ref,
        "binding_id": binding_id or None,
        "runtime_id": runtime_id or None,
        "deployment_stage": deployment_stage,
        "deployment_plan_id": deployment_plan_id or None,
        "capital_pool_id": capital_pool_id or None,
        "persona_capital_binding_id": persona_capital_binding_id or None,
        "artifact_id": artifact_id or None,
        "artifact_version": artifact_version or None,
        "trace_id": trace_id or None,
        "telemetry_event_ids": [event_id] if event_id else [],
        "baseline_ref": baseline_ref,
        "current_ref": current_ref,
        "severity": severity,
        "metrics": {
            "baseline_metrics": baseline,
            "current_metrics": observed_metrics,
            "drift_checks": checks,
            "breached_metric_ids": [str(check["metric"]) for check in breached],
            "worst_metric": str(worst_check["metric"]),
        },
        "evidence_refs": evidence_refs,
        "recommended_action": _recommended_action(severity, deployment_stage),
        "status": "open",
        "generated_at": timestamp,
        "source_contract": {
            "telemetry_truth_owner": "telemetry-ingest",
            "derived_only": True,
            "emergency_control_chain_affected": False,
        },
    }
    incoming_envelope = correlation_envelope
    if isinstance(incoming_envelope, dict):
        result["correlation_envelope"] = propagate_envelope(
            incoming_envelope,
            producer="reconciliation.drift",
            event_id=recon_run_id,
            event_time=timestamp,
        )
    return result


def post_events(service_url: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    url = service_url.rstrip("/") + "/api/reconciliation-drift/telemetry-events/consume"
    tenant_ids = {
        str(
            event.get("tenant_id")
            or (
                event.get("correlation_envelope", {}).get("tenant_id")
                if isinstance(event.get("correlation_envelope"), dict)
                else ""
            )
            or os.getenv("PANTHEON_TENANT_ID")
            or ""
        ).strip()
        for event in events
    }
    tenant_ids.discard("")
    if len(tenant_ids) > 1:
        raise RuntimeError("one consumer delivery cannot span multiple tenants")
    tenant_id = next(iter(tenant_ids), "")
    headers = {"Content-Type": "application/json"}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    auth_token = os.getenv("RECONCILIATION_DRIFT_AUTH_TOKEN", "")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(
            {
                "tenant_id": tenant_id or None,
                "worker_id": os.getenv(
                    "RECONCILIATION_DRIFT_CONSUMER_WORKER_ID",
                    f"{os.getenv('HOSTNAME', 'consumer')}:{os.getpid()}",
                ),
                "events": events,
            }
        ).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - dev service URL is operator configured.
            body = response.read().decode("utf-8")
            decoded = json.loads(body) if body else {}
            if isinstance(decoded, dict) and decoded.get("status") == "deferred":
                raise RuntimeError("reconciliation-drift consume lease is active")
            return decoded
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"reconciliation-drift consume failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"reconciliation-drift service unavailable: {exc.reason}") from exc


def fetch_runtime_summaries(telemetry_url: str, *, timeout_seconds: float = 10.0) -> list[dict[str, Any]]:
    """Read the telemetry-owned authoritative runtime-summary projection."""
    if not telemetry_url:
        raise RuntimeError("PANTHEON_TELEMETRY_API_URL is required")
    request = urllib.request.Request(
        telemetry_url.rstrip("/") + "/api/telemetry/runtime-summaries",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"telemetry runtime summaries rejected: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"telemetry service unavailable: {exc.reason}") from exc

    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("telemetry runtime summaries returned invalid JSON") from exc
    if isinstance(payload, list):
        summaries = payload
    elif isinstance(payload, dict):
        summaries = payload.get("summaries") or payload.get("items") or []
    else:
        raise RuntimeError("telemetry runtime summaries returned an invalid envelope")
    if not isinstance(summaries, list):
        raise RuntimeError("telemetry runtime summaries must be a list")
    return [item for item in summaries if isinstance(item, dict)]


def runtime_summary_to_event(summary: dict[str, Any]) -> dict[str, Any]:
    """Convert one authoritative summary without fabricating identity or metrics."""
    event_id = str(summary.get("last_event_id") or summary.get("last_heartbeat_event_id") or "").strip()
    binding_id = str(summary.get("binding_id") or summary.get("runtime_binding_id") or "").strip()
    runtime_id = str(summary.get("runtime_id") or summary.get("id") or "").strip()
    deployment_stage = str(summary.get("deployment_stage") or "").strip().lower()
    required = {
        "event_id": event_id,
        "binding_id": binding_id,
        "runtime_id": runtime_id,
        "deployment_stage": deployment_stage,
        "deployment_plan_id": str(summary.get("deployment_plan_id") or summary.get("plan_id") or "").strip(),
        "capital_pool_id": str(summary.get("capital_pool_id") or "").strip(),
        "persona_capital_binding_id": str(summary.get("persona_capital_binding_id") or "").strip(),
        "artifact_id": str(summary.get("artifact_id") or "").strip(),
        "artifact_version": str(summary.get("artifact_version") or "").strip(),
        "trace_id": str(summary.get("trace_id") or "").strip(),
    }
    missing = sorted(key for key, value in required.items() if not value)
    if missing:
        raise ValueError("runtime summary missing authoritative identity: " + ", ".join(missing))

    metrics: dict[str, float] = {}
    nested_metrics = summary.get("observed_metrics") or summary.get("metrics") or {}
    if isinstance(nested_metrics, dict):
        for key, value in nested_metrics.items():
            numeric = _numeric(value)
            if numeric is not None:
                metrics[str(key)] = numeric
    for field in SUMMARY_NUMERIC_FIELDS:
        numeric = _numeric(summary.get(field))
        if numeric is not None:
            metrics[field] = numeric
    if not metrics:
        raise ValueError("runtime summary has no authoritative numeric actual-state metrics")

    event: dict[str, Any] = {
        **required,
        "event_type": str(summary.get("last_event_type") or "runtime_summary"),
        "created_at": str(summary.get("last_event_at") or summary.get("collected_at") or ""),
        "metrics": metrics,
        "baseline_metrics": summary.get("baseline_metrics") if isinstance(summary.get("baseline_metrics"), dict) else {},
        "thresholds": summary.get("thresholds") if isinstance(summary.get("thresholds"), dict) else {},
        "baseline_ref": str(summary.get("baseline_ref") or ""),
        "current_ref": f"telemetry-runtime-summary:{runtime_id}:{event_id}",
        "telemetry_event_ids": list(
            dict.fromkeys(
                str(value)
                for value in (summary.get("last_event_id"), summary.get("last_heartbeat_event_id"))
                if value
            )
        ),
        "runtime_state": summary.get("state"),
        "health_summary": summary.get("health_summary") if isinstance(summary.get("health_summary"), dict) else {},
        "source_contract": {
            "telemetry_truth_owner": "telemetry-ingest",
            "projection_source": summary.get("projection_source") or "telemetry_ingest",
            "synthetic": False,
        },
    }
    correlation_envelope = summary.get("correlation_envelope")
    if isinstance(correlation_envelope, dict):
        event["correlation_envelope"] = correlation_envelope
    tenant_id = str(
        summary.get("tenant_id")
        or (
            correlation_envelope.get("tenant_id")
            if isinstance(correlation_envelope, dict)
            else ""
        )
        or os.getenv("PANTHEON_TENANT_ID")
        or ""
    ).strip()
    if tenant_id:
        event["tenant_id"] = tenant_id
    return event


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


class ConsumerWorkerState:
    """Fail-closed durable delivery state with a cross-process worker lease."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.pending: dict[str, dict[str, Any]] = {}
        self.dead_letters: dict[str, dict[str, Any]] = {}
        self.completed: dict[str, dict[str, Any]] = {}
        self.last_success_at: str | None = None
        self.last_failure_at: str | None = None
        self.last_failure_error: str | None = None
        self.lease_owner: str | None = None
        self.lease_token: str | None = None
        self.lease_expires_at: str | None = None
        self.load_error: str | None = None
        self._held_lease_owner: str | None = None
        self._held_lease_token: str | None = None
        self._held_lease_seconds: float | None = None
        self.reload()

    @contextlib.contextmanager
    def _locked(self):
        if self.path is None:
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.parent / f".{self.path.name}.lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _parse_document(path: Path, text: str) -> dict[str, Any]:
        def reject_constant(value: str) -> None:
            raise ConsumerStateError(
                f"{path}: non-standard JSON constant {value!r} is not allowed"
            )

        def reject_duplicates(pairs):
            payload: dict[str, Any] = {}
            for key, value in pairs:
                if key in payload:
                    raise ConsumerStateError(
                        f"{path}: duplicate JSON key {key!r} is not allowed"
                    )
                payload[key] = value
            return payload

        try:
            payload = json.loads(
                text,
                parse_constant=reject_constant,
                object_pairs_hook=reject_duplicates,
            )
        except json.JSONDecodeError as exc:
            raise ConsumerStateError(f"{path}: malformed JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ConsumerStateError(f"{path}: state must be a JSON object")
        if payload.get("version") not in {1, CONSUMER_STATE_VERSION}:
            raise ConsumerStateError(f"{path}: unsupported state version")
        for field in ("pending", "dead_letters", "completed"):
            value = payload.get(field, {})
            if not isinstance(value, dict):
                raise ConsumerStateError(f"{path}: {field} must be an object")
            for identity, record in value.items():
                if not isinstance(record, dict):
                    raise ConsumerStateError(
                        f"{path}: {field}[{identity!r}] must be an object"
                    )
        return payload

    def _apply_document(self, payload: dict[str, Any]) -> None:
        self.pending = dict(payload.get("pending") or {})
        self.dead_letters = dict(payload.get("dead_letters") or {})
        self.completed = dict(payload.get("completed") or {})
        self.last_success_at = payload.get("last_success_at")
        self.last_failure_at = payload.get("last_failure_at")
        self.last_failure_error = payload.get("last_failure_error")
        self.lease_owner = payload.get("lease_owner")
        self.lease_token = payload.get("lease_token")
        self.lease_expires_at = payload.get("lease_expires_at")

    def _read_document_locked(self) -> dict[str, Any] | None:
        if self.path is None or not self.path.exists():
            return None
        text = self.path.read_text(encoding="utf-8")
        if not text.strip():
            raise ConsumerStateError(f"{self.path}: state file is empty")
        return self._parse_document(self.path, text)

    def _read_locked(self) -> None:
        try:
            payload = self._read_document_locked()
            if payload is not None:
                self._apply_document(payload)
            self.load_error = None
        except (OSError, UnicodeError, TypeError, ValueError, ConsumerStateError) as exc:
            self.load_error = f"{type(exc).__name__}: {exc}"

    def reload(self) -> None:
        with self._locked():
            self._held_lease_owner = None
            self._held_lease_token = None
            self._held_lease_seconds = None
            self._read_locked()

    def _document(self) -> dict[str, Any]:
        return {
            "version": CONSUMER_STATE_VERSION,
            "pending": self.pending,
            "dead_letters": self.dead_letters,
            "completed": self.completed,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_failure_error": self.last_failure_error,
            "lease_owner": self.lease_owner,
            "lease_token": self.lease_token,
            "lease_expires_at": self.lease_expires_at,
            "updated_at": utc_now(),
        }

    def _write_locked(self) -> None:
        if self.path is None:
            return
        temporary_name: str | None = None
        try:
            fd, temporary_name = tempfile.mkstemp(
                dir=str(self.path.parent),
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    self._document(),
                    handle,
                    sort_keys=True,
                    allow_nan=False,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
            temporary_name = None
            directory_fd = os.open(str(self.path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if temporary_name:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(temporary_name)

    def save(self) -> None:
        if self.path is None:
            return
        if self.load_error:
            raise ConsumerStateError(
                f"refusing to overwrite unreadable consumer state: {self.load_error}"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._locked():
            try:
                canonical = self._read_document_locked()
            except (
                OSError,
                UnicodeError,
                TypeError,
                ValueError,
                ConsumerStateError,
            ) as exc:
                self.load_error = f"{type(exc).__name__}: {exc}"
                raise ConsumerStateError(self.load_error) from exc
            self._assert_held_lease(canonical)
            canonical_expiry = _parse_timestamp(
                canonical.get("lease_expires_at") if canonical else None
            )
            if canonical_expiry is None or self._held_lease_seconds is None:
                raise ConsumerStateError(
                    "consumer worker lease token has no renewable expiry"
                )
            renewed_expiry = datetime.now(timezone.utc) + timedelta(
                seconds=self._held_lease_seconds
            )
            self.lease_owner = self._held_lease_owner
            self.lease_token = self._held_lease_token
            self.lease_expires_at = max(
                canonical_expiry,
                renewed_expiry,
            ).isoformat().replace("+00:00", "Z")
            self._write_locked()

    def _assert_held_lease(
        self,
        canonical: dict[str, Any] | None,
        *,
        worker_id: str | None = None,
    ) -> None:
        if (
            canonical is None
            or not self._held_lease_owner
            or not self._held_lease_token
            or canonical.get("lease_owner") != self._held_lease_owner
            or canonical.get("lease_token") != self._held_lease_token
            or (
                worker_id is not None
                and worker_id != self._held_lease_owner
            )
        ):
            raise ConsumerStateError(
                "consumer worker lease token changed or was not acquired"
            )

    def acquire_lease(
        self,
        *,
        worker_id: str,
        lease_seconds: float,
        now: datetime,
    ) -> bool:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be > 0")
        current = now.astimezone(timezone.utc)
        with self._locked():
            self._read_locked()
            if self.load_error:
                raise ConsumerStateError(self.load_error)
            expiry = _parse_timestamp(self.lease_expires_at)
            if (
                self.lease_owner
                and self.lease_token
                and expiry is not None
                and expiry > current
            ):
                self._held_lease_owner = None
                self._held_lease_token = None
                self._held_lease_seconds = None
                return False
            self.lease_owner = worker_id
            self.lease_token = uuid.uuid4().hex
            self.lease_expires_at = (
                current + timedelta(seconds=lease_seconds)
            ).isoformat().replace("+00:00", "Z")
            self._write_locked()
            self._held_lease_owner = worker_id
            self._held_lease_token = self.lease_token
            self._held_lease_seconds = lease_seconds
            return True

    def release_lease(self, *, worker_id: str) -> None:
        if self.path is None:
            if (
                self._held_lease_owner != worker_id
                or not self._held_lease_token
                or self.lease_token != self._held_lease_token
            ):
                raise ConsumerStateError(
                    "consumer worker lease token changed or was not acquired"
                )
            self.lease_owner = None
            self.lease_token = None
            self.lease_expires_at = None
            self._held_lease_owner = None
            self._held_lease_token = None
            self._held_lease_seconds = None
            return
        with self._locked():
            try:
                canonical = self._read_document_locked()
            except (
                OSError,
                UnicodeError,
                TypeError,
                ValueError,
                ConsumerStateError,
            ) as exc:
                self.load_error = f"{type(exc).__name__}: {exc}"
                raise ConsumerStateError(self.load_error) from exc
            self._assert_held_lease(canonical, worker_id=worker_id)
            self._apply_document(canonical)
            self.lease_owner = None
            self.lease_token = None
            self.lease_expires_at = None
            self._write_locked()
            self._held_lease_owner = None
            self._held_lease_token = None
            self._held_lease_seconds = None

    def enqueue(self, event: dict[str, Any], *, observed_at: str) -> bool:
        event_id = str(event["event_id"])
        envelope = event.get("correlation_envelope")
        tenant_id = str(
            event.get("tenant_id")
            or (
                envelope.get("tenant_id")
                if isinstance(envelope, dict)
                else ""
            )
            or ""
        ).strip()
        if tenant_id:
            digest = hashlib.sha256(
                f"{tenant_id}\0{event_id}".encode("utf-8")
            ).hexdigest()
            key = f"tenant-event-{digest}"
        else:
            key = event_id
        if key in self.completed or key in self.pending or key in self.dead_letters:
            return False
        self.pending[key] = {
            "event": event,
            "attempt_count": 0,
            "first_seen_at": observed_at,
            "last_attempt_at": None,
            "last_error": None,
        }
        return True

    def replay_dead_letters(self) -> int:
        replayed = 0
        for key, record in list(self.dead_letters.items()):
            restored = dict(record)
            restored["attempt_count"] = 0
            restored["last_error"] = None
            self.pending[key] = restored
            del self.dead_letters[key]
            replayed += 1
        return replayed

    def oldest_backlog_lag_seconds(self, *, now: datetime) -> float:
        timestamps = [
            parsed
            for record in [*self.pending.values(), *self.dead_letters.values()]
            if (parsed := _parse_timestamp(record.get("first_seen_at"))) is not None
        ]
        if not timestamps:
            return 0.0
        return max(0.0, (now - min(timestamps)).total_seconds())


def run_runtime_summary_consumer_once(
    *,
    service_url: str,
    telemetry_url: str,
    state: ConsumerWorkerState,
    max_attempts: int = 3,
    retry_backoff_seconds: float = 0.0,
    replay_dead_letters: bool = False,
    worker_id: str = "reconciliation-consumer",
    lease_seconds: float = 120.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Fetch real summaries, durably deliver them, and expose controller truth."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be > 0")
    now = now_fn().astimezone(timezone.utc)
    observed_at = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    try:
        acquired = state.acquire_lease(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=now,
        )
    except ConsumerStateError as exc:
        return {
            "status": "failure",
            "controller_status": "unhealthy",
            "summary_count": 0,
            "pending_count": len(state.pending),
            "dead_letter_count": len(state.dead_letters),
            "backlog_count": len(state.pending) + len(state.dead_letters),
            "source_error": f"consumer state unavailable: {exc}",
            "worker_id": worker_id,
            "lease_status": "state_error",
        }
    if not acquired:
        return {
            "status": "deferred",
            "controller_status": "degraded",
            "summary_count": 0,
            "pending_count": len(state.pending),
            "dead_letter_count": len(state.dead_letters),
            "backlog_count": len(state.pending) + len(state.dead_letters),
            "source_error": None,
            "worker_id": worker_id,
            "lease_status": "lease_active",
            "lease_owner": state.lease_owner,
            "lease_expires_at": state.lease_expires_at,
        }
    replayed_count = state.replay_dead_letters() if replay_dead_letters else 0
    invalid_summaries: list[dict[str, Any]] = []
    source_error: str | None = None
    summaries: list[dict[str, Any]] = []
    try:
        summaries = fetch_runtime_summaries(telemetry_url)
    except RuntimeError as exc:
        source_error = str(exc)
        state.last_failure_at = observed_at
        state.last_failure_error = source_error

    enqueued_count = 0
    source_lags: list[float] = []
    for index, summary in enumerate(summaries):
        try:
            event = runtime_summary_to_event(summary)
        except ValueError as exc:
            invalid_summaries.append({"index": index, "detail": str(exc)})
            continue
        event_at = _parse_timestamp(event.get("created_at"))
        if event_at is not None:
            source_lags.append(max(0.0, (now - event_at).total_seconds()))
        if state.enqueue(event, observed_at=observed_at):
            enqueued_count += 1
    state.save()

    delivered_count = 0
    delivery_errors: list[dict[str, Any]] = []
    drift_report_count = 0
    incident_case_count = 0
    for key, record in list(state.pending.items()):
        while int(record.get("attempt_count") or 0) < max_attempts:
            record["attempt_count"] = int(record.get("attempt_count") or 0) + 1
            record["last_attempt_at"] = observed_at
            state.save()
            try:
                response = post_events(service_url, [record["event"]])
            except RuntimeError as exc:
                record["last_error"] = str(exc)
                if int(record["attempt_count"]) < max_attempts and retry_backoff_seconds > 0:
                    sleep_fn(retry_backoff_seconds)
                continue
            delivered_count += 1
            drift_report_count += int(response.get("drift_report_count") or 0)
            incident_case_count += int(response.get("incident_case_count") or 0)
            state.completed[key] = {
                "completed_at": observed_at,
                "attempt_count": record["attempt_count"],
                "tenant_id": record["event"].get("tenant_id"),
                "event_id": record["event"].get("event_id"),
            }
            del state.pending[key]
            state.save()
            break
        if key in state.pending and int(record.get("attempt_count") or 0) >= max_attempts:
            state.dead_letters[key] = dict(record)
            del state.pending[key]
            delivery_errors.append(
                {
                    "event_id": record["event"].get("event_id"),
                    "tenant_id": record["event"].get("tenant_id"),
                    "detail": record.get("last_error"),
                }
            )
            state.save()

    if len(state.completed) > 2000:
        state.completed = dict(list(state.completed.items())[-2000:])

    if delivered_count and not source_error and not invalid_summaries and not state.dead_letters:
        state.last_success_at = observed_at
        state.last_failure_error = None
    elif source_error or invalid_summaries or delivery_errors:
        state.last_failure_at = observed_at
        state.last_failure_error = source_error or str(invalid_summaries[0] if invalid_summaries else delivery_errors[0])
    state.save()

    if source_error:
        status = "failure"
    elif not summaries:
        status = "degraded"
    elif invalid_summaries or state.dead_letters or delivery_errors:
        status = "degraded"
    else:
        status = "ok"
    backlog_count = len(state.pending) + len(state.dead_letters)
    result = {
        "status": status,
        "source": "telemetry_runtime_summaries",
        "summary_count": len(summaries),
        "invalid_summary_count": len(invalid_summaries),
        "invalid_summaries": invalid_summaries,
        "enqueued_event_count": enqueued_count,
        "delivered_event_count": delivered_count,
        "drift_report_count": drift_report_count,
        "incident_case_count": incident_case_count,
        "pending_count": len(state.pending),
        "dead_letter_count": len(state.dead_letters),
        "backlog_count": backlog_count,
        "oldest_backlog_lag_seconds": state.oldest_backlog_lag_seconds(now=now),
        "max_source_lag_seconds": max(source_lags) if source_lags else None,
        "replayed_dead_letter_count": replayed_count,
        "source_error": source_error,
        "delivery_errors": delivery_errors,
        "last_success_at": state.last_success_at,
        "last_failure_at": state.last_failure_at,
        "controller_status": "healthy" if status == "ok" and backlog_count == 0 else status,
        "worker_id": worker_id,
        "lease_status": "acquired",
    }
    state.release_lease(worker_id=worker_id)
    result["lease_status"] = "released"
    return result


def _input_paths_from_env() -> list[str]:
    raw = os.getenv("RECONCILIATION_DRIFT_CONSUMER_INPUT") or os.getenv("RECONCILIATION_DRIFT_CONSUMER_FIXTURE")
    if not raw:
        return []
    return [item for item in raw.split(os.pathsep) if item]


def run_consumer_once(*, service_url: str, input_paths: Iterable[str]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for path in input_paths:
        events.extend(load_telemetry_events_from_path(path))
    if not events:
        return {"status": "no_events", "consumed_event_count": 0, "drift_report_count": 0}
    return post_events(service_url, events)


def healthcheck() -> int:
    try:
        interval_seconds = float(
            os.getenv("RECONCILIATION_DRIFT_CONSUMER_INTERVAL_SECONDS", "60")
        )
        max_age_seconds = float(
            os.getenv(
                "RECONCILIATION_DRIFT_CONSUMER_HEALTH_MAX_AGE_SECONDS",
                "300",
            )
        )
    except ValueError:
        return 1
    return check_worker_health(
        health_file=os.getenv(
            "RECONCILIATION_DRIFT_CONSUMER_HEALTH_FILE",
            "",
        ),
        interval_seconds=interval_seconds,
        max_age_seconds=max_age_seconds,
        worker_name=_WORKER_NAME,
    )


def _health_failure_reason(result: dict[str, Any]) -> str | None:
    source_error = result.get("source_error")
    if source_error:
        return str(source_error)
    errors = result.get("errors") or result.get("delivery_errors")
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("detail") or first)
        return str(first)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consume authoritative runtime telemetry into reconciliation-drift.")
    parser.add_argument(
        "--service-url",
        default=os.getenv("RECONCILIATION_DRIFT_URL", "http://reconciliation-drift-svc:8102"),
    )
    parser.add_argument("--input", action="append", dest="inputs", default=None)
    parser.add_argument(
        "--telemetry-url",
        default=os.getenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry:8083"),
    )
    parser.add_argument(
        "--state-path",
        default=os.getenv(
            "RECONCILIATION_DRIFT_CONSUMER_WORKER_STATE_PATH",
            "/data/reconciliation-drift/consumer-worker-state.json",
        ),
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.getenv("RECONCILIATION_DRIFT_CONSUMER_INTERVAL_SECONDS", "60")),
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=int(os.getenv("RECONCILIATION_DRIFT_CONSUMER_MAX_TICKS", "1")),
    )
    args = parser.parse_args(argv)

    health_file = os.getenv("RECONCILIATION_DRIFT_CONSUMER_HEALTH_FILE", "")
    health: dict[str, Any] = {
        "worker_name": _WORKER_NAME,
        "status": "starting",
        "ticks": 0,
        "last_tick_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": None,
        "controller_status": "starting",
        "pid": os.getpid(),
    }
    write_health(health_file, health)

    inputs = args.inputs or _input_paths_from_env()
    state = ConsumerWorkerState(args.state_path or None)
    max_attempts = int(os.getenv("RECONCILIATION_DRIFT_CONSUMER_MAX_ATTEMPTS", "3"))
    retry_backoff_seconds = float(os.getenv("RECONCILIATION_DRIFT_CONSUMER_RETRY_BACKOFF_SECONDS", "1"))
    replay_dead_letters = os.getenv("RECONCILIATION_DRIFT_CONSUMER_REPLAY_DLQ", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    worker_id = os.getenv(
        "RECONCILIATION_DRIFT_CONSUMER_WORKER_ID",
        f"{os.getenv('HOSTNAME', 'consumer')}:{os.getpid()}",
    ).strip()
    lease_seconds = float(
        os.getenv("RECONCILIATION_DRIFT_CONSUMER_LEASE_SECONDS", "120")
    )
    tick = 0
    while args.max_ticks <= 0 or tick < args.max_ticks:
        try:
            if inputs:
                result = run_consumer_once(
                    service_url=args.service_url,
                    input_paths=inputs,
                )
                result["source"] = "explicit_fixture_input"
            else:
                result = run_runtime_summary_consumer_once(
                    service_url=args.service_url,
                    telemetry_url=args.telemetry_url,
                    state=state,
                    max_attempts=max_attempts,
                    retry_backoff_seconds=retry_backoff_seconds,
                    replay_dead_letters=replay_dead_letters,
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                )
        except Exception as exc:
            failed_at = utc_now()
            health.update(
                {
                    "status": "error",
                    "ticks": tick,
                    "last_tick_at": failed_at,
                    "last_failure_at": failed_at,
                    "last_failure_reason": str(exc),
                    "controller_status": "unhealthy",
                }
            )
            write_health(health_file, health)
            raise

        tick += 1
        tick_at = utc_now()
        controller_status = str(
            result.get("controller_status")
            or (
                "healthy"
                if result.get("status") in {"ok", "no_events"}
                else result.get("status")
                or "unknown"
            )
        )
        failure_reason = _health_failure_reason(result)
        health.update(
            {
                "status": "ok",
                "ticks": tick,
                "last_tick_at": tick_at,
                "controller_status": controller_status,
            }
        )
        if controller_status == "healthy":
            health["last_success_at"] = tick_at
            health["last_failure_reason"] = None
        else:
            health["last_failure_at"] = tick_at
            health["last_failure_reason"] = failure_reason
        write_health(health_file, health)
        print(json.dumps(result, sort_keys=True))
        if args.max_ticks == 1:
            break
        time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["healthcheck"]:
        raise SystemExit(healthcheck())
    raise SystemExit(main())
