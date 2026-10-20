#!/usr/bin/env python3
"""Retire dev paper release probe cases through the provisioning coordinator.

Every Root deploy provisions one ``dev-paper-release-<run>-<attempt>`` Persona
through ``bootstrap_dev_paper_baseline.py``.  A release case is one aggregate
(Persona, PCB, capital pool, first-evaluation cron, Deployment saga,
RuntimeBindings, ledger row) and only the BFF provisioning coordinator knows
the whole inventory.  This script therefore asks the coordinator to retire each
selected case (``retire_persona_provisioning_case`` ->
``PersonaProvisioningCoordinator.retire_case``) and never posts saga retire
requests, classifies sagas, writes a database or deletes records itself.

A case that does not converge stays ``pending`` and is reported as evidence;
the running image having no ``retire_case`` is reported as
``retire_case_unavailable`` without any fallback.

Run inside the dev operator-bff container (stdin keeps it independent of the
checked-out image after a compensation)::

    docker exec -i pantheon-operator-bff-1 python - --dry-run --outcome accepted < this_file

Scope (the served case is never inferred from recency):
* ``accepted``    -> every release case except this run's case.
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
SCHEMA_VERSION = "pantheon.dev-paper-release-case-retire.v2"
RETIRABLE_CASE_STATES = frozenset(
    {"reserved", "provisioning", "succeeded", "failed", "compensated"}
)
RETIRED_STATE = "retired"
SERVED_CASE_STATE = "succeeded"

CASE_RETIRED = "retired"
CASE_PENDING = "pending"


class RetireError(RuntimeError):
    """Raised when retirement inputs are unsafe."""


class RetireCaseUnavailable(RuntimeError):
    """The running image has no provisioning-coordinator retire_case."""


def retire_reason(case_key: str) -> str:
    # Stable across replays: the coordinator keeps the first recorded reason.
    return f"retire dev paper release case {case_key}"


def release_cases(records: Sequence[Any]) -> list[Any]:
    """Release probe cases the coordinator may retire, in a stable order.

    Covers every case that was reserved or provisioned (including ones stuck in
    ``reserved``/``provisioning`` and contained failures) plus already
    ``retired`` ones, which are reported but never touched again.
    """
    cases = [
        record
        for record in records
        if str(record.idempotency_key).startswith(CASE_KEY_PREFIX)
        and record.state in RETIRABLE_CASE_STATES | {RETIRED_STATE}
    ]
    return sorted(cases, key=lambda r: (str(r.created_at), str(r.idempotency_key)))


def plan_retirement(
    cases: Sequence[Any], *, outcome: str, new_case_key: str
) -> dict[str, Any]:
    """Pure decision: which cases to retire.

    accepted    -> every case except ``new_case_key``.
    compensated -> only ``new_case_key``; nothing else, ever.
    """
    if outcome not in OUTCOMES:
        raise RetireError(f"outcome must be one of {OUTCOMES}")
    if outcome == "accepted":
        scope = [c for c in cases if str(c.idempotency_key) != new_case_key]
    else:
        scope = [
            c for c in cases if new_case_key and str(c.idempotency_key) == new_case_key
        ]
    return {
        "outcome": outcome,
        "new_case_key": new_case_key,
        "retire": [str(c.idempotency_key) for c in scope if c.state != RETIRED_STATE],
        "observe": [],
        "already_retired": [
            str(c.idempotency_key) for c in scope if c.state == RETIRED_STATE
        ],
        "skipped": [],
    }


def served_refusal(outcome: str, new_case_key: str, cases: Sequence[Any]) -> str:
    """Why accepted-release retirement writes must be refused ('' when safe).

    The served case is this run's case; it must exist as a succeeded release
    case so that no other case is retired while the served one is unknown.
    """
    if outcome != "accepted":
        return ""
    if not new_case_key:
        return "served case key missing"
    served = [c for c in cases if str(c.idempotency_key) == new_case_key]
    if not served:
        return "served case unknown"
    if served[0].state != SERVED_CASE_STATE:
        return f"served case not succeeded: {served[0].state}"
    return ""


def _summarize(summary: Any) -> str:
    """Map a coordinator retirement summary to retired/pending."""
    if not isinstance(summary, Mapping):
        return CASE_PENDING
    retirement = summary.get("retirement")
    status = retirement.get("status") if isinstance(retirement, Mapping) else ""
    if summary.get("state") == RETIRED_STATE and status == RETIRED_STATE:
        return CASE_RETIRED
    return CASE_PENDING


def execute(
    *,
    store: Any,
    retire_case: Callable[..., Any],
    outcome: str,
    new_case_key: str,
    run_id: str,
    dry_run: bool,
    timeout_seconds: float,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    cases = release_cases(store.list_all())
    by_key = {str(c.idempotency_key): c for c in cases}
    decision = plan_retirement(cases, outcome=outcome, new_case_key=new_case_key)
    case_states = {
        k: by_key[k].state for k in [*decision["retire"], *decision["already_retired"]]
    }
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "dry_run": dry_run,
        "run_id": run_id,
        **decision,
        "observed_cases": len(cases),
        "case_states": case_states,
        "errors": {},
        "results": {},
    }
    errors: dict[str, str] = result["errors"]
    refusal = served_refusal(outcome, new_case_key, cases)
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
        case = by_key[key]
        try:
            summary = retire_case(
                str(case.tenant_id), str(case.persona_id), reason=retire_reason(key)
            )
        except RetireCaseUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, never fatal
            errors[key] = f"{exc.__class__.__name__}: {exc}"
            results[key] = "error"
            continue
        results[key] = _summarize(summary)
    result["results"] = results
    unsettled = [k for k, v in results.items() if v != CASE_RETIRED]
    result["status"] = "retired" if not unsettled and not errors else "partial"
    return result


def _build_runtime() -> tuple[Any, Callable[..., Any]]:
    from services.control_plane.bff.personas import service

    retire = getattr(service, "retire_persona_provisioning_case", None)
    if not callable(retire):
        raise RetireCaseUnavailable(
            "running image has no provisioning-coordinator retire_case"
        )
    # Importing the BFF main composes the existing app (app_factory is the
    # sole bind of PersonaService/write owner) so the coordinator can confirm
    # the final Persona archive.
    import importlib

    importlib.import_module("services.control_plane.bff.main")
    if getattr(service, "_composed_persona_service", None) is None:
        raise RuntimeError("persona service was not composed by the app factory")
    return service._persona_provisioning_store(), retire


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
    parser.add_argument(
        "--poll-seconds", type=float, default=3.0, help="accepted for compatibility"
    )
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
        store, retire_case = _build_runtime()
        result = execute(
            store=store,
            retire_case=retire_case,
            outcome=args.outcome,
            new_case_key=args.new_case_key,
            run_id=args.run_id,
            dry_run=args.dry_run,
            timeout_seconds=args.timeout_seconds,
        )
    except RetireCaseUnavailable as exc:
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "retire_case_unavailable",
            "dry_run": args.dry_run,
            "run_id": args.run_id,
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001 - evidence, not a release failure
        result = {
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "dry_run": args.dry_run,
            "run_id": args.run_id,
            "error": f"{exc.__class__.__name__}: {exc}",
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"retired", "dry_run"} else 1


if __name__ == "__main__":
    sys.exit(main())
