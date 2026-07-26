#!/usr/bin/env python3
"""Seed or verify the non-secret Agora restart-persistence probe.

This helper runs inside the deployed operator-bff container.  It deliberately
uses the configured workshop and governance stores instead of an HTTP identity
so a deployment check never needs to invent credentials or weaken strict JWT
auth.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
BFF_ROOT = ROOT / "services" / "control-plane" / "bff"
if str(BFF_ROOT) not in sys.path:
    sys.path.insert(0, str(BFF_ROOT))

from agora.governance.store import (  # noqa: E402
    BACKEND_ENV as GOVERNANCE_BACKEND_ENV,
    ProposalStore,
    payload_fingerprint,
)
from agora.strategy_workshop.store import (  # noqa: E402
    BACKEND_ENV as WORKSHOP_BACKEND_ENV,
    make_workshop_store,
)
from agora.dataset_extraction.extractor import (  # noqa: E402
    BACKEND_ENV as DATASET_BACKEND_ENV,
    AgoraDatasetStore,
    evidence_request_digest,
)
from agora.dataset_extraction.models import AgoraInteractionEvidenceRequest  # noqa: E402


def require_postgres_backends() -> None:
    for env_name in (WORKSHOP_BACKEND_ENV, GOVERNANCE_BACKEND_ENV, DATASET_BACKEND_ENV):
        backend = os.environ.get(env_name, "").strip().lower()
        if backend != "postgres":
            raise RuntimeError(
                f"restart-persistence smoke requires {env_name}=postgres; "
                f"got {backend or '<unset>'}"
            )


def proposal_id_for(workshop_id: str) -> str:
    return f"proposal-{workshop_id}"


def evidence_id_for(workshop_id: str) -> str:
    return f"evidence-{workshop_id}"


def proposal_record(
    *,
    workshop_id: str,
    tenant_id: str,
    user_id: str,
) -> dict[str, Any]:
    return {
        "proposal_id": proposal_id_for(workshop_id),
        "revision": 1,
        "tenant_id": tenant_id,
        "owner_user_id": user_id,
        "state": "draft",
        "target_kind": "strategy",
        "target_id": f"strategy-{workshop_id}",
        "target_version": "deploy-smoke-v1",
        "current_value": {"risk": 0.10},
        "proposed_value": {"risk": 0.08},
        "created_at": "2026-07-14T00:00:00Z",
        "updated_at": "2026-07-14T00:00:00Z",
        "audit": [{"action": "create", "actor": "internal-deploy-smoke"}],
    }


def command_scope(*, tenant_id: str, user_id: str) -> str:
    return f"deploy-smoke:{tenant_id}:{user_id}"


def recovery_command_key(workshop_id: str) -> str:
    return f"{workshop_id}:recovery"


def command_result(workshop_id: str) -> dict[str, Any]:
    return {
        "interaction_id": f"interaction-{workshop_id}",
        "status": "queued",
        "execution_authority": "none",
    }


def seed(
    workshop_store: Any,
    proposal_store: ProposalStore,
    *,
    workshop_id: str,
    tenant_id: str,
    user_id: str,
    dataset_store: AgoraDatasetStore | None = None,
) -> None:
    if dataset_store is None:
        dataset_store = AgoraDatasetStore()

    created = workshop_store.create_session(
        {
            "workshop_id": workshop_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "status": "open",
        }
    )
    verify_workshop_record(
        created,
        workshop_id=workshop_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    base = proposal_record(
        workshop_id=workshop_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    proposal_store.create(base, f"{workshop_id}:proposal-create")
    latest = proposal_store.get(base["proposal_id"], tenant_id, user_id)
    if latest is None:
        raise RuntimeError(f"proposal {base['proposal_id']!r} was not created")

    if int(latest["revision"]) < 2:
        modified = {
            **latest,
            "revision": 2,
            "state": "draft",
            "proposed_value": {"risk": 0.07},
            "updated_at": "2026-07-14T00:01:00Z",
            "audit": [
                *latest["audit"],
                {"action": "modify", "actor": "internal-deploy-smoke"},
            ],
        }
        latest = proposal_store.append(
            base["proposal_id"],
            proposal_store.etag(latest),
            modified,
        )

    if int(latest["revision"]) < 3:
        validated = {
            **latest,
            "revision": 3,
            "state": "validated",
            "validation": {
                "status": "passed",
                "environment": "paper",
                "execution_attempted": False,
            },
            "validation_result_digest": "deploy-smoke-validation-v1",
            "updated_at": "2026-07-14T00:02:00Z",
            "audit": [
                *latest["audit"],
                {"action": "validate", "actor": "internal-deploy-smoke"},
            ],
        }
        proposal_store.append(
            base["proposal_id"],
            proposal_store.etag(latest),
            validated,
        )

    result = command_result(workshop_id)
    scope = command_scope(tenant_id=tenant_id, user_id=user_id)
    fingerprint = payload_fingerprint(result)
    once = proposal_store.once(
        scope,
        workshop_id,
        fingerprint,
        lambda: result,
    )
    if once.run_side_effects:
        proposal_store.complete_side_effects(scope, workshop_id)

    recovery_key = recovery_command_key(workshop_id)
    recoverable = proposal_store.once(
        scope,
        recovery_key,
        fingerprint,
        lambda: result,
    )
    if not recoverable.run_side_effects:
        raise RuntimeError(
            f"proposal {base['proposal_id']!r} recovery outbox was not claimable"
        )
    # Model a handled partial side-effect failure.  The pending row must be
    # reclaimed by the fresh helper process after operator-bff restarts.
    proposal_store.release_side_effects(scope, recovery_key)

    # Seed dataset/evidence inbox persistence check
    evidence_id = evidence_id_for(workshop_id)
    evidence = AgoraInteractionEvidenceRequest(
        evidence_id=evidence_id,
        interaction_kind="ask",
        persona_id="persona-smoke",
        captured_at="2026-07-14T00:00:00Z",
        content={"text": f"smoke test query for {workshop_id}"},
    )
    _, is_new = dataset_store.add_to_inbox(
        evidence,
        tenant_id,
        user_id,
        "2026-07-14T00:01:00Z",
        idempotency_key=f"restart-smoke:{evidence_id}",
        request_digest=evidence_request_digest(evidence),
    )
    if not is_new:
        raise RuntimeError(f"evidence {evidence_id!r} was already in inbox during seed")


def verify_workshop_record(
    record: Any,
    *,
    workshop_id: str,
    tenant_id: str,
    user_id: str,
) -> None:
    if not isinstance(record, dict):
        raise RuntimeError(f"workshop {workshop_id!r} was not found")

    expected = {
        "workshop_id": workshop_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "status": "open",
    }
    mismatches = {
        key: {"expected": value, "actual": record.get(key)}
        for key, value in expected.items()
        if record.get(key) != value
    }
    if mismatches:
        fields = ", ".join(sorted(mismatches))
        raise RuntimeError(
            f"workshop {workshop_id!r} failed persistence verification: "
            f"mismatched fields {fields}"
        )


def verify(
    workshop_store: Any,
    proposal_store: ProposalStore,
    *,
    workshop_id: str,
    tenant_id: str,
    user_id: str,
    dataset_store: AgoraDatasetStore | None = None,
) -> None:
    if dataset_store is None:
        dataset_store = AgoraDatasetStore()

    verify_workshop_record(
        workshop_store.get_session(workshop_id),
        workshop_id=workshop_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )

    proposal_id = proposal_id_for(workshop_id)
    latest = proposal_store.get(proposal_id, tenant_id, user_id)
    if not isinstance(latest, dict):
        raise RuntimeError(f"proposal {proposal_id!r} was not found")
    history = proposal_store.history(proposal_id, tenant_id, user_id)
    revisions = [row.get("revision") for row in history]
    actions = [event.get("action") for event in latest.get("audit", [])]
    if revisions != [1, 2, 3]:
        raise RuntimeError(
            f"proposal {proposal_id!r} revision history mismatch: {revisions!r}"
        )
    if latest.get("state") != "validated" or actions != ["create", "modify", "validate"]:
        raise RuntimeError(
            f"proposal {proposal_id!r} audit/state mismatch: "
            f"state={latest.get('state')!r}, actions={actions!r}"
        )

    replay = proposal_store.create(
        proposal_record(
            workshop_id=workshop_id,
            tenant_id=tenant_id,
            user_id=user_id,
        ),
        f"{workshop_id}:proposal-create",
    )
    if replay.get("revision") != 3:
        raise RuntimeError(f"proposal {proposal_id!r} idempotent replay was not durable")

    result = command_result(workshop_id)
    scope = command_scope(tenant_id=tenant_id, user_id=user_id)
    fingerprint = payload_fingerprint(result)
    once = proposal_store.once(
        scope,
        workshop_id,
        fingerprint,
        lambda: result,
    )
    if not once.replayed or once.run_side_effects or once.data != result:
        raise RuntimeError(
            f"proposal {proposal_id!r} outbox replay was not exactly-once"
        )

    recovery_key = recovery_command_key(workshop_id)
    recovered = proposal_store.once(
        scope,
        recovery_key,
        fingerprint,
        lambda: result,
    )
    if (
        not recovered.replayed
        or not recovered.run_side_effects
        or recovered.data != result
    ):
        raise RuntimeError(
            f"proposal {proposal_id!r} pending outbox was not recovered after restart"
        )
    proposal_store.complete_side_effects(scope, recovery_key)
    recovered_replay = proposal_store.once(
        scope,
        recovery_key,
        fingerprint,
        lambda: result,
    )
    if (
        not recovered_replay.replayed
        or recovered_replay.run_side_effects
        or recovered_replay.data != result
    ):
        raise RuntimeError(
            f"proposal {proposal_id!r} recovered outbox was not completed exactly once"
        )
    if len(proposal_store.history(proposal_id, tenant_id, user_id)) != 3:
        raise RuntimeError(f"proposal {proposal_id!r} replay created duplicate revisions")

    # Verify dataset/evidence inbox persistence check
    evidence_id = evidence_id_for(workshop_id)
    backlog = dataset_store.get_backlog(tenant_id, user_id)
    backlog_ids = [item["evidence_id"] for item in backlog]
    if evidence_id not in backlog_ids:
        raise RuntimeError(f"evidence {evidence_id!r} was not found in backlog during verify")

    dataset_result = dataset_store.process_inbox(
        tenant_id=tenant_id,
        user_id=user_id,
        worker_id="restart-persistence-smoke",
    )
    if dataset_result["processed"] < 1:
        raise RuntimeError(f"evidence {evidence_id!r} was not processed by dataset worker")

    backlog_after = dataset_store.get_backlog(tenant_id, user_id)
    backlog_ids_after = [item["evidence_id"] for item in backlog_after]
    if evidence_id in backlog_ids_after:
        raise RuntimeError(f"evidence {evidence_id!r} remained in backlog after processing")

    record = dataset_store.get(
        evidence_id,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if record is None:
        raise RuntimeError(f"dataset record for {evidence_id!r} was not found after processing")
    if record.tenant_id != tenant_id or record.user_id != user_id:
        raise RuntimeError(f"dataset record field mismatch for {evidence_id!r}")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed", "verify"))
    parser.add_argument("--workshop-id", required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--user-id", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    require_postgres_backends()
    workshop_store = make_workshop_store()
    proposal_store = ProposalStore()
    dataset_store = AgoraDatasetStore()

    operation = seed if args.action == "seed" else verify
    operation(
        workshop_store,
        proposal_store,
        workshop_id=args.workshop_id,
        tenant_id=args.tenant_id,
        user_id=args.user_id,
        dataset_store=dataset_store,
    )
    outbox_status = (
        "completed_outbox=completed recovery_outbox=pending"
        if args.action == "seed"
        else "completed_outbox=replayed recovery_outbox=recovered"
    )
    print(
        f"{args.action} ok: workshop={args.workshop_id} "
        f"proposal={proposal_id_for(args.workshop_id)} "
        f"evidence={evidence_id_for(args.workshop_id)} {outbox_status}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
