#!/usr/bin/env python3
"""Narrow recovery for a stranded schema-v1 activity rotation intent.

OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001.

This tool resolves exactly one incident class: a valid, fully staged
schema-v1 activity rotation intent whose transaction was superseded by a
later legacy timestamp rotation before the active-file swap completed. The
proven byte relationships are:

    staged_archive_payload + staged_tail == intent_source            (exact)
    installed_content_archive_payload   == staged_archive_payload    (exact)
    superseding_legacy_payload          == intent_source + appended  (exact)
    active                              == legacy_suffix(k) + newer  (exact)

Recovery never rewrites, truncates, renames, recompresses, or deletes the
active log, any archive, or any historical byte. It publishes one durable,
idempotent, crash-safe resolution record that registers the orphan
content-addressed archive as superseded, preserves the original intent and
staged files as immutable evidence copies, and only then removes the
pending intent marker so governed readers and writers can resume.

Modes:
  inventory  read-only capture of the incident state (no locks are opened).
  dry-run    read-only re-proof of every byte relationship against a pinned
             inventory; prints the proposed transaction without mutating.
  execute    the gated live transaction. Requires the exclusive activity
             lock, the exact pinned inventory digest, a stable-input
             recheck, an explicit writer-guard attestation, and the
             PANTHEON_ACTIVITY_PENDING_INTENT_RECOVERY_EXECUTE environment
             opt-in. Fails closed on any ambiguity.

If any byte or event relationship cannot be proved exactly and uniquely,
every mode fails closed with a diagnostic instead of inventing a repair.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

MANIFEST_SCHEMA_VERSION = 1
EXECUTE_ENV = "PANTHEON_ACTIVITY_PENDING_INTENT_RECOVERY_EXECUTE"
EXECUTE_ENV_VALUE = "I-UNDERSTAND-LIVE-MUTATION"
FAULT_ENV = "LOOP_TEST_PENDING_INTENT_RECOVERY_SIGKILL_AFTER"

PRESERVED_INTENT_NAME = "intent.json"
PRESERVED_STAGE_ARCHIVE_NAME = "staged-archive.gz"
PRESERVED_STAGE_TAIL_NAME = "staged-tail.bin"
PRESERVED_MANIFEST_NAME = "preserved-manifest.json"


class RecoveryProofError(RuntimeError):
    """A required byte/event relationship is ambiguous or violated."""


def _fault(point: str) -> None:
    """Process-test-only SIGKILL seam used to prove restart convergence."""

    requested = str(os.environ.get(FAULT_ENV) or "").strip()
    if requested == point:
        os.kill(os.getpid(), 9)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _line_count(payload: bytes) -> int:
    return len(payload.splitlines()) if payload else 0


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def manifest_digest(manifest: dict[str, Any]) -> str:
    return _sha(_canonical_json(manifest).encode("utf-8"))


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _relative_to_root(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _lstat_record(path: Path, root: Path) -> dict[str, Any]:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return {"relative_path": _relative_or_name(path, root), "exists": False}
    return {
        "relative_path": _relative_or_name(path, root),
        "exists": True,
        "is_symlink": stat.S_ISLNK(st.st_mode),
        "is_regular": stat.S_ISREG(st.st_mode),
        "byte_count": st.st_size,
        "inode": st.st_ino,
        "device": st.st_dev,
        "mtime_ns": st.st_mtime_ns,
    }


def _relative_or_name(path: Path, root: Path) -> str:
    try:
        return _relative_to_root(path, root)
    except ValueError:
        return path.name


def _read_artifact(
    path: Path,
    root: Path,
    *,
    source: str,
    gzip_payload: bool = False,
) -> tuple[dict[str, Any], bytes | None, bytes | None]:
    """lstat + stable O_NOFOLLOW read; returns (record, raw, payload)."""

    record = _lstat_record(path, root)
    if not record.get("exists"):
        return record, None, None
    if record.get("is_symlink") or not record.get("is_regular"):
        raise RecoveryProofError(f"{source} must be a stable regular file: {path}")
    raw = common.read_regular_file_bytes(path, source=source)
    record["sha256"] = _sha(raw)
    record["byte_count"] = len(raw)
    payload = None
    if gzip_payload:
        try:
            payload = gzip.decompress(raw)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise RecoveryProofError(f"{source} gzip stream is invalid: {path}") from exc
        record["payload_sha256"] = _sha(payload)
        record["payload_byte_count"] = len(payload)
        record["payload_line_count"] = _line_count(payload)
    else:
        record["line_count"] = _line_count(raw)
        record["ends_with_newline"] = raw.endswith(b"\n") if raw else True
    return record, raw, payload


def _event_ids(payload: bytes, *, source: str) -> list[str]:
    ids: list[str] = []
    for line_no, line in enumerate(payload.splitlines(), start=1):
        try:
            entry = json.loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RecoveryProofError(
                f"{source} line {line_no} is not valid JSON"
            ) from exc
        if not isinstance(entry, dict):
            raise RecoveryProofError(f"{source} line {line_no} is not an object row")
        event_id = str(entry.get("event_id") or "").strip()
        ids.append(event_id if event_id else "line-digest:" + _sha(line))
    return ids


def _require_unique(ids: list[str], *, source: str) -> None:
    if len(ids) != len(set(ids)):
        raise RecoveryProofError(f"{source} contains duplicate logical event ids")


def _legacy_listing(log_path: Path, root: Path) -> list[dict[str, Any]]:
    listing: list[dict[str, Any]] = []
    archive_dir = log_path.parent / common.ACTIVITY_LOG_ARCHIVE_SUBDIR
    legacy_dir = log_path.parent / common.ACTIVITY_LOG_LEGACY_ARCHIVE_SUBDIR
    patterns = [
        (archive_dir, f"{log_path.name}-*.gz"),
        (legacy_dir, f"{log_path.stem}-*.jsonl.gz"),
    ]
    for directory, pattern in patterns:
        if not directory.is_dir():
            continue
        for entry in sorted(directory.glob(pattern)):
            record = _lstat_record(entry, root)
            if record.get("is_symlink") or not record.get("is_regular"):
                raise RecoveryProofError(
                    f"activity archive leaf must be a regular file: {entry}"
                )
            raw = common.read_regular_file_bytes(entry, source="activity archive leaf")
            record["sha256"] = _sha(raw)
            record["byte_count"] = len(raw)
            record["source_class"] = common.classify_source(entry)
            if record["source_class"] == "unknown":
                raise RecoveryProofError(f"unknown activity archive name: {entry.name}")
            listing.append(record)
    return listing


def capture_inventory(
    status_root: str | Path,
    *,
    log_name: str = "ai-activity-log.jsonl",
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Read-only incident inventory. Opens no lock and writes nothing.

    Returns (manifest, artifact_bytes). artifact_bytes retains the raw bytes
    read during this capture so proofs use the exact captured snapshot.
    """

    root = Path(status_root).expanduser().resolve()
    log_path = root / log_name
    rotation_dir = log_path.parent / common.ACTIVITY_LOG_ROTATION_SUBDIR
    intent_path = common.activity_rotation_intent_path(log_path)

    artifacts: dict[str, Any] = {}
    raw_bytes: dict[str, bytes] = {}

    intent_record, intent_raw, _ = _read_artifact(
        intent_path, root, source="pending rotation intent"
    )
    artifacts["intent"] = intent_record
    intent_payload: dict[str, Any] | None = None
    if intent_raw is not None:
        raw_bytes["intent"] = intent_raw
        try:
            parsed = json.loads(intent_raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RecoveryProofError("pending rotation intent is unreadable") from exc
        intent_payload = common.validated_schema_v1_rotation_intent(log_path, parsed)

    if intent_payload is not None:
        transaction_id = str(intent_payload["transaction_id"])
        stage_archive_path, stage_tail_path = common._activity_rotation_stage_paths(
            log_path, transaction_id
        )
        installed_path = (
            log_path.parent / str(intent_payload["archive_relative_path"])
        ).resolve()
        record, raw, payload = _read_artifact(
            stage_archive_path, root, source="staged rotation archive", gzip_payload=True
        )
        artifacts["stage_archive"] = record
        if raw is not None:
            raw_bytes["stage_archive"] = raw
            raw_bytes["stage_archive_payload"] = payload or b""
        record, raw, _ = _read_artifact(
            stage_tail_path, root, source="staged rotation tail"
        )
        artifacts["stage_tail"] = record
        if raw is not None:
            raw_bytes["stage_tail"] = raw
        record, raw, payload = _read_artifact(
            installed_path, root, source="installed content archive", gzip_payload=True
        )
        artifacts["installed_archive"] = record
        if raw is not None:
            raw_bytes["installed_archive"] = raw
            raw_bytes["installed_archive_payload"] = payload or b""

    record, raw, _ = _read_artifact(log_path, root, source="active activity log")
    artifacts["active"] = record
    if raw is not None:
        raw_bytes["active"] = raw

    lineage_path = common.activity_rotation_lineage_path(log_path)
    artifacts["lineage"] = _lstat_record(lineage_path, root)
    resolutions_path = common.activity_rotation_resolutions_path(log_path)
    record, raw, _ = _read_artifact(
        resolutions_path, root, source="rotation resolutions"
    )
    artifacts["resolutions"] = record
    if raw is not None:
        raw_bytes["resolutions"] = raw

    rotation_listing = (
        sorted(entry.name for entry in rotation_dir.iterdir())
        if rotation_dir.is_dir()
        else []
    )

    manifest: dict[str, Any] = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "task": "OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001",
        "captured_utc": _utc_now(),
        "status_root": str(root),
        "log_name": log_name,
        "intent_payload": intent_payload,
        "artifacts": artifacts,
        "rotation_dir_listing": rotation_listing,
        "archive_listing": _legacy_listing(log_path, root),
        "locks": {
            "activity_audit_lock": _lstat_record(
                common.activity_audit_lock_path(log_path), root
            ),
            "note": "lstat metadata only; inventory never opens or locks these",
        },
    }
    manifest["proof"] = prove_relations(
        root, log_name=log_name, manifest=manifest, raw=raw_bytes
    )
    return manifest, raw_bytes


