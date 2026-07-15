#!/usr/bin/env python3
"""Live verifier for the threshold-breach producer chain.

The probe is intentionally mutating and therefore requires an explicit opt-in
and target runtime.  It injects paper telemetry, exercises the real threshold
worker and evolution sweep, then proves the exact incident -> proposal ->
formal journal -> Persona Fleet lineage.  All producer identities are stable
within the configured threshold window so reruns reuse one incident and one
decision instead of rotating through arbitrary paper runtimes.
"""
from __future__ import annotations

import json
import math
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.evolution.threshold_sweep_worker import (  # noqa: E402
    DEFAULT_BASELINES_PATH,
    DEFAULT_CONFIG_PATH,
    default_fetch_summaries,
    load_baselines,
    load_thresholds,
    run_tick,
)


@dataclass(frozen=True)
class ProducerPolicy:
    threshold: dict[str, Any]
    baselines: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class ProducerIdentity:
    dedupe_key: str
    event_id: str
    incident_id: str
    window: str


class VerificationFailure(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def _fail(stage: str, message: str) -> None:
    raise VerificationFailure(stage, message)


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    if os.environ.get("BFF_INSECURE", "0") == "1":
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def _http_request(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    method: str = "GET",
    data: Mapping[str, Any] | None = None,
    context: ssl.SSLContext | None = None,
    timeout: float = 20.0,
) -> tuple[int, Any]:
    request_headers = dict(headers or {})
    body = None
    if data is not None:
        request_headers.setdefault("Content-Type", "application/json")
        body = json.dumps(dict(data)).encode("utf-8")
    request = urllib.request.Request(
        url,
        headers=request_headers,
        method=method,
        data=body,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            response_body = response.read().decode("utf-8")
            return response.status, json.loads(response_body) if response_body else {}
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8")
        try:
            payload = json.loads(response_body)
        except (TypeError, ValueError):
            payload = response_body
        return exc.code, payload
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        return 0, str(exc)


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(payload.get("items"), list):
        return [item for item in payload["items"] if isinstance(item, dict)]
    return []


def _summaries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping) and isinstance(payload.get("summaries"), list):
        return [item for item in payload["summaries"] if isinstance(item, dict)]
    return []


def _record(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    data = payload.get("data")
    if isinstance(data, Mapping):
        return dict(data)
    return dict(payload)


def _utc_timestamp(moment: datetime | None = None) -> str:
    value = moment or datetime.now(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _load_policy() -> ProducerPolicy:
    config_path = os.environ.get("EVOCHAIN_THRESHOLD_SWEEP_CONFIG_PATH") or DEFAULT_CONFIG_PATH
    baselines_path = (
        os.environ.get("EVOCHAIN_THRESHOLD_SWEEP_BASELINES_PATH") or DEFAULT_BASELINES_PATH
    )
    thresholds = load_thresholds(config_path)
    candidates = [
        threshold
        for threshold in thresholds
        if threshold.get("summary_field") == "drawdown"
        and threshold.get("ratio_baseline_key")
        and threshold.get("comparator") in {"gt", "gte"}
    ]
    if not candidates:
        _fail(
            "baseline_policy",
            f"no enabled governed drawdown-ratio threshold in {config_path}",
        )
    candidates.sort(key=lambda item: (str(item.get("metric_name")), str(item.get("window"))))
    threshold = dict(candidates[0])
    threshold_value = threshold.get("threshold_value")
    if (
        isinstance(threshold_value, bool)
        or not isinstance(threshold_value, (int, float))
        or not math.isfinite(float(threshold_value))
        or float(threshold_value) <= 0
    ):
        _fail("baseline_policy", f"invalid positive threshold_value: {threshold_value!r}")

    baselines = load_baselines(baselines_path)
    if not baselines:
        _fail("baseline_policy", f"no governed artifact baselines loaded from {baselines_path}")
    return ProducerPolicy(threshold=threshold, baselines=baselines)


def _active_paper_runtime(runtime: Mapping[str, Any]) -> bool:
    status = str(runtime.get("status") or runtime.get("state") or "").strip().lower()
    stage = str(
        runtime.get("deployment_mode")
        or runtime.get("deployment_stage")
        or runtime.get("runtime_kind")
        or ""
    ).strip().lower()
    return status in {"active", "running"} and stage == "paper"


def _positive_baseline(
    baselines: Mapping[str, Mapping[str, Any]],
    artifact_id: str,
    baseline_key: str,
) -> float | None:
    record = baselines.get(artifact_id)
    value = record.get(baseline_key) if isinstance(record, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def _select_binding(
    runtimes: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
    policy: ProducerPolicy,
    *,
    requested_runtime_id: str,
) -> tuple[dict[str, Any], float]:
    baseline_key = str(policy.threshold["ratio_baseline_key"])
    runtimes_by_id = {
        str(runtime.get("runtime_id") or runtime.get("id") or ""): runtime
        for runtime in runtimes
        if _active_paper_runtime(runtime)
    }
    candidates: list[tuple[dict[str, Any], float]] = []
    for summary in summaries:
        runtime_id = str(summary.get("runtime_id") or summary.get("id") or "")
        runtime = runtimes_by_id.get(runtime_id)
        if runtime is None or runtime_id != requested_runtime_id:
            continue
        if str(summary.get("deployment_stage") or "").strip().lower() != "paper":
            continue
        artifact_id = str(summary.get("artifact_id") or "").strip()
        baseline = _positive_baseline(policy.baselines, artifact_id, baseline_key)
        if baseline is None:
            continue
        selected = dict(runtime)
        for field in (
            "binding_id",
            "runtime_id",
            "capital_pool_id",
            "artifact_id",
            "artifact_version",
            "deployment_stage",
            "deployment_plan_id",
            "plan_id",
            "persona_capital_binding_id",
        ):
            if summary.get(field) not in (None, ""):
                selected[field] = summary[field]
        selected["runtime_id"] = runtime_id
        selected["deployment_stage"] = "paper"
        canonical_plan_id = (
            summary.get("plan_id")
            or summary.get("deployment_plan_id")
            or selected.get("plan_id")
            or selected.get("deployment_plan_id")
        )
        selected["plan_id"] = canonical_plan_id
        selected["deployment_plan_id"] = canonical_plan_id
        candidates.append((selected, baseline))

    if not candidates:
        governed_artifacts = sorted(policy.baselines)
        _fail(
            "binding_resolution",
            "requested runtime has no active paper telemetry summary with a governed baseline: "
            f"runtime_id={requested_runtime_id!r}, governed_artifacts={governed_artifacts}",
        )
    candidates.sort(
        key=lambda item: (
            str(item[0].get("artifact_id")),
            str(item[0].get("binding_id")),
        )
    )
    selected, baseline = candidates[0]
    required = (
        "binding_id",
        "runtime_id",
        "capital_pool_id",
        "artifact_id",
        "artifact_version",
        "plan_id",
        "persona_capital_binding_id",
        "persona_id",
    )
    missing = [field for field in required if selected.get(field) in (None, "")]
    if missing:
        _fail("binding_resolution", f"selected runtime is missing identity fields: {missing}")
    return selected, baseline


def _producer_identity(
    binding_id: str,
    threshold: Mapping[str, Any],
    *,
    moment: datetime,
) -> ProducerIdentity:
    window = f"{threshold.get('window') or 'sweep'}:{moment.date().isoformat()}"
    metric_name = str(threshold["metric_name"])
    dedupe_key = json.dumps([binding_id, metric_name, window], separators=(",", ":"))
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, dedupe_key))
    incident_seed = f"{event_id}:{metric_name}"
    incident_id = f"inc-threshold-{uuid.uuid5(uuid.NAMESPACE_URL, incident_seed).hex[:12]}"
    return ProducerIdentity(
        dedupe_key=dedupe_key,
        event_id=event_id,
        incident_id=incident_id,
        window=window,
    )


def _breach_value(baseline: float, threshold: Mapping[str, Any]) -> float:
    threshold_value = float(threshold["threshold_value"])
    observed_multiple = threshold_value + max(abs(threshold_value) * 0.2, 0.1)
    return baseline * observed_multiple


def _event_payload(
    binding: Mapping[str, Any],
    *,
    event_id: str,
    event_type: str,
    created_at: str,
    trace_id: str,
    metrics: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "created_at": created_at,
        "execution_mode": "paper",
        "binding_id": binding["binding_id"],
        "runtime_id": binding["runtime_id"],
        "capital_pool_id": binding["capital_pool_id"],
        "artifact_id": binding["artifact_id"],
        "artifact_version": binding["artifact_version"],
        "deployment_stage": "paper",
        "plan_id": binding["plan_id"],
        "persona_capital_binding_id": binding["persona_capital_binding_id"],
        "trace_id": trace_id,
        "target": {"strategy_id": binding["artifact_id"]},
        "metrics": dict(metrics),
        "metadata": dict(metadata),
    }


def _poll_summary(
    telemetry_url: str,
    binding_id: str,
    predicate: Callable[[Mapping[str, Any]], bool],
    *,
    attempts: int = 12,
) -> dict[str, Any] | None:
    for _ in range(attempts):
        status, payload = _http_request(
            telemetry_url.rstrip("/") + "/api/telemetry/runtime-summaries"
        )
        if status == 200:
            for summary in _summaries(payload):
                if str(summary.get("binding_id") or "") == binding_id and predicate(summary):
                    return summary
        time.sleep(0.5)
    return None


def _get_incident(
    bff_base: str,
    incident_id: str,
    headers: Mapping[str, str],
    context: ssl.SSLContext,
) -> tuple[int, dict[str, Any] | None]:
    path = "/bff/incidents/" + urllib.parse.quote(incident_id, safe="")
    status, payload = _http_request(
        bff_base.rstrip("/") + path,
        headers=headers,
        context=context,
    )
    return status, _record(payload) if status == 200 else None


def _assert_incident(
    incident: Mapping[str, Any],
    binding: Mapping[str, Any],
    identity: ProducerIdentity,
) -> None:
    expected = {
        "incident_id": identity.incident_id,
        "binding_id": str(binding["binding_id"]),
        "runtime_id": str(binding["runtime_id"]),
        "artifact_id": str(binding["artifact_id"]),
        "deployment_plan_id": str(binding["deployment_plan_id"]),
        "persona_capital_binding_id": str(binding["persona_capital_binding_id"]),
    }
    mismatches = {
        field: {"expected": value, "actual": incident.get(field)}
        for field, value in expected.items()
        if str(incident.get(field) or "") != value
    }
    event_ids = {str(value) for value in incident.get("telemetry_event_ids") or []}
    if identity.event_id not in event_ids:
        mismatches["telemetry_event_ids"] = {
            "expected_contains": identity.event_id,
            "actual": sorted(event_ids),
        }
    evidence = str(incident.get("evidence_summary") or "")
    marker = f"dedupe_key={identity.dedupe_key}"
    if marker not in evidence:
        mismatches["evidence_summary"] = {"expected_contains": marker, "actual": evidence}
    if mismatches:
        _fail("incident_dedupe", f"exact producer incident lineage mismatch: {mismatches}")


def _selected_summary_fetcher(binding_id: str) -> Callable[..., list[dict[str, Any]]]:
    def fetch(telemetry_api_url: str, *, timeout: float) -> list[dict[str, Any]]:
        return [
            summary
            for summary in default_fetch_summaries(telemetry_api_url, timeout=timeout)
            if str(summary.get("binding_id") or "") == binding_id
        ]

    return fetch


def _sweep_item(payload: Mapping[str, Any], incident_id: str) -> dict[str, Any]:
    matches = [
        item
        for item in _items(payload)
        if str(item.get("incident_id") or "") == incident_id
    ]
    if len(matches) != 1:
        _fail(
            "proposal_sweep",
            f"expected one sweep item for incident {incident_id}, got {matches}",
        )
    item = matches[0]
    if item.get("status") not in {"created", "existing"}:
        _fail("proposal_sweep", f"incident was not proposed: {item}")
    if not item.get("decision_id"):
        _fail("proposal_sweep", f"sweep item has no decision_id: {item}")
    return item


def _assert_proposal(
    proposal: Mapping[str, Any],
    binding: Mapping[str, Any],
    identity: ProducerIdentity,
    threshold: Mapping[str, Any],
) -> None:
    expected = {
        "linked_incident_id": identity.incident_id,
        "target_id": str(binding["artifact_id"]),
        "target_version": str(binding["artifact_version"]),
    }
    mismatches = {
        field: {"expected": value, "actual": proposal.get(field)}
        for field, value in expected.items()
        if str(proposal.get(field) or "") != value
    }
    snapshots = proposal.get("threshold_snapshots") or []
    snapshot = next(
        (
            item
            for item in snapshots
            if isinstance(item, Mapping)
            and item.get("metric_name") == threshold.get("metric_name")
            and item.get("window") == identity.window
        ),
        None,
    )
    if snapshot is None:
        mismatches["threshold_snapshots"] = {
            "expected_metric": threshold.get("metric_name"),
            "expected_window": identity.window,
            "actual": snapshots,
        }
    evidence_ids = {
        str(item.get("ref_id") or "")
        for item in proposal.get("evidence_refs") or []
        if isinstance(item, Mapping)
    }
    for expected_ref in (identity.incident_id, identity.event_id):
        if expected_ref not in evidence_ids:
            mismatches.setdefault("evidence_refs", []).append(expected_ref)
    if mismatches:
        _fail("proposal_sweep", f"proposal lineage mismatch: {mismatches}")


def _formal_journal_entry(items: Sequence[Mapping[str, Any]], decision_id: str) -> dict[str, Any]:
    matches = [
        dict(item)
        for item in items
        if item.get("entry_type") == "mutation_review"
        and str(item.get("source_id") or "") == decision_id
    ]
    if len(matches) != 1:
        _fail(
            "formal_journal",
            f"expected one exact live mutation_review for {decision_id}, got {matches}",
        )
    entry = matches[0]
    # Journal provenance is deliberately fail-closed: current BFF contracts
    # report ``unknown`` when a canonical service record does not carry an
    # explicit origin marker. Exact telemetry -> incident -> proposal lineage
    # above proves this row came from the live producer chain; the formal row
    # must only reject a fixture/seed classification here.
    if entry.get("origin") == "seed":
        _fail("formal_journal", "formal entry is classified as seed")
    return entry


def _assert_fleet_link(persona: Mapping[str, Any], persona_id: str, decision_id: str) -> None:
    expected = {
        "id": persona_id,
        "last_mutation_kind": "formal_mutation",
        "mutation_entry_id": decision_id,
        "evolution_entry_id": decision_id,
        "mutation_confidence": "formal",
    }
    mismatches = {
        field: {"expected": value, "actual": persona.get(field)}
        for field, value in expected.items()
        if str(persona.get(field) or "") != value
    }
    href = str(persona.get("evolution_href") or "")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    if query.get("persona") != [persona_id] or query.get("mutation_review") != [decision_id]:
        mismatches["evolution_href"] = {
            "expected": {"persona": persona_id, "mutation_review": decision_id},
            "actual": href,
        }
    if mismatches:
        _fail("persona_fleet", f"Persona Fleet mutation link mismatch: {mismatches}")


def _run() -> int:
    if os.environ.get("ALLOW_MUTATING_E2E") != "1":
        _fail(
            "mutation_opt_in",
            "set ALLOW_MUTATING_E2E=1; this verifier injects paper telemetry and creates "
            "one deduped incident/proposal chain",
        )
    requested_runtime_id = os.environ.get("EVOCHAIN_VERIFY_RUNTIME_ID", "").strip()
    if not requested_runtime_id:
        _fail(
            "mutation_opt_in",
            "set EVOCHAIN_VERIFY_RUNTIME_ID to an explicit disposable/dev paper runtime",
        )

    bff_base = os.environ.get("BFF_BASE", "").strip()
    if not bff_base:
        _fail("configuration", "set BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    telemetry_url = os.environ.get("TELEMETRY_API_URL", "http://localhost:18083")
    incidents_url = os.environ.get("INCIDENTS_API_URL", "http://localhost:18090")
    evolution_url = os.environ.get("EVOLUTION_API_URL", "http://localhost:18093")
    context = _ssl_context()
    headers = {"Authorization": f"Bearer {token}"}
    moment = datetime.now(timezone.utc)

    print("== EVOCHAIN-010 producer-chain live verifier ==")
    print(f"[binding_resolution] requested runtime: {requested_runtime_id}")

    policy = _load_policy()
    status, runtime_payload = _http_request(
        bff_base.rstrip("/") + "/bff/runtimes?page_size=200",
        headers=headers,
        context=context,
    )
    if status != 200:
        _fail("binding_resolution", f"BFF runtimes returned {status}: {runtime_payload}")
    summary_status, summary_payload = _http_request(
        telemetry_url.rstrip("/") + "/api/telemetry/runtime-summaries"
    )
    if summary_status != 200:
        _fail(
            "binding_resolution",
            f"telemetry runtime summaries returned {summary_status}: {summary_payload}",
        )
    binding, baseline = _select_binding(
        _items(runtime_payload),
        _summaries(summary_payload),
        policy,
        requested_runtime_id=requested_runtime_id,
    )
    identity = _producer_identity(str(binding["binding_id"]), policy.threshold, moment=moment)
    breach_value = _breach_value(baseline, policy.threshold)
    print(
        "[baseline_policy] "
        f"artifact={binding['artifact_id']} baseline={baseline} "
        f"threshold={policy.threshold['threshold_value']} raw_breach={breach_value}"
    )
    print(
        "[incident_dedupe] "
        f"dedupe_key={identity.dedupe_key} incident_id={identity.incident_id}"
    )

    run_id = str(uuid.uuid4())
    created_at = _utc_timestamp(moment)
    heartbeat_event_id = str(uuid.uuid4())
    heartbeat = _event_payload(
        binding,
        event_id=heartbeat_event_id,
        event_type="heartbeat",
        created_at=created_at,
        trace_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"evochain-010:{run_id}:heartbeat")),
        metrics={"heartbeat": 1},
        metadata={
            "connectivity_status": "connected",
            "e2e_verifier": "EVOCHAIN-010",
            "e2e_run_id": run_id,
        },
    )
    heartbeat_status, heartbeat_response = _http_request(
        telemetry_url.rstrip("/") + "/api/telemetry/ingest",
        method="POST",
        data=heartbeat,
    )
    if heartbeat_status != 202:
        _fail(
            "heartbeat_ingest",
            f"telemetry ingest returned {heartbeat_status}: {heartbeat_response}",
        )
    heartbeat_summary = _poll_summary(
        telemetry_url,
        str(binding["binding_id"]),
        lambda summary: summary.get("last_heartbeat_event_id") == heartbeat_event_id,
    )
    if heartbeat_summary is None:
        _fail(
            "heartbeat_readback",
            f"runtime summary did not project heartbeat event {heartbeat_event_id}",
        )
    print(f"[heartbeat_readback] projected event_id={heartbeat_event_id}")

    metric_key = "drawdown_pct"
    breach_event_id = str(uuid.uuid4())
    breach = _event_payload(
        binding,
        event_id=breach_event_id,
        event_type=str(policy.threshold["telemetry_event_type"]),
        created_at=_utc_timestamp(),
        trace_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"evochain-010:{run_id}:breach")),
        metrics={metric_key: breach_value},
        metadata={"e2e_verifier": "EVOCHAIN-010", "e2e_run_id": run_id},
    )
    breach_status, breach_response = _http_request(
        telemetry_url.rstrip("/") + "/api/telemetry/ingest",
        method="POST",
        data=breach,
    )
    if breach_status != 202:
        _fail(
            "breach_ingest",
            f"telemetry ingest returned {breach_status}: {breach_response}",
        )
    breach_summary = _poll_summary(
        telemetry_url,
        str(binding["binding_id"]),
        lambda summary: math.isclose(
            float(summary.get("drawdown")),
            breach_value,
            rel_tol=1e-9,
            abs_tol=1e-12,
        )
        if isinstance(summary.get("drawdown"), (int, float))
        else False,
    )
    if breach_summary is None:
        _fail(
            "breach_readback",
            f"runtime summary did not project drawdown={breach_value} for {binding['binding_id']}",
        )
    print(f"[breach_readback] projected event_id={breach_event_id} drawdown={breach_value}")

    tick_moment = datetime.now(timezone.utc)
    if tick_moment.date() != moment.date():
        _fail(
            "configuration",
            "verification crossed a UTC day boundary; rerun so producer dedupe identity is unambiguous",
        )

    incident_status, incident = _get_incident(
        bff_base,
        identity.incident_id,
        headers,
        context,
    )
    if incident_status not in {200, 404}:
        _fail("incident_dedupe", f"incident lookup returned {incident_status}")

    tick_result: dict[str, Any] | None = None
    replay_result: dict[str, Any] | None = None
    if incident is None:
        state_path = os.environ.get("EVOCHAIN_VERIFY_STATE_PATH") or os.path.join(
            os.environ.get("EVOLUTION_DATA_DIR", "/tmp/pantheon/evolution"),
            "evochain_010_verifier_state.json",
        )
        tick_kwargs = {
            "telemetry_api_url": telemetry_url,
            "incidents_api_url": incidents_url,
            "state_path": state_path,
            "thresholds": [policy.threshold],
            "baselines": {str(binding["artifact_id"]): policy.baselines[str(binding["artifact_id"])]},
            "now": tick_moment,
            "fetch_summaries": _selected_summary_fetcher(str(binding["binding_id"])),
        }
        tick_result = run_tick(**tick_kwargs)
        if tick_result.get("errors"):
            _fail("threshold_sweep", f"worker errors: {tick_result}")
        if tick_result.get("candidates") != 1:
            _fail("threshold_sweep", f"expected one selected breach candidate: {tick_result}")
        if tick_result.get("incidents_created", 0) + tick_result.get("incidents_deduped", 0) != 1:
            _fail("threshold_sweep", f"expected one incident delivery: {tick_result}")
        replay_result = run_tick(**tick_kwargs)
        if replay_result.get("errors") or replay_result.get("incidents_created") != 0:
            _fail("threshold_sweep_replay", f"second tick was not idempotent: {replay_result}")
        if replay_result.get("incidents_deduped", 0) != 1:
            _fail("threshold_sweep_replay", f"second tick did not dedupe exactly once: {replay_result}")
        print(
            "[threshold_sweep_replay] "
            f"created={tick_result['incidents_created']} first_deduped={tick_result['incidents_deduped']} "
            f"replay_deduped={replay_result['incidents_deduped']}"
        )
        for _ in range(20):
            incident_status, incident = _get_incident(
                bff_base,
                identity.incident_id,
                headers,
                context,
            )
            if incident_status == 200 and incident is not None:
                break
            time.sleep(0.5)
    else:
        print("[threshold_sweep_replay] exact incident already exists; producer replay is deduped")

    if incident is None:
        _fail("incident_dedupe", f"exact incident {identity.incident_id} did not appear in BFF")
    _assert_incident(incident, binding, identity)
    print(f"[incident_dedupe] exact incident verified status={incident.get('status')}")

    sweep_payload = {
        "incident_ids": [identity.incident_id],
        "sweep_id": f"evochain-010-{moment.date().isoformat()}",
    }
    sweep_status, sweep_response = _http_request(
        evolution_url.rstrip("/") + "/api/evolution/daily-sweep",
        method="POST",
        data=sweep_payload,
    )
    if sweep_status != 200 or not isinstance(sweep_response, Mapping):
        _fail("proposal_sweep", f"daily sweep returned {sweep_status}: {sweep_response}")
    item = _sweep_item(sweep_response, identity.incident_id)
    decision_id = str(item["decision_id"])
    if str(item.get("target_id") or "") != str(binding["artifact_id"]):
        _fail("proposal_sweep", f"sweep target mismatch: {item}")

    replay_status, replay_response = _http_request(
        evolution_url.rstrip("/") + "/api/evolution/daily-sweep",
        method="POST",
        data=sweep_payload,
    )
    if replay_status != 200 or not isinstance(replay_response, Mapping):
        _fail("proposal_sweep_replay", f"replay returned {replay_status}: {replay_response}")
    replay_item = _sweep_item(replay_response, identity.incident_id)
    if replay_item.get("status") != "existing" or replay_item.get("decision_id") != decision_id:
        _fail("proposal_sweep_replay", f"second sweep did not reuse decision: {replay_item}")
    print(
        f"[proposal_sweep_replay] first={item['status']} replay=existing decision_id={decision_id}"
    )

    proposal_status, proposal_payload = _http_request(
        evolution_url.rstrip("/")
        + "/api/evolution/proposals/"
        + urllib.parse.quote(decision_id, safe="")
    )
    proposal = _record(proposal_payload)
    if proposal_status != 200 or proposal is None:
        _fail("proposal_sweep", f"proposal readback returned {proposal_status}: {proposal_payload}")
    _assert_proposal(proposal, binding, identity, policy.threshold)
    print("[proposal_sweep] exact incident/target/threshold/evidence lineage verified")

    journal_query = urllib.parse.urlencode(
        {
            "persona": str(binding["persona_id"]),
            "mutation_review": decision_id,
            "page_size": 200,
        }
    )
    formal_entry: dict[str, Any] | None = None
    for _ in range(20):
        journal_status, journal_payload = _http_request(
            bff_base.rstrip("/") + "/bff/management/evolution-journal?" + journal_query,
            headers=headers,
            context=context,
        )
        if journal_status == 200:
            try:
                formal_entry = _formal_journal_entry(_items(journal_payload), decision_id)
            except VerificationFailure:
                formal_entry = None
            if formal_entry is not None:
                break
        time.sleep(0.5)
    if formal_entry is None:
        _fail("formal_journal", f"live mutation_review did not appear for {decision_id}")
    formal_record = formal_entry.get("mutation_review") or formal_entry.get("record") or {}
    linked_incident = (
        formal_record.get("linked_incident_id") or formal_record.get("incident_ref")
        if isinstance(formal_record, Mapping)
        else None
    )
    if str(linked_incident or "") != identity.incident_id:
        _fail(
            "formal_journal",
            f"formal entry linked incident mismatch: expected {identity.incident_id}, got {linked_incident}",
        )
    print(f"[formal_journal] exact non-seed mutation_review verified id={formal_entry['id']}")

    fleet_query = urllib.parse.urlencode({"q": str(binding["persona_id"]), "page_size": 200})
    fleet_persona: dict[str, Any] | None = None
    for _ in range(20):
        fleet_status, fleet_payload = _http_request(
            bff_base.rstrip("/") + "/bff/management/persona-fleet?" + fleet_query,
            headers=headers,
            context=context,
        )
        if fleet_status == 200:
            fleet_persona = next(
                (
                    row
                    for row in _items(fleet_payload)
                    if str(row.get("id") or row.get("persona_id") or "")
                    == str(binding["persona_id"])
                ),
                None,
            )
            if fleet_persona is not None:
                try:
                    _assert_fleet_link(fleet_persona, str(binding["persona_id"]), decision_id)
                    break
                except VerificationFailure:
                    fleet_persona = None
        time.sleep(0.5)
    if fleet_persona is None:
        _fail("persona_fleet", f"formal mutation link did not appear for {binding['persona_id']}")
    print("[persona_fleet] recent formal MUTATION links to the exact journal decision")

    proof = {
        "runtime_id": binding["runtime_id"],
        "binding_id": binding["binding_id"],
        "persona_id": binding["persona_id"],
        "artifact_id": binding["artifact_id"],
        "dedupe_key": identity.dedupe_key,
        "telemetry_event_id": identity.event_id,
        "incident_id": identity.incident_id,
        "decision_id": decision_id,
        "journal_id": formal_entry["id"],
    }
    print("OK: producer-chain live verification passed")
    print(json.dumps(proof, sort_keys=True))
    return 0


def main() -> int:
    try:
        return _run()
    except VerificationFailure as exc:
        print(f"FAIL [{exc.stage}]: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - final diagnostic boundary.
        print(f"FAIL [unexpected]: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
