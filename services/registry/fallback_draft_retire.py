"""Retire the one-article fallback StrategySpec drafts and keep the record.

STRATEGY-FALLBACK-DRAFT-RETIRE-20261125 (RC2). Operator decision 2026-11-25
(conversation 2dc972b7): the drafts that distillation wrote since 2026-11-12
from one article each, with the fallback hypothesis ``Evidence suggests:
<first sentence>`` and no hypothesis card, are retired. Nothing is deleted:
a retired entry stays readable by id and is not listed as a draft.

This is a Registry-owner maintenance path, off by default. It runs inside the
Registry container against the selected owner store and is refused unless
``PANTHEON_REGISTRY_FALLBACK_DRAFT_RETIRE_ENABLED=true`` is set for the one
invocation::

    python -m services.registry.fallback_draft_retire plan
    python -m services.registry.fallback_draft_retire apply --digest sha256:... [--batch-size 200]
    python -m services.registry.fallback_draft_retire restore --batch-id <id>

Selection (all must hold, otherwise the entry is excluded and counted by reason):

* ``artifact_type == strategy_spec`` and ``artifact_state == draft`` (or
  already ``retired`` by this exact decision, so a partly applied run keeps the
  same digest);
* ``metadata.distillation`` is present (the source-to-draft path);
* ``metadata.strategy_spec.hypothesis`` is the fallback form;
* created at or after ``2026-11-12T00:00:00Z`` (the earlier market-seed drafts
  are never selected);
* no hypothesis card, compiled rule, card wiring, strategy artifact or
  backtest binding anywhere on the entry;
* no admission, gate verdict, replication proof, approval, deployment summary,
  deployment stage, persona or capital-pool binding on the entry, and only the
  metadata / evaluation-summary keys the distillation path itself writes;
* no other Registry entry refers to its registry_id or strategy_id (parents,
  execution bundles, candidates, pools and personas recorded in Registry);
* not listed in the optional external-reference file (ids referred to by
  owners outside Registry: research demand, persona runtime bindings,
  capital pools, deployments).

The retire write goes through :meth:`RegistryService.advance_artifact_state`
(the existing transition path: CAS on the read snapshot plus an idempotent
command receipt). The actor recorded on the entry (``last_actor``) and in the
receipt carries the reason, the operator-decision reference, the selection
digest and the batch id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

from .models import ArtifactState, RegistryEntry

ENABLED_ENV = "PANTHEON_REGISTRY_FALLBACK_DRAFT_RETIRE_ENABLED"
FALLBACK_HYPOTHESIS_PREFIX = "Evidence suggests: "
DEFAULT_CREATED_FROM = "2026-11-12T00:00:00Z"
OPERATOR_DECISION_REF = (
    "operator-decision:2026-11-25:conversation-2dc972b7:"
    "retire-fallback-drafts-keep-record"
)
RETIRE_REASON = (
    "fallback_hypothesis_without_card: one-article distillation draft with the "
    "fallback hypothesis and no hypothesis card (SD_STRATEGY_DISCOVERY_SOURCES "
    "section 8); retired and kept by operator decision 2026-11-25"
)
ACTOR_ID = "registry-maintenance:fallback-draft-retire"
RESTORE_ACTOR_ID = "registry-maintenance:fallback-draft-restore"
DEFAULT_BATCH_SIZE = 200
MAX_BATCH_SIZE = 500
SELECTION_SCHEMA = "fallback_draft_retire_selection.v1"

# Top-level metadata keys the distillation path writes on a fallback draft
# (StrategySpecConversionService registry payload + controller additions).
_DISTILLATION_METADATA_KEYS = frozenset({
    "conversion_service", "conversion_version", "source_seed_id",
    "evidence_bundle_id", "research_only", "registry_payload_built",
    "registry_write_performed", "execution_route", "strategy_spec",
    "tenant_id", "distillation",
})
_DISTILLATION_EVALUATION_KEYS = frozenset({
    "source_seed_id", "evidence_bundle_id", "confidence", "candidate_gate",
})
# Keys inside strategy_spec.metadata that mark a card, a bound rule or an
# owner binding; any one of them excludes the entry.
_CARD_OR_BINDING_KEYS = frozenset({
    "hypothesis_card", "hypothesis_predictions", "card_wiring", "factor_rule_v1",
    "strategy_artifact", "strategy_artifact_checksum", "backtest_dataset",
    "persona_id", "persona_ids", "personaIds", "capital_pool_id", "capitalPoolId",
    "admission", "admission_status", "gate_verdict", "replication_proof",
})


class FallbackDraftRetireError(RuntimeError):
    """A refused or stopped maintenance run; the message is the readable reason."""


def enabled() -> bool:
    return str(os.getenv(ENABLED_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def require_enabled() -> None:
    if not enabled():
        raise FallbackDraftRetireError(
            f"fallback draft retire is off by default; set {ENABLED_ENV}=true for this invocation"
        )


def _parse_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _state(entry: Mapping[str, Any]) -> str:
    return str(entry.get("artifact_state") or "").strip().lower()


def retired_by_this_decision(entry: Mapping[str, Any]) -> bool:
    actor = _dict(entry.get("last_actor"))
    return (
        _state(entry) == "retired"
        and actor.get("actor_id") == ACTOR_ID
        and actor.get("operator_decision_ref") == OPERATOR_DECISION_REF
    )


def _walk_strings(value: Any) -> Iterable[str]:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, Mapping):
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)


def exclusion_reason(
    entry: Mapping[str, Any],
    *,
    created_from: datetime,
    referenced: frozenset[str] | set[str] = frozenset(),
) -> Optional[str]:
    """``None`` when the entry is a retirable fallback draft, else the first exclusion reason."""
    if str(entry.get("artifact_type") or "") != "strategy_spec":
        return "not_strategy_spec"
    state = _state(entry)
    if state != "draft" and not retired_by_this_decision(entry):
        return f"state_{state or 'unknown'}"
    meta = _dict(entry.get("metadata"))
    distillation = _dict(meta.get("distillation"))
    if not distillation.get("source_id"):
        return "not_distillation"
    spec = _dict(meta.get("strategy_spec"))
    hypothesis = spec.get("hypothesis")
    if not isinstance(hypothesis, str) or not hypothesis.startswith(FALLBACK_HYPOTHESIS_PREFIX):
        return "hypothesis_not_fallback"
    created = _parse_utc(entry.get("created_at")) or _parse_utc(
        _dict(spec.get("provenance")).get("created_at")
    )
    if created is None:
        return "created_at_unknown"
    if created < created_from:
        return "created_before_cutoff"
    spec_meta = _dict(spec.get("metadata"))
    if _CARD_OR_BINDING_KEYS & (set(spec_meta) | set(meta)):
        return "card_or_binding_present"
    if spec.get("persona_ids") or spec.get("capital_pool_id") or spec.get("capitalPoolId"):
        return "card_or_binding_present"
    if set(meta) - _DISTILLATION_METADATA_KEYS:
        return "unexpected_metadata"
    if set(_dict(entry.get("evaluation_summary"))) - _DISTILLATION_EVALUATION_KEYS:
        return "unexpected_evaluation_summary"
    if (
        entry.get("approval_decision_id") or entry.get("approval_evidence") or entry.get("approver")
        or entry.get("approved_at") or entry.get("replication_success") is not None
        or entry.get("replication_proof")
    ):
        return "approval_or_admission_present"
    summary = _dict(entry.get("deployment_summary"))
    if summary and any(v not in (None, "", "none", [], {}) for v in summary.values()):
        return "deployment_present"
    if entry.get("registry_id") in referenced or entry.get("strategy_id") in referenced:
        return "referenced"
    return None


def selection_digest(registry_ids: Iterable[str]) -> str:
    canonical = json.dumps(
        {"schema": SELECTION_SCHEMA, "decision": OPERATOR_DECISION_REF, "ids": sorted(registry_ids)},
        sort_keys=True, separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def batch_id_for(digest: str, index: int) -> str:
    return f"fdr-{digest.removeprefix('sha256:')[:12]}-{index:04d}"


def _source_key(entry: Mapping[str, Any]) -> str:
    spec = _dict(_dict(entry.get("metadata")).get("strategy_spec"))
    return str(_dict(spec.get("provenance")).get("source_kind") or "unknown")


@dataclass
class RetirePlan:
    created_from: str
    selected_ids: list[str]
    digest: str
    by_source: dict[str, int]
    already_retired: int
    excluded: dict[str, int]
    external_reference_count: int
    scanned: int
    entries: dict[str, dict] = field(default_factory=dict, repr=False)

    def summary(self, batch_size: int = DEFAULT_BATCH_SIZE) -> dict[str, Any]:
        size = max(1, int(batch_size))
        return {
            "schema": SELECTION_SCHEMA,
            "operator_decision_ref": OPERATOR_DECISION_REF,
            "created_from": self.created_from,
            "scanned": self.scanned,
            "selected_count": len(self.selected_ids),
            "to_retire_count": len(self.selected_ids) - self.already_retired,
            "already_retired_count": self.already_retired,
            "by_source": dict(sorted(self.by_source.items())),
            "excluded_by_reason": dict(sorted(self.excluded.items())),
            "external_reference_count": self.external_reference_count,
            "selection_digest": self.digest,
            "batch_size": size,
            "batch_count": (len(self.selected_ids) + size - 1) // size,
            "first_id": self.selected_ids[0] if self.selected_ids else None,
            "last_id": self.selected_ids[-1] if self.selected_ids else None,
        }


def build_plan(
    store: Any,
    *,
    created_from: str = DEFAULT_CREATED_FROM,
    external_references: Iterable[str] = (),
) -> RetirePlan:
    """Read-only selection over every owner entry. Changes nothing."""
    cutoff = _parse_utc(created_from)
    if cutoff is None:
        raise FallbackDraftRetireError(f"created_from must be an RFC3339 UTC time: {created_from!r}")
    external = {str(v).strip() for v in external_references if str(v).strip()}
    rows = [e.to_dict() if isinstance(e, RegistryEntry) else dict(e) for e in store.list_all_entries()]
    # Any string another entry carries that equals a draft's registry_id or
    # strategy_id is a reference (lineage parents, execution bundles, pools...).
    candidate_keys: set[str] = set()
    for row in rows:
        if str(row.get("artifact_type") or "") == "strategy_spec":
            candidate_keys.add(str(row.get("registry_id") or ""))
            candidate_keys.add(str(row.get("strategy_id") or ""))
    candidate_keys.discard("")
    referrers: dict[str, set[str]] = {}
    for row in rows:
        own = str(row.get("registry_id") or "")
        for value in _walk_strings({k: v for k, v in row.items() if k not in {"registry_id", "strategy_id"}}):
            if value in candidate_keys:
                referrers.setdefault(value, set()).add(own)
    referenced: set[str] = set(external)
    by_id = {str(row.get("registry_id")): row for row in rows}
    for key, sources in referrers.items():
        # A self-reference (own spec carrying its own strategy_id) is not a reference.
        owners = {
            src for src in sources
            if src != key and str(_dict(by_id.get(src)).get("strategy_id") or "") != key
        }
        if owners:
            referenced.add(key)

    selected: list[str] = []
    by_source: dict[str, int] = {}
    excluded: dict[str, int] = {}
    already = 0
    kept: dict[str, dict] = {}
    for row in rows:
        reason = exclusion_reason(row, created_from=cutoff, referenced=referenced)
        if reason is not None:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        rid = str(row["registry_id"])
        selected.append(rid)
        kept[rid] = row
        key = _source_key(row)
        by_source[key] = by_source.get(key, 0) + 1
        if retired_by_this_decision(row):
            already += 1
    selected.sort()
    return RetirePlan(
        created_from=created_from,
        selected_ids=selected,
        digest=selection_digest(selected),
        by_source=by_source,
        already_retired=already,
        excluded=excluded,
        external_reference_count=len(external),
        scanned=len(rows),
        entries=kept,
    )


def _actor(entry: Mapping[str, Any], *, digest: str, batch_id: str, actor_id: str = ACTOR_ID) -> dict:
    return {
        "actor_id": actor_id,
        "roles": ["system", "registry_maintenance"],
        "tenant": entry.get("owner_tenant"),
        "token_kind": "maintenance",
        "reason": RETIRE_REASON,
        "operator_decision_ref": OPERATOR_DECISION_REF,
        "selection_digest": digest,
        "batch_id": batch_id,
    }


def _command_key(registry_id: str) -> str:
    return f"fallback-draft-retire:{OPERATOR_DECISION_REF}:{registry_id}"


def apply_plan(
    service: Any,
    *,
    expected_digest: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: Optional[int] = None,
    created_from: str = DEFAULT_CREATED_FROM,
    external_references: Iterable[str] = (),
    clock=time.monotonic,
) -> dict[str, Any]:
    """Retire the planned set in bounded batches; refuse unless the digest matches.

    Idempotent: entries already retired by this decision are skipped, and a
    retry of one entry replays its command receipt. Stops at the first entry
    whose state or content is no longer the planned one, with the reason.
    """
    require_enabled()
    size = int(batch_size)
    if size < 1 or size > MAX_BATCH_SIZE:
        raise FallbackDraftRetireError(f"batch_size must be 1..{MAX_BATCH_SIZE}, got {batch_size}")
    plan = build_plan(service.store, created_from=created_from, external_references=external_references)
    if plan.digest != expected_digest:
        raise FallbackDraftRetireError(
            f"selection digest mismatch: planned {plan.digest}, given {expected_digest}; "
            "re-run plan and re-check the list"
        )
    cutoff = _parse_utc(created_from)
    report: dict[str, Any] = {
        **plan.summary(size),
        "batches": [],
        "retired": 0,
        "skipped_already_retired": 0,
        "stopped": None,
        "complete": False,
    }
    batches_run = 0
    for index in range(0, len(plan.selected_ids), size):
        ids = plan.selected_ids[index:index + size]
        batch_id = batch_id_for(plan.digest, index // size)
        pending = [rid for rid in ids if not retired_by_this_decision(plan.entries[rid])]
        if not pending:
            report["skipped_already_retired"] += len(ids)
            continue
        if max_batches is not None and batches_run >= int(max_batches):
            report["stopped"] = {"reason": "max_batches_reached", "next_batch_id": batch_id}
            return report
        started = clock()
        batch = {"batch_id": batch_id, "first_id": ids[0], "last_id": ids[-1], "retired": 0, "skipped": 0}
        report["batches"].append(batch)
        batches_run += 1
        for rid in ids:
            current = service.store.get(rid)
            row = current.to_dict() if current is not None else None
            if row is None:
                report["stopped"] = {"registry_id": rid, "batch_id": batch_id, "reason": "entry_missing"}
                return report
            if retired_by_this_decision(row):
                batch["skipped"] += 1
                report["skipped_already_retired"] += 1
                continue
            reason = exclusion_reason(row, created_from=cutoff)
            if reason is not None or row != plan.entries[rid]:
                report["stopped"] = {
                    "registry_id": rid, "batch_id": batch_id,
                    "reason": f"unexpected_state:{reason or 'entry_changed_since_plan'}",
                    "artifact_state": row.get("artifact_state"),
                }
                return report
            try:
                service.advance_artifact_state(
                    rid,
                    ArtifactState.RETIRED,
                    command_key=_command_key(rid),
                    actor=_actor(row, digest=plan.digest, batch_id=batch_id),
                    expected_artifact_state=ArtifactState.DRAFT,
                    expected_version=row.get("version"),
                    expected_updated_at=row.get("updated_at"),
                )
            except Exception as exc:  # noqa: BLE001 - stop with a readable reason
                report["stopped"] = {
                    "registry_id": rid, "batch_id": batch_id,
                    "reason": f"transition_refused:{type(exc).__name__}:{str(exc)[:300]}",
                }
                return report
            batch["retired"] += 1
            report["retired"] += 1
        batch["seconds"] = round(clock() - started, 3)
    report["complete"] = True
    return report


def restore_batch(store: Any, *, batch_id: str) -> dict[str, Any]:
    """Undo one batch: put entries this decision retired under ``batch_id`` back to draft.

    Owner-only maintenance outside the normal transition table (retired is
    terminal there). Only entries whose recorded actor is this decision and
    this batch are touched, each by CAS on its current snapshot; the restore
    actor keeps the batch id and the reason so the record stays readable.
    """
    require_enabled()
    restored: list[str] = []
    for entry in store.list_all_entries():
        row = entry.to_dict() if isinstance(entry, RegistryEntry) else dict(entry)
        if not retired_by_this_decision(row) or _dict(row.get("last_actor")).get("batch_id") != batch_id:
            continue
        actor = {
            **_dict(row.get("last_actor")),
            "actor_id": RESTORE_ACTOR_ID,
            "reason": f"restore of batch {batch_id} retired by {OPERATOR_DECISION_REF}",
            "restored_from_batch": batch_id,
        }

        def _validate(base: RegistryEntry, _rid=row["registry_id"]) -> None:
            if base.artifact_state != ArtifactState.RETIRED:
                raise FallbackDraftRetireError(f"{_rid} is no longer retired")

        store.commit_artifact_state_cas(
            registry_id=row["registry_id"],
            base_snapshot=row,
            target_state=ArtifactState.DRAFT,
            actor=actor,
            validate=_validate,
        )
        restored.append(row["registry_id"])
    restored.sort()
    return {"batch_id": batch_id, "restored_count": len(restored), "restored_ids": restored}


def _read_external_references(path: Optional[str]) -> list[str]:
    if not path:
        return []
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, Mapping):
        data = data.get("ids") or []
    if not isinstance(data, list) or not all(isinstance(v, str) for v in data):
        raise FallbackDraftRetireError("external reference file must be a JSON list of ids or {\"ids\": [...]}")
    return data


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.registry.fallback_draft_retire")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--created-from", default=DEFAULT_CREATED_FROM)
        cmd.add_argument("--external-references", default=None,
                         help="JSON list of registry/strategy ids referenced outside Registry")
        cmd.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
        if name == "plan":
            cmd.add_argument("--list-ids", action="store_true")
        else:
            cmd.add_argument("--digest", required=True)
            cmd.add_argument("--max-batches", type=int, default=None)
    restore = sub.add_parser("restore")
    restore.add_argument("--batch-id", required=True)
    args = parser.parse_args(argv)

    from .split_api import RegistryService
    from .storage import get_store

    try:
        require_enabled()
        store = get_store()
        if args.command == "plan":
            plan = build_plan(
                store, created_from=args.created_from,
                external_references=_read_external_references(args.external_references),
            )
            out = plan.summary(args.batch_size)
            if args.list_ids:
                out["selected_ids"] = plan.selected_ids
        elif args.command == "apply":
            out = apply_plan(
                RegistryService(store), expected_digest=args.digest, batch_size=args.batch_size,
                max_batches=args.max_batches, created_from=args.created_from,
                external_references=_read_external_references(args.external_references),
            )
        else:
            out = restore_batch(store, batch_id=args.batch_id)
    except FallbackDraftRetireError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(out, indent=2, sort_keys=True))
    if args.command == "apply" and not out.get("complete"):
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
