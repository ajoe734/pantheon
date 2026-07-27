"""Reproducible hosted probe for the EVOLOOP-001 dispatch worker.

The probe deliberately never calls an evolution execute endpoint. It creates,
reviews, and approves one research decision plus one daily-sweep-shaped active
live freeze, then observes whether the independently running worker executes
only the research decision. A second, read-only phase verifies restart
idempotence from the first phase's sanitized JSON output.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProbeError(RuntimeError):
    """Raised when hosted dispatch evidence violates the task contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _request_json(
    *,
    api_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    timeout_seconds: float,
    request_ledger: list[dict[str, Any]],
    tenant_id: str | None = None,
    auth_token: str | None = None,
) -> dict[str, Any]:
    requested_at = _utc_now()
    url = api_url.rstrip("/") + path
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    effective_tenant = (
        tenant_id
        or os.getenv("EVOLUTION_PROBE_TENANT_ID")
        or os.getenv("EVOLUTION_DEFAULT_TENANT_ID")
        or "pantheon-default"
    ).strip()
    effective_token = (
        auth_token
        if auth_token is not None
        else os.getenv("EVOLUTION_AUTH_TOKEN", "").strip() or None
    )
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Tenant-Id": effective_tenant,
    }
    if effective_token:
        headers["Authorization"] = f"Bearer {effective_token}"
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = response.status
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ProbeError(f"{method} {path} failed: HTTP {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise ProbeError(f"{method} {path} failed: {exc}") from exc

    request_ledger.append(
        {
            "method": method,
            "path": path,
            "status": status,
            "requested_at": requested_at,
            "completed_at": _utc_now(),
        }
    )
    if not response_body.strip():
        raise ProbeError(f"{method} {path} returned an empty response")
    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"{method} {path} returned malformed JSON") from exc
    if not isinstance(data, dict) or not data:
        raise ProbeError(f"{method} {path} returned a non-object or empty payload")
    return data