def _load_superseded_resolution(
    log_path: Path,
    transaction_id: str,
) -> dict[str, Any] | None:
    resolutions_bytes, rows, _paths = (
        common._load_activity_rotation_resolutions_unlocked(
            log_path, validate_archives=True
        )
    )
    del resolutions_bytes
    for row in rows:
        if row.get("resolved_transaction_id") == transaction_id:
            return row
    return None


def prove_relations(
    status_root: Path,
    *,
    log_name: str,
    manifest: dict[str, Any],
    raw: dict[str, bytes],
) -> dict[str, Any]:
    """Prove every incident byte/event relationship or fail closed."""

    root = Path(status_root).resolve()
    log_path = root / log_name
    intent = manifest.get("intent_payload")
    artifacts = manifest["artifacts"]

    lineage_path = common.activity_rotation_lineage_path(log_path)
    if artifacts["lineage"].get("exists"):
        raise RecoveryProofError(
            "a rotation lineage file exists; this is not the stranded "
            "schema-v1 pending-intent incident class"
        )
    del lineage_path

    _resolution_bytes, resolution_rows, superseded_paths = (
        common._load_activity_rotation_resolutions_unlocked(
            log_path, validate_archives=True
        )
    )
    superseded_relative = {
        _relative_to_root(path, root) for path in superseded_paths
    }

    active_raw = raw.get("active")
    if active_raw is None:
        raise RecoveryProofError("active activity log is missing")
    if active_raw and not active_raw.endswith(b"\n"):
        raise RecoveryProofError("active activity log has a partial trailing line")

    already_resolved_row = None
    if intent is None:
        # The intent may already have been resolved by a completed or
        # partially completed run of this same transaction.
        if not resolution_rows:
            raise RecoveryProofError(
                "no pending schema-v1 intent and no resolution record; "
                "nothing for this recovery class to do"
            )
        proof: dict[str, Any] = {
            "incident_class": "schema-v1-pending-intent-superseded",
            "intent_present": False,
            "already_resolved_transaction_ids": [
                str(row["resolved_transaction_id"]) for row in resolution_rows
            ],
        }
        return proof

    transaction_id = str(intent["transaction_id"])
    already_resolved_row = _load_superseded_resolution(log_path, transaction_id)

    stage_archive_payload = raw.get("stage_archive_payload")
    stage_tail = raw.get("stage_tail")
    installed_payload = raw.get("installed_archive_payload")
    if stage_archive_payload is None:
        raise RecoveryProofError("staged rotation archive is missing")
    if stage_tail is None:
        raise RecoveryProofError("staged rotation tail is missing")
    if installed_payload is None:
        raise RecoveryProofError("installed content archive is missing")

    if _sha(stage_archive_payload) != intent["archive_sha256"]:
        raise RecoveryProofError("staged archive payload digest mismatch vs intent")
    if _sha(stage_tail) != intent["tail_sha256"]:
        raise RecoveryProofError("staged tail digest mismatch vs intent")
    source = stage_archive_payload + stage_tail
    if _sha(source) != intent["source_sha256"]:
        raise RecoveryProofError(
            "staged archive plus staged tail does not reconstruct the intent source"
        )
    if installed_payload != stage_archive_payload:
        raise RecoveryProofError(
            "installed content archive differs from the staged archive payload"
        )

    # Every content-addressed archive on disk must be exactly the intent's
    # installed archive or an archive already accounted by a resolution row.
    installed_relative = str(intent["archive_relative_path"])
    for entry in manifest["archive_listing"]:
        if entry["source_class"] != "content_addressed":
            continue
        if entry["relative_path"] == installed_relative:
            continue
        if entry["relative_path"] in superseded_relative:
            continue
        raise RecoveryProofError(
            "unexplained content-addressed archive on disk: "
            + entry["relative_path"]
        )

    # Identify the unique superseding legacy archive: exactly one legacy
    # archive whose decompressed payload begins with the intent source.
    candidates: list[tuple[dict[str, Any], bytes]] = []
    for entry in manifest["archive_listing"]:
        if entry["source_class"] not in ("legacy_ts_std", "legacy_ts_old"):
            continue
        entry_path = root / entry["relative_path"]
        compressed = common.read_regular_file_bytes(
            entry_path, source="legacy activity archive"
        )
        if _sha(compressed) != entry["sha256"]:
            raise RecoveryProofError(
                f"legacy archive changed during proof: {entry['relative_path']}"
            )
        try:
            payload = gzip.decompress(compressed)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise RecoveryProofError(
                f"legacy archive gzip stream is invalid: {entry['relative_path']}"
            ) from exc
        if payload.startswith(source):
            candidates.append((entry, payload))
    if len(candidates) != 1:
        raise RecoveryProofError(
            "expected exactly one legacy archive containing the intent source "
            f"as an exact byte prefix; found {len(candidates)}"
        )
    superseding_entry, superseding_payload = candidates[0]
    post_intent_suffix = superseding_payload[len(source):]
    if post_intent_suffix and not post_intent_suffix.endswith(b"\n"):
        raise RecoveryProofError(
            "post-intent suffix in the superseding archive is not newline-terminated"
        )

    superseding_ids = _event_ids(
        superseding_payload, source="superseding legacy archive"
    )
    _require_unique(superseding_ids, source="superseding legacy archive")
    source_line_count = _line_count(source)
    source_ids = superseding_ids[:source_line_count]
    suffix_ids = superseding_ids[source_line_count:]

    # Classify the current active file: it must equal a line-aligned suffix
    # of the superseding archive payload followed only by newer appends.
    superseding_lines = superseding_payload.splitlines(keepends=True)
    active_lines = active_raw.splitlines(keepends=True)
    overlap_lines = 0
    for k in range(min(len(superseding_lines), len(active_lines)), 0, -1):
        if active_raw.startswith(b"".join(superseding_lines[-k:])):
            overlap_lines = k
            break
    retained_overlap = b"".join(superseding_lines[-overlap_lines:]) if overlap_lines else b""
    post_rotation_suffix = active_raw[len(retained_overlap):]
    if post_rotation_suffix and not post_rotation_suffix.endswith(b"\n"):
        raise RecoveryProofError(
            "post-rotation active suffix is not newline-terminated"
        )
    active_ids = _event_ids(active_raw, source="active activity log")
    _require_unique(active_ids, source="active activity log")
    suffix_event_ids = set(active_ids[overlap_lines:])
    if suffix_event_ids & set(superseding_ids):
        raise RecoveryProofError(
            "post-rotation active suffix repeats events from the superseding "
            "archive; byte relation is ambiguous"
        )

    # Logical event conservation across the affected artifacts: every event
    # appears exactly once in the logical stream after recovery.
    logical_total = len(superseding_ids) + len(active_ids) - overlap_lines
    distinct_total = len(set(superseding_ids) | set(active_ids))
    if logical_total != distinct_total:
        raise RecoveryProofError("logical event conservation failed")

    proof = {
        "incident_class": "schema-v1-pending-intent-superseded",
        "intent_present": True,
        "transaction_id": transaction_id,
        "already_resolved": already_resolved_row is not None,
        "source_sha256": _sha(source),
        "source_byte_count": len(source),
        "source_line_count": source_line_count,
        "archive_payload_sha256": _sha(stage_archive_payload),
        "archive_byte_count": len(stage_archive_payload),
        "archive_line_count": _line_count(stage_archive_payload),
        "stage_tail_sha256": _sha(stage_tail),
        "stage_tail_byte_count": len(stage_tail),
        "stage_tail_line_count": _line_count(stage_tail),
        "installed_equals_staged": True,
        "superseding_relative_path": superseding_entry["relative_path"],
        "superseding_gzip_sha256": superseding_entry["sha256"],
        "superseding_payload_sha256": _sha(superseding_payload),
        "superseding_byte_count": len(superseding_payload),
        "superseding_line_count": len(superseding_ids),
        "post_intent_suffix_sha256": _sha(post_intent_suffix),
        "post_intent_suffix_byte_count": len(post_intent_suffix),
        "post_intent_suffix_line_count": len(suffix_ids),
        "active_sha256": _sha(active_raw),
        "active_byte_count": len(active_raw),
        "active_line_count": len(active_ids),
        "retained_overlap_sha256": _sha(retained_overlap),
        "retained_overlap_byte_count": len(retained_overlap),
        "retained_overlap_line_count": overlap_lines,
        "post_rotation_suffix_sha256": _sha(post_rotation_suffix),
        "post_rotation_suffix_byte_count": len(post_rotation_suffix),
        "post_rotation_suffix_line_count": len(active_ids) - overlap_lines,
        "logical_event_total": logical_total,
        "logical_event_distinct": distinct_total,
        "missing_event_count": 0,
        "duplicate_event_count": 0,
    }
    return proof


