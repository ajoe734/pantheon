"""Retire dev paper release probe cases that hosted no longer serves.

Every Root deploy provisions one ``dev-paper-release-<run>-<attempt>`` Persona
through ``bootstrap_dev_paper_baseline.py`` and never retires it, so each old
case keeps an active RuntimeBinding in the paper fleet.  After the release
controller has either accepted or compensated the release, this script moves
every release case that is *not* the served one out of the active fleet
(on a compensated release, only this run's own case).

Retirement uses the Runtime owner's original retire route
(``POST /api/runtime-bindings/{id}/retire``) through the existing
``RuntimeManagerClient`` and its configured service authority.  Bindings are
found by the case's deterministic DeploymentPlan id and are only retired when
their plan, tenant and paper stage match the case.  The script never writes a
database, never adds an endpoint or credential, and never deletes
provisioning, registry or telemetry records.

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
from collections.abc import Callable, Mapping, Sequence
from typing import Any

CASE_KEY_PREFIX = "dev-paper-release-"
OUTCOMES = ("accepted", "compensated")

BINDING_ACTIVE = "active"
BINDING_RETIRED = "retired"
BINDING_MISSING = "missing"
TERMINAL_STATUSES = {"retired", "failed"}


class RetireError(RuntimeError):
    """Raised when retirement inputs are unsafe."""


def case_plan_id(record: Any) -> str:
    """Return the DeploymentPlan id derived exactly as provisioning derives it."""
    from services.control_plane.bff.persona_provisioning_coordinator import (
        deterministic_provisioning_ids,
    )

    return deterministic_provisioning_ids(record).deployment_plan_id


def case_bindings(runtime: Any, record: Any, plan_id: str) -> list[Mapping[str, Any]]:
    """Paper bindings of this case, asserting plan, tenant and stage identity."""
    verified = []
    for binding in runtime.list_by_plan(plan_id):
        tenant = str((binding.get("metadata") or {}).get("tenant_id") or "")
        if binding.get("plan_id") != plan_id:
            raise RetireError(f"binding {binding.get('binding_id')} has the wrong plan")
        if tenant != record.tenant_id:
            raise RetireError(f"binding {binding.get('binding_id')} has the wrong tenant")
        if binding.get("deployment_mode") != "paper":
            raise RetireError(f"binding {binding.get('binding_id')} is not paper")
        verified.append(binding)
    return verified


def classify_bindings(bindings: Sequence[Mapping[str, Any]]) -> str:
    if not bindings:
        return BINDING_MISSING
    if any(b.get("status") not in TERMINAL_STATUSES for b in bindings):
        return BINDING_ACTIVE
    return BINDING_RETIRED


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
    states: Mapping[str, str],
    *,
    outcome: str,
    new_case_key: str,
) -> dict[str, Any]:
    """Pure decision: which cases stay served and which must be retired.

    ``states`` maps idempotency_key to its classify_bindings() state.
    Already-retired cases are reported but never re-posted.
    """
    if outcome not in OUTCOMES:
        raise RetireError(f"outcome must be one of {OUTCOMES}")
    keys = [str(case.idempotency_key) for case in cases]
    live = [k for k in keys if states.get(k) == BINDING_ACTIVE]
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
        "already_retired": [k for k in keys if states.get(k) == BINDING_RETIRED],
    }


def retire_case(runtime: Any, record: Any, plan_id: str) -> str:
    """Retire the case's live bindings once; idempotent on replay."""
    for binding in case_bindings(runtime, record, plan_id):
        if binding.get("status") in TERMINAL_STATUSES:
            continue
        try:
            runtime.retire(str(binding["binding_id"]))
        except Exception:  # response loss is reconciled by the readback below
            pass
    return classify_bindings(case_bindings(runtime, record, plan_id))


def execute(
    *,
    store: Any,
    runtime: Any,
    outcome: str,
    new_case_key: str,
    run_id: str,
    dry_run: bool,
    timeout_seconds: float,
    monotonic: Callable[[], float] | None = None,
) -> dict[str, Any]:
    import time

    monotonic = monotonic or time.monotonic
    deadline = monotonic() + timeout_seconds
    cases = release_cases(store.list_all())
    by_key = {str(c.idempotency_key): c for c in cases}
    plan_ids = {key: case_plan_id(case) for key, case in by_key.items()}
    states: dict[str, str] = {}
    errors: dict[str, str] = {}
    for key, case in by_key.items():
        try:
            states[key] = classify_bindings(case_bindings(runtime, case, plan_ids[key]))
        except Exception as exc:  # noqa: BLE001 - recorded, never fatal
            errors[key] = f"{exc.__class__.__name__}: {exc}"
            states[key] = BINDING_MISSING
    decision = plan_retirement(cases, states, outcome=outcome, new_case_key=new_case_key)
    served = decision["served_case_key"]
    refusal = None
    if outcome == "accepted" and not dry_run:
        # Fail closed before any owner write: the accepted served case must be
        # identified, readable and still active, otherwise every old case
        # would be retired including the one actually serving.
        if served is None:
            refusal = "accepted served case identity is missing or unknown"
        elif served in errors:
            refusal = f"accepted served case {served} state is unreadable"
        elif states.get(served) != BINDING_ACTIVE:
            refusal = (
                f"accepted served case {served} is not active "
                f"(binding state {states.get(served)})"
            )
        if refusal:
            decision["retire"] = []
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
    if refusal:
        result["status"] = "refused"
        result["error"] = refusal
        return result
    if dry_run:
        result["status"] = "dry_run"
        return result

    results: dict[str, str] = {}
    for key in decision["retire"]:
        if monotonic() >= deadline:
            results[key] = "not_attempted_deadline"
            continue
        try:
            results[key] = retire_case(runtime, by_key[key], plan_ids[key])
        except Exception as exc:  # noqa: BLE001
            errors[key] = f"{exc.__class__.__name__}: {exc}"
            results[key] = "error"
    result["results"] = results
    unsettled = [k for k, v in results.items() if v != BINDING_RETIRED]
    result["status"] = "retired" if not unsettled and not errors else "partial"
    return result


def _runtime_client() -> Any:
    # The BFF's existing runtime-manager client: configured URL and service
    # authority, no credential of its own.
    from services.control_plane.bff import command_executor

    return command_executor._runtime_manager_client()


def _build_runtime() -> tuple[Any, Any]:
    from services.control_plane.bff.personas.service import _persona_provisioning_store

    return _persona_provisioning_store(), _runtime_client()


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
    args = parser.parse_args(argv)
    if args.new_case_key and not args.new_case_key.startswith(CASE_KEY_PREFIX):
        parser.error(f"--new-case-key must start with {CASE_KEY_PREFIX!r}")
    if not 0 < args.timeout_seconds <= 600:
        parser.error("--timeout-seconds must be in (0, 600]")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        store, runtime = _build_runtime()
        result = execute(
            store=store,
            runtime=runtime,
            outcome=args.outcome,
            new_case_key=args.new_case_key,
            run_id=args.run_id,
            dry_run=args.dry_run,
            timeout_seconds=args.timeout_seconds,
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
