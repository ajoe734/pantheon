from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


RBAC_LABEL = "Authenticated: strict bearer RBAC matrix evidence passed."
DRY_RUN_LABEL = "Authenticated: strict live dry-run evidence has BffErrorEnvelope and no side effects."
APPROVAL_LABEL = "Authenticated: strict multi-operator approval race evidence is bounded."
TWO_MAN_LABEL = "Authenticated: strict two-man-sign race evidence is operator-scoped."
SSE_LABEL = "Authenticated: strict SSE soak observes heartbeat and no duplicate replay."
CURRENT_RUN_LABEL = "Evidence written to `.lovable/audits/current-run`."
BEARER_SHAPE_REQUIRED_SOURCES = (
    "smoke",
    "rbac:viewer",
    "rbac:operator",
    "rbac:reviewer",
    "rbac:approver",
    "rbac:admin",
    "rbac:empty",
    "rbac:unknown",
    "approval_race:a",
    "approval_race:b",
)
CROSS_SECRET_REQUIRED_SOURCES = BEARER_SHAPE_REQUIRED_SOURCES
RBAC_REQUIRED_LABELS = ("viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown")
MIN_BEARER_TOKEN_LENGTH = 12
TARGET_URL = "https://pantheon-bff-dev.example.test"


def strict_live_evidence_run(**overrides: str) -> dict[str, str]:
    payload = {
        "github_environment": "dev",
        "github_run_id": "123456789",
        "github_run_attempt": "1",
        "github_workflow": "Stage 0 CI",
        "github_job": "live-evidence",
        "repository": "ajoe734/pantheon",
        "ref": "refs/heads/dev",
        "sha": "a" * 40,
    }
    payload.update(overrides)
    return payload


def run_verifier(artifact_dir: Path) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "verify_bff_live_evidence_artifact.py"), str(artifact_dir), "--json"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )


def write_summary(artifact_dir: Path, *, status: str = "pass") -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    gate3 = [
        {"label": SSE_LABEL, "status": status, "note": "sse evidence"},
        {"label": RBAC_LABEL, "status": status, "note": "rbac evidence"},
        {"label": DRY_RUN_LABEL, "status": status, "note": "dry run evidence"},
        {"label": APPROVAL_LABEL, "status": status, "note": "approval evidence"},
        {"label": TWO_MAN_LABEL, "status": status, "note": "two-man evidence"},
    ]
    gate7 = [{"label": CURRENT_RUN_LABEL, "status": "pass", "note": "3 audit file(s) found"}]
    (artifact_dir / "release-gate-summary.json").write_text(
        json.dumps({"overall": status, "gates": {"3": gate3, "7": gate7}}),
        encoding="utf-8",
    )


def dry_run_side_effect_entries() -> list[dict[str, object]]:
    def meta(kind: str, digest: str = "") -> dict[str, object]:
        item: dict[str, object] = {
            "kind": kind,
            "ok": True,
            "dryRun": True,
            "durable": False,
            "liveCapitalSideEffects": False,
        }
        if digest:
            item["target_id_sha256_12"] = digest
        return item

    def readback(family: str, digest: str) -> dict[str, object]:
        path = (
            "/bff/strategies/dry-run-generated"
            if family == "dry-run-strategy-create"
            else "/bff/ranking-formulas/dry-run-generated"
        )
        return {
            "family": f"{family}-readback-not-persisted",
            "method": "GET",
            "path": path,
            "status": 404,
            "ok": True,
            "error_envelope": True,
            "error_code": "RESOURCE_NOT_FOUND",
            "side_effect_check": {
                "kind": "readback_not_persisted",
                "ok": True,
                "target_family": family,
                "target_id_sha256_12": digest,
                "error_code": "RESOURCE_NOT_FOUND",
            },
        }

    def validation(family: str) -> dict[str, object]:
        path = "/bff/strategies" if family == "dry-run-invalid-strategy" else "/bff/ranking-formulas"
        return {
            "family": family,
            "method": "POST",
            "path": path,
            "status": 422,
            "ok": True,
            "error_envelope": True,
            "error_code": "VALIDATION_FAILED",
            "side_effect_check": {
                "kind": "validation_rejected_before_persistence",
                "ok": True,
                "error_code": "VALIDATION_FAILED",
            },
        }

    return [
        {
            "family": "dry-run-strategy-create",
            "method": "POST",
            "path": "/bff/strategies",
            "status": 200,
            "ok": True,
            "error_envelope": False,
            "side_effect_check": meta("dry_run_preview_meta", "abc123abc123"),
        },
        readback("dry-run-strategy-create", "abc123abc123"),
        {
            "family": "dry-run-ranking-formula-create",
            "method": "POST",
            "path": "/bff/ranking-formulas",
            "status": 200,
            "ok": True,
            "error_envelope": False,
            "side_effect_check": meta("dry_run_preview_meta", "def456def456"),
        },
        readback("dry-run-ranking-formula-create", "def456def456"),
        {
            "family": "dry-run-v5-intervention-claim",
            "method": "POST",
            "path": "/bff/v5/interventions/int-live-dry-run/claim",
            "status": 200,
            "ok": True,
            "error_envelope": False,
            "side_effect_check": meta("dry_run_command_meta"),
        },
        validation("dry-run-invalid-strategy"),
        validation("dry-run-invalid-ranking-formula"),
    ]