def _build_resolution_row(
    log_path: Path,
    manifest: dict[str, Any],
    *,
    previous_resolutions_bytes: bytes,
    sequence: int,
    inventory_sha256: str,
    writer_guard_attestation: str,
) -> dict[str, Any]:
    proof = manifest["proof"]
    intent = manifest["intent_payload"]
    transaction_id = str(intent["transaction_id"])
    intent_record = manifest["artifacts"]["intent"]
    preserved_dir = common.activity_rotation_preserved_dir(log_path, transaction_id)
    row: dict[str, Any] = {
        "record_type": common.ACTIVITY_ROTATION_RESOLUTION_RECORD_TYPE,
        "schema_version": common.ACTIVITY_LOG_ROTATION_SCHEMA_VERSION,
        "resolution_type": common.ACTIVITY_ROTATION_RESOLUTION_TYPE_SUPERSEDED,
        "log_name": log_path.name,
        "sequence": sequence,
        "resolution_id": "",
        "previous_resolutions_sha256": _sha(previous_resolutions_bytes),
        "resolved_transaction_id": transaction_id,
        "intent_schema_version": 1,
        "intent_sha256": intent_record["sha256"],
        "intent_payload": dict(intent),
        "archive_relative_path": str(intent["archive_relative_path"]),
        "archive_gzip_sha256": manifest["artifacts"]["installed_archive"]["sha256"],
        "archive_payload_sha256": proof["archive_payload_sha256"],
        "archive_byte_count": proof["archive_byte_count"],
        "archive_line_count": proof["archive_line_count"],
        "stage_tail_sha256": proof["stage_tail_sha256"],
        "stage_tail_byte_count": proof["stage_tail_byte_count"],
        "stage_tail_line_count": proof["stage_tail_line_count"],
        "source_sha256": proof["source_sha256"],
        "source_byte_count": proof["source_byte_count"],
        "source_line_count": proof["source_line_count"],
        "superseding_relative_path": proof["superseding_relative_path"],
        "superseding_gzip_sha256": proof["superseding_gzip_sha256"],
        "superseding_payload_sha256": proof["superseding_payload_sha256"],
        "superseding_byte_count": proof["superseding_byte_count"],
        "superseding_line_count": proof["superseding_line_count"],
        "post_intent_suffix_sha256": proof["post_intent_suffix_sha256"],
        "post_intent_suffix_byte_count": proof["post_intent_suffix_byte_count"],
        "post_intent_suffix_line_count": proof["post_intent_suffix_line_count"],
        "active_sha256": proof["active_sha256"],
        "active_byte_count": proof["active_byte_count"],
        "active_line_count": proof["active_line_count"],
        "retained_overlap_sha256": proof["retained_overlap_sha256"],
        "retained_overlap_byte_count": proof["retained_overlap_byte_count"],
        "retained_overlap_line_count": proof["retained_overlap_line_count"],
        "post_rotation_suffix_sha256": proof["post_rotation_suffix_sha256"],
        "post_rotation_suffix_byte_count": proof["post_rotation_suffix_byte_count"],
        "post_rotation_suffix_line_count": proof["post_rotation_suffix_line_count"],
        "inventory_sha256": inventory_sha256,
        "writer_guard_attestation": writer_guard_attestation,
        "preserved_relative_dir": _relative_to_root(preserved_dir, log_path.parent),
    }
    row["resolution_id"] = common.activity_rotation_resolution_id(row)
    return row


