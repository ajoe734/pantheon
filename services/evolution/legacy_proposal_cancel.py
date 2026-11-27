"""Default-off Evolution maintenance CLI to cancel pre-cutoff legacy proposals.

EVOLUTION-LEGACY-DEFAULT-CANCEL-MAINTENANCE-20261126 (RC2, supporting
operator-authorized EVOLUTION-LEGACY-PROPOSALS-CANCEL-20261110).

On Hosted Dev, historical ``proposed`` EvolutionDecision rows created before the
cutoff (``2026-11-10T03:43:51.369000+00:00``) include ``pantheon-default`` rows
written before ``EVOLUTION-EXISTING-OWNER-READ-WIRING-20261016`` switched the
default tenant to ``tenant-dev``. Because external HTTP requests under
``EVOLUTION_AUTH_MODE=token`` with ``EVOLUTION_AUTH_ALLOWED_TENANTS=tenant-dev``
reject ``X-Tenant-Id: pantheon-default`` at the HTTP middleware boundary and
out-of-band edits to ``decisions.json`` are forbidden, this owner maintenance
CLI runs inside the Evolution container against the owner store and invokes the
existing ``cancel_proposal`` route handler under the owning decision's tenant
context.

Refused unless ``PANTHEON_EVOLUTION_LEGACY_CANCEL_ENABLED=true`` is set for the
invocation::

    python -m services.evolution.legacy_proposal_cancel plan \\
        [--before 2026-11-10T03:43:51.369000+00:00] \\
        [--task-id EVOLUTION-LEGACY-PROPOSALS-CANCEL-20261110]

    python -m services.evolution.legacy_proposal_cancel apply \\
        --digest sha256:... \\
        [--before 2026-11-10T03:43:51.369000+00:00] \\
        [--task-id EVOLUTION-LEGACY-PROPOSALS-CANCEL-20261110]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

ENABLED_ENV = "PANTHEON_EVOLUTION_LEGACY_CANCEL_ENABLED"
DEFAULT_CUTOFF_BEFORE = "2026-11-10T03:43:51.369000+00:00"
DEFAULT_TASK_ID = "EVOLUTION-LEGACY-PROPOSALS-CANCEL-20261110"
SELECTION_SCHEMA = "evolution_legacy_proposal_cancel_selection.v1"
OUTSIDE_SCHEMA = "evolution_legacy_proposal_cancel_outside.v1"


class LegacyProposalCancelError(RuntimeError):
    """Raised when a legacy proposal cancel maintenance run is refused or fails closed."""


def enabled() -> bool:
    return str(os.getenv(ENABLED_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def require_enabled() -> None:
    if not enabled():
        raise LegacyProposalCancelError(
            f"legacy proposal cancel is off by default; set {ENABLED_ENV}=true for this invocation"
        )


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _parse_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(
            raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        )
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _step_field(step: Any, name: str) -> str:
    if isinstance(step, Mapping):
        value = step.get(name)
    else:
        value = getattr(step, name, None)
    return str(_enum_value(value) or "")


def is_canceled_by_task(decision: Any, *, task_id: str) -> bool:
    """Return True when a decision is already canceled with a review step citing task_id."""
    state = str(_enum_value(getattr(decision, "decision_state", ""))).strip().lower()
    if state != "canceled":
        return False
    needle = str(task_id or "").strip()
    if not needle:
        return False
    for step in getattr(decision, "review_chain", None) or []:
        step_type = _step_field(step, "step_type").strip().lower()
        if step_type not in {"canceled", "cancel"}:
            continue
        note = _step_field(step, "note")
        actor_id = _step_field(step, "actor_id")
        if needle in note or needle in actor_id:
            return True
    return False


def is_legacy_candidate(
    decision: Any,
    *,
    cutoff: datetime,
    task_id: str,
) -> bool:
    """Select pre-cutoff decisions that are proposed or already canceled under task_id."""
    created_at = _parse_utc(getattr(decision, "created_at", None))
    if created_at is None or created_at > cutoff:
        return False
    state = str(_enum_value(getattr(decision, "decision_state", ""))).strip().lower()
    if state == "proposed":
        return True
    return is_canceled_by_task(decision, task_id=task_id)


def _decision_summary_row(decision: Any) -> dict[str, Any]:
    return {
        "decision_id": str(decision.decision_id),
        "tenant_id": str(decision.tenant_id),
        "target_type": str(_enum_value(decision.target_type)),
        "target_id": str(decision.target_id),
        "action_type": str(_enum_value(decision.action_type)),
        "decision_state": str(_enum_value(decision.decision_state)),
        "created_at": str(decision.created_at),
    }


def compute_selection_digest(
    decisions: Iterable[Any],
    *,
    cutoff_before: str,
    task_id: str,
) -> str:
    """Compute a stable SHA-256 digest over immutable fields of selected legacy decisions."""
    immutable_rows = sorted(
        (
            {
                "decision_id": str(d.decision_id),
                "tenant_id": str(d.tenant_id),
                "target_type": str(_enum_value(d.target_type)),
                "target_id": str(d.target_id),
                "action_type": str(_enum_value(d.action_type)),
                "created_at": str(d.created_at),
            }
            for d in decisions
        ),
        key=lambda item: item["decision_id"],
    )
    canonical = json.dumps(
        {
            "schema": SELECTION_SCHEMA,
            "task_id": task_id,
            "cutoff_before": cutoff_before,
            "decisions": immutable_rows,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_outside_digest(decisions: Iterable[Any]) -> str:
    """Compute a SHA-256 digest over (decision_id, tenant_id, decision_state, len(review_chain)) of outside rows."""
    rows = sorted(
        (
            [
                str(d.decision_id),
                str(d.tenant_id),
                str(_enum_value(d.decision_state)),
                len(getattr(d, "review_chain", None) or []),
            ]
            for d in decisions
        ),
        key=lambda item: (item[0], item[1]),
    )
    canonical = json.dumps(
        {
            "schema": OUTSIDE_SCHEMA,
            "rows": rows,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class LegacyCancelPlan:
    cutoff_before: str
    task_id: str
    scanned_count: int
    decisions: list[dict[str, Any]]
    counts_by_tenant: dict[str, int]
    counts_by_state: dict[str, int]
    counts_by_tenant_and_state: dict[str, dict[str, int]]
    outside_count: int
    outside_counts_by_tenant_and_state: dict[str, dict[str, int]]
    selection_digest: str
    outside_digest: str
    selected_ids: list[str] = field(default_factory=list)

    @property
    def selected_count(self) -> int:
        return len(self.decisions)

    @property
    def to_cancel_count(self) -> int:
        return sum(
            1
            for row in self.decisions
            if str(row.get("decision_state") or "").strip().lower() == "proposed"
        )

    @property
    def already_canceled_count(self) -> int:
        return sum(
            1
            for row in self.decisions
            if str(row.get("decision_state") or "").strip().lower() == "canceled"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SELECTION_SCHEMA,
            "task_id": self.task_id,
            "cutoff_before": self.cutoff_before,
            "scanned_count": self.scanned_count,
            "selected_count": self.selected_count,
            "to_cancel_count": self.to_cancel_count,
            "already_canceled_count": self.already_canceled_count,
            "outside_count": self.outside_count,
            "counts_by_tenant": dict(sorted(self.counts_by_tenant.items())),
            "counts_by_state": dict(sorted(self.counts_by_state.items())),
            "counts_by_tenant_and_state": {
                tenant: dict(sorted(states.items()))
                for tenant, states in sorted(self.counts_by_tenant_and_state.items())
            },
            "outside_counts_by_tenant_and_state": {
                tenant: dict(sorted(states.items()))
                for tenant, states in sorted(self.outside_counts_by_tenant_and_state.items())
            },
            "selection_digest": self.selection_digest,
            "outside_digest": self.outside_digest,
            "selected_ids": list(self.selected_ids),
            "decisions": list(self.decisions),
        }


def build_plan(
    store: Any = None,
    *,
    before: str = DEFAULT_CUTOFF_BEFORE,
    task_id: str = DEFAULT_TASK_ID,
) -> LegacyCancelPlan:
    """Read-only plan across all tenants in the Evolution owner store."""
    require_enabled()
    cutoff = _parse_utc(before)
    if cutoff is None:
        raise LegacyProposalCancelError(
            f"before cutoff must be a valid ISO-8601 / RFC3339 timestamp: {before!r}"
        )
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        raise LegacyProposalCancelError("task_id must not be empty")

    if store is None:
        from services.evolution import main as evo_main

        store = evo_main.store

    all_decisions = list(store.list_all(tenant_id=None))
    selected_objects: list[Any] = []
    outside_objects: list[Any] = []

    for decision in all_decisions:
        if is_legacy_candidate(decision, cutoff=cutoff, task_id=normalized_task_id):
            selected_objects.append(decision)
        else:
            outside_objects.append(decision)

    selected_objects.sort(key=lambda d: str(d.decision_id))
    decision_rows = [_decision_summary_row(d) for d in selected_objects]
    selected_ids = [row["decision_id"] for row in decision_rows]

    counts_by_tenant: dict[str, int] = {}
    counts_by_state: dict[str, int] = {}
    counts_by_tenant_and_state: dict[str, dict[str, int]] = {}
    for row in decision_rows:
        tenant = row["tenant_id"]
        state = row["decision_state"]
        counts_by_tenant[tenant] = counts_by_tenant.get(tenant, 0) + 1
        counts_by_state[state] = counts_by_state.get(state, 0) + 1
        tenant_bucket = counts_by_tenant_and_state.setdefault(tenant, {})
        tenant_bucket[state] = tenant_bucket.get(state, 0) + 1

    outside_counts_by_tenant_and_state: dict[str, dict[str, int]] = {}
    for decision in outside_objects:
        tenant = str(decision.tenant_id)
        state = str(_enum_value(decision.decision_state))
        tenant_bucket = outside_counts_by_tenant_and_state.setdefault(tenant, {})
        tenant_bucket[state] = tenant_bucket.get(state, 0) + 1

    sel_digest = compute_selection_digest(
        selected_objects,
        cutoff_before=before,
        task_id=normalized_task_id,
    )
    out_digest = compute_outside_digest(outside_objects)

    return LegacyCancelPlan(
        cutoff_before=before,
        task_id=normalized_task_id,
        scanned_count=len(all_decisions),
        decisions=decision_rows,
        counts_by_tenant=counts_by_tenant,
        counts_by_state=counts_by_state,
        counts_by_tenant_and_state=counts_by_tenant_and_state,
        outside_count=len(outside_objects),
        outside_counts_by_tenant_and_state=outside_counts_by_tenant_and_state,
        selection_digest=sel_digest,
        outside_digest=out_digest,
        selected_ids=selected_ids,
    )


def apply_plan(
    store: Any = None,
    *,
    expected_digest: str,
    before: str = DEFAULT_CUTOFF_BEFORE,
    task_id: str = DEFAULT_TASK_ID,
) -> dict[str, Any]:
    """Cancel selected pre-cutoff proposed decisions through cancel_proposal after verifying digest."""
    require_enabled()
    from services.evolution import main as evo_main
    from services.evolution.models import CancelRequest

    target_store = store if store is not None else evo_main.store
    plan = build_plan(target_store, before=before, task_id=task_id)
    if plan.selection_digest != expected_digest:
        raise LegacyProposalCancelError(
            f"selection digest mismatch: planned {plan.selection_digest}, given {expected_digest}; "
            "re-run plan and verify the selection before applying"
        )

    initial_outside_digest = plan.outside_digest
    actor_id = f"operator-maintenance:{plan.task_id}"
    note = f"Cancelled legacy proposed decision per {plan.task_id}"

    receipts: list[dict[str, Any]] = []
    skipped_already_canceled = 0

    original_store = evo_main.store
    evo_main.store = target_store
    try:
        for row in plan.decisions:
            decision_id = row["decision_id"]
            tenant_id = row["tenant_id"]
            state = str(row.get("decision_state") or "").strip().lower()
            if state == "canceled":
                skipped_already_canceled += 1
                continue
            token = evo_main._TENANT_CONTEXT.set(tenant_id)
            try:
                response = evo_main.cancel_proposal(
                    decision_id,
                    CancelRequest(
                        actor_role="operator",
                        actor_id=actor_id,
                        note=note,
                        tenant_id=tenant_id,
                    ),
                )
            finally:
                evo_main._TENANT_CONTEXT.reset(token)

            receipt = (
                response.model_dump(mode="json")
                if hasattr(response, "model_dump")
                else response.dict()
            )
            receipts.append(receipt)
    finally:
        evo_main.store = original_store

    post_plan = build_plan(target_store, before=before, task_id=task_id)
    if post_plan.to_cancel_count != 0:
        raise LegacyProposalCancelError(
            f"post-apply verification failed: {post_plan.to_cancel_count} legacy decisions remain proposed"
        )
    if post_plan.outside_digest != initial_outside_digest:
        raise LegacyProposalCancelError(
            "post-apply verification failed: outside_digest changed from "
            f"{initial_outside_digest} to {post_plan.outside_digest}"
        )
    if post_plan.selection_digest != expected_digest:
        raise LegacyProposalCancelError(
            "post-apply verification failed: selection_digest changed from "
            f"{expected_digest} to {post_plan.selection_digest}"
        )

    return {
        **post_plan.to_dict(),
        "canceled_count": len(receipts),
        "skipped_already_canceled_count": skipped_already_canceled,
        "remaining_legacy_proposed_count": post_plan.to_cancel_count,
        "pre_outside_digest": initial_outside_digest,
        "post_outside_digest": post_plan.outside_digest,
        "outside_digest_unchanged": post_plan.outside_digest == initial_outside_digest,
        "receipts": receipts,
        "complete": True,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m services.evolution.legacy_proposal_cancel"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    plan_cmd = sub.add_parser("plan")
    plan_cmd.add_argument("--before", default=DEFAULT_CUTOFF_BEFORE)
    plan_cmd.add_argument("--task-id", default=DEFAULT_TASK_ID)

    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--digest", required=True)
    apply_cmd.add_argument("--before", default=DEFAULT_CUTOFF_BEFORE)
    apply_cmd.add_argument("--task-id", default=DEFAULT_TASK_ID)

    args = parser.parse_args(argv)

    try:
        require_enabled()
        if args.command == "plan":
            out = build_plan(before=args.before, task_id=args.task_id).to_dict()
        else:
            out = apply_plan(
                expected_digest=args.digest,
                before=args.before,
                task_id=args.task_id,
            )
    except LegacyProposalCancelError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2

    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