def _request(
    api_url: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float,
    request_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    return _request_json(
        api_url=api_url,
        method=method,
        path=path,
        payload=payload,
        timeout_seconds=timeout_seconds,
        request_ledger=request_ledger,
    )


def _proposal_payload(
    *, decision_id: str, action_type: str, target_stage: str, risk_level: str
) -> dict[str, Any]:
    is_freeze = action_type == "freeze"
    threshold = (
        {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
            "signal_type": "governance_incident",
            "metric_name": "severity1_incident_count",
            "comparator": "gte",
            "observed_value": 1,
            "threshold_value": 1,
            "window": "active-incident",
            "breached": True,
        }
        if is_freeze
        else {
            "policy_source": "EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1",
            "signal_type": "performance_degradation",
            "metric_name": "sharpe_ratio",
            "comparator": "lt",
            "observed_value": 0.3,
            "threshold_value": 0.5,
            "window": "30d",
            "breached": True,
        }
    )
    payload: dict[str, Any] = {
        "decision_id": decision_id,
        "target_type": "candidate_artifact",
        "target_id": f"artifact-{decision_id}",
        "target_version": "v1.0",
        "action_type": action_type,
        "rationale": f"EVOLOOP-001 hosted {action_type} probe",
        "created_by_id": "evoloop-001-hosted-probe",
        "created_by_role": "evolution_controller",
        "risk_level": risk_level,
        "target_stage": target_stage,
        "threshold_snapshots": [threshold],
    }
    if is_freeze:
        payload["metadata"] = {
            "source": "evolution_daily_sweep",
            "runtime_binding_id": f"rb-{decision_id}",
            "deployment_stage_snapshot": "live",
            "threshold_evaluation": {
                "requires_runtime_followthrough": True,
                "committee_review_required": True,
            },
            "proposal_only": True,
            "runtime_binding_mutation_allowed": False,
        }
    return payload


def _advance_to_approved(
    *,
    api_url: str,
    decision_id: str,
    action_type: str,
    target_stage: str,
    risk_level: str,
    reviewer_role: str,
    timeout_seconds: float,
    request_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    created = _request(
        api_url,
        "POST",
        "/api/evolution/proposals",
        payload=_proposal_payload(
            decision_id=decision_id,
            action_type=action_type,
            target_stage=target_stage,
            risk_level=risk_level,
        ),
        timeout_seconds=timeout_seconds,
        request_ledger=request_ledger,
    )
    if created.get("decision_state") != "proposed":
        raise ProbeError(f"{decision_id} was not created in proposed state")

    approval_id = f"approval-{decision_id}"
    reviewed = _request(
        api_url,
        "POST",
        f"/api/evolution/proposals/{decision_id}/review",
        payload={
            "actor_role": reviewer_role,
            "actor_id": "evoloop-001-hosted-reviewer",
            "approval_decision_id": approval_id,
        },
        timeout_seconds=timeout_seconds,
        request_ledger=request_ledger,
    )
    if reviewed.get("decision_state") != "reviewed":
        raise ProbeError(f"{decision_id} was not advanced to reviewed")

    approved = _request(
        api_url,
        "POST",
        f"/api/evolution/proposals/{decision_id}/approve",
        payload={
            "actor_role": reviewer_role,
            "actor_id": "evoloop-001-hosted-reviewer",
            "approval_decision_id": approval_id,
        },
        timeout_seconds=timeout_seconds,
        request_ledger=request_ledger,
    )
    if approved.get("decision_state") != "approved":
        raise ProbeError(f"{decision_id} was not advanced to approved")
    return approved


def _read_decision(
    *,
    api_url: str,
    decision_id: str,
    timeout_seconds: float,
    request_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    return _request(
        api_url,
        "GET",
        f"/api/evolution/proposals/{decision_id}",
        timeout_seconds=timeout_seconds,
        request_ledger=request_ledger,
    )


def _executed_steps(decision: dict[str, Any]) -> list[dict[str, Any]]:
    chain = decision.get("review_chain")
    if not isinstance(chain, list):
        raise ProbeError(f"{decision.get('decision_id')} has invalid review_chain")
    return [
        step
        for step in chain
        if isinstance(step, dict) and step.get("step_type") == "executed"
    ]


def _assert_research_execution(
    decision: dict[str, Any], *, expected_execution_ref_id: str | None
) -> None:
    decision_id = decision.get("decision_id")
    if decision.get("decision_state") != "executed":
        raise ProbeError(f"{decision_id} is not executed")
    execution = decision.get("execution_result")
    if not isinstance(execution, dict):
        raise ProbeError(f"{decision_id} has no execution_result")
    expected = {"status": "succeeded", "plane": "research"}
    for field, value in expected.items():
        if execution.get(field) != value:
            raise ProbeError(
                f"{decision_id} execution {field}={execution.get(field)!r}; "
                f"expected {value!r}"
            )
    execution_ref_id = execution.get("execution_ref_id")
    if not isinstance(execution_ref_id, str) or not execution_ref_id:
        raise ProbeError(f"{decision_id} execution_result lacks a downstream receipt ref")
    if (
        expected_execution_ref_id is not None
        and execution_ref_id != expected_execution_ref_id
    ):
        raise ProbeError(
            f"{decision_id} execution_ref_id={execution_ref_id!r}; "
            f"expected preserved receipt {expected_execution_ref_id!r}"
        )
    if not execution.get("executed_at"):
        raise ProbeError(f"{decision_id} execution_result lacks executed_at")
    if not decision.get("cooldown_ends_at") or not decision.get(
        "observation_window_ends_at"
    ):
        raise ProbeError(f"{decision_id} lacks cooldown/observation timestamps")
    steps = _executed_steps(decision)
    if len(steps) != 1:
        raise ProbeError(f"{decision_id} has {len(steps)} executed review steps")
    if steps[0].get("actor_id") != "evolution-dispatch-worker":
        raise ProbeError(
            f"{decision_id} executed actor is {steps[0].get('actor_id')!r}"
        )


def _assert_freeze_unconsumed(decision: dict[str, Any]) -> None:
    decision_id = decision.get("decision_id")
    if decision.get("decision_state") != "approved":
        raise ProbeError(
            f"{decision_id} freeze was consumed into {decision.get('decision_state')!r}"
        )
    if decision.get("execution_result") is not None:
        raise ProbeError(f"{decision_id} freeze unexpectedly has execution_result")
    if _executed_steps(decision):
        raise ProbeError(f"{decision_id} freeze unexpectedly has an executed step")
    metadata = decision.get("metadata")
    if not isinstance(metadata, dict) or not metadata.get("runtime_binding_id"):
        raise ProbeError(f"{decision_id} freeze lost its runtime binding snapshot")
    if "has_active_runtime" in metadata:
        raise ProbeError(f"{decision_id} freeze unexpectedly carries caller runtime truth")


def _normalized_decision(decision: dict[str, Any]) -> dict[str, Any]:
    execution = decision.get("execution_result") or {}
    steps = _executed_steps(decision)
    metadata = decision.get("metadata") or {}
    approved_steps = [
        step
        for step in decision.get("review_chain", [])
        if isinstance(step, dict) and step.get("step_type") == "approved"
    ]
    return {
        "observed_at": _utc_now(),
        "decision_id": decision.get("decision_id"),
        "action_type": decision.get("action_type"),
        "risk_level": decision.get("risk_level"),
        "target_type": decision.get("target_type"),
        "target_id": decision.get("target_id"),
        "target_version": decision.get("target_version"),
        "target_stage": decision.get("target_stage"),
        "decision_state": decision.get("decision_state"),
        "approved_at": approved_steps[-1].get("timestamp") if approved_steps else None,
        "execution_result": execution or None,
        "cooldown_ends_at": decision.get("cooldown_ends_at"),
        "observation_window_ends_at": decision.get("observation_window_ends_at"),
        "executed_step_count": len(steps),
        "executed_step_actor": steps[0].get("actor_id") if steps else None,
        "runtime_binding_id": metadata.get("runtime_binding_id"),
        "metadata_has_active_runtime": "has_active_runtime" in metadata,
    }


def _mutation_summary(request_ledger: list[dict[str, Any]]) -> dict[str, Any]:
    mutations = [entry for entry in request_ledger if entry["method"] != "GET"]
    direct_execute = [
        entry
        for entry in mutations
        if entry["path"].endswith("/execute")
        or entry["path"].endswith("/rollback-followthrough")
    ]
    unexpected_mutations = [
        entry
        for entry in mutations
        if not (
            entry["method"] == "POST"
            and (
                entry["path"] == "/api/evolution/proposals"
                or entry["path"].endswith("/review")
                or entry["path"].endswith("/approve")
            )
        )
    ]
    return {
        "mutating_request_count": len(mutations),
        "mutating_requests": mutations,
        "direct_execute_calls_by_probe": len(direct_execute),
        "unexpected_mutating_requests": unexpected_mutations,
        "mutation_whitelist_passed": not unexpected_mutations,
    }


def run_initial_probe(
    *,
    api_url: str,
    prefix: str,
    timeout_seconds: float,
    poll_timeout_seconds: float,
    poll_interval_seconds: float,
    freeze_observation_seconds: float,
) -> dict[str, Any]:
    started_at = _utc_now()
    request_ledger: list[dict[str, Any]] = []
    research_id = f"{prefix}-research"
    freeze_id = f"{prefix}-freeze-live"

    _advance_to_approved(
        api_url=api_url,
        decision_id=research_id,
        action_type="retrain",
        target_stage="paper",
        risk_level="low",
        reviewer_role="reviewer_on_duty",
        timeout_seconds=timeout_seconds,
        request_ledger=request_ledger,
    )
    _advance_to_approved(
        api_url=api_url,
        decision_id=freeze_id,
        action_type="freeze",
        target_stage="live",
        risk_level="high",
        reviewer_role="governance_committee",
        timeout_seconds=timeout_seconds,
        request_ledger=request_ledger,
    )

    deadline = time.monotonic() + poll_timeout_seconds
    research: dict[str, Any] | None = None
    freeze: dict[str, Any] | None = None
    while time.monotonic() <= deadline:
        research = _read_decision(
            api_url=api_url,
            decision_id=research_id,
            timeout_seconds=timeout_seconds,
            request_ledger=request_ledger,
        )
        freeze = _read_decision(
            api_url=api_url,
            decision_id=freeze_id,
            timeout_seconds=timeout_seconds,
            request_ledger=request_ledger,
        )
        _assert_freeze_unconsumed(freeze)
        if research.get("decision_state") == "executed":
            break
        time.sleep(poll_interval_seconds)
    if research is None or research.get("decision_state") != "executed":
        raise ProbeError(f"{research_id} was not executed before probe timeout")
    _assert_research_execution(research, expected_execution_ref_id=None)

    freeze_deadline = time.monotonic() + freeze_observation_seconds
    while time.monotonic() < freeze_deadline:
        time.sleep(min(poll_interval_seconds, max(0.0, freeze_deadline - time.monotonic())))
        freeze = _read_decision(
            api_url=api_url,
            decision_id=freeze_id,
            timeout_seconds=timeout_seconds,
            request_ledger=request_ledger,
        )
        _assert_freeze_unconsumed(freeze)
    assert freeze is not None

    output = {
        "schema_version": "evoloop-001-hosted-probe.v1",
        "phase": "initial",
        "started_at": started_at,
        "completed_at": _utc_now(),
        "research": _normalized_decision(research),
        "freeze": _normalized_decision(freeze),
        "request_ledger": request_ledger,
        **_mutation_summary(request_ledger),
    }
    if output["direct_execute_calls_by_probe"] != 0:
        raise ProbeError("probe request ledger contains a direct execute call")
    if not output["mutation_whitelist_passed"]:
        raise ProbeError("probe request ledger contains an unexpected mutation")
    return output


def run_verify_probe(
    *,
    api_url: str,
    initial: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    if initial.get("schema_version") != "evoloop-001-hosted-probe.v1":
        raise ProbeError("input is not an EVOLOOP-001 hosted probe artifact")
    research_initial = initial.get("research")
    freeze_initial = initial.get("freeze")
    if not isinstance(research_initial, dict) or not isinstance(freeze_initial, dict):
        raise ProbeError("input lacks normalized research/freeze decisions")
    research_id = research_initial.get("decision_id")
    freeze_id = freeze_initial.get("decision_id")
    if not isinstance(research_id, str) or not isinstance(freeze_id, str):
        raise ProbeError("input decision ids are invalid")

    request_ledger: list[dict[str, Any]] = []
    research = _read_decision(
        api_url=api_url,
        decision_id=research_id,
        timeout_seconds=timeout_seconds,
        request_ledger=request_ledger,
    )
    freeze = _read_decision(
        api_url=api_url,
        decision_id=freeze_id,
        timeout_seconds=timeout_seconds,
        request_ledger=request_ledger,
    )
    expected_ref = research_initial.get("execution_result", {}).get("execution_ref_id")
    if not isinstance(expected_ref, str) or not expected_ref:
        raise ProbeError("input research decision lacks an execution ref")
    _assert_research_execution(research, expected_execution_ref_id=expected_ref)
    _assert_freeze_unconsumed(freeze)

    output = {
        "schema_version": "evoloop-001-hosted-probe.v1",
        "phase": "verify",
        "completed_at": _utc_now(),
        "research": _normalized_decision(research),
        "freeze": _normalized_decision(freeze),
        "request_ledger": request_ledger,
        **_mutation_summary(request_ledger),
    }
    if output["mutating_request_count"] != 0:
        raise ProbeError("verify phase must be read-only")
    return output


def _write_and_print(output: dict[str, Any], path: str | None) -> None:
    rendered = json.dumps(output, indent=2, sort_keys=True)
    if path:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:18093")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--output")
    subparsers = parser.add_subparsers(dest="phase", required=True)

    initial = subparsers.add_parser("initial", help="create and observe probe decisions")
    initial.add_argument(
        "--prefix", default=f"evoloop-001-{uuid.uuid4().hex[:10]}"
    )
    initial.add_argument("--poll-timeout-seconds", type=float, default=120.0)
    initial.add_argument("--poll-interval-seconds", type=float, default=2.0)
    initial.add_argument("--freeze-observation-seconds", type=float, default=35.0)

    verify = subparsers.add_parser("verify", help="read-only restart verification")
    verify.add_argument("--input", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.phase == "initial":
        output = run_initial_probe(
            api_url=args.api_url,
            prefix=args.prefix,
            timeout_seconds=args.timeout_seconds,
            poll_timeout_seconds=args.poll_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            freeze_observation_seconds=args.freeze_observation_seconds,
        )
    else:
        initial = json.loads(Path(args.input).read_text(encoding="utf-8"))
        output = run_verify_probe(
            api_url=args.api_url,
            initial=initial,
            timeout_seconds=args.timeout_seconds,
        )
    _write_and_print(output, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