def _compare_pinned_immutable(
    pinned: dict[str, Any],
    fresh: dict[str, Any],
    *,
    require_exact_active: bool,
    cleanup_phase: bool = False,
) -> list[str]:
    """Compare a fresh capture against the pinned manifest.

    Immutable incident artifacts must match exactly. The active log may only
    have grown by appends unless require_exact_active is set (execute mode
    under the all-writer guard requires byte-exact equality and a stable
    inode). Recovery-owned artifacts (resolutions file, intent/stage
    presence) are phase-checked by the caller, not here.
    """

    problems: list[str] = []

    def check(name: str, keys: tuple[str, ...]) -> None:
        left = pinned["artifacts"].get(name) or {}
        right = fresh["artifacts"].get(name) or {}
        if not left.get("exists"):
            return
        if not right.get("exists"):
            if cleanup_phase and name in (
                "intent",
                "stage_archive",
                "stage_tail",
                "installed_archive",
            ):
                # A completed or partially completed resolution legitimately
                # removed the intent/stage markers; the installed archive is
                # then re-verified byte-exactly at final readback because the
                # fresh capture cannot locate it without the intent.
                return
            problems.append(f"{name}: present at pin time but missing now")
            return
        for key in keys:
            if left.get(key) != right.get(key):
                problems.append(
                    f"{name}.{key}: pinned {left.get(key)!r} != current {right.get(key)!r}"
                )

    check("installed_archive", ("sha256", "byte_count", "inode", "device"))
    check("stage_archive", ("sha256", "byte_count", "inode", "device"))
    check("stage_tail", ("sha256", "byte_count", "inode", "device"))
    check("intent", ("sha256", "byte_count", "inode", "device"))

    pinned_archives = {
        entry["relative_path"]: entry for entry in pinned["archive_listing"]
    }
    fresh_archives = {
        entry["relative_path"]: entry for entry in fresh["archive_listing"]
    }
    if set(pinned_archives) != set(fresh_archives):
        problems.append(
            "archive listing changed since pinning: "
            + repr(sorted(set(pinned_archives) ^ set(fresh_archives)))
        )
    else:
        for name, entry in pinned_archives.items():
            if entry["sha256"] != fresh_archives[name]["sha256"]:
                problems.append(f"archive {name} content changed since pinning")

    pinned_active = pinned["artifacts"]["active"]
    fresh_active = fresh["artifacts"]["active"]
    if require_exact_active:
        for key in ("sha256", "byte_count", "inode", "device"):
            if pinned_active.get(key) != fresh_active.get(key):
                problems.append(
                    f"active.{key}: pinned {pinned_active.get(key)!r} != "
                    f"current {fresh_active.get(key)!r}"
                )
    return problems


