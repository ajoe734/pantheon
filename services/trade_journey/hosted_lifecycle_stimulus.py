"""Hosted stimulus for LOOP-PROD-TEL-002 canonical paper lifecycle proof.

The stimulus does not write telemetry directly.  It discovers an active paper
RuntimeBinding, enqueues one identity-complete signal onto that binding's
pending Redis queue, waits for the existing paper runtime to emit a
``position_snapshot``, and then triggers scheduled reconciliation so the
read-only hosted probe can prove the committed aggregate.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence
import urllib.error
import urllib.request

from services.execution.lean_runtime.paper_signal_producer import BoundedPaperStrategy
from services.execution.lean_runtime.pending_signal_store import (
    RedisPendingSignalStore,
    binding_queue_key,
)
from services.execution.lean_runtime.signal_producer import DecisionSignalProducer
from services.trade_journey.hosted_lifecycle_probe import _atomic_write_json


SCHEMA_VERSION = "pantheon.loop-prod-tel-002-hosted-stimulus.v1"
TASK_ID = "LOOP-PROD-TEL-002"
_REQUIRED_BINDING_FIELDS = (
    "binding_id",
    "runtime_id",
    "capital_pool_id",
    "artifact_id",
    "artifact_version",
    "persona_capital_binding_id",
)

JsonGetter = Callable[..., Mapping[str, Any]]
JsonPoster = Callable[..., Mapping[str, Any]]
StoreFactory = Callable[[Mapping[str, Any]], Any]
Sleeper = Callable[[float], None]
Clock = Callable[[], float]
NowFactory = Callable[[], str]


class StimulusError(RuntimeError):
    """A redaction-safe, fail-closed stimulus rejection."""

    def __init__(self, code: str, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.timed_out = timed_out


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _headers(token: str | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _read_response_json(response: Any) -> Mapping[str, Any]:
    raw = response.read()
    parsed = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    if not isinstance(parsed, Mapping):
        raise ValueError("response body is not a JSON object")
    return parsed


def _http_get_json(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 10.0,
) -> Mapping[str, Any]:
    request = urllib.request.Request(
        url,
        headers=dict(headers or {"Accept": "application/json"}),
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return _read_response_json(response)


def _http_post_json(
    url: str,
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
    timeout: float = 10.0,
) -> Mapping[str, Any]:
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        **dict(headers or {}),
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        ),
        headers=request_headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return _read_response_json(response)


def _http_error(code: str, message: str, exc: BaseException) -> StimulusError:
    error = StimulusError(code, message)
    error.__cause__ = exc
    return error


def _clean(value: Any) -> str:
    return str(value or "").strip()


def fetch_active_paper_bindings(
    runtime_manager_url: str,
    token: str | None,
    *,
    http_get_json: JsonGetter = _http_get_json,
) -> list[dict[str, Any]]:
    url = (
        runtime_manager_url.rstrip("/")
        + "/api/runtime-fleet/desired-state?stage=paper"
    )
    try:
        payload = http_get_json(url, headers=_headers(token), timeout=10.0)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise _http_error(
            "runtime_manager_unavailable",
            "runtime-manager desired-state query failed",
            exc,
        )
    bindings = payload.get("bindings")
    if not isinstance(bindings, list):
        raise StimulusError(
            "runtime_manager_response_invalid",
            "runtime-manager desired-state response was malformed",
        )
    return [dict(item) for item in bindings if isinstance(item, Mapping)]


def fetch_running_paper_workers(
    paper_fleet_reconciler_url: str,
    *,
    http_get_json: JsonGetter = _http_get_json,
) -> list[dict[str, Any]]:
    url = paper_fleet_reconciler_url.rstrip("/") + "/api/fleet/state"
    try:
        payload = http_get_json(url, timeout=10.0)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise _http_error(
            "paper_fleet_reconciler_unavailable",
            "paper fleet reconciler state query failed",
            exc,
        )
    workers = payload.get("workers")
    if not isinstance(workers, list):
        raise StimulusError(
            "paper_fleet_reconciler_response_invalid",
            "paper fleet reconciler state response was malformed",
        )
    return [dict(item) for item in workers if isinstance(item, Mapping)]


def _binding_stage(binding: Mapping[str, Any]) -> str:
    metadata = binding.get("metadata") if isinstance(binding.get("metadata"), Mapping) else {}
    return _clean(
        binding.get("deployment_mode")
        or binding.get("deployment_stage")
        or binding.get("environment")
        or metadata.get("environment")
    ).lower()


def _running_worker_by_binding(
    workers: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    running: dict[str, Mapping[str, Any]] = {}
    for worker in workers:
        binding_id = _clean(worker.get("binding_id"))
        if not binding_id:
            continue
        if _clean(worker.get("status")).lower() != "running":
            continue
        running[binding_id] = worker
    return running


def select_active_paper_binding(
    bindings: Sequence[Mapping[str, Any]],
    *,
    running_workers: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    running_by_binding = (
        _running_worker_by_binding(running_workers)
        if running_workers is not None
        else None
    )
    active_paper_binding_seen = False
    for binding in bindings:
        if _clean(binding.get("status")).lower() != "active":
            continue
        if _binding_stage(binding) != "paper":
            continue
        active_paper_binding_seen = True
        if any(not _clean(binding.get(field)) for field in _REQUIRED_BINDING_FIELDS):
            continue
        if running_by_binding is not None:
            worker = running_by_binding.get(_clean(binding.get("binding_id")))
            if worker is None:
                continue
            if _clean(worker.get("runtime_id")) != _clean(binding.get("runtime_id")):
                continue
            if _clean(worker.get("capital_pool_id")) != _clean(binding.get("capital_pool_id")):
                continue
        return dict(binding)
    if active_paper_binding_seen and running_by_binding is not None:
        raise StimulusError(
            "no_running_paper_worker",
            "no active paper binding had a running paper runtime worker",
        )
    raise StimulusError(
        "no_active_paper_binding",
        "no active paper binding with complete lifecycle identity was available",
    )


def _default_store_factory(signal_store_url: str) -> StoreFactory:
    if not _clean(signal_store_url):
        raise StimulusError(
            "signal_store_not_configured",
            "SIGNAL_STORE_URL is required to enqueue paper stimulus",
        )

    def store_for(binding: Mapping[str, Any]) -> RedisPendingSignalStore:
        binding_id = _clean(binding.get("binding_id"))
        return RedisPendingSignalStore(
            signal_store_url,
            queue_key=binding_queue_key(binding_id),
        )

    return store_for


def enqueue_stimulus_signal(
    binding: Mapping[str, Any],
    *,
    now_iso: str,
    store_factory: StoreFactory,
    quantity: float = 7.0,
    symbol: str = "AAPL.US",
) -> dict[str, Any]:
    decision = BoundedPaperStrategy(quantity=quantity, symbol=symbol)(binding, now_iso)
    store = store_factory(binding)
    try:
        batch = DecisionSignalProducer(store).produce(
            decision,
            now_iso=now_iso,
            strategy_id=_clean(decision.get("strategy_id")) or None,
            source_worker="loop-prod-tel-002-hosted-stimulus",
            binding_id=_clean(binding.get("binding_id")),
            runtime_id=_clean(binding.get("runtime_id")),
            run_id=_clean(decision.get("run_id")),
        )
    except Exception as exc:  # noqa: BLE001 - never expose Redis/payload details
        raise StimulusError(
            "signal_enqueue_failed",
            "paper stimulus signal enqueue failed",
        ) from exc
    if batch.count < 1:
        raise StimulusError(
            "signal_enqueue_failed",
            "paper stimulus did not enqueue a signal",
        )
    signal_ids = [_clean(signal.get("signal_id")) for signal in batch.signals]
    return {
        "run_id": _clean(decision.get("run_id")),
        "signal_ids": [value for value in signal_ids if value],
        "signal_count": batch.count,
        "queue_key": binding_queue_key(_clean(binding.get("binding_id"))),
    }


def _summaries(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = payload.get("summaries")
    if not isinstance(raw, list):
        raise StimulusError(
            "telemetry_response_invalid",
            "telemetry runtime summaries response was malformed",
        )
    return [item for item in raw if isinstance(item, Mapping)]


def _matching_lifecycle_identity(
    summaries: Sequence[Mapping[str, Any]],
    *,
    binding: Mapping[str, Any],
    run_id: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    binding_id = _clean(binding.get("binding_id"))
    runtime_id = _clean(binding.get("runtime_id"))
    for summary in summaries:
        if _clean(summary.get("binding_id") or summary.get("runtime_binding_id")) != binding_id:
            continue
        if runtime_id and _clean(summary.get("runtime_id")) != runtime_id:
            continue
        identity = summary.get("last_lifecycle_identity")
        if not isinstance(identity, Mapping):
            continue
        sequence_no = identity.get("sequence_no")
        if isinstance(sequence_no, bool) or not isinstance(sequence_no, int):
            continue
        if (
            _clean(identity.get("run_id")) == run_id
            and _clean(identity.get("event_type")) == "position_snapshot"
            and sequence_no >= 5
            and _clean(identity.get("environment")).lower() == "paper"
            and _clean(identity.get("execution_mode")).lower() == "paper"
            and _clean(identity.get("deployment_stage")).lower() == "paper"
            and _clean(identity.get("source_mode")).lower() == "live"
        ):
            return summary, identity
    return None


def wait_for_lifecycle_summary(
    *,
    telemetry_url: str,
    binding: Mapping[str, Any],
    run_id: str,
    timeout_seconds: float,
    poll_seconds: float,
    http_get_json: JsonGetter = _http_get_json,
    sleeper: Sleeper = time.sleep,
    monotonic: Clock = time.monotonic,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    endpoint = telemetry_url.rstrip("/") + "/api/telemetry/runtime-summaries"
    deadline = monotonic() + max(0.0, timeout_seconds)
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise StimulusError(
                "lifecycle_signal_timeout",
                "paper runtime did not publish the stimulus lifecycle snapshot before the deadline",
                timed_out=True,
            )
        try:
            payload = http_get_json(endpoint, timeout=min(10.0, remaining))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise _http_error(
                "telemetry_unavailable",
                "telemetry runtime summaries query failed",
                exc,
            )
        match = _matching_lifecycle_identity(
            _summaries(payload),
            binding=binding,
            run_id=run_id,
        )
        if match is not None:
            return match
        sleeper(min(max(0.05, poll_seconds), max(0.0, deadline - monotonic())))


def trigger_reconciliation(
    *,
    reconciliation_url: str,
    binding_id: str,
    tick_id: str,
    http_post_json: JsonPoster = _http_post_json,
) -> Mapping[str, Any]:
    endpoint = (
        reconciliation_url.rstrip()
        .rstrip("/")
        + "/api/reconciliation-drift/scheduled-reconcile"
    )
    try:
        payload = http_post_json(
            endpoint,
            {
                "tick_id": tick_id,
                "binding_id": binding_id,
                "dispatch_incidents": False,
            },
            timeout=20.0,
        )
    except urllib.error.HTTPError as exc:
        raise _http_error(
            "reconciliation_http_error",
            f"scheduled reconciliation returned HTTP {int(exc.code)}",
            exc,
        )
    except TimeoutError as exc:
        raise _http_error(
            "reconciliation_timeout",
            "scheduled reconciliation request timed out",
            exc,
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise _http_error(
            "reconciliation_unavailable",
            "scheduled reconciliation request failed",
            exc,
        )
    results = payload.get("lifecycle_append_results")
    if not isinstance(results, list):
        raise StimulusError(
            "reconciliation_response_invalid",
            "scheduled reconciliation response was malformed",
        )
    for receipt in results:
        if not isinstance(receipt, Mapping):
            continue
        if _clean(receipt.get("binding_id")) != binding_id:
            continue
        if _clean(receipt.get("status")) == "accepted" and _clean(receipt.get("event_id")):
            return dict(receipt)
        raise StimulusError(
            "reconciliation_append_not_accepted",
            "scheduled reconciliation did not accept a lifecycle append for the stimulus binding",
        )
    raise StimulusError(
        "reconciliation_append_not_accepted",
        "scheduled reconciliation did not return a lifecycle receipt for the stimulus binding",
    )


def run_stimulus(
    *,
    runtime_manager_url: str,
    runtime_manager_token: str | None,
    paper_fleet_reconciler_url: str,
    telemetry_url: str,
    reconciliation_url: str,
    signal_store_url: str,
    timeout_seconds: float,
    poll_seconds: float,
    quantity: float = 7.0,
    symbol: str = "AAPL.US",
    http_get_json: JsonGetter = _http_get_json,
    http_post_json: JsonPoster = _http_post_json,
    store_factory: StoreFactory | None = None,
    sleeper: Sleeper = time.sleep,
    monotonic: Clock = time.monotonic,
    now_factory: NowFactory = _utc_now,
) -> dict[str, Any]:
    bindings = fetch_active_paper_bindings(
        runtime_manager_url,
        runtime_manager_token,
        http_get_json=http_get_json,
    )
    workers = fetch_running_paper_workers(
        paper_fleet_reconciler_url,
        http_get_json=http_get_json,
    )
    binding = select_active_paper_binding(
        bindings,
        running_workers=workers,
    )
    if store_factory is None:
        store_factory = _default_store_factory(signal_store_url)
    now_iso = now_factory()
    enqueued = enqueue_stimulus_signal(
        binding,
        now_iso=now_iso,
        store_factory=store_factory,
        quantity=quantity,
        symbol=symbol,
    )
    _summary, identity = wait_for_lifecycle_summary(
        telemetry_url=telemetry_url,
        binding=binding,
        run_id=enqueued["run_id"],
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
        http_get_json=http_get_json,
        sleeper=sleeper,
        monotonic=monotonic,
    )
    tick_id = f"loop-prod-tel-002-{enqueued['run_id']}"
    receipt = trigger_reconciliation(
        reconciliation_url=reconciliation_url,
        binding_id=_clean(binding.get("binding_id")),
        tick_id=tick_id,
        http_post_json=http_post_json,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": TASK_ID,
        "outcome": "passed",
        "observed_at": _utc_now(),
        "stimulus": {
            "binding_id": _clean(binding.get("binding_id")),
            "runtime_id": _clean(binding.get("runtime_id")),
            "run_id": enqueued["run_id"],
            "signal_ids": enqueued["signal_ids"],
            "signal_count": enqueued["signal_count"],
            "queue_key": enqueued["queue_key"],
            "lifecycle_summary_event_id": _clean(identity.get("event_id")),
            "lifecycle_sequence_no": identity.get("sequence_no"),
            "tick_id": tick_id,
            "reconciliation_event_id": _clean(receipt.get("event_id")),
        },
        "redaction": {
            "tokens_included": False,
            "credentials_included": False,
            "response_payloads_included": False,
        },
    }


def _failure_artifact(
    *,
    code: str,
    message: str,
    timed_out: bool = False,
) -> dict[str, Any]:
    failure: dict[str, Any] = {"code": code, "message": message}
    if timed_out:
        failure["timed_out"] = True
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": TASK_ID,
        "outcome": "failed",
        "observed_at": _utc_now(),
        "failure": failure,
        "redaction": {
            "tokens_included": False,
            "credentials_included": False,
            "response_payloads_included": False,
        },
    }


def execute(
    *,
    output: Path | None = None,
    **kwargs: Any,
) -> tuple[int, dict[str, Any]]:
    try:
        artifact = run_stimulus(**kwargs)
        code = 0
    except StimulusError as exc:
        artifact = _failure_artifact(
            code=exc.code,
            message=exc.safe_message,
            timed_out=exc.timed_out,
        )
        code = 1
    except Exception:  # noqa: BLE001 - keep unexpected failures redacted
        artifact = _failure_artifact(
            code="unexpected_stimulus_error",
            message="hosted lifecycle stimulus failed unexpectedly",
        )
        code = 1
    if output is not None:
        _atomic_write_json(output, artifact)
    return code, artifact


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout-seconds", "--timeout", dest="timeout", type=float, default=180.0)
    parser.add_argument("--poll-seconds", "--poll", dest="poll", type=float, default=5.0)
    parser.add_argument(
        "--runtime-manager-url",
        default=os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "http://runtime-manager:8081"),
    )
    parser.add_argument(
        "--telemetry-url",
        default=os.getenv(
            "PANTHEON_TELEMETRY_URL",
            os.getenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry:8083"),
        ),
    )
    parser.add_argument(
        "--paper-fleet-reconciler-url",
        default=os.getenv(
            "PANTHEON_PAPER_FLEET_RECONCILER_URL",
            "http://paper-fleet-reconciler:8011",
        ),
    )
    parser.add_argument(
        "--reconciliation-url",
        default=os.getenv("RECONCILIATION_DRIFT_URL", "http://reconciliation-drift-svc:8102"),
    )
    parser.add_argument("--signal-store-url", default=os.getenv("SIGNAL_STORE_URL", ""))
    parser.add_argument("--quantity", type=float, default=7.0)
    parser.add_argument("--symbol", default="AAPL.US")
    args = parser.parse_args(argv)

    code, artifact = execute(
        output=args.output,
        runtime_manager_url=args.runtime_manager_url,
        runtime_manager_token=os.getenv("PANTHEON_RUNTIME_MANAGER_TOKEN", "").strip() or None,
        paper_fleet_reconciler_url=args.paper_fleet_reconciler_url,
        telemetry_url=args.telemetry_url,
        reconciliation_url=args.reconciliation_url,
        signal_store_url=args.signal_store_url,
        timeout_seconds=args.timeout,
        poll_seconds=args.poll,
        quantity=args.quantity,
        symbol=args.symbol,
    )
    print(json.dumps(artifact, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
