"""
Evolution dispatch worker — durable outbox delivery (L12-EVO-001).

Polls the durable dispatch outbox rather than the approved-decision list, drives
each claimed intent to a real downstream, and only asks the evolution service to
execute a decision once the downstream reports a terminal receipt.

What each tick does:

1. **Reconcile.** Prepared intents whose decision is durably approved are
   activated.  This is the crash-after-approval recovery path: approval and
   activation are separate writes, so a process that died between them left an
   inert intent that would otherwise never be dispatched.
2. **Claim.** Due records are leased, so two workers cannot both dispatch the
   same approved action.
3. **Submit and read back.** The plane adapter submits idempotently and then
   reads the downstream's own status.  A run that is still executing is left
   claimed until its lease expires — it is a healthy in-flight dispatch and must
   not consume the retry budget reserved for real failures.
4. **Converge.** A terminal success calls ``/execute`` with the receipt, which
   the service independently re-reads before moving the decision to ``executed``.
   A terminal downstream failure, or a downstream that ran while the decision
   could not record it, records a durable compensation and dead-letters.

The worker never marks a decision executed itself, never fabricates a receipt,
and never bypasses the boundary gate on ``/execute``.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Mapping

from services.evolution.dispatch_outbox import (
    CompensationLedger,
    EvolutionDispatchOutbox,
    build_dispatch_outbox_store,
    reconcile_dispatch_outbox,
)
from services.evolution.dispatch_receipts import (
    OUTCOME_FAILED,
    OUTCOME_PENDING,
    OUTCOME_RETRYABLE,
    OUTCOME_SUCCEEDED,
    OUTCOME_TERMINAL_ERROR,
    OUTCOME_UNSUPPORTED,
    build_adapter_registry,
)
from services.foundation.persistence_posture import require_persistence_posture

_WORKER_NAME = "evolution-dispatch-worker"
_ACTOR_ROLE = "evolution_controller"

# Decision states that mean "this intent has nothing left to deliver".
_CONVERGED_STATES = frozenset({"executed"})
# Decision states that mean the intent will never be deliverable again.
_ABANDONED_STATES = frozenset({"rejected", "canceled", "superseded"})


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decode_json_response(body: str, *, context: str) -> Any:
    if not body.strip():
        raise RuntimeError(f"{context} returned an empty 2xx response")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{context} returned malformed JSON") from exc


def _service_headers(*, tenant_id: str | None, auth_token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if tenant_id:
        headers["X-Tenant-Id"] = tenant_id
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    return headers


def _http_get(
    url: str,
    timeout_seconds: float,
    *,
    tenant_id: str | None = None,
    auth_token: str | None = None,
) -> Any:
    request = urllib.request.Request(
        url,
        headers=_service_headers(tenant_id=tenant_id, auth_token=auth_token),
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return _decode_json_response(body, context=f"GET {url}")


def _http_post(
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    *,
    tenant_id: str | None = None,
    auth_token: str | None = None,
) -> Any:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            **_service_headers(tenant_id=tenant_id, auth_token=auth_token),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
    return _decode_json_response(body, context=f"POST {url}")


def fetch_decision(
    *,
    api_url: str,
    decision_id: str,
    tenant_id: str,
    auth_token: str | None,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    """Return the decision, or ``None`` when the service says it is gone."""
    url = api_url.rstrip("/") + f"/api/evolution/proposals/{decision_id}"
    try:
        payload = _http_get(
            url,
            timeout_seconds,
            tenant_id=tenant_id,
            auth_token=auth_token,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if not isinstance(payload, dict):
        raise RuntimeError(f"decision {decision_id} returned a non-object payload")
    return payload


def execute_with_receipt(
    *,
    api_url: str,
    decision_id: str,
    tenant_id: str,
    actor_id: str,
    downstream_kind: str,
    downstream_ref_id: str,
    auth_token: str | None,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Ask the evolution service to execute on a downstream receipt.

    Only the reference is sent, never a claimed status: the service re-reads the
    downstream itself, so a compromised or buggy worker cannot assert an outcome
    the downstream never reported.
    """
    url = api_url.rstrip("/") + f"/api/evolution/proposals/{decision_id}/execute"
    payload = {
        "actor_role": _ACTOR_ROLE,
        "actor_id": actor_id,
        "tenant_id": tenant_id,
        "execution_receipt": {
            "downstream_kind": downstream_kind,
            "downstream_ref_id": downstream_ref_id,
        },
    }
    result = _http_post(
        url,
        payload,
        timeout_seconds,
        tenant_id=tenant_id,
        auth_token=auth_token,
    )
    if not isinstance(result, dict):
        raise RuntimeError(f"execute response for {decision_id} was not an object")
    return result


