"""Shared fail-closed consumer for the loop-product sequencing release."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any


PROGRAM_ID = "loop-product-level-remediation-2026-07-13"
SOURCE_CATALOG_SHA256 = (
    "44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357"
)
SEQUENCING_ADDENDUM_SHA256 = (
    "9a3b735ac161b612e35a1d0e313cc7037da444f8b0311c623d27396a06d4b519"
)
MERGE_PR_3737_SHA = "a4b5df9a51bc3da6df0d39d422d9db4edc553aba"
EFFECTIVE_CATALOG_SHA256 = (
    "875f2cea8c3120f0024cf902e4718c7c15f521a9b61f0bb43356c1bb56ec8e11"
)
SEQUENCING_OVERLAY_SHA256 = (
    "ec4e2d0209fdf430a279a3dd669923f9c3b4abb84d785501993c425b528b55b6"
)
RELEASE_GATE_ID = "hardening-after-g2-paper-trade-v1"
RELEASE_PREDICATE = "g2_evidence_contract_v4_valid"
TARGET_TASK_ID = "LOOP-PROD-VERIFY-EXEC-001"
CANONICAL_DATABASE_NAME = "pantheon"
CANONICAL_DATABASE_ROLE = "pantheon_app"
CANONICAL_DATABASE_SCHEMA = "public"
CANONICAL_DATABASE_TABLE = "telemetry_events"
CANONICAL_DATABASE_HOST = "postgres"
CANONICAL_DATABASE_PORT = 5432
CANONICAL_DATABASE_TLS_MODE = "verify-full"
CANONICAL_PROJECTION_ROOT = "/data/bff/lifecycle-projection"
SOURCE_GRAPH_PROJECTION_SHA256 = (
    "163f6686624e41120ba752de938e0283202026695358d7e4eca274fbad671cea"
)
EFFECTIVE_GRAPH_PROJECTION_SHA256 = (
    "a24617c5c6cfe798f668443a097818e9a3f8c720ec5d2d0c35230262346126b1"
)
BASE_SOURCE_REF_FIELDS = {
    "plan",
    "packet",
    "catalog",
    "catalog_sha256",
    "task_contract_sha256",
    "execution_authority_sha256",
    "completion_authority_sha256",
    "auth_lifecycle_sha256",
    "contract_fixtures_sha256",
    "program_id",
}
BASE_SOURCE_REF_AUTHORITY = {
    "plan": "docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md",
    "packet": "docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/INDEX.md",
    "catalog": "docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/tasks.json",
    "catalog_sha256": SOURCE_CATALOG_SHA256,
    "execution_authority_sha256": (
        "4388a50f9baf36504a71e233d54efdc115d67d719c0c4a2b2cbaf24c86d60f45"
    ),
    "completion_authority_sha256": (
        "d4a05ccf4d7b3becd60a05a0ff7106abed2af137687333a4a2731970a15da31f"
    ),
    "auth_lifecycle_sha256": (
        "94f389bcb0cb65dbc44a00ccea0c057965bc3348fc73836ea19760df2c21500f"
    ),
    "contract_fixtures_sha256": (
        "9de93f911fd255a07e3e8719ef63c05a0e8631b276725cfafc73815203f154be"
    ),
    "program_id": PROGRAM_ID,
}
EXPECTED_TASK_IDS = (
    "LOOP-PROD-000",
    "LOOP-PROD-001",
    "LOOP-PROD-002",
    "LOOP-PROD-AUTH-001",
    "LOOP-PROD-FE-001",
    "LOOP-PROD-REC-001",
    "LOOP-PROD-SRC-001",
    "LOOP-PROD-DIST-001",
    "LOOP-PROD-ALPHA-001",
    "LOOP-PROD-TEACH-001",
    "LOOP-PROD-AGORA-001",
    "LOOP-PROD-CONS-001",
    "LOOP-PROD-AGORA-002",
    "LOOP-PROD-AGORA-003",
    "LOOP-PROD-IMIT-001",
    "LOOP-PROD-DEP-001",
    "LOOP-PROD-CAP-001",
    "LOOP-PROD-TEL-001",
    "LOOP-PROD-TEL-002",
    "LOOP-PROD-EVO-001",
    "LOOP-PROD-BFF-001",
    "LOOP-PROD-OODA-001",
    "LOOP-PROD-PER-001",
    "LOOP-PROD-TJ-001",
    "LOOP-PROD-TJ-002",
    "LOOP-PROD-MAI-001",
    "LOOP-PROD-MAI-002",
    "LOOP-PROD-VERIFY-KNOW-001",
    "LOOP-PROD-VERIFY-EXEC-001",
    "LOOP-PROD-VERIFY-HUMAN-001",
    "LOOP-PROD-VERIFY-OODA-001",
    "LOOP-PROD-PPL-001",
    "LOOP-PROD-TJ-003",
    "LOOP-PROD-PINT-001",
    "LOOP-PROD-MAI-003",
    "LOOP-PROD-CLOSE-001",
    "LOOP-PROD-DELIVERY-001",
    "LOOP-PROD-AUTH-BOOT-001",
    "LOOP-PROD-WORKER-001",
    "LOOP-PROD-LEASE-001",
    "LOOP-PROD-BROWSER-AUTH-001",
    "LOOP-PROD-FLEET-001",
    "LOOP-PROD-ATTEST-001",
    "LOOP-PROD-AUTH-OPS-001",
    "LOOP-PROD-FE-EVID-001",
    "LOOP-PROD-FE-BUILD-001",
    "LOOP-PROD-SIGNOFF-001",
    "LOOP-PROD-CLOSE-002",
)
EXPECTED_TASK_COUNT = len(EXPECTED_TASK_IDS)
EXPECTED_GATED_TASK_IDS = (
    "LOOP-PROD-AUTH-001",
    "LOOP-PROD-FE-001",
    "LOOP-PROD-MAI-001",
    "LOOP-PROD-MAI-002",
    "LOOP-PROD-VERIFY-OODA-001",
    "LOOP-PROD-MAI-003",
    "LOOP-PROD-CLOSE-001",
    "LOOP-PROD-DELIVERY-001",
    "LOOP-PROD-AUTH-BOOT-001",
    "LOOP-PROD-WORKER-001",
    "LOOP-PROD-LEASE-001",
    "LOOP-PROD-BROWSER-AUTH-001",
    "LOOP-PROD-FLEET-001",
    "LOOP-PROD-ATTEST-001",
    "LOOP-PROD-AUTH-OPS-001",
    "LOOP-PROD-FE-EVID-001",
    "LOOP-PROD-FE-BUILD-001",
    "LOOP-PROD-SIGNOFF-001",
    "LOOP-PROD-CLOSE-002",
)
GATED_CLASSIFICATIONS = {
    "deferred strict-auth/security/governance work",
    "final verification/closeout after the appropriate gate",
}
PRE_G2_CLASSIFICATIONS = {
    "permitted before the paper-trade proof",
    "part of the G2 proof path",
}
EXPECTED_CLASSIFICATION_BY_TASK_ID = {
    "LOOP-PROD-000": "permitted before the paper-trade proof",
    "LOOP-PROD-001": "permitted before the paper-trade proof",
    "LOOP-PROD-002": "permitted before the paper-trade proof",
    "LOOP-PROD-AUTH-001": "deferred strict-auth/security/governance work",
    "LOOP-PROD-FE-001": "deferred strict-auth/security/governance work",
    "LOOP-PROD-REC-001": "permitted before the paper-trade proof",
    "LOOP-PROD-SRC-001": "part of the G2 proof path",
    "LOOP-PROD-DIST-001": "part of the G2 proof path",
    "LOOP-PROD-ALPHA-001": "part of the G2 proof path",
    "LOOP-PROD-TEACH-001": "permitted before the paper-trade proof",
    "LOOP-PROD-AGORA-001": "permitted before the paper-trade proof",
    "LOOP-PROD-CONS-001": "permitted before the paper-trade proof",
    "LOOP-PROD-AGORA-002": "permitted before the paper-trade proof",
    "LOOP-PROD-AGORA-003": "permitted before the paper-trade proof",
    "LOOP-PROD-IMIT-001": "permitted before the paper-trade proof",
    "LOOP-PROD-DEP-001": "part of the G2 proof path",
    "LOOP-PROD-CAP-001": "part of the G2 proof path",
    "LOOP-PROD-TEL-001": "part of the G2 proof path",
    "LOOP-PROD-TEL-002": "part of the G2 proof path",
    "LOOP-PROD-EVO-001": "permitted before the paper-trade proof",
    "LOOP-PROD-BFF-001": "permitted before the paper-trade proof",
    "LOOP-PROD-OODA-001": "permitted before the paper-trade proof",
    "LOOP-PROD-PER-001": "permitted before the paper-trade proof",
    "LOOP-PROD-TJ-001": "permitted before the paper-trade proof",
    "LOOP-PROD-TJ-002": "permitted before the paper-trade proof",
    "LOOP-PROD-MAI-001": "deferred strict-auth/security/governance work",
    "LOOP-PROD-MAI-002": "final verification/closeout after the appropriate gate",
    "LOOP-PROD-VERIFY-KNOW-001": "permitted before the paper-trade proof",
    "LOOP-PROD-VERIFY-EXEC-001": "part of the G2 proof path",
    "LOOP-PROD-VERIFY-HUMAN-001": "permitted before the paper-trade proof",
    "LOOP-PROD-VERIFY-OODA-001": "final verification/closeout after the appropriate gate",
    "LOOP-PROD-PPL-001": "permitted before the paper-trade proof",
    "LOOP-PROD-TJ-003": "permitted before the paper-trade proof",
    "LOOP-PROD-PINT-001": "permitted before the paper-trade proof",
    "LOOP-PROD-MAI-003": "final verification/closeout after the appropriate gate",
    "LOOP-PROD-CLOSE-001": "final verification/closeout after the appropriate gate",
    "LOOP-PROD-DELIVERY-001": "deferred strict-auth/security/governance work",
    "LOOP-PROD-AUTH-BOOT-001": "deferred strict-auth/security/governance work",
    "LOOP-PROD-WORKER-001": "deferred strict-auth/security/governance work",
    "LOOP-PROD-LEASE-001": "deferred strict-auth/security/governance work",
    "LOOP-PROD-BROWSER-AUTH-001": "deferred strict-auth/security/governance work",
    "LOOP-PROD-FLEET-001": "deferred strict-auth/security/governance work",
    "LOOP-PROD-ATTEST-001": "deferred strict-auth/security/governance work",
    "LOOP-PROD-AUTH-OPS-001": "deferred strict-auth/security/governance work",
    "LOOP-PROD-FE-EVID-001": "final verification/closeout after the appropriate gate",
    "LOOP-PROD-FE-BUILD-001": "final verification/closeout after the appropriate gate",
    "LOOP-PROD-SIGNOFF-001": "final verification/closeout after the appropriate gate",
    "LOOP-PROD-CLOSE-002": "final verification/closeout after the appropriate gate",
}
BASE_TASK_CONTRACT_FIELDS = {
    "id",
    "title",
    "summary_zh",
    "phase",
    "depends_on",
    "artifacts",
    "acceptance",
    "wave",
    "fleet_lane",
    "target_repo",
    "merge_target",
    "loop_ids",
    "current_maturity",
    "target_maturity",
    "desired_state_sources",
    "actual_state_sources",
    "proof_required",
    "non_goals",
    "dispatch_rules",
    "product_level_required",
    "evidence_root",
    "task_doc",
    "requires_human_ops_signoff",
}
BASE_RUNTIME_AUTHORITY = {
    "task_class": "execution",
    "auto_created_by": "dispatch_loop_product_level_remediation_2026-07-13",
    "auto_generated": True,
    "delivery_layer": "primary",
    "mutates_canonical": True,
    "helper_kind": "loop_product_level_execution_slice",
    "execution_role": "supervisor_admitted_fleet_worker",
    "review_role": "distinct_supervisor_admitted_fleet_reviewer",
    "planner_controller_identity": "/root",
    "planner_may_edit_declared_product_artifacts": False,
    "formal_review_required": True,
}
BASE_RUNTIME_TASK_FIELDS = (
    BASE_TASK_CONTRACT_FIELDS
    | set(BASE_RUNTIME_AUTHORITY)
    | {
        "owner",
        "reviewer",
        "status",
        "next",
        "created_at",
        "last_update",
        "completion_role",
        "source_ref",
    }
)
CHECKPOINT_TASK_IDS = {"LOOP-PROD-CLOSE-001"}
GUARD_INSTALL_TASK_ID = "LOOP-PROD-SIGNOFF-001"
FINAL_AUTHORITY_TASK_ID = "LOOP-PROD-CLOSE-002"

RELEASE_RECORD_FIELDS = {
    "schema_version",
    "program_id",
    "effective_catalog_sha256",
    "sequencing_overlay_sha256",
    "release_gate_id",
    "release_predicate",
    "sequencing_epoch_sha256",
    "released_at",
    "g2_issued_at",
    "closeout_at",
    "g2_evidence_sha256",
    "canonical_record_bundle_sha256",
    "canonical_source_snapshot_sha256",
    "canonical_source_attestation",
    "hosted_probe_sha256",
    "g2_artifact_commit_sha",
    "g2_artifact_merge_target_sha",
    "g2_authoritative_remote_head_sha",
    "g2_github_pr_snapshot_sha256",
    "g2_artifact_github_pr_snapshot_sha256",
    "g2_hosted_deployment_sha",
    "product_manifest_sha256",
    "product_manifest_sidecar_sha256",
    "target_task_snapshot_sha256",
    "reviewer",
    "review_binding_sha256",
    "review_approval_event_sha256",
    "review_verdict_sha256",
    "release_admission_sha256",
    "released_task_transitions",
    "released_task_transition_set_sha256",
}
RELEASE_ADMISSION_FIELDS = {
    "g2_evidence_sha256",
    "canonical_record_bundle_sha256",
    "canonical_source_snapshot_sha256",
    "canonical_source_attestation",
    "hosted_probe_sha256",
    "g2_artifact_commit_sha",
    "g2_artifact_merge_target_sha",
    "g2_authoritative_remote_head_sha",
    "g2_github_pr_snapshot_sha256",
    "g2_artifact_github_pr_snapshot_sha256",
    "g2_hosted_deployment_sha",
    "product_manifest_sha256",
    "product_manifest_sidecar_sha256",
    "target_task_snapshot_sha256",
    "reviewer",
    "review_binding_sha256",
    "review_approval_event_sha256",
    "review_verdict_sha256",
    "g2_issued_at",
    "closeout_at",
}
SOURCE_ATTESTATION_FIELDS = {
    "database",
    "role",
    "schema",
    "table",
    "database_host",
    "database_port",
    "database_tls_mode",
    "database_server_address",
    "database_tls_protocol",
    "database_tls_cipher",
    "projection_root",
    "live_source_high_watermark",
    "captured_generation_name",
    "current_generation_name",
    "captured_projection_checkpoint",
    "captured_projection_source_high_watermark",
    "current_projection_checkpoint",
    "current_projection_source_high_watermark",
    "rows_sha256",
    "projection_sha256",
    "current_projection_sha256",
}
RELEASE_TRANSITION_FIELDS = {
    "task_id",
    "before_task_snapshot_sha256",
    "after_task_snapshot_sha256",
    "before_status",
    "after_status",
}
RELEASE_AUDIT_COMMON_FIELDS = {
    "event_id",
    "ordinal",
    "event_count",
    "ts",
    "agent",
    "type",
    "task_id",
    "message",
    "program_id",
    "catalog_sha256",
    "transaction_id",
    "actor_policy_sha256",
    "affected_state_projection_sha256",
}
RELEASE_AUDIT_EXTRA_FIELDS = {
    "release_gate_id",
    "sequencing_overlay_sha256",
    "release_record_sha256",
    "released_task_transition_set_sha256",
}
EPOCH_FIELDS = {
    "schema_version",
    "program_id",
    "source_catalog_sha256",
    "effective_catalog_sha256",
    "sequencing_overlay_sha256",
    "release_gate_id",
    "install_mode",
    "applied_at",
    "source_graph_projection_sha256",
    "effective_graph_projection_sha256",
    "task_count",
    "task_transitions",
    "task_transition_set_sha256",
}
EPOCH_TRANSITION_FIELDS = {
    "task_id",
    "before_task_snapshot",
    "before_task_snapshot_sha256",
    "after_task_snapshot_sha256",
    "before_task_contract_sha256",
    "after_task_contract_sha256",
    "before_source_ref_sha256",
    "after_source_ref_sha256",
    "before_status",
    "after_status",
    "acceptance_deferral_sha256",
    "gate_marker_sha256",
}


def canonical_sha256(value: Any) -> str:
    body = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    )


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        return None
    return parsed.astimezone(timezone.utc)


def _is_exact_utc_z(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.endswith("Z")
        and parse_utc(value) is not None
    )


def _task_contract_sha256(task: dict[str, Any]) -> str:
    return canonical_sha256(
        {field: task.get(field) for field in BASE_TASK_CONTRACT_FIELDS}
    )


def _expected_completion_role(task_id: str) -> str:
    if task_id == FINAL_AUTHORITY_TASK_ID:
        return "final_authority"
    if task_id == GUARD_INSTALL_TASK_ID:
        return "guard_installer"
    if task_id in CHECKPOINT_TASK_IDS:
        return "checkpoint_only"
    return "ordinary"


def _base_preimage_is_exact(
    preimage: Any,
    task_id: str,
    applied_at: datetime,
) -> bool:
    if (
        not isinstance(preimage, dict)
        or set(preimage) != BASE_RUNTIME_TASK_FIELDS
        or preimage.get("id") != task_id
        or preimage.get("status") != "todo"
        or preimage.get("completion_role") != _expected_completion_role(task_id)
        or any(
            preimage.get(field) != value
            for field, value in BASE_RUNTIME_AUTHORITY.items()
        )
        or not isinstance(preimage.get("owner"), str)
        or not preimage["owner"].strip()
        or not isinstance(preimage.get("reviewer"), str)
        or not preimage["reviewer"].strip()
        or preimage["owner"] == preimage["reviewer"]
        or not isinstance(preimage.get("next"), str)
        or not preimage["next"].strip()
    ):
        return False
    source_ref = preimage.get("source_ref")
    if (
        not isinstance(source_ref, dict)
        or set(source_ref) != BASE_SOURCE_REF_FIELDS
        or any(
            source_ref.get(field) != value
            for field, value in BASE_SOURCE_REF_AUTHORITY.items()
        )
        or source_ref.get("task_contract_sha256")
        != _task_contract_sha256(preimage)
    ):
        return False
    created_at = parse_utc(preimage.get("created_at"))
    last_update = parse_utc(preimage.get("last_update"))
    return bool(
        created_at is not None
        and last_update is not None
        and created_at <= last_update <= applied_at
    )


def status_has_pending_program_activity_outbox(status: dict[str, Any]) -> bool:
    """Treat every present non-null dispatcher transaction as pending."""

    return status.get("program_activity_outbox") is not None


def status_declares_sequencing_release(status: dict[str, Any]) -> bool:
    releases = status.get("program_sequencing_releases")
    record = releases.get(PROGRAM_ID) if isinstance(releases, dict) else None
    return isinstance(record, dict) and bool(record)


def _source_ref(task: dict[str, Any]) -> dict[str, Any]:
    value = task.get("source_ref")
    return value if isinstance(value, dict) else {}


def _classification(task: dict[str, Any]) -> str:
    return str(_source_ref(task).get("sequencing_classification") or "")


def _has_exact_authority_source_ref(task: dict[str, Any]) -> bool:
    task_id = str(task.get("id") or "").strip()
    source_ref = _source_ref(task)
    return bool(
        task_id in EXPECTED_CLASSIFICATION_BY_TASK_ID
        and source_ref.get("program_id") == PROGRAM_ID
        and source_ref.get("catalog_sha256") == EFFECTIVE_CATALOG_SHA256
        and source_ref.get("source_catalog_sha256") == SOURCE_CATALOG_SHA256
        and source_ref.get("sequencing_addendum_sha256")
        == SEQUENCING_ADDENDUM_SHA256
        and source_ref.get("merge_pr_3737_sha") == MERGE_PR_3737_SHA
        and source_ref.get("sequencing_overlay_sha256")
        == SEQUENCING_OVERLAY_SHA256
        and source_ref.get("release_gate_id") == RELEASE_GATE_ID
        and source_ref.get("sequencing_classification")
        == EXPECTED_CLASSIFICATION_BY_TASK_ID[task_id]
    )


def _task_matches_epoch_authority(
    task: dict[str, Any],
    transition: dict[str, Any],
) -> bool:
    source_ref = _source_ref(task)
    if transition.get("after_status") == "absent":
        return bool(
            _has_exact_authority_source_ref(task)
            and transition.get("after_source_ref_sha256")
            == canonical_sha256(None)
            and source_ref.get("task_contract_sha256")
            == _task_contract_sha256(task)
        )
    return bool(
        _has_exact_authority_source_ref(task)
        and transition.get("after_source_ref_sha256")
        == canonical_sha256(source_ref)
    )


def _validated_epoch(status: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    epochs = status.get("program_sequencing_epochs")
    epoch = epochs.get(PROGRAM_ID) if isinstance(epochs, dict) else None
    transitions = epoch.get("task_transitions") if isinstance(epoch, dict) else None
    applied_at = parse_utc(epoch.get("applied_at")) if isinstance(epoch, dict) else None
    if (
        not isinstance(epoch, dict)
        or set(epoch) != EPOCH_FIELDS
        or epoch.get("schema_version") != 2
        or epoch.get("program_id") != PROGRAM_ID
        or epoch.get("source_catalog_sha256") != SOURCE_CATALOG_SHA256
        or epoch.get("effective_catalog_sha256") != EFFECTIVE_CATALOG_SHA256
        or epoch.get("sequencing_overlay_sha256") != SEQUENCING_OVERLAY_SHA256
        or epoch.get("release_gate_id") != RELEASE_GATE_ID
        or epoch.get("install_mode")
        not in {"base_epoch_migration", "fresh_materialization"}
        or applied_at is None
        or not _is_exact_utc_z(epoch.get("applied_at"))
        or epoch.get("source_graph_projection_sha256")
        != SOURCE_GRAPH_PROJECTION_SHA256
        or epoch.get("effective_graph_projection_sha256")
        != EFFECTIVE_GRAPH_PROJECTION_SHA256
        or not is_sha256(epoch.get("task_transition_set_sha256"))
        or not isinstance(transitions, list)
        or len(transitions) != EXPECTED_TASK_COUNT
        or epoch.get("task_count") != EXPECTED_TASK_COUNT
        or epoch.get("task_transition_set_sha256")
        != canonical_sha256(transitions)
    ):
        return None

    null_sha256 = canonical_sha256(None)
    expected_before_status = (
        "todo"
        if epoch["install_mode"] == "base_epoch_migration"
        else "absent"
    )
    task_ids: list[str] = []
    gated_ids: list[str] = []
    for transition in transitions:
        task_id = (
            str(transition.get("task_id") or "").strip()
            if isinstance(transition, dict)
            else ""
        )
        preimage = (
            transition.get("before_task_snapshot")
            if isinstance(transition, dict)
            else None
        )
        is_gated = task_id in EXPECTED_GATED_TASK_IDS
        fresh_deferred = bool(
            epoch["install_mode"] == "fresh_materialization" and is_gated
        )
        expected_after_status = (
            "absent" if fresh_deferred else ("blocked" if is_gated else "todo")
        )
        expected_marker = (
            {
                "schema_version": 1,
                "gate_id": RELEASE_GATE_ID,
                "release_predicate": RELEASE_PREDICATE,
                "sequencing_overlay_sha256": SEQUENCING_OVERLAY_SHA256,
                "state": "parked",
                "previous_status": "todo",
                "parked_at": epoch.get("applied_at"),
            }
            if is_gated and not fresh_deferred
            else None
        )
        if (
            not isinstance(transition, dict)
            or set(transition) != EPOCH_TRANSITION_FIELDS
            or not task_id
            or transition.get("before_status") != expected_before_status
            or transition.get("after_status") != expected_after_status
            or any(
                not is_sha256(transition.get(field))
                for field in EPOCH_TRANSITION_FIELDS
                if field.endswith("sha256")
            )
            or (
                epoch["install_mode"] == "fresh_materialization"
                and (
                    preimage is not None
                    or transition.get("before_task_snapshot_sha256")
                    != null_sha256
                    or transition.get("before_task_contract_sha256")
                    != null_sha256
                    or transition.get("before_source_ref_sha256") != null_sha256
                )
            )
            or (
                epoch["install_mode"] == "base_epoch_migration"
                and (
                    not _base_preimage_is_exact(preimage, task_id, applied_at)
                    or canonical_sha256(preimage)
                    != transition.get("before_task_snapshot_sha256")
                    or transition.get("before_task_contract_sha256")
                    != _task_contract_sha256(preimage)
                    or canonical_sha256(preimage.get("source_ref"))
                    != transition.get("before_source_ref_sha256")
                )
            )
            or (
                transition.get("gate_marker_sha256")
                != canonical_sha256(expected_marker)
            )
            or (
                fresh_deferred
                and any(
                    transition.get(field) != null_sha256
                    for field in (
                        "after_task_snapshot_sha256",
                        "after_task_contract_sha256",
                        "after_source_ref_sha256",
                        "acceptance_deferral_sha256",
                    )
                )
            )
            or (
                transition.get("acceptance_deferral_sha256") == null_sha256
                and EXPECTED_CLASSIFICATION_BY_TASK_ID.get(task_id)
                in PRE_G2_CLASSIFICATIONS
            )
            or (
                transition.get("acceptance_deferral_sha256") != null_sha256
                and EXPECTED_CLASSIFICATION_BY_TASK_ID.get(task_id)
                not in PRE_G2_CLASSIFICATIONS
            )
        ):
            return None
        task_ids.append(task_id)
        if is_gated:
            gated_ids.append(task_id)
    if tuple(task_ids) != EXPECTED_TASK_IDS or tuple(gated_ids) != EXPECTED_GATED_TASK_IDS:
        return None
    return epoch, transitions


def _validated_release_record(
    status: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]] | None:
    epoch_result = _validated_epoch(status)
    if epoch_result is None:
        return None
    epoch, epoch_transitions = epoch_result
    epoch_by_id = {str(row["task_id"]): row for row in epoch_transitions}

    releases = status.get("program_sequencing_releases")
    record = releases.get(PROGRAM_ID) if isinstance(releases, dict) else None
    transitions = (
        record.get("released_task_transitions")
        if isinstance(record, dict)
        else None
    )
    if (
        not isinstance(record, dict)
        or set(record) != RELEASE_RECORD_FIELDS
        or record.get("schema_version") != 2
        or record.get("program_id") != PROGRAM_ID
        or record.get("effective_catalog_sha256") != EFFECTIVE_CATALOG_SHA256
        or record.get("sequencing_overlay_sha256") != SEQUENCING_OVERLAY_SHA256
        or record.get("release_gate_id") != RELEASE_GATE_ID
        or record.get("release_predicate") != RELEASE_PREDICATE
        or record.get("sequencing_epoch_sha256") != canonical_sha256(epoch)
        or not isinstance(record.get("reviewer"), str)
        or not record["reviewer"].strip()
        or not isinstance(transitions, list)
        or len(transitions) != len(EXPECTED_GATED_TASK_IDS)
    ):
        return None
    if any(
        not is_sha256(record.get(field))
        for field in (
            "g2_evidence_sha256",
            "canonical_record_bundle_sha256",
            "canonical_source_snapshot_sha256",
            "g2_github_pr_snapshot_sha256",
            "g2_artifact_github_pr_snapshot_sha256",
            "hosted_probe_sha256",
            "product_manifest_sha256",
            "product_manifest_sidecar_sha256",
            "target_task_snapshot_sha256",
            "review_binding_sha256",
            "review_approval_event_sha256",
            "review_verdict_sha256",
            "sequencing_epoch_sha256",
            "release_admission_sha256",
            "released_task_transition_set_sha256",
        )
    ) or any(
        not isinstance(record.get(field), str)
        or re.fullmatch(r"[0-9a-f]{40}", record[field]) is None
        for field in (
            "g2_artifact_commit_sha",
            "g2_artifact_merge_target_sha",
            "g2_authoritative_remote_head_sha",
            "g2_hosted_deployment_sha",
        )
    ):
        return None
    attestation = record.get("canonical_source_attestation")
    if (
        not isinstance(attestation, dict)
        or set(attestation) != SOURCE_ATTESTATION_FIELDS
        or attestation.get("database") != CANONICAL_DATABASE_NAME
        or attestation.get("role") != CANONICAL_DATABASE_ROLE
        or attestation.get("schema") != CANONICAL_DATABASE_SCHEMA
        or attestation.get("table") != CANONICAL_DATABASE_TABLE
        or attestation.get("database_host") != CANONICAL_DATABASE_HOST
        or attestation.get("database_port") != CANONICAL_DATABASE_PORT
        or attestation.get("database_tls_mode") != CANONICAL_DATABASE_TLS_MODE
        or any(
            not isinstance(attestation.get(field), str)
            or not attestation[field]
            for field in (
                "database_server_address",
                "database_tls_protocol",
                "database_tls_cipher",
            )
        )
        or attestation.get("projection_root") != CANONICAL_PROJECTION_ROOT
        or type(attestation.get("live_source_high_watermark")) is not int
        or attestation["live_source_high_watermark"] <= 0
        or any(
            type(attestation.get(field)) is not int
            or attestation[field] != attestation["live_source_high_watermark"]
            for field in (
                "captured_projection_checkpoint",
                "captured_projection_source_high_watermark",
                "current_projection_checkpoint",
                "current_projection_source_high_watermark",
            )
        )
        or not is_sha256(attestation.get("rows_sha256"))
        or not is_sha256(attestation.get("projection_sha256"))
        or not is_sha256(attestation.get("current_projection_sha256"))
        or re.fullmatch(
            r"g[0-9]{12}-[0-9a-f]{64}",
            str(attestation.get("captured_generation_name") or ""),
        )
        is None
        or re.fullmatch(
            r"g[0-9]{12}-[0-9a-f]{64}",
            str(attestation.get("current_generation_name") or ""),
        )
        is None
    ):
        return None

    applied_at = parse_utc(epoch.get("applied_at"))
    closeout_at = parse_utc(record.get("closeout_at"))
    g2_issued_at = parse_utc(record.get("g2_issued_at"))
    released_at = parse_utc(record.get("released_at"))
    if (
        applied_at is None
        or closeout_at is None
        or g2_issued_at is None
        or released_at is None
        or not _is_exact_utc_z(record.get("closeout_at"))
        or not _is_exact_utc_z(record.get("g2_issued_at"))
        or not _is_exact_utc_z(record.get("released_at"))
        or g2_issued_at > closeout_at
        or closeout_at > released_at
        or applied_at > released_at
    ):
        return None
    admission = {
        field: record.get(field) for field in RELEASE_ADMISSION_FIELDS
    }
    if record.get("release_admission_sha256") != canonical_sha256(admission):
        return None

    transition_ids: list[str] = []
    for transition in transitions:
        task_id_value = str(
            transition.get("task_id") if isinstance(transition, dict) else ""
        ).strip()
        epoch_transition = epoch_by_id.get(task_id_value) or {}
        expected_before_status = epoch_transition.get("after_status")
        if (
            not isinstance(transition, dict)
            or set(transition) != RELEASE_TRANSITION_FIELDS
            or task_id_value not in epoch_by_id
            or expected_before_status not in {"absent", "blocked"}
            or transition.get("before_status") != expected_before_status
            or transition.get("after_status") != "todo"
            or not is_sha256(transition.get("before_task_snapshot_sha256"))
            or not is_sha256(transition.get("after_task_snapshot_sha256"))
            or transition.get("before_task_snapshot_sha256")
            != epoch_transition.get("after_task_snapshot_sha256")
        ):
            return None
        transition_ids.append(task_id_value)
    if (
        tuple(transition_ids) != EXPECTED_GATED_TASK_IDS
        or record.get("released_task_transition_set_sha256")
        != canonical_sha256(transitions)
    ):
        return None
    return record, epoch_by_id


@dataclass(frozen=True)
class SequencingReleaseAuditProof:
    release_record_sha256: str
    event_id: str


def build_sequencing_release_audit_proof(
    status: dict[str, Any],
    durable_records: list[dict[str, Any]],
) -> SequencingReleaseAuditProof | None:
    """Bind a release record to its one external, durable activity event."""

    if status_has_pending_program_activity_outbox(status):
        return None
    release_result = _validated_release_record(status)
    if release_result is None:
        return None
    record, _ = release_result
    candidates = [
        entry
        for entry in durable_records
        if isinstance(entry, dict)
        and entry.get("program_id") == PROGRAM_ID
        and entry.get("type") == "sequencing_gate_release"
    ]
    if len(candidates) != 1:
        return None
    event = candidates[0]
    unsigned = {key: value for key, value in event.items() if key != "event_id"}
    expected_event_id = "loop-product-event-" + canonical_sha256(unsigned)
    release_record_sha256 = canonical_sha256(record)
    ordinal = event.get("ordinal")
    event_count = event.get("event_count")
    if (
        set(event) != RELEASE_AUDIT_COMMON_FIELDS | RELEASE_AUDIT_EXTRA_FIELDS
        or event.get("event_id") != expected_event_id
        or type(ordinal) is not int
        or type(event_count) is not int
        or ordinal < 0
        or event_count <= ordinal
        or not isinstance(event.get("agent"), str)
        or not event["agent"].strip()
        or event.get("task_id") != TARGET_TASK_ID
        or event.get("message")
        != "Released exact sequencing gate after G2 admission"
        or event.get("catalog_sha256") != EFFECTIVE_CATALOG_SHA256
        or event.get("sequencing_overlay_sha256")
        != SEQUENCING_OVERLAY_SHA256
        or event.get("release_gate_id") != RELEASE_GATE_ID
        or event.get("release_record_sha256") != release_record_sha256
        or event.get("released_task_transition_set_sha256")
        != record.get("released_task_transition_set_sha256")
        or event.get("ts") != record.get("released_at")
        or not _is_exact_utc_z(event.get("ts"))
        or not isinstance(event.get("transaction_id"), str)
        or re.fullmatch(
            r"loop-product-tx-[0-9a-f]{64}", event["transaction_id"]
        )
        is None
        or not is_sha256(event.get("actor_policy_sha256"))
        or not is_sha256(event.get("affected_state_projection_sha256"))
    ):
        return None
    return SequencingReleaseAuditProof(
        release_record_sha256=release_record_sha256,
        event_id=expected_event_id,
    )


def task_has_valid_sequencing_release_admission(
    task: dict[str, Any],
    status: dict[str, Any],
    *,
    release_audit_proof: SequencingReleaseAuditProof | None = None,
) -> bool:
    """Accept only the task-scoped tag from the exact audited 19-task release."""

    task_id = str(task.get("id") or "").strip()
    release_result = _validated_release_record(status)
    if release_result is None:
        return False
    record, epoch_by_id = release_result
    task_admission = task.get("sequencing_release_admission_sha256")
    if (
        task_id not in EXPECTED_GATED_TASK_IDS
        or not _task_matches_epoch_authority(task, epoch_by_id[task_id])
        or not is_sha256(task_admission)
        or record.get("release_admission_sha256") != task_admission
        or not isinstance(release_audit_proof, SequencingReleaseAuditProof)
        or release_audit_proof.release_record_sha256 != canonical_sha256(record)
    ):
        return False
    return True


def task_is_sequencing_parked(
    task: dict[str, Any],
    status: dict[str, Any] | None = None,
    *,
    release_audit_proof: SequencingReleaseAuditProof | None = None,
) -> bool:
    """Fail closed from exact task IDs, epoch membership, marker, and admission."""

    task_id = str(task.get("id") or "").strip()
    if "sequencing_release_gate" in task:
        return True
    classification = _classification(task)
    definitely_gated = (
        task_id in EXPECTED_GATED_TASK_IDS
        or classification in GATED_CLASSIFICATIONS
    )
    if status is None:
        return definitely_gated

    source_ref = _source_ref(task)
    overlay_bound = (
        source_ref.get("program_id") == PROGRAM_ID
        or source_ref.get("sequencing_overlay_sha256") == SEQUENCING_OVERLAY_SHA256
        or task_id in EXPECTED_GATED_TASK_IDS
    )
    epoch_result = _validated_epoch(status)
    if epoch_result is None:
        return definitely_gated or overlay_bound
    _, transitions = epoch_result
    epoch_by_id = {str(row["task_id"]): row for row in transitions}
    epoch_transition = epoch_by_id.get(task_id)
    if epoch_transition is None:
        return definitely_gated or overlay_bound
    if not _task_matches_epoch_authority(task, epoch_transition):
        return True
    epoch_gated = task_id in EXPECTED_GATED_TASK_IDS
    if epoch_gated:
        return not task_has_valid_sequencing_release_admission(
            task,
            status,
            release_audit_proof=release_audit_proof,
        )
    if classification not in PRE_G2_CLASSIFICATIONS:
        return True
    return False