def _active_prefix_matches_pin(
    pinned: dict[str, Any],
    fresh_active_bytes: bytes,
) -> tuple[bool, int]:
    pinned_active = pinned["artifacts"]["active"]
    pinned_count = int(pinned_active["byte_count"])
    if len(fresh_active_bytes) < pinned_count:
        return False, 0
    prefix = fresh_active_bytes[:pinned_count]
    if _sha(prefix) != pinned_active["sha256"]:
        return False, 0
    return True, len(fresh_active_bytes) - pinned_count


def dry_run(
    status_root: str | Path,
    pinned_manifest: dict[str, Any],
    *,
    log_name: str = "ai-activity-log.jsonl",
) -> dict[str, Any]:
    """Read-only: re-prove everything against the pinned inventory."""

    root = Path(status_root).expanduser().resolve()
    log_path = root / log_name
    fresh_manifest, fresh_raw = capture_inventory(root, log_name=log_name)
    pinned_digest = manifest_digest(pinned_manifest)

    problems = _compare_pinned_immutable(
        pinned_manifest, fresh_manifest, require_exact_active=False
    )
    prefix_ok, appended_bytes = _active_prefix_matches_pin(
        pinned_manifest, fresh_raw.get("active") or b""
    )
    if not prefix_ok:
        problems.append(
            "active log is not the pinned bytes plus appended suffix; repin required"
        )
    if problems:
        raise RecoveryProofError(
            "dry-run input drift vs pinned inventory: " + "; ".join(problems)
        )

    proof = fresh_manifest["proof"]
    report: dict[str, Any] = {
        "mode": "dry-run",
        "status": "already-resolved" if not proof.get("intent_present") or proof.get("already_resolved") else "resolvable",
        "generated_utc": _utc_now(),
        "status_root": str(root),
        "pinned_inventory_sha256": pinned_digest,
        "fresh_inventory_sha256": manifest_digest(fresh_manifest),
        "active_appended_bytes_since_pin": appended_bytes,
        "proof": proof,
        "mutation_performed": False,
    }
    if proof.get("intent_present") and not proof.get("already_resolved"):
        resolutions_bytes = fresh_raw.get("resolutions") or b""
        _bytes, rows, _paths = common._load_activity_rotation_resolutions_unlocked(
            log_path, validate_archives=True
        )
        del _bytes
        proposed = _build_resolution_row(
            log_path,
            fresh_manifest,
            previous_resolutions_bytes=resolutions_bytes,
            sequence=len(rows) + 1,
            inventory_sha256=pinned_digest,
            writer_guard_attestation="<execute-mode-attestation>",
        )
        report["proposed_resolution_row"] = proposed
        report["proposed_mutations"] = [
            "write preserved evidence copies under "
            + proposed["preserved_relative_dir"],
            "append one resolution row to "
            + _relative_to_root(
                common.activity_rotation_resolutions_path(log_path), root
            ),
            "remove the pending intent marker and staged files for "
            + proposed["resolved_transaction_id"],
        ]
    return report


