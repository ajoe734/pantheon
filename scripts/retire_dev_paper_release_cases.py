#!/usr/bin/env python3
"""Retire dev paper release probe cases through the Deployment retire action.

Every Root deploy provisions one ``dev-paper-release-<run>-<attempt>`` Persona
through ``bootstrap_dev_paper_baseline.py`` and never retires it, so each old
case keeps an active RuntimeBinding in the paper fleet.  This script moves
release cases out of the active fleet once the release outcome is known.

Retirement uses only the Deployment owner's retire action
(``POST /api/deployment/sagas/{id}/retire``) with the deploy's existing service
identity.  Deployment's outbox consumer retires the exact RuntimeBinding and
finalizes the saga as ``retired``.  The script never uses a saga failure route,
the runtime-manager operator/MFA routes, a database write, or record deletion.

Run inside the dev operator-bff container (stdin keeps it independent of the
checked-out image after a compensation)::

    docker exec -i pantheon-operator-bff-1 python - --dry-run --outcome accepted < this_file

Scope (the served case is never inferred from recency):
* ``accepted``    -> every active release case except this run's case.
* ``compensated`` -> only this run's case; no other case is touched.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

CASE_KEY_PREFIX = "dev-paper-release-"
OUTCOMES = ("accepted", "compensated")
RETIRABLE_CASE_STATES = frozenset({"succeeded", "failed", "compensated", "provisioning"})

SAGA_ACTIVE = "active"  # completed or failed-and-compensated: can be retired
SAGA_PENDING = "pending"  # retiring: waiting for the outbox consumer
SAGA_RETIRED = "retired"
SAGA_NOT_RETIRABLE = "not_retirable"  # failed/compensated/in flight
SAGA_MISSING = "missing"


class RetireError(RuntimeError):
    """Raised when retirement inputs are unsafe."""


def _saga_path(saga_id: str) -> str:
    return f"/api/deployment/sagas/{saga_id}"


def retire_reason(case_key: str) -> str:
    # Stable across replays: the owner rejects a different reason on a saga
    # that is already retiring or retired.
    return f"retire dev paper release case {case_key}"


def classify_saga(saga: Mapping[str, Any] | None) -> str:
    if saga is None:
        return SAGA_MISSING
    status = str(saga.get("status") or "")
    if status == "retired":
        return SAGA_RETIRED
    if status == "retiring":
        return SAGA_PENDING
    if status == "completed":
        return SAGA_ACTIVE
    if status == "failed" and str(saga.get("current_step") or "") == "compensated":
        return SAGA_ACTIVE  # contained failure: binding paused, compensation done
    return SAGA_NOT_RETIRABLE


def case_ids(record: Any) -> tuple[str, str]:
    """Return (plan_id, saga_id) derived exactly as provisioning derives them."""
    from services.control_plane.bff.persona_provisioning_coordinator import (
        deterministic_provisioning_ids,
    )

    ids = deterministic_provisioning_ids(record)
    return ids.deployment_plan_id, ids.deployment_saga_id


def release_cases(records: Sequence[Any]) -> list[Any]:
    """Release probe cases that may hold a retirable saga, in a stable order.

    Besides ``succeeded`` this covers contained failures (``failed`` /
    ``compensated``) and cases stuck in ``provisioning``; the saga readback,
    not the case state, decides retirability.
    """
    cases = [
        record
        for record in records
        if str(record.idempotency_key).startswith(CASE_KEY_PREFIX)
        and record.state in RETIRABLE_CASE_STATES
    ]
    return sorted(cases, key=lambda r: (str(r.created_at), str(r.idempotency_key)))


def plan_retirement(
    cases: Sequence[Any],
    sagas: Mapping[str, str],
    *,
    outcome: str,
    new_case_key: str,
) -> dict[str, Any]:
    """Pure decision: which cases to retire.

    accepted    -> every case except ``new_case_key``.
    compensated -> only ``new_case_key``; nothing else, ever.
    Only cases whose saga is still ``active`` are requested; pending ones are
    re-observed, retired and non-retirable ones are reported only.
    """
    if outcome not in OUTCOMES:
        raise RetireError(f"outcome must be one of {OUTCOMES}")
    keys = [str(case.idempotency_key) for case in cases]
    if outcome == "accepted":
        scope = [k for k in keys if k != new_case_key]
    else:
        scope = [k for k in keys if new_case_key and k == new_case_key]
    return {
        "outcome": outcome,
        "new_case_key": new_case_key,
        "retire": [k for k in scope if sagas.get(k) == SAGA_ACTIVE],
        "observe": [k for k in scope if sagas.get(k) == SAGA_PENDING],
        "already_retired": [k for k in scope if sagas.get(k) == SAGA_RETIRED],
        "skipped": [
            k for k in scope if sagas.get(k) in {SAGA_NOT_RETIRABLE, SAGA_MISSING}
        ],
    }


def _identity_ok(saga: Mapping[str, Any], plan_id: str, saga_id: str) -> bool:
    return saga.get("saga_id") == saga_id and saga.get("plan_id") == plan_id


def _read_state(transport: Any, plan_id: str, saga_id: str) -> str:
    saga = transport.get("deployment", _saga_path(saga_id))
    if saga is not None and not _identity_ok(saga, plan_id, saga_id):
        raise RetireError(f"saga readback for {saga_id} has the wrong identity")
    return classify_saga(saga)


def retire_case(
    transport: Any, *, plan_id: str, saga_id: str, case_key: str
) -> str:
    """Request retirement once; idempotent on replay.  Returns post-request state."""
    state = _read_state(transport, plan_id, saga_id)
    if state != SAGA_ACTIVE:
        return state
    try:
        transport.post(
            "deployment",
            f"{_saga_path(saga_id)}/retire",
            {"reason": retire_reason(case_key)},
        )
    except Exception:  # response loss is reconciled by the readback below
        pass
    return _read_state(transport, plan_id, saga_id)


def served_refusal(
    outcome: str, new_case_key: str, sagas: Mapping[str, str], errors: Mapping[str, str]
) -> str:
    """Why accepted-release retirement writes must be refused ('' when safe).

    The served case is this run's case; it must exist as a succeeded release
    case whose Saga is authoritatively readable and still active/completed.
    """
    if outcome != "accepted":
        return ""
    if not new_case_key:
        return "served case key missing"
    if new_case_key not in sagas:
        return "served case unknown"
    if new_case_key in errors:
        return "served saga readback unavailable"
    if sagas[new_case_key] != SAGA_ACTIVE:
        return f"served saga not active: {sagas[new_case_key]}"
    return ""


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
            sagas[key] = _read_state(transport_for(case), *ids[key])
        except Exception as exc:  # noqa: BLE001 - recorded, never fatal
            errors[key] = f"{exc.__class__.__name__}: {exc}"
            sagas[key] = SAGA_MISSING
    decision = plan_retirement(
        cases, sagas, outcome=outcome, new_case_key=new_case_key
    )
    result: dict[str, Any] = {
        "schema_version": "pantheon.dev-paper-release-case-retire.v2",
        "dry_run": dry_run,
        "run_id": run_id,
        **decision,
        "observed_cases": len(cases),
        "case_states": {
            k: sagas[k]
            for k in [*decision["retire"], *decision["observe"],
                      *decision["already_retired"], *decision["skipped"]]
        },
        "errors": errors,
        "results": {},
    }
    refusal = served_refusal(outcome, new_case_key, sagas, errors)
    result["executable"] = not refusal
    result["refusal"] = refusal
    if dry_run:
        result["status"] = "dry_run"
        return result
    if refusal:
        result["status"] = "refused"
        return result

    results: dict[str, str] = {}
    for key in decision["retire"]:
        if monotonic() >= deadline:
            results[key] = "not_attempted_deadline"
            continue
        try:
            results[key] = retire_case(
                transport_for(by_key[key]),
                plan_id=ids[key][0],
                saga_id=ids[key][1],
                case_key=key,
            )
        except Exception as exc:  # noqa: BLE001
            errors[key] = f"{exc.__class__.__name__}: {exc}"
            results[key] = "error"
    for key in decision["observe"]:
        results[key] = SAGA_PENDING
    # Bounded wait for the Deployment outbox consumer to finalize retirement.
    while monotonic() < deadline:
        waiting = [k for k, v in results.items() if v == SAGA_PENDING]
        if not waiting:
            break
        sleep(poll_seconds)
        for key in waiting:
            try:
                results[key] = _read_state(
                    transport_for(by_key[key]), *ids[key]
                )
            except Exception as exc:  # noqa: BLE001
                errors[key] = f"{exc.__class__.__name__}: {exc}"
                results[key] = "error"
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
    if args.outcome == "compensated" and not args.new_case_key and not args.dry_run:
        parser.error("--new-case-key is required to retire on a compensated release")
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
            "schema_version": "pantheon.dev-paper-release-case-retire.v2",
            "status": "error",
            "dry_run": args.dry_run,
            "run_id": args.run_id,
            "error": f"{exc.__class__.__name__}: {exc}",
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"retired", "dry_run"} else 1


if __name__ == "__main__":
    sys.exit(main())
