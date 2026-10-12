#!/usr/bin/env python3
"""Retire dev paper release probe cases that hosted no longer serves.

Every Root deploy provisions one ``dev-paper-release-<run>-<attempt>`` Persona
through ``bootstrap_dev_paper_baseline.py`` and never retires it, so each old
case keeps an active RuntimeBinding in the paper fleet.  After the release
controller has either accepted or compensated the release, this script moves
every release case that is *not* the served one out of the active fleet
(on a compensated release, only this run's own case).

Retirement uses only an existing owner path: the Deployment owner's saga
failure route (``POST /api/deployment/sagas/{id}/failure`` with
``failed_step=runtime_load_requested``).  Deployment records the
``mark_binding_failed_inactive`` compensation decision and its outbox consumer
transitions the RuntimeBinding to ``failed`` with the runtime-manager service
identity.  The script never calls the operator/MFA runtime-binding routes,
never writes a database, and never deletes provisioning, registry or
telemetry records.

Run inside the dev operator-bff container (stdin keeps it independent of the
checked-out image after a compensation)::

    docker exec -i pantheon-operator-bff-1 python - --dry-run ... < this_file

Served case:
* ``accepted``    -> the case of this run stays; every other active case goes.
* ``compensated`` -> only the case of this run goes; every other case,
  including the previously served one, is left untouched.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

CASE_KEY_PREFIX = "dev-paper-release-"
RETIRE_FAILED_STEP = "runtime_load_requested"
OUTCOMES = ("accepted", "compensated")

SAGA_ACTIVE = "active"
SAGA_PENDING = "pending"
SAGA_RETIRED = "retired"
SAGA_MISSING = "missing"


class RetireError(RuntimeError):
    """Raised when retirement inputs are unsafe."""


def _saga_path(saga_id: str) -> str:
    return f"/api/deployment/sagas/{saga_id}"


def classify_saga(saga: Mapping[str, Any] | None) -> str:
    if saga is None:
        return SAGA_MISSING
    status = str(saga.get("status") or "")
    step = str(saga.get("current_step") or "")
    if status in {"failed", "aborted"} and step == "compensated":
        return SAGA_RETIRED
    if status in {"compensating", "failed", "aborted"}:
        return SAGA_PENDING
    return SAGA_ACTIVE


def case_ids(record: Any) -> tuple[str, str]:
    """Return (plan_id, saga_id) derived exactly as provisioning derives them."""
    from services.control_plane.bff.persona_provisioning_coordinator import (
        deterministic_provisioning_ids,
    )

    ids = deterministic_provisioning_ids(record)
    return ids.deployment_plan_id, ids.deployment_saga_id


def release_cases(records: Sequence[Any]) -> list[Any]:
    """Succeeded release probe cases, newest first."""
    cases = [
        record
        for record in records
        if str(record.idempotency_key).startswith(CASE_KEY_PREFIX)
        and record.state == "succeeded"
    ]
    return sorted(
        cases, key=lambda r: (str(r.created_at), str(r.idempotency_key)), reverse=True
    )


def plan_retirement(
    cases: Sequence[Any],
    sagas: Mapping[str, str],
    *,
    outcome: str,
    new_case_key: str,
) -> dict[str, Any]:
    """Pure decision: which cases stay served and which must be retired.

    ``cases`` is newest first; ``sagas`` maps idempotency_key to its
    classify_saga() state.  Already-retired cases are reported but never
    re-posted; pending ones are re-observed only.
    """
    if outcome not in OUTCOMES:
        raise RetireError(f"outcome must be one of {OUTCOMES}")
    keys = [str(case.idempotency_key) for case in cases]
    live = [k for k in keys if sagas.get(k) != SAGA_RETIRED]
    if outcome == "accepted":
        served = new_case_key if new_case_key in keys else None
        retire = [k for k in live if k != served]
    else:
        # The previously served case cannot be identified reliably, so a
        # compensated release retires ONLY this run's case and leaves every
        # other case (including the served baseline) untouched.
        served = None
        retire = [k for k in live if k == new_case_key]
    return {
        "outcome": outcome,
        "new_case_key": new_case_key,
        "served_case_key": served,
        "retire": retire,
        "already_retired": [k for k in keys if sagas.get(k) == SAGA_RETIRED],
    }


def _identity_ok(saga: Mapping[str, Any], plan_id: str, saga_id: str) -> bool:
    return saga.get("saga_id") == saga_id and saga.get("plan_id") == plan_id


def _read_state(
    transport: Any, plan_id: str, saga_id: str
) -> tuple[str, Mapping[str, Any] | None]:
    saga = transport.get("deployment", _saga_path(saga_id))
    if saga is not None and not _identity_ok(saga, plan_id, saga_id):
        raise RetireError(f"saga readback for {saga_id} has the wrong identity")
    return classify_saga(saga), saga


def retire_case(
    transport: Any,
    *,
    plan_id: str,
    saga_id: str,
    case_key: str,
    run_id: str,
) -> str:
    """Request retirement once; idempotent on replay.  Returns post-request state."""
    state, _ = _read_state(transport, plan_id, saga_id)
    if state in {SAGA_RETIRED, SAGA_PENDING, SAGA_MISSING}:
        return state
    try:
        transport.post(
            "deployment",
            f"{_saga_path(saga_id)}/failure",
            {
                "reason": f"retire dev paper release case {case_key} (run {run_id})",
                "failed_step": RETIRE_FAILED_STEP,
            },
        )
    except Exception:  # response loss is reconciled by the readback below
        pass
    state, _ = _read_state(transport, plan_id, saga_id)
    return state


def execute(
    *,
    store: Any,
    transport_factory: Callable[[str], Any],
    outcome: str,
    new_case_key: str,
    run_id: str,
    dry_run: bool,
    timeout_seconds: float,
    poll_seconds: float = 3.0,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    cases = release_cases(store.list_all())
    by_key = {str(c.idempotency_key): c for c in cases}
    transports: dict[str, Any] = {}

    def transport_for(case: Any) -> Any:
        if case.tenant_id not in transports:
            transports[case.tenant_id] = transport_factory(case.tenant_id)
        return transports[case.tenant_id]

    ids = {key: case_ids(case) for key, case in by_key.items()}
    sagas: dict[str, str] = {}
    errors: dict[str, str] = {}
    for key, case in by_key.items():
        try:
            sagas[key], _ = _read_state(transport_for(case), *ids[key])
        except Exception as exc:  # noqa: BLE001 - recorded, never fatal
            errors[key] = f"{exc.__class__.__name__}: {exc}"
            sagas[key] = SAGA_MISSING
    decision = plan_retirement(
        cases, sagas, outcome=outcome, new_case_key=new_case_key
    )
    # Cases whose state could not be read are never retired or relied upon.
    decision["retire"] = [k for k in decision["retire"] if k not in errors]
    served = decision["served_case_key"]
    assert served not in decision["retire"]
    result: dict[str, Any] = {
        "schema_version": "pantheon.dev-paper-release-case-retire.v1",
        "dry_run": dry_run,
        "run_id": run_id,
        **decision,
        "observed_cases": len(cases),
        "errors": errors,
        "results": {},
    }
    if dry_run:
        result["status"] = "dry_run"
        return result

    results: dict[str, str] = {}
    for key in decision["retire"]:
        if monotonic() >= deadline:
            results[key] = "not_attempted_deadline"
            continue
        plan_id, saga_id = ids[key]
        try:
            results[key] = retire_case(
                transport_for(by_key[key]),
                plan_id=plan_id,
                saga_id=saga_id,
                case_key=key,
                run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001
            errors[key] = f"{exc.__class__.__name__}: {exc}"
            results[key] = "error"
    # Bounded wait for the Deployment outbox consumer to converge bindings.
    while monotonic() < deadline:
        waiting = [k for k, v in results.items() if v == SAGA_PENDING]
        if not waiting:
            break
        sleep(poll_seconds)
        for key in waiting:
            try:
                state, _ = _read_state(transport_for(by_key[key]), *ids[key])
            except Exception as exc:  # noqa: BLE001
                errors[key] = f"{exc.__class__.__name__}: {exc}"
                results[key] = "error"
                continue
            results[key] = state
    result["results"] = results
    unsettled = [k for k, v in results.items() if v != SAGA_RETIRED]
    result["status"] = "retired" if not unsettled and not errors else "partial"
    return result


def _build_runtime() -> tuple[Any, Callable[[str], Any]]:
    from services.control_plane.bff.personas.service import (
        _persona_provisioning_store,
        _PersonaOwnerHttpTransport,
    )

    return (
        _persona_provisioning_store(),
        lambda tenant_id: _PersonaOwnerHttpTransport(tenant_id=tenant_id),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--outcome", choices=OUTCOMES, required=True)
    parser.add_argument(
        "--new-case-key",
        default="",
        help="idempotency key of this run's case; omit for a pre-Root dry-run",
    )
    parser.add_argument("--run-id", default="manual")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    args = parser.parse_args(argv)
    if args.new_case_key and not args.new_case_key.startswith(CASE_KEY_PREFIX):
        parser.error(f"--new-case-key must start with {CASE_KEY_PREFIX!r}")
    if not 0 < args.timeout_seconds <= 600:
        parser.error("--timeout-seconds must be in (0, 600]")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        store, transport_factory = _build_runtime()
        result = execute(
            store=store,
            transport_factory=transport_factory,
            outcome=args.outcome,
            new_case_key=args.new_case_key,
            run_id=args.run_id,
            dry_run=args.dry_run,
            timeout_seconds=args.timeout_seconds,
            poll_seconds=args.poll_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - evidence, not a release failure
        result = {
            "schema_version": "pantheon.dev-paper-release-case-retire.v1",
            "status": "error",
            "dry_run": args.dry_run,
            "run_id": args.run_id,
            "error": f"{exc.__class__.__name__}: {exc}",
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"retired", "dry_run"} else 1


if __name__ == "__main__":
    sys.exit(main())