def _durable_copy(destination: Path, payload: bytes) -> None:
    common.durable_write_bytes(destination, payload)
    if common.read_regular_file_bytes(
        destination, source="preserved incident copy"
    ) != payload:
        raise RecoveryProofError(f"preserved copy readback mismatch: {destination}")


def execute(
    status_root: str | Path,
    pinned_manifest: dict[str, Any],
    *,
    expected_inventory_sha256: str,
    writer_guard_attestation: str,
    log_name: str = "ai-activity-log.jsonl",
) -> dict[str, Any]:
    """The gated live recovery transaction. Fails closed on any drift."""

    if os.environ.get(EXECUTE_ENV) != EXECUTE_ENV_VALUE:
        raise RecoveryProofError(
            f"execute mode requires {EXECUTE_ENV}={EXECUTE_ENV_VALUE}"
        )
    if not str(writer_guard_attestation or "").strip():
        raise RecoveryProofError(
            "execute mode requires a non-empty all-writer guard attestation"
        )
    pinned_digest = manifest_digest(pinned_manifest)
    if pinned_digest != str(expected_inventory_sha256 or "").strip():
        raise RecoveryProofError(
            "expected inventory digest does not match the pinned manifest"
        )

    root = Path(status_root).expanduser().resolve()
    log_path = root / log_name
    with common.activity_audit_lock_file(log_path, shared=False, nonblocking=True):
        return _execute_unlocked(
            root,
            log_path,
            pinned_manifest,
            pinned_digest=pinned_digest,
            writer_guard_attestation=str(writer_guard_attestation).strip(),
            log_name=log_name,
        )