def strict_rbac_auth_source() -> dict[str, object]:
    labels = ["viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    cases: dict[str, object] = {"anonymous": {"kind": "anonymous"}}
    cases.update({label: {"kind": "provided_bearer", "sha256_12": f"rbac-{label}-hash"} for label in labels})
    return {
        "kind": "rbac_matrix",
        "cases": cases,
        "provided_bearer_count": len(labels),
        "distinct_provided_bearer_count": len(labels),
        "distinct_provided_bearers": True,
        "duplicate_bearer_label_groups": [],
    }


def strict_rbac_matrix_entries() -> list[dict[str, object]]:
    labels = ["anonymous", "viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown"]
    read_paths = {
        "bff-strategies": "/bff/strategies",
        "bff-ranking-formulas": "/bff/ranking-formulas",
        "bff-agora-signals": "/bff/agora/signals",
    }
    write_paths = {
        "strategy": "/bff/strategies",
        "ranking-formula": "/bff/ranking-formulas",
        "agora-note": "/bff/agora/notes",
        "intervention-claim": "/bff/v5/interventions/int-live-rbac-matrix/claim",
    }
    read_allowed = {"viewer", "operator", "reviewer", "approver", "admin"}
    write_allowed = {"operator", "reviewer", "approver", "admin"}
    items: list[dict[str, object]] = []
    for label in labels:
        for resource, path in read_paths.items():
            denied = label not in read_allowed
            item: dict[str, object] = {
                "family": f"rbac-read-{label}-{resource}",
                "method": "GET",
                "path": path,
                "status": 403 if denied else 200,
                "ok": True,
                "error_envelope": denied,
                "rbac_label": label,
                "rbac_operation": "read",
                "rbac_resource": resource,
                "auth_case_kind": "anonymous" if label == "anonymous" else "provided_bearer",
            }
            if denied:
                item["error_code"] = "FORBIDDEN"
            if label != "anonymous":
                item["request_bearer_sha256_12"] = f"rbac-{label}-hash"
            items.append(item)
    for label in labels:
        for resource, path in write_paths.items():
            denied = label not in write_allowed
            marker_hash = f"marker-{label}-{resource}"
            item = {
                "family": f"rbac-write-{label}-{resource}",
                "method": "POST",
                "path": path,
                "status": 403 if denied else 200,
                "ok": True,
                "error_envelope": denied,
                "request_marker_sha256_12": marker_hash,
                "rbac_label": label,
                "rbac_operation": "write",
                "rbac_resource": resource,
                "auth_case_kind": "anonymous" if label == "anonymous" else "provided_bearer",
            }
            if label != "anonymous":
                item["request_bearer_sha256_12"] = f"rbac-{label}-hash"
            if denied:
                item["error_code"] = "FORBIDDEN"
                item["side_effect_check"] = {
                    "kind": "authorization_rejected_before_persistence",
                    "ok": True,
                    "error_code": "FORBIDDEN",
                    "target_marker_sha256_12": marker_hash,
                }
            else:
                readback = None
                if resource == "agora-note":
                    readback = {
                        "kind": "list_readback_not_persisted",
                        "ok": True,
                        "target_id_sha256_12": f"target-{label}-{resource}",
                        "status": 200,
                        "absent_checks": 2,
                        "items_checked": 3,
                    }
                elif resource in {"strategy", "ranking-formula"}:
                    readback = {
                        "kind": "readback_not_persisted",
                        "ok": True,
                        "target_id_sha256_12": f"target-{label}-{resource}",
                        "status": 404,
                        "error_envelope": True,
                        "error_code": "RESOURCE_NOT_FOUND",
                    }
                item["side_effect_check"] = {
                    "kind": "rbac_dry_run_write_meta",
                    "ok": True,
                    "dryRun": True,
                    "durable": False,
                    "liveCapitalSideEffects": False,
                    "target_marker_sha256_12": marker_hash,
                }
                if readback is not None:
                    item["side_effect_check"]["readback_not_persisted"] = readback
            items.append(item)
    return items


def strict_approval_race_entry() -> dict[str, object]:
    race_path = "/bff/approvals/approval-live-race/decide"
    target_hash = "approvaltarget"
    return {
        "family": "approval-race",
        "method": "POST",
        "path": race_path,
        "status": "202/409",
        "target_id_sha256_12": target_hash,
        "duration_ms": 42,
        "ok": True,
        "bounded": True,
        "accepted_count": 1,
        "safe_error_count": 1,
        "duplicate_winners": False,
        "concurrency": {
            "timing_proof": "monotonic_ms_relative_to_race_start",
            "actor_count": 2,
            "start_skew_ms": 3.0,
            "overlap_ms": 37.0,
            "concurrent": True,
        },
        "token_source": {
            "kind": "provided_bearer_pair",
            "token_a_sha256_12": "race-token-a",
            "token_b_sha256_12": "race-token-b",
        },
        "results": [
            {
                "family": "approval-race-a",
                "method": "POST",
                "path": race_path,
                "status": 202,
                "ok": True,
                "error_envelope": False,
                "actor_label": "a",
                "target_id_sha256_12": target_hash,
                "request_bearer_sha256_12": "race-token-a",
                "request_idempotency_key_sha256_12": "approval-idem-a",
                "race_timing": {"start_ms": 1.0, "end_ms": 41.0, "duration_ms": 40.0},
            },
            {
                "family": "approval-race-b",
                "method": "POST",
                "path": race_path,
                "status": 409,
                "ok": True,
                "error_envelope": True,
                "error_code": "STATE_CONFLICT",
                "actor_label": "b",
                "target_id_sha256_12": target_hash,
                "request_bearer_sha256_12": "race-token-b",
                "request_idempotency_key_sha256_12": "approval-idem-b",
                "race_timing": {"start_ms": 4.0, "end_ms": 42.0, "duration_ms": 38.0},
            },
        ],
    }


def strict_two_man_race_entry() -> dict[str, object]:
    race_path = "/bff/v5/interventions/intervention-live-race/two-man-sign"
    target_hash = "twomantarget1"
    return {
        "family": "two-man-race",
        "method": "POST",
        "path": race_path,
        "status": "202/202",
        "target_id_sha256_12": target_hash,
        "duration_ms": 37,
        "ok": True,
        "operator_scoped": True,
        "accepted_count": 2,
        "replayed_count": 0,
        "distinct_command_ids": True,
        "command_id_count": 2,
        "concurrency": {
            "timing_proof": "monotonic_ms_relative_to_race_start",
            "actor_count": 2,
            "start_skew_ms": 1.0,
            "overlap_ms": 32.0,
            "concurrent": True,
        },
        "token_source": {
            "kind": "provided_bearer_pair",
            "token_a_sha256_12": "race-token-a",
            "token_b_sha256_12": "race-token-b",
        },
        "results": [
            {
                "family": "two-man-race",
                "method": "POST",
                "path": race_path,
                "status": 202,
                "ok": True,
                "error_envelope": False,
                "actor_label": "a",
                "target_id_sha256_12": target_hash,
                "request_bearer_sha256_12": "race-token-a",
                "request_idempotency_key_sha256_12": "two-man-shared-idem",
                "request_signature_id_sha256_12": "two-man-sig-a",
                "race_timing": {"start_ms": 2.0, "end_ms": 35.0, "duration_ms": 33.0},
                "extracted": {
                    "meta.idempotency.replayed": False,
                    "data.command_id": "command-a",
                },
            },
            {
                "family": "two-man-race",
                "method": "POST",
                "path": race_path,
                "status": 202,
                "ok": True,
                "error_envelope": False,
                "actor_label": "b",
                "target_id_sha256_12": target_hash,
                "request_bearer_sha256_12": "race-token-b",
                "request_idempotency_key_sha256_12": "two-man-shared-idem",
                "request_signature_id_sha256_12": "two-man-sig-b",
                "race_timing": {"start_ms": 3.0, "end_ms": 36.0, "duration_ms": 33.0},
                "extracted": {
                    "meta.idempotency.replayed": False,
                    "data.command_id": "command-b",
                },
            },
        ],
    }


def write_strict_auth_json(artifact_dir: Path) -> None:
    (artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json").write_text(
        json.dumps(
            {
                "strict_live_evidence": True,
                "strict_live_evidence_run": strict_live_evidence_run(),
                "target_url": TARGET_URL,
                "auth_source": {"kind": "provided_bearer"},
                "rbac_auth_source": strict_rbac_auth_source(),
                "include_writes": True,
                "include_rbac_matrix": True,
                "include_dry_run": True,
                "include_approval_race": True,
                "include_two_man_race": True,
                "summary": {
                    "rbac_matrix_probes": 56,
                    "rbac_write_probes": 32,
                    "rbac_write_side_effect_proofs": 32,
                    "dry_run_probes": 7,
                    "approval_race_probes": 1,
                    "approval_race_bounded": True,
                    "two_man_race_probes": 1,
                    "two_man_race_operator_scoped": True,
                    "live_capital_side_effects": False,
                },
                "rbac_matrix": strict_rbac_matrix_entries(),
                "dry_run": dry_run_side_effect_entries(),
                "approval_race": strict_approval_race_entry(),
                "two_man_race": strict_two_man_race_entry(),
            }
        ),
        encoding="utf-8",
    )


def strict_sse_reconnect_bearer(attempt_count: int = 5) -> dict[str, object]:
    expected_event_ids = [f"evt-{index}" for index in range(2, 2 + attempt_count)]
    cursor_event_ids = [f"evt-{index}" for index in range(1, 1 + attempt_count)]
    attempts = []
    for index, expected_event_id in enumerate(expected_event_ids):
        cursor_event_id = cursor_event_ids[index]
        attempts.append(
            {
                "mode": "bearer_polyfill",
                "ok": True,
                "attempt": index + 1,
                "cursor_event_id": cursor_event_id,
                "expected_replayed_event_id": expected_event_id,
                "observed_replayed_event_id": expected_event_id,
                "replayed_expected_event": True,
                "url_path": "/bff/events/stream?channel=approval",
                "request_headers": {
                    "Authorization": "present",
                    "Cookie": "absent",
                    "Last-Event-ID": cursor_event_id,
                    "Accept": "text/event-stream",
                },
                "response_headers": {
                    "X-SSE-Channel": "approval",
                    "X-SSE-Replay-Supported": "true",
                },
                "lineage_checks": {
                    "last_event_id_sent": True,
                    "response_channel_ok": True,
                    "response_replay_supported": True,
                    "sse_id_line_matches_observed": True,
                    "data_id_matches_observed": True,
                    "shape_id_matches_data": True,
                    "shape_event_matches_type": True,
                    "data_json_parse_ok": True,
                },
            }
        )
    return {
        "mode": "bearer_polyfill",
        "ok": True,
        "attempt_count": attempt_count,
        "cursor_event_ids": cursor_event_ids,
        "expected_event_ids": expected_event_ids,
        "observed_event_ids": list(expected_event_ids),
        "missing_expected_event_ids": [],
        "duplicate_event_ids": [],
        "cursors_advanced": True,
        "attempts": attempts,
    }


def write_strict_sse_json(artifact_dir: Path) -> None:
    (artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json").write_text(
        json.dumps(
            {
                "strict_live_evidence": True,
                "strict_live_evidence_run": strict_live_evidence_run(),
                "target_url": TARGET_URL,
                "channel": "approval",
                "auth_source": {"kind": "provided_bearer", "token_sha256_12": "abcdef123456"},
                "strict_live_evidence_requirements": {
                    "min_soak_seconds": 75,
                    "min_heartbeats": 1,
                    "min_reconnect_attempts": 5,
                },
                "summary": {"passed": True},
                "soak": {
                    "enabled": True,
                    "seconds": 75.0,
                    "min_heartbeats": 1,
                    "bearer_polyfill": {
                        "ok": True,
                        "request_headers": {
                            "Authorization": "present",
                            "Cookie": "absent",
                            "Accept": "text/event-stream",
                        },
                        "missing_expected_event_ids": [],
                        "blocks": {
                            "heartbeat_count": 2,
                            "duplicate_event_ids": [],
                        },
                    },
                },
                "reconnect_sequence": {"bearer_polyfill": strict_sse_reconnect_bearer()},
            }
        ),
        encoding="utf-8",
    )


def strict_preflight_rbac_matrix(
    *,
    present: bool = True,
    duplicate_groups: list[list[str]] | None = None,
) -> dict[str, object]:
    present_labels = list(RBAC_REQUIRED_LABELS) if present else []
    duplicate_groups = duplicate_groups or []
    return {
        "required_labels": list(RBAC_REQUIRED_LABELS),
        "present_labels": present_labels,
        "missing_labels": [label for label in RBAC_REQUIRED_LABELS if label not in set(present_labels)],
        "provided_cases": len(present_labels),
        "expected_cases": len(RBAC_REQUIRED_LABELS),
        "distinct_bearers": present and not duplicate_groups,
        "distinct_bearer_count": len(present_labels) - len(duplicate_groups),
        "duplicate_label_groups": duplicate_groups,
    }


def strict_preflight_approval_race_tokens(*, present: bool = True, distinct: bool = True) -> dict[str, object]:
    return {
        "token_a_present": present,
        "token_b_present": present,
        "distinct_bearers": present and distinct,
    }


def strict_preflight_cross_secret(
    *,
    present: bool = True,
    duplicate_groups: list[list[str]] | None = None,
) -> dict[str, object]:
    present_sources = list(CROSS_SECRET_REQUIRED_SOURCES) if present else []
    duplicate_groups = duplicate_groups or []
    return {
        "required_sources": list(CROSS_SECRET_REQUIRED_SOURCES),
        "present_sources": present_sources,
        "missing_sources": [source for source in CROSS_SECRET_REQUIRED_SOURCES if source not in set(present_sources)],
        "provided_sources": len(present_sources),
        "expected_sources": len(CROSS_SECRET_REQUIRED_SOURCES),
        "distinct_bearers": present and not duplicate_groups,
        "distinct_bearer_count": len(present_sources) - len(duplicate_groups),
        "duplicate_source_groups": duplicate_groups,
    }


def strict_preflight_bearer_shape(
    *,
    checked: bool = True,
    valid: bool = True,
    invalid_sources: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "required_sources": list(BEARER_SHAPE_REQUIRED_SOURCES),
        "checked_sources": list(BEARER_SHAPE_REQUIRED_SOURCES) if checked else [],
        "valid_sources": list(BEARER_SHAPE_REQUIRED_SOURCES) if valid else [],
        "invalid_sources": invalid_sources or [],
        "min_length": MIN_BEARER_TOKEN_LENGTH,
        "placeholder_values_rejected": True,
    }


def write_passing_artifact(artifact_dir: Path) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LIVE-EVIDENCE-PREFLIGHT",
                "strict_live_evidence_preflight": True,
                "strict_live_evidence_run": strict_live_evidence_run(),
                "github_environment": "dev",
                "target_url": TARGET_URL,
                "missing": [],
                "invalid": [],
                "output_scope": ".lovable/audits/current-run",
                "ref": "refs/heads/dev",
                "sha": "a" * 40,
                "secret_values_written": False,
                "rbac_matrix": strict_preflight_rbac_matrix(),
                "approval_race_tokens": strict_preflight_approval_race_tokens(),
                "cross_secret_bearers": strict_preflight_cross_secret(),
                "bearer_shape": strict_preflight_bearer_shape(),
            }
        ),
        encoding="utf-8",
    )
    write_summary(artifact_dir)
    write_strict_auth_json(artifact_dir)
    write_strict_sse_json(artifact_dir)


