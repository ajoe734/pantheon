"""Shared fail-closed consumer for the loop-product sequencing release."""

from __future__ import annotations

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
    "147ae747179f3c78fc9de1c62c823a72307a3f8028a92088eb9ccc0c49a85412"
)
SEQUENCING_OVERLAY_SHA256 = (
    "f175df35de77bedff674896b60510defcec7a4794ac2f5856eef966ef989d22b"
)
RELEASE_GATE_ID = "hardening-after-g2-paper-trade-v1"
RELEASE_PREDICATE = "g2_evidence_contract_v2_valid"
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

RELEASE_RECORD_FIELDS = {
    "schema_version",
    "program_id",
    "effective_catalog_sha256",
    "sequencing_overlay_sha256",
    "release_gate_id",
    "release_predicate",
    "released_at",
    "g2_issued_at",
    "closeout_at",
    "g2_evidence_sha256",
    "canonical_record_bundle_sha256",
    "hosted_probe_sha256",
    "product_manifest_sha256",
    "product_manifest_sidecar_sha256",
    "target_task_snapshot_sha256",
    "reviewer",
    "review_verdict_sha256",
    "release_admission_sha256",
    "released_task_transitions",
    "released_task_transition_set_sha256",
}
RELEASE_ADMISSION_FIELDS = {
    "g2_evidence_sha256",
    "canonical_record_bundle_sha256",
    "hosted_probe_sha256",
    "product_manifest_sha256",
    "product_manifest_sidecar_sha256",
    "target_task_snapshot_sha256",
    "reviewer",
    "review_verdict_sha256",
    "g2_issued_at",
    "closeout_at",
}
RELEASE_TRANSITION_FIELDS = {
    "task_id",
    "before_task_snapshot_sha256",
    "after_task_snapshot_sha256",
    "before_status",
    "after_status",
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


def status_has_pending_program_activity_outbox(status: dict[str, Any]) -> bool:
    """Treat every present non-null dispatcher transaction as pending."""

    return status.get("program_activity_outbox") is not None


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
    return bool(
        _has_exact_authority_source_ref(task)
        and transition.get("after_source_ref_sha256")
        == canonical_sha256(source_ref)
    )


def _validated_epoch(status: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    epochs = status.get("program_sequencing_epochs")
    epoch = epochs.get(PROGRAM_ID) if isinstance(epochs, dict) else None
    transitions = epoch.get("task_transitions") if isinstance(epoch, dict) else None
    if (
        not isinstance(epoch, dict)
        or set(epoch) != EPOCH_FIELDS
        or epoch.get("schema_version") != 1
        or epoch.get("program_id") != PROGRAM_ID
        or epoch.get("source_catalog_sha256") != SOURCE_CATALOG_SHA256
        or epoch.get("effective_catalog_sha256") != EFFECTIVE_CATALOG_SHA256
        or epoch.get("sequencing_overlay_sha256") != SEQUENCING_OVERLAY_SHA256
        or epoch.get("release_gate_id") != RELEASE_GATE_ID
        or epoch.get("install_mode")
        not in {"base_epoch_migration", "fresh_materialization"}
        or parse_utc(epoch.get("applied_at")) is None
        or any(
            not is_sha256(epoch.get(field))
            for field in (
                "source_graph_projection_sha256",
                "effective_graph_projection_sha256",
                "task_transition_set_sha256",
            )
        )
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
        if (
            not isinstance(transition, dict)
            or set(transition) != EPOCH_TRANSITION_FIELDS
            or transition.get("before_status") != expected_before_status
            or transition.get("after_status") not in {"blocked", "todo"}
            or any(
                not is_sha256(transition.get(field))
                for field in EPOCH_TRANSITION_FIELDS
                if field.endswith("sha256")
            )
            or (
                epoch["install_mode"] == "fresh_materialization"
                and transition.get("before_task_snapshot_sha256") != null_sha256
            )
            or (
                epoch["install_mode"] == "fresh_materialization"
                and transition.get("before_source_ref_sha256") != null_sha256
            )
            or (
                transition.get("after_status") == "blocked"
                and transition.get("gate_marker_sha256") == null_sha256
            )
            or (
                transition.get("after_status") == "todo"
                and transition.get("gate_marker_sha256") != null_sha256
            )
        ):
            return None
        task_id = str(transition.get("task_id") or "").strip()
        if not task_id:
            return None
        task_ids.append(task_id)
        if transition["after_status"] == "blocked":
            gated_ids.append(task_id)
    if tuple(task_ids) != EXPECTED_TASK_IDS or tuple(gated_ids) != EXPECTED_GATED_TASK_IDS:
        return None
    return epoch, transitions


def task_has_valid_sequencing_release_admission(
    task: dict[str, Any],
    status: dict[str, Any],
) -> bool:
    """Accept only the task-scoped tag from the exact immutable 19-task release."""

    task_id = str(task.get("id") or "").strip()
    epoch_result = _validated_epoch(status)
    if epoch_result is None:
        return False
    epoch, epoch_transitions = epoch_result
    epoch_by_id = {str(row["task_id"]): row for row in epoch_transitions}
    if (
        task_id not in EXPECTED_GATED_TASK_IDS
        or not _task_matches_epoch_authority(task, epoch_by_id[task_id])
    ):
        return False

    releases = status.get("program_sequencing_releases")
    record = releases.get(PROGRAM_ID) if isinstance(releases, dict) else None
    transitions = (
        record.get("released_task_transitions")
        if isinstance(record, dict)
        else None
    )
    task_admission = task.get("sequencing_release_admission_sha256")
    if (
        not is_sha256(task_admission)
        or not isinstance(record, dict)
        or set(record) != RELEASE_RECORD_FIELDS
        or record.get("schema_version") != 1
        or record.get("program_id") != PROGRAM_ID
        or record.get("effective_catalog_sha256") != EFFECTIVE_CATALOG_SHA256
        or record.get("sequencing_overlay_sha256") != SEQUENCING_OVERLAY_SHA256
        or record.get("release_gate_id") != RELEASE_GATE_ID
        or record.get("release_predicate") != RELEASE_PREDICATE
        or record.get("release_admission_sha256") != task_admission
        or not isinstance(record.get("reviewer"), str)
        or not record["reviewer"].strip()
        or not isinstance(transitions, list)
        or len(transitions) != len(EXPECTED_GATED_TASK_IDS)
    ):
        return False
    if any(
        not is_sha256(record.get(field))
        for field in (
            "g2_evidence_sha256",
            "canonical_record_bundle_sha256",
            "hosted_probe_sha256",
            "product_manifest_sha256",
            "product_manifest_sidecar_sha256",
            "target_task_snapshot_sha256",
            "review_verdict_sha256",
            "release_admission_sha256",
            "released_task_transition_set_sha256",
        )
    ):
        return False

    applied_at = parse_utc(epoch.get("applied_at"))
    closeout_at = parse_utc(record.get("closeout_at"))
    g2_issued_at = parse_utc(record.get("g2_issued_at"))
    released_at = parse_utc(record.get("released_at"))
    if (
        applied_at is None
        or closeout_at is None
        or g2_issued_at is None
        or released_at is None
        or closeout_at > g2_issued_at
        or g2_issued_at > released_at
        or applied_at > released_at
    ):
        return False
    admission = {
        field: record.get(field) for field in RELEASE_ADMISSION_FIELDS
    }
    if record.get("release_admission_sha256") != canonical_sha256(admission):
        return False

    transition_ids: list[str] = []
    for transition in transitions:
        task_id_value = str(
            transition.get("task_id") if isinstance(transition, dict) else ""
        ).strip()
        if (
            not isinstance(transition, dict)
            or set(transition) != RELEASE_TRANSITION_FIELDS
            or task_id_value not in epoch_by_id
            or transition.get("before_status") != "blocked"
            or transition.get("after_status") != "todo"
            or not is_sha256(transition.get("before_task_snapshot_sha256"))
            or not is_sha256(transition.get("after_task_snapshot_sha256"))
            or transition.get("before_task_snapshot_sha256")
            != epoch_by_id[task_id_value].get("after_task_snapshot_sha256")
        ):
            return False
        transition_ids.append(task_id_value)
    if (
        tuple(transition_ids) != EXPECTED_GATED_TASK_IDS
        or record.get("released_task_transition_set_sha256")
        != canonical_sha256(transitions)
    ):
        return False
    return True


def task_is_sequencing_parked(
    task: dict[str, Any],
    status: dict[str, Any] | None = None,
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
    epoch_gated = epoch_transition.get("after_status") == "blocked"
    if epoch_gated:
        return not task_has_valid_sequencing_release_admission(task, status)
    if classification not in PRE_G2_CLASSIFICATIONS:
        return True
    return False