def _execute_unlocked(
    root: Path,
    log_path: Path,
    pinned_manifest: dict[str, Any],
    *,
    pinned_digest: str,
    writer_guard_attestation: str,
    log_name: str,
) -> dict[str, Any]:
    fresh_manifest, fresh_raw = capture_inventory(root, log_name=log_name)

    pinned_intent = pinned_manifest.get("intent_payload")
    if pinned_intent is None:
        raise RecoveryProofError("pinned inventory has no pending schema-v1 intent")
    transaction_id = str(pinned_intent["transaction_id"])
    fresh_intent = fresh_manifest.get("intent_payload")
    existing_row = _load_superseded_resolution(log_path, transaction_id)

    problems = _compare_pinned_immutable(
        pinned_manifest,
        fresh_manifest,
        require_exact_active=True,
        cleanup_phase=existing_row is not None,
    )

    if fresh_intent is not None:
        if fresh_intent != pinned_intent:
            problems.append("pending intent changed since pinning")
    elif existing_row is None:
        problems.append(
            "pending intent is gone but no resolution record exists for it"
        )
    if problems:
        raise RecoveryProofError(
            "stable-input recheck failed: " + "; ".join(problems)
        )

    _fault("pin-recheck")

    stage_archive_path, stage_tail_path = common._activity_rotation_stage_paths(
        log_path, transaction_id
    )
    intent_path = common.activity_rotation_intent_path(log_path)
    resolutions_path = common.activity_rotation_resolutions_path(log_path)
    preserved_dir = common.activity_rotation_preserved_dir(log_path, transaction_id)
    active_before = fresh_raw.get("active") or b""

    pinned_artifacts = pinned_manifest["artifacts"]

    # Step 1: preserve immutable evidence copies of the intent and stages.
    preserved = {
        PRESERVED_INTENT_NAME: (
            fresh_raw.get("intent"),
            pinned_artifacts["intent"]["sha256"],
        ),
        PRESERVED_STAGE_ARCHIVE_NAME: (
            fresh_raw.get("stage_archive"),
            pinned_artifacts["stage_archive"]["sha256"],
        ),
        PRESERVED_STAGE_TAIL_NAME: (
            fresh_raw.get("stage_tail"),
            pinned_artifacts["stage_tail"]["sha256"],
        ),
    }
    preserved_dir.mkdir(parents=True, exist_ok=True)
    preserved_manifest: dict[str, Any] = {
        "resolved_transaction_id": transaction_id,
        "inventory_sha256": pinned_digest,
        "files": {},
    }
    for name, (payload, expected_sha) in preserved.items():
        destination = preserved_dir / name
        if payload is None:
            # Crash-retry path: the original is already gone; the preserved
            # copy written by the earlier attempt must match the pinned sha.
            existing = common.read_regular_file_bytes(
                destination, source="preserved incident copy"
            )
            if _sha(existing) != expected_sha:
                raise RecoveryProofError(
                    f"preserved copy digest mismatch for {name}"
                )
        else:
            if _sha(payload) != expected_sha:
                raise RecoveryProofError(
                    f"live artifact digest mismatch for {name} during preserve"
                )
            _durable_copy(destination, payload)
        preserved_manifest["files"][name] = expected_sha
    _durable_copy(
        preserved_dir / PRESERVED_MANIFEST_NAME,
        (_canonical_json(preserved_manifest) + "\n").encode("utf-8"),
    )
    _fault("preserve")

    # Step 2: append the resolution record (idempotent).
    resolutions_bytes = (
        common.read_regular_file_bytes(
            resolutions_path, source="activity rotation resolutions"
        )
        if resolutions_path.exists() or resolutions_path.is_symlink()
        else b""
    )
    if existing_row is None:
        _bytes, rows, _paths = common._load_activity_rotation_resolutions_unlocked(
            log_path, validate_archives=True
        )
        del _bytes, _paths
        row = _build_resolution_row(
            log_path,
            fresh_manifest if fresh_intent is not None else pinned_manifest,
            previous_resolutions_bytes=resolutions_bytes,
            sequence=len(rows) + 1,
            inventory_sha256=pinned_digest,
            writer_guard_attestation=writer_guard_attestation,
        )
        new_bytes = resolutions_bytes + (
            _canonical_json(row) + "\n"
        ).encode("utf-8")
        common.durable_write_bytes(resolutions_path, new_bytes)
    else:
        row = existing_row
        if row.get("inventory_sha256") != pinned_digest:
            raise RecoveryProofError(
                "existing resolution row was pinned to a different inventory"
            )
    _fault("resolution")

    # Step 3: full readback of the resolutions chain.
    _bytes, rows, _paths = common._load_activity_rotation_resolutions_unlocked(
        log_path, validate_archives=True
    )
    del _bytes, _paths
    if not any(
        r.get("resolved_transaction_id") == transaction_id for r in rows
    ):
        raise RecoveryProofError("resolution record readback missing")
    _fault("resolution-readback")

    # Step 4: resolve the pending intent marker and staged files.
    intent_path.unlink(missing_ok=True)
    _fault("unlink-intent")
    stage_archive_path.unlink(missing_ok=True)
    stage_tail_path.unlink(missing_ok=True)
    common._fsync_directory(intent_path.parent)
    _fault("unlink-stage")

    # Step 5: final readback. The reader contract must accept the layout,
    # the active log must be byte-identical, and the archive untouched.
    common.assert_activity_audit_stable_unlocked(log_path)
    sources = common.activity_audit_source_paths_unlocked(log_path)
    installed_path = (
        log_path.parent / str(pinned_intent["archive_relative_path"])
    ).resolve()
    if installed_path in [source.resolve() for source in sources]:
        raise RecoveryProofError(
            "superseded archive is still enumerated as a logical source"
        )
    installed_raw = common.read_regular_file_bytes(
        installed_path, source="installed content archive"
    )
    if _sha(installed_raw) != pinned_artifacts["installed_archive"]["sha256"]:
        raise RecoveryProofError("installed archive changed during recovery")
    active_after = (
        common.read_regular_file_bytes(log_path, source="active activity log")
        if log_path.exists()
        else b""
    )
    if active_after != active_before:
        raise RecoveryProofError("active activity log changed during recovery")

    return {
        "mode": "execute",
        "status": "resolved" if existing_row is None else "already-resolved",
        "generated_utc": _utc_now(),
        "status_root": str(root),
        "pinned_inventory_sha256": pinned_digest,
        "resolved_transaction_id": transaction_id,
        "resolution_id": row["resolution_id"],
        "resolution_sequence": row["sequence"],
        "preserved_relative_dir": row["preserved_relative_dir"],
        "active_sha256_after": _sha(active_after),
        "logical_source_count_after": len(sources),
        "writer_guard_attestation": writer_guard_attestation,
        "mutation_performed": existing_row is None,
    }