def test_verifier_accepts_complete_strict_live_artifact(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)

    result = run_verifier(artifact_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["overall"] == "pass"
    assert payload["criteria"]["rbac_matrix"]["status"] == "pass"
    assert payload["criteria"]["dry_run_no_side_effects"]["status"] == "pass"
    assert payload["criteria"]["approval_race"]["status"] == "pass"
    assert payload["criteria"]["two_man_race"]["status"] == "pass"
    assert payload["criteria"]["sse_reconnect_soak"]["status"] == "pass"
    assert payload["criteria"]["current_run_only"]["status"] == "pass"
    assert payload["criteria"]["raw_secret_scan"]["status"] == "pass"


def test_verifier_rejects_auth_json_from_stale_sha_even_when_summary_passes(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["strict_live_evidence_run"]["sha"] = "b" * 40
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    for key in ("rbac_matrix", "dry_run_no_side_effects", "approval_race", "two_man_race"):
        item = payload["criteria"][key]
        assert item["status"] == "fail"
        assert "runProvenance:sha" in item["note"]


def test_verifier_rejects_sse_json_from_stale_run_id_even_when_summary_passes(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    sse_path = artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json"
    sse = json.loads(sse_path.read_text(encoding="utf-8"))
    sse["strict_live_evidence_run"]["github_run_id"] = "987654321"
    sse_path.write_text(json.dumps(sse), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["sse_reconnect_soak"]
    assert item["status"] == "fail"
    assert "runProvenance:github_run_id" in item["note"]


def test_verifier_rejects_sse_json_from_different_target_url_even_when_summary_passes(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    sse_path = artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json"
    sse = json.loads(sse_path.read_text(encoding="utf-8"))
    sse["target_url"] = "https://pantheon-bff-staging.example.test"
    sse_path.write_text(json.dumps(sse), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["sse_reconnect_soak"]
    assert item["status"] == "fail"
    assert "runProvenance:target_url" in item["note"]



def test_verifier_rejects_rbac_matrix_without_detail_links_even_when_summary_passes(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["rbac_matrix"][0].pop("rbac_label")
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["rbac_matrix"]
    assert item["status"] == "fail"
    assert "detailLinks:55/56" in item["note"]



def test_verifier_rejects_rbac_matrix_without_distinct_provided_bearers(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    source = auth["rbac_auth_source"]
    source["distinct_provided_bearers"] = False
    source["distinct_provided_bearer_count"] = 6
    source["duplicate_bearer_label_groups"] = [["viewer", "operator"]]
    source["cases"]["operator"]["sha256_12"] = source["cases"]["viewer"]["sha256_12"]
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["rbac_matrix"]
    assert item["status"] == "fail"
    assert "distinctBearers:6/7" in item["note"]


def test_verifier_rejects_rbac_matrix_request_path_swap(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    read_item = next(item for item in auth["rbac_matrix"] if item["family"] == "rbac-read-viewer-bff-ranking-formulas")
    read_item["path"] = "/bff/strategies"
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["rbac_matrix"]
    assert item["status"] == "fail"
    assert "requestLinks:55/56" in item["note"]
    assert "rbac-request-link" in item["note"]


def test_verifier_rejects_rbac_allowed_read_without_success_status(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    read_item = next(item for item in auth["rbac_matrix"] if item["family"] == "rbac-read-viewer-bff-strategies")
    read_item["status"] = 204
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["rbac_matrix"]
    assert item["status"] == "fail"
    assert "readAllowed:14/15" in item["note"]
    assert "read-allowed-status" in item["note"]


def test_verifier_rejects_rbac_denied_write_without_forbidden_status(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    write_item = next(item for item in auth["rbac_matrix"] if item["family"] == "rbac-write-viewer-strategy")
    write_item["status"] = 200
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["rbac_matrix"]
    assert item["status"] == "fail"
    assert "writeDenials:15/16" in item["note"]
    assert "write-denial-status" in item["note"]



def test_verifier_rejects_rbac_write_without_side_effect_proof(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    write_item = next(item for item in auth["rbac_matrix"] if item["family"] == "rbac-write-operator-strategy")
    write_item.pop("side_effect_check")
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["rbac_matrix"]
    assert item["status"] == "fail"
    assert "writeSideEffectProofs:31/32" in item["note"]


def test_verifier_rejects_rbac_write_without_readback_proof(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    write_item = next(item for item in auth["rbac_matrix"] if item["family"] == "rbac-write-operator-strategy")
    write_item["side_effect_check"].pop("readback_not_persisted")
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["rbac_matrix"]
    assert item["status"] == "fail"
    assert "writeReadbackProofs:11/12" in item["note"]



def test_verifier_rejects_approval_race_without_detail_results_even_when_summary_passes(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["approval_race"] = {"ok": True, "bounded": True}
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["approval_race"]
    assert item["status"] == "fail"
    assert "approvalResults:0/2" in item["note"]


def test_verifier_rejects_approval_race_without_distinct_provided_bearers(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["approval_race"]["token_source"]["token_b_sha256_12"] = "race-token-a"
    auth["approval_race"]["results"][1]["request_bearer_sha256_12"] = "race-token-a"
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["approval_race"]
    assert item["status"] == "fail"
    assert "distinctTokens:1/2" in item["note"]


def test_verifier_rejects_approval_race_without_concurrency_timing(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["approval_race"].pop("concurrency")
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["approval_race"]
    assert item["status"] == "fail"
    assert "raceTiming:missing" in item["note"]


def test_verifier_rejects_two_man_race_with_idempotency_replay(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["two_man_race"]["results"][1]["extracted"]["meta.idempotency.replayed"] = True
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["two_man_race"]
    assert item["status"] == "fail"
    assert "replayed:1/0" in item["note"]


def test_verifier_rejects_two_man_race_without_timing_overlap(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["two_man_race"]["concurrency"] = {
        "timing_proof": "monotonic_ms_relative_to_race_start",
        "actor_count": 2,
        "start_skew_ms": 50.0,
        "overlap_ms": -40.0,
        "concurrent": True,
    }
    auth["two_man_race"]["results"][0]["race_timing"] = {"start_ms": 0.0, "end_ms": 10.0, "duration_ms": 10.0}
    auth["two_man_race"]["results"][1]["race_timing"] = {"start_ms": 50.0, "end_ms": 80.0, "duration_ms": 30.0}
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["two_man_race"]
    assert item["status"] == "fail"
    assert "overlapMs:-40.0" in item["note"]
    assert "timing-overlap" in item["note"]


def test_verifier_rejects_dry_run_missing_side_effect_detail_even_when_summary_passes(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["dry_run"][0].pop("side_effect_check")
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["dry_run_no_side_effects"]
    assert item["status"] == "fail"
    assert "side-effect-check-missing" in item["note"]


def test_verifier_rejects_dry_run_validation_without_error_envelope(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    invalid = next(
        item
        for item in auth["dry_run"]
        if item["side_effect_check"]["kind"] == "validation_rejected_before_persistence"
    )
    invalid["error_envelope"] = False
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["dry_run_no_side_effects"]
    assert item["status"] == "fail"
    assert "validation-error-envelope" in item["note"]


def test_verifier_rejects_dry_run_readback_target_mismatch(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    readback = next(
        item
        for item in auth["dry_run"]
        if item["family"] == "dry-run-strategy-create-readback-not-persisted"
    )
    readback["side_effect_check"]["target_id_sha256_12"] = "wrongtarget12"
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["dry_run_no_side_effects"]
    assert item["status"] == "fail"
    assert "readback-target-link:dry-run-strategy-create" in item["note"]


def test_verifier_rejects_dry_run_request_path_swap(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    auth_path = artifact_dir / "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    meta = next(item for item in auth["dry_run"] if item["family"] == "dry-run-ranking-formula-create")
    meta["path"] = "/bff/strategies"
    auth_path.write_text(json.dumps(auth), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["dry_run_no_side_effects"]
    assert item["status"] == "fail"
    assert "meta-request-link" in item["note"]


def test_verifier_fails_when_preflight_rbac_matrix_is_missing(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight.pop("rbac_matrix")
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert item["note"] == "rbac_matrix:missing"


def test_verifier_fails_when_ready_preflight_rbac_matrix_is_incomplete(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["rbac_matrix"]["present_labels"].remove("unknown")
    preflight["rbac_matrix"]["missing_labels"] = ["unknown"]
    preflight["rbac_matrix"]["provided_cases"] = 6
    preflight["rbac_matrix"]["distinct_bearer_count"] = 6
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert "present_labels:6/7" in item["note"]
    assert "missing_labels_ready" in item["note"]


def test_verifier_rejects_rbac_matrix_duplicate_groups_without_matching_invalid(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["rbac_matrix"] = strict_preflight_rbac_matrix(
        duplicate_groups=[["viewer", "operator"]],
    )
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert "duplicate_groups_without_invalid" in item["note"]


def test_verifier_fails_when_preflight_approval_race_tokens_are_missing(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight.pop("approval_race_tokens")
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert item["note"] == "approval_race_tokens:missing"


def test_verifier_fails_when_ready_preflight_approval_race_tokens_are_not_distinct(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["approval_race_tokens"] = strict_preflight_approval_race_tokens(distinct=False)
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert item["note"] == "approval_race_tokens:distinct_bearers"


def test_verifier_fails_when_preflight_cross_secret_bearers_are_missing(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight.pop("cross_secret_bearers")
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert item["note"] == "cross_secret_bearers:missing"


def test_verifier_fails_when_ready_preflight_cross_secret_sources_are_incomplete(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["cross_secret_bearers"]["present_sources"].remove("approval_race:b")
    preflight["cross_secret_bearers"]["missing_sources"] = ["approval_race:b"]
    preflight["cross_secret_bearers"]["provided_sources"] = 9
    preflight["cross_secret_bearers"]["distinct_bearer_count"] = 9
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert "present_sources:9/10" in item["note"]
    assert "missing_sources_ready" in item["note"]


def test_verifier_rejects_cross_secret_duplicate_groups_without_matching_invalid(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["cross_secret_bearers"] = strict_preflight_cross_secret(
        duplicate_groups=[["smoke", "rbac:viewer"]],
    )
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert "duplicate_groups_without_invalid" in item["note"]


def test_verifier_allows_cross_secret_duplicate_groups_only_when_preflight_reports_cross_secret_invalid(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["invalid"] = [
        {
            "name": "PANTHEON_BFF_LIVE_EVIDENCE_BEARERS",
            "reason": "bearer tokens must be unique across smoke, RBAC, and approval race sources: smoke/rbac:viewer",
        }
    ]
    preflight["cross_secret_bearers"] = strict_preflight_cross_secret(
        duplicate_groups=[["smoke", "rbac:viewer"]],
    )
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert item["note"] == "environment:dev invalid:PANTHEON_BFF_LIVE_EVIDENCE_BEARERS"


def test_verifier_fails_when_preflight_bearer_shape_is_missing(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight.pop("bearer_shape")
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert item["note"] == "bearer_shape:missing"


def test_verifier_fails_when_ready_preflight_bearer_shape_sources_are_incomplete(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["bearer_shape"]["checked_sources"].remove("rbac:unknown")
    preflight["bearer_shape"]["valid_sources"].remove("approval_race:b")
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert "checked_sources:9/10" in item["note"]
    assert "valid_sources:9/10" in item["note"]


def test_verifier_allows_bearer_shape_invalid_sources_only_when_preflight_reports_shape_invalid(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["invalid"] = [
        {
            "name": "PANTHEON_BFF_LIVE_EVIDENCE_BEARER_SHAPE",
            "reason": "bearer tokens must not be placeholders and must be at least 12 characters: rbac:viewer=placeholder_prefix",
        }
    ]
    preflight["bearer_shape"] = strict_preflight_bearer_shape(
        valid=False,
        invalid_sources=[{"source": "rbac:viewer", "reason": "placeholder_prefix"}],
    )
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert item["note"] == "environment:dev invalid:PANTHEON_BFF_LIVE_EVIDENCE_BEARER_SHAPE"


def test_verifier_fails_preflight_blocked_artifact(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LIVE-EVIDENCE-PREFLIGHT",
                "strict_live_evidence_preflight": True,
                "strict_live_evidence_run": strict_live_evidence_run(sha="b" * 40),
                "github_environment": "dev",
                "target_url": TARGET_URL,
                "missing": ["PANTHEON_BFF_SMOKE_BEARER_TOKEN", "PANTHEON_BFF_RBAC_TOKENS_JSON"],
                "invalid": [],
                "output_scope": ".lovable/audits/current-run",
                "ref": "refs/heads/dev",
                "sha": "b" * 40,
                "secret_values_written": False,
                "rbac_matrix": strict_preflight_rbac_matrix(present=False),
                "approval_race_tokens": strict_preflight_approval_race_tokens(present=False),
                "cross_secret_bearers": strict_preflight_cross_secret(present=False),
                "bearer_shape": strict_preflight_bearer_shape(checked=False, valid=False),
            }
        ),
        encoding="utf-8",
    )

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    assert payload["criteria"]["preflight_ready"]["status"] == "fail"
    assert "PANTHEON_BFF_SMOKE_BEARER_TOKEN" in payload["criteria"]["preflight_ready"]["note"]
    assert payload["criteria"]["rbac_matrix"]["status"] == "missing"


def test_verifier_fails_when_preflight_secret_safety_flag_is_missing(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    (artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").write_text(
        json.dumps(
            {
                "task_id": "BFF-LIVE-EVIDENCE-PREFLIGHT",
                "strict_live_evidence_preflight": True,
                "strict_live_evidence_run": strict_live_evidence_run(sha="c" * 40),
                "github_environment": "dev",
                "target_url": TARGET_URL,
                "missing": [],
                "invalid": [],
                "output_scope": ".lovable/audits/current-run",
                "ref": "refs/heads/dev",
                "sha": "c" * 40,
            }
        ),
        encoding="utf-8",
    )

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    assert payload["criteria"]["preflight_ready"]["status"] == "fail"
    assert "secret_values_written must be false" in payload["criteria"]["preflight_ready"]["note"]


def test_verifier_fails_when_preflight_reports_secret_values_written(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight = json.loads((artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").read_text(encoding="utf-8"))
    preflight["secret_values_written"] = True
    (artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json").write_text(
        json.dumps(preflight),
        encoding="utf-8",
    )

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    assert payload["criteria"]["preflight_ready"]["status"] == "fail"
    assert "secret_values_written must be false" in payload["criteria"]["preflight_ready"]["note"]


def test_verifier_rejects_master_preflight_provenance_even_when_checks_pass(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["ref"] = "refs/heads/master"
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert item["note"] == "provenance:ref,strict_live_evidence_run.ref"


def test_verifier_rejects_missing_preflight_provenance_even_when_checks_pass(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight.pop("task_id")
    preflight["strict_live_evidence_preflight"] = False
    preflight["output_scope"] = ".lovable/audits/historical"
    preflight["github_environment"] = "production"
    preflight["sha"] = "not-a-sha"
    preflight_path.write_text(json.dumps(preflight), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["preflight_ready"]
    assert item["status"] == "fail"
    assert item["note"] == "provenance:task_id,strict_live_evidence_preflight,output_scope,github_environment,sha,strict_live_evidence_run.github_environment,strict_live_evidence_run.sha"


def test_verifier_rejects_raw_bearer_material_without_echoing_secret(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    leaked = "Bearer live-secret-token-abc123456"
    (artifact_dir / "leaky.json").write_text(json.dumps({"Authorization": leaked}), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    item = payload["criteria"]["raw_secret_scan"]
    assert item["status"] == "fail"
    assert "leaky.json:raw_bearer" in item["note"]
    assert "live-secret-token-abc123456" not in item["note"]


def test_verifier_rejects_jwt_shaped_material_without_echoing_secret(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    leaked = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiJsaXZlLXNtb2tlIn0."
        "signaturepart123456"
    )
    (artifact_dir / "jwt-leak.json").write_text(json.dumps({"token": leaked}), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    item = payload["criteria"]["raw_secret_scan"]
    assert item["status"] == "fail"
    assert "jwt-leak.json:jwt" in item["note"]
    assert leaked not in item["note"]


def test_verifier_rejects_sensitive_json_key_without_echoing_secret(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    leaked = "opaque-live-token-abc123456789"
    (artifact_dir / "sensitive-key.json").write_text(
        json.dumps({"auth": {"token": leaked}}),
        encoding="utf-8",
    )

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    item = payload["criteria"]["raw_secret_scan"]
    assert item["status"] == "fail"
    assert "sensitive-key.json:json_key:$.auth.token" in item["note"]
    assert leaked not in item["note"]


def test_verifier_accepts_redacted_sensitive_json_keys(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    (artifact_dir / "redacted-sensitive.json").write_text(
        json.dumps(
            {
                "authorization": "[REDACTED]",
                "access_token": "<redacted>",
                "client_secret": "***",
                "api_key": "sha256_12:abcdef123456",
            }
        ),
        encoding="utf-8",
    )

    result = run_verifier(artifact_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["criteria"]["raw_secret_scan"]["status"] == "pass"


def test_verifier_accepts_downloaded_current_run_bundle_root(tmp_path: Path) -> None:
    artifact_root = tmp_path / "downloaded"
    write_passing_artifact(artifact_root / "bff-live-evidence-current-run")

    result = run_verifier(artifact_root)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["overall"] == "pass"
    assert payload["criteria"]["current_run_only"]["status"] == "pass"


def test_verifier_rejects_non_current_run_sibling_paths_even_without_forbidden_names(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    old_runs = artifact_dir / "old-runs"
    old_runs.mkdir()
    (old_runs / "old-audit.json").write_text("{}", encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["current_run_only"]
    assert item["status"] == "fail"
    assert "outside current-run scope: old-runs/old-audit.json" in item["note"]


def test_verifier_rejects_historical_audit_paths_even_when_checks_pass(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    historical = artifact_dir / "historical"
    historical.mkdir()
    (historical / "old-audit.json").write_text("{}", encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["overall"] == "fail"
    assert payload["criteria"]["current_run_only"]["status"] == "fail"
    assert "historical/old-audit.json" in payload["criteria"]["current_run_only"]["note"]


def test_verifier_rejects_sse_without_provided_bearer_auth_source(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    sse_path = artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json"
    sse = json.loads(sse_path.read_text(encoding="utf-8"))
    sse["auth_source"] = {"kind": "minted_hs256_jwt", "secret_sha256_12": "abcdef123456"}
    sse_path.write_text(json.dumps(sse), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["sse_reconnect_soak"]
    assert item["status"] == "fail"
    assert "authSource:minted_hs256_jwt" in item["note"]
    assert "tokenHash:False" in item["note"]


def test_verifier_rejects_sse_reconnect_attempt_without_bearer_authorization(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    sse_path = artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json"
    sse = json.loads(sse_path.read_text(encoding="utf-8"))
    attempt = sse["reconnect_sequence"]["bearer_polyfill"]["attempts"][0]
    attempt["request_headers"]["Authorization"] = "absent"
    sse_path.write_text(json.dumps(sse), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["sse_reconnect_soak"]
    assert item["status"] == "fail"
    assert "bearerAttemptAuth:4/5" in item["note"]


def test_verifier_rejects_short_sse_reconnect_even_when_summary_claims_pass(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    (artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json").write_text(
        json.dumps(
            {
                "strict_live_evidence": True,
                "soak": {"seconds": 30.0},
                "reconnect_sequence": {"bearer_polyfill": {"ok": True, "attempt_count": 1}},
            }
        ),
        encoding="utf-8",
    )

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["criteria"]["sse_reconnect_soak"]["status"] == "fail"
    assert "soak:30/75" in payload["criteria"]["sse_reconnect_soak"]["note"]



def test_verifier_rejects_sse_without_heartbeat_or_duplicate_proof(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    sse_path = artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json"
    sse = json.loads(sse_path.read_text(encoding="utf-8"))
    blocks = sse["soak"]["bearer_polyfill"]["blocks"]
    blocks["heartbeat_count"] = 0
    blocks["duplicate_event_ids"] = ["evt-2"]
    sse_path.write_text(json.dumps(sse), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["sse_reconnect_soak"]
    assert item["status"] == "fail"
    assert "heartbeat:0/1" in item["note"]
    assert "duplicates:1" in item["note"]



def test_verifier_rejects_sse_when_requested_reconnect_attempts_are_not_proven(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    sse_path = artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json"
    sse = json.loads(sse_path.read_text(encoding="utf-8"))
    sse["strict_live_evidence_requirements"]["requested_reconnect_attempts"] = 7
    sse_path.write_text(json.dumps(sse), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["sse_reconnect_soak"]
    assert item["status"] == "fail"
    assert "reconnect:5/7" in item["note"]
    assert "observed:5/7" in item["note"]



def test_verifier_rejects_sse_reconnect_when_last_event_id_is_spoofed(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    sse_path = artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json"
    sse = json.loads(sse_path.read_text(encoding="utf-8"))
    bearer = sse["reconnect_sequence"]["bearer_polyfill"]
    bearer["attempts"][2]["request_headers"]["Last-Event-ID"] = "evt-wrong-cursor"
    sse_path.write_text(json.dumps(sse), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["sse_reconnect_soak"]
    assert item["status"] == "fail"
    assert "attemptLineage:False" in item["note"]



def test_verifier_rejects_sse_reconnect_without_lineage_or_observed_sequence(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifact"
    write_passing_artifact(artifact_dir)
    sse_path = artifact_dir / "BFF-CONSOL-011-sse-replay-smoke.json"
    sse = json.loads(sse_path.read_text(encoding="utf-8"))
    bearer = sse["reconnect_sequence"]["bearer_polyfill"]
    bearer["observed_event_ids"][-1] = bearer["observed_event_ids"][0]
    bearer["duplicate_event_ids"] = [bearer["observed_event_ids"][0]]
    bearer["attempts"][-1]["lineage_checks"]["last_event_id_sent"] = False
    sse_path.write_text(json.dumps(sse), encoding="utf-8")

    result = run_verifier(artifact_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    item = payload["criteria"]["sse_reconnect_soak"]
    assert item["status"] == "fail"
    assert "attemptLineage:False" in item["note"]
    assert "observedSequence:False" in item["note"]
    assert "duplicates:1" in item["note"]
