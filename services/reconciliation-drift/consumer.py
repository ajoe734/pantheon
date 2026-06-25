from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_WARNING_RELATIVE_DELTA = 0.2
DEFAULT_CRITICAL_RELATIVE_DELTA = 0.5
DRIFT_STATUSES = {"warning", "critical"}


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
    baseline_ref = str(normalized.get("baseline_ref") or normalized.get("paper_baseline_ref") or "telemetry_fixture_baseline")
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
    for ref in normalized.get("evidence_refs") or []:
        if isinstance(ref, str):
            evidence_refs.append(ref)

    return {
        "id": report_id,
        "drift_report_id": report_id,
        "recon_run_id": recon_run_id,
        "drift_type": _drift_type_for_metric(str(worst_check["metric"])),
        "scope_ref": scope_ref,
        "binding_id": binding_id or None,
        "runtime_id": runtime_id or None,
        "deployment_stage": deployment_stage,
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


def post_events(service_url: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    url = service_url.rstrip("/") + "/api/reconciliation-drift/telemetry-events/consume"
    request = urllib.request.Request(
        url,
        data=json.dumps({"events": events}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - dev service URL is operator configured.
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"reconciliation-drift consume failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"reconciliation-drift service unavailable: {exc.reason}") from exc


def _input_paths_from_env() -> list[str]:
    raw = os.getenv("RECONCILIATION_DRIFT_CONSUMER_INPUT") or os.getenv("RECONCILIATION_DRIFT_CONSUMER_FIXTURE")
    if not raw:
        raw = "/fixtures/reconciliation-drift"
    return [item for item in raw.split(os.pathsep) if item]


def run_consumer_once(*, service_url: str, input_paths: Iterable[str]) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for path in input_paths:
        events.extend(load_telemetry_events_from_path(path))
    if not events:
        return {"status": "no_events", "consumed_event_count": 0, "drift_report_count": 0}
    return post_events(service_url, events)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Consume telemetry fixtures into reconciliation-drift DriftReports.")
    parser.add_argument(
        "--service-url",
        default=os.getenv("RECONCILIATION_DRIFT_URL", "http://reconciliation-drift-svc:8102"),
    )
    parser.add_argument("--input", action="append", dest="inputs", default=None)
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

    inputs = args.inputs or _input_paths_from_env()
    tick = 0
    while args.max_ticks <= 0 or tick < args.max_ticks:
        result = run_consumer_once(service_url=args.service_url, input_paths=inputs)
        print(json.dumps(result, sort_keys=True))
        tick += 1
        if args.max_ticks == 1:
            break
        time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