def _write_report(path: str | None, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if path:
        Path(path).write_text(text, encoding="utf-8")
    sys.stdout.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)

    p_inventory = sub.add_parser("inventory", help="read-only incident capture")
    p_dry = sub.add_parser("dry-run", help="read-only proof against a pinned inventory")
    p_exec = sub.add_parser("execute", help="gated live recovery transaction")
    for p in (p_inventory, p_dry, p_exec):
        p.add_argument("--status-root", required=True)
        p.add_argument("--log-name", default="ai-activity-log.jsonl")
        p.add_argument("--output", default=None)
    for p in (p_dry, p_exec):
        p.add_argument("--inventory", required=True, help="pinned inventory manifest path")
    p_exec.add_argument("--expected-inventory-sha256", required=True)
    p_exec.add_argument("--writer-guard-attestation", required=True)

    args = parser.parse_args(argv)
    try:
        if args.mode == "inventory":
            manifest, _raw = capture_inventory(
                args.status_root, log_name=args.log_name
            )
            manifest["inventory_sha256"] = manifest_digest(manifest)
            _write_report(args.output, manifest)
        elif args.mode == "dry-run":
            pinned = _load_pinned_manifest(args.inventory)
            report = dry_run(args.status_root, pinned, log_name=args.log_name)
            _write_report(args.output, report)
        else:
            pinned = _load_pinned_manifest(args.inventory)
            report = execute(
                args.status_root,
                pinned,
                expected_inventory_sha256=args.expected_inventory_sha256,
                writer_guard_attestation=args.writer_guard_attestation,
                log_name=args.log_name,
            )
            _write_report(args.output, report)
    except (RecoveryProofError, RuntimeError) as exc:
        sys.stderr.write(f"fail-closed: {exc}\n")
        return 1
    return 0


def _load_pinned_manifest(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RecoveryProofError("pinned inventory manifest is not an object")
    payload.pop("inventory_sha256", None)
    return payload


if __name__ == "__main__":
    sys.exit(main())