def _transition_applied_check(
    *,
    api_url: str,
    auth_token: str | None,
    timeout_seconds: float,
):
    """Build the reconcile predicate: is the intent's decision durably approved?

    Fails closed.  A decision the service cannot currently answer for is *not*
    reported as applied, so a transient outage leaves the intent prepared for
    the next tick instead of activating a dispatch whose approval was never
    confirmed.
    """

    def transition_applied(transition: Mapping[str, Any]) -> bool:
        if transition.get("aggregate_type") != "evolution_decision":
            return False
        decision_id = str(transition.get("aggregate_id") or "")
        tenant_id = str(transition.get("tenant_id") or "")
        if not decision_id or not tenant_id:
            return False
        try:
            decision = fetch_decision(
                api_url=api_url,
                decision_id=decision_id,
                tenant_id=tenant_id,
                auth_token=auth_token,
                timeout_seconds=timeout_seconds,
            )
        except Exception:  # noqa: BLE001 - any read failure must not activate
            return False
        if decision is None:
            return False
        if str(decision.get("tenant_id") or "") != str(transition.get("tenant_id") or ""):
            return False
        expected = set(transition.get("expected_states") or [])
        return str(decision.get("decision_state") or "") in expected

    return transition_applied


def run_poll(
    *,
    api_url: str,
    outbox: EvolutionDispatchOutbox,
    registry: Mapping[str, Any],
    compensations: CompensationLedger,
    actor_id: str = _WORKER_NAME,
    worker_id: str = _WORKER_NAME,
    timeout_seconds: float = 10.0,
    auth_token: str | None = None,
    claim_limit: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one delivery tick over the durable dispatch outbox."""
    result: dict[str, Any] = {
        "reconciled": 0,
        "claimed": 0,
        "executed": 0,
        "pending": 0,
        "retried": 0,
        "dead_lettered": 0,
        "compensated": 0,
        "unsupported": 0,
        "errors": [],
        "items": [],
    }

    try:
        result["reconciled"] = reconcile_dispatch_outbox(
            outbox,
            transition_applied=_transition_applied_check(
                api_url=api_url,
                auth_token=auth_token,
                timeout_seconds=timeout_seconds,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"reconcile_error={exc}")

    try:
        claimed = outbox.claim_due(worker_id=worker_id, now=now, limit=claim_limit)
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"claim_error={exc}")
        return result

    result["claimed"] = len(claimed)

    for record in claimed:
        payload = dict(record.event.payload or {})
        decision_id = str(payload.get("decision_id") or "")
        tenant_id = str(payload.get("tenant_id") or "")
        plane = str(payload.get("execution_plane") or "")
        item: dict[str, Any] = {
            "outbox_id": record.outbox_id,
            "decision_id": decision_id,
            "tenant_id": tenant_id,
            "execution_plane": plane,
            "action_type": payload.get("action_type"),
        }
        try:
            _deliver_one(
                record=record,
                decision_id=decision_id,
                tenant_id=tenant_id,
                plane=plane,
                payload=payload,
                api_url=api_url,
                outbox=outbox,
                registry=registry,
                compensations=compensations,
                actor_id=actor_id,
                timeout_seconds=timeout_seconds,
                auth_token=auth_token,
                now=now,
                result=result,
                item=item,
            )
        except Exception as exc:  # noqa: BLE001
            # An unexpected error must not leave the record silently claimed
            # forever; count it as a delivery attempt so backoff and the DLQ
            # still apply.
            item["disposition"] = "error"
            item["detail"] = str(exc)
            result["errors"].append(f"decision_id={decision_id} error={exc}")
            try:
                _fail(record, outbox, f"unexpected dispatch error: {exc}", result, now=now)
            except Exception as inner:  # noqa: BLE001
                result["errors"].append(f"decision_id={decision_id} complete_error={inner}")
        result["items"].append(item)

    return result


def _fail(
    record,
    outbox: EvolutionDispatchOutbox,
    error: str,
    result: dict[str, Any],
    *,
    permanent: bool = False,
    now: datetime | None = None,
) -> None:
    _, completed = outbox.complete_failed(record, error, permanent=permanent, now=now)
    if completed.status.value == "dead_lettered":
        result["dead_lettered"] += 1
    else:
        result["retried"] += 1


def _deliver_one(
    *,
    record,
    decision_id: str,
    tenant_id: str,
    plane: str,
    payload: Mapping[str, Any],
    api_url: str,
    outbox: EvolutionDispatchOutbox,
    registry: Mapping[str, Any],
    compensations: CompensationLedger,
    actor_id: str,
    timeout_seconds: float,
    auth_token: str | None,
    now: datetime | None,
    result: dict[str, Any],
    item: dict[str, Any],
) -> None:
    decision = fetch_decision(
        api_url=api_url,
        decision_id=decision_id,
        tenant_id=tenant_id,
        auth_token=auth_token,
        timeout_seconds=timeout_seconds,
    )
    if decision is None:
        item["disposition"] = "dead_lettered"
        item["detail"] = "decision no longer exists"
        _fail(
            record,
            outbox,
            f"decision {decision_id} no longer exists",
            result,
            permanent=True,
            now=now,
        )
        return

    state = str(decision.get("decision_state") or "")
    if state in _CONVERGED_STATES:
        # The decision already reached its terminal state — an earlier attempt
        # (or an operator) converged it.  The intent is delivered.
        item["disposition"] = "already_executed"
        outbox.complete_published(record)
        return
    if state in _ABANDONED_STATES:
        item["disposition"] = "dead_lettered"
        item["detail"] = f"decision is {state}; nothing left to dispatch"
        _fail(
            record,
            outbox,
            f"decision {decision_id} is {state}",
            result,
            permanent=True,
            now=now,
        )
        return
    if state != "approved":
        # proposed/reviewed: the intent was activated too early. Retry rather
        # than dead-letter; the approval may still be in flight.
        item["disposition"] = "retry"
        item["detail"] = f"decision is {state}, not yet approved"
        _fail(record, outbox, f"decision {decision_id} is {state}", result, now=now)
        return

    if str(decision.get("tenant_id") or "") != tenant_id:
        item["disposition"] = "dead_lettered"
        item["detail"] = "decision tenant no longer matches the dispatch intent"
        _fail(
            record,
            outbox,
            f"decision {decision_id} tenant changed since the intent was prepared",
            result,
            permanent=True,
            now=now,
        )
        return

    adapter = registry.get(plane)
    if adapter is None:
        item["disposition"] = "dead_lettered"
        item["detail"] = f"no adapter for execution plane {plane!r}"
        _fail(
            record,
            outbox,
            f"no downstream adapter for execution plane {plane!r}",
            result,
            permanent=True,
            now=now,
        )
        return

    submission = adapter.submit(payload)
    if submission.outcome == OUTCOME_UNSUPPORTED:
        result["unsupported"] += 1
        item["disposition"] = "unsupported"
        item["detail"] = submission.detail
        _fail(record, outbox, f"unsupported plane: {submission.detail}", result, permanent=True, now=now)
        return
    if submission.outcome == OUTCOME_TERMINAL_ERROR:
        item["disposition"] = "dead_lettered"
        item["detail"] = submission.detail
        _fail(record, outbox, str(submission.detail), result, permanent=True, now=now)
        return
    if submission.outcome == OUTCOME_RETRYABLE:
        item["disposition"] = "retry"
        item["detail"] = submission.detail
        _fail(record, outbox, str(submission.detail), result, now=now)
        return

    downstream_ref_id = submission.downstream_ref_id
    if not downstream_ref_id:
        item["disposition"] = "retry"
        item["detail"] = "submission returned no downstream reference"
        _fail(record, outbox, "submission returned no downstream reference", result, now=now)
        return

    item["downstream_kind"] = submission.downstream_kind
    item["downstream_ref_id"] = downstream_ref_id

    receipt = adapter.read_receipt(
        downstream_ref_id,
        expected_intent=payload,
    )
    item["downstream_status"] = receipt.downstream_status
    if receipt.outcome == OUTCOME_PENDING:
        # Healthy in-flight work.  Leave the claim to expire rather than
        # recording a failed attempt: a long research run must not be
        # dead-lettered for taking longer than the retry budget.
        result["pending"] += 1
        item["disposition"] = "pending"
        item["detail"] = receipt.detail
        return
    if receipt.outcome == OUTCOME_RETRYABLE:
        item["disposition"] = "retry"
        item["detail"] = receipt.detail
        _fail(record, outbox, str(receipt.detail), result, now=now)
        return
    if receipt.outcome in {OUTCOME_TERMINAL_ERROR, OUTCOME_UNSUPPORTED}:
        item["disposition"] = "dead_lettered"
        item["detail"] = receipt.detail
        _fail(record, outbox, str(receipt.detail), result, permanent=True, now=now)
        return
    if receipt.outcome == OUTCOME_FAILED:
        # The downstream really ran and really failed.  The decision stays
        # approved; the obligation to unwind whatever the downstream did is
        # recorded durably rather than left in a log line.
        compensations.record(
            tenant_id=tenant_id,
            decision_id=decision_id,
            outbox_id=record.outbox_id,
            reason=f"downstream terminal failure: {receipt.detail}",
            downstream_kind=receipt.downstream_kind,
            downstream_ref_id=downstream_ref_id,
        )
        result["compensated"] += 1
        item["disposition"] = "compensated"
        item["detail"] = receipt.detail
        _fail(record, outbox, str(receipt.detail), result, permanent=True, now=now)
        return

    if receipt.outcome != OUTCOME_SUCCEEDED:  # pragma: no cover - defensive
        item["disposition"] = "retry"
        item["detail"] = f"unrecognised receipt outcome {receipt.outcome!r}"
        _fail(record, outbox, f"unrecognised receipt outcome {receipt.outcome!r}", result, now=now)
        return

    try:
        executed = execute_with_receipt(
            api_url=api_url,
            decision_id=decision_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            downstream_kind=receipt.downstream_kind,
            downstream_ref_id=downstream_ref_id,
            auth_token=auth_token,
            timeout_seconds=timeout_seconds,
        )
    except urllib.error.HTTPError as exc:
        if 500 <= exc.code <= 599 or exc.code in {408, 429}:
            item["disposition"] = "retry"
            item["detail"] = f"execute failed: HTTP {exc.code} {exc.reason}"
            _fail(record, outbox, f"execute failed: HTTP {exc.code}", result, now=now)
            return
        # The downstream work completed but the decision cannot record it.
        # That is a genuine half-applied dispatch and needs compensation.
        compensations.record(
            tenant_id=tenant_id,
            decision_id=decision_id,
            outbox_id=record.outbox_id,
            reason=(
                f"downstream succeeded but execute was rejected: HTTP {exc.code} {exc.reason}"
            ),
            downstream_kind=receipt.downstream_kind,
            downstream_ref_id=downstream_ref_id,
        )
        result["compensated"] += 1
        item["disposition"] = "compensated"
        item["detail"] = f"execute rejected: HTTP {exc.code} {exc.reason}"
        _fail(
            record,
            outbox,
            f"execute rejected: HTTP {exc.code} {exc.reason}",
            result,
            permanent=True,
            now=now,
        )
        return
    except Exception as exc:  # noqa: BLE001
        item["disposition"] = "retry"
        item["detail"] = f"execute transport error: {exc}"
        _fail(record, outbox, f"execute transport error: {exc}", result, now=now)
        return

    result["executed"] += 1
    item["disposition"] = "executed"
    item["decision_state"] = executed.get("decision_state")
    item["execution_result"] = executed.get("execution_result")
    outbox.complete_published(record)


def _write_health(path: str, state: dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass


def healthcheck() -> int:
    """Return success only after a recent, error-free dispatch poll."""
    health_file = os.getenv("EVOLUTION_DISPATCH_HEALTH_FILE", "")
    interval_seconds = _env_int("EVOLUTION_DISPATCH_INTERVAL_SECONDS", 30, minimum=1)
    if not health_file:
        print("EVOLUTION_DISPATCH_HEALTH_FILE is not configured", file=sys.stderr)
        return 1

    try:
        with open(health_file, encoding="utf-8") as fh:
            state = json.load(fh)
        age_seconds = max(0.0, time.time() - os.path.getmtime(health_file))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"evolution dispatch health unavailable: {exc}", file=sys.stderr)
        return 1

    if not isinstance(state, dict):
        print("evolution dispatch health payload is not an object", file=sys.stderr)
        return 1

    max_age_seconds = max(60, interval_seconds * 3)
    if state.get("status") != "ok" or state.get("ticks", 0) < 1:
        print(
            "evolution dispatch health is not ready: "
            f"status={state.get('status')} ticks={state.get('ticks')}",
            file=sys.stderr,
        )
        return 1
    if age_seconds > max_age_seconds:
        print(
            "evolution dispatch health is stale: "
            f"age_seconds={age_seconds:.1f} max_age_seconds={max_age_seconds}",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    api_url = os.getenv("EVOLUTION_API_URL", "http://127.0.0.1:8093")
    research_api_url = (
        os.getenv("EVOLUTION_RESEARCH_API_URL")
        or os.getenv("RESEARCH_ORCHESTRATOR_URL", "http://research-orchestrator-svc:8101")
    )
    data_dir = os.getenv("EVOLUTION_DATA_DIR", "/tmp/pantheon/evolution")
    actor_id = os.getenv("EVOLUTION_DISPATCH_ACTOR_ID", _WORKER_NAME)
    interval_seconds = _env_int("EVOLUTION_DISPATCH_INTERVAL_SECONDS", 30, minimum=1)
    max_ticks = _env_int("EVOLUTION_DISPATCH_MAX_TICKS", 0, minimum=0)
    timeout_seconds = float(os.getenv("EVOLUTION_DISPATCH_TIMEOUT_SECONDS", "10"))
    health_file = os.getenv("EVOLUTION_DISPATCH_HEALTH_FILE", "")
    auth_mode = os.getenv("EVOLUTION_AUTH_MODE", "disabled").strip().lower()
    auth_token = os.getenv("EVOLUTION_AUTH_TOKEN", "").strip() or None
    if auth_mode not in {"disabled", "token"}:
        raise RuntimeError("EVOLUTION_AUTH_MODE must be disabled or token")
    if auth_mode == "token" and auth_token is None:
        raise RuntimeError("EVOLUTION_AUTH_TOKEN is required when EVOLUTION_AUTH_MODE=token")

    persistence_posture = require_persistence_posture(
        "evolution-dispatch-worker",
        backend_env_vars={"EVOLUTION_STORE_BACKEND": "json"},
        require_object_store=False,
    )

    outbox = EvolutionDispatchOutbox(build_dispatch_outbox_store(data_dir=data_dir))
    compensations = CompensationLedger(data_dir=data_dir)
    registry = build_adapter_registry(
        research_api_url=research_api_url, timeout=timeout_seconds
    )

    health: dict[str, Any] = {
        "worker_name": actor_id,
        "status": "starting",
        "total_executed": 0,
        "total_dead_lettered": 0,
        "total_compensated": 0,
        "total_errors": 0,
        "ticks": 0,
        "last_success": None,
        "last_failure": None,
        "last_failure_reason": None,
        "store_backend": os.getenv("EVOLUTION_STORE_BACKEND", "json"),
        "persistence_posture": persistence_posture.mode,
        "auth_mode": auth_mode,
    }
    if health_file:
        # A container restart can retain the prior writable-layer health file.
        # Reset it before the first network call so stale "ok" state cannot
        # make a newly booted worker healthy before it completes a poll.
        _write_health(health_file, health)

    tick = 0
    result: dict[str, Any] = {}
    while True:
        tick += 1
        try:
            result = run_poll(
                api_url=api_url,
                outbox=outbox,
                registry=registry,
                compensations=compensations,
                actor_id=actor_id,
                timeout_seconds=timeout_seconds,
                auth_token=auth_token,
            )
            health["ticks"] = tick
            health["total_executed"] += result["executed"]
            health["total_dead_lettered"] += result["dead_lettered"]
            health["total_compensated"] += result["compensated"]
            if result["errors"]:
                health["total_errors"] += len(result["errors"])
                health["status"] = "degraded"
                health["last_failure"] = _utc_now()
                health["last_failure_reason"] = "; ".join(result["errors"])
            else:
                health["status"] = "ok"
                health["last_success"] = _utc_now()
            if health_file:
                _write_health(health_file, health)
        except Exception as exc:  # noqa: BLE001
            health["ticks"] = tick
            health["total_errors"] += 1
            health["status"] = "degraded"
            health["last_failure"] = _utc_now()
            health["last_failure_reason"] = str(exc)
            result = {
                "reconciled": 0,
                "claimed": 0,
                "executed": 0,
                "pending": 0,
                "retried": 0,
                "dead_lettered": 0,
                "compensated": 0,
                "unsupported": 0,
                "errors": [str(exc)],
                "items": [],
            }
            if health_file:
                _write_health(health_file, health)

        print(
            json.dumps({"tick": tick, "health": health, "result": result}, sort_keys=True),
            flush=True,
        )

        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover - exercised through compose/smoke.
    if sys.argv[1:] == ["healthcheck"]:
        raise SystemExit(healthcheck())
    if sys.argv[1:]:
        print(
            "usage: python -m services.evolution.dispatch_worker [healthcheck]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raise SystemExit(main())
