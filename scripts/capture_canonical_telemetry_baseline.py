#!/usr/bin/env python3
"""Capture canonical telemetry baseline disposition (SD-DATA-02 / PFG-DATA-TELEMETRY-BASELINE-20260822).

Non-destructively inspects `public.telemetry_events` in PostgreSQL, captures
row counts, timestamps, watermarks, query SHA256, and records history disposition.
Inventories backup and import candidates, rejecting derived Lifecycle JSON
as canonical source truth and failing closed on invalid or truncated inputs.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

CANONICAL_SOURCE_TABLE = "public.telemetry_events"
ALLOWED_DISPOSITIONS = frozenset({"complete", "partial", "irrecoverable", "unknown"})
HEX_SHA40_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
HEX_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
RECOVERY_ATTESTATION_SCHEMA_VERSION = "pantheon.telemetry_recovery_source_attestation.v1"

AUTHORITATIVE_PROOF_PATTERNS: tuple[re.Pattern[str], ...] = (
    # GCS / Cloud Storage object. Existence, generation, and the object-bound
    # SHA-256 metadata are checked independently by gcloud.
    re.compile(r"^g(?:s|cs)://[a-z0-9][-_.a-z0-9]{1,61}[a-z0-9]/.+$"),
    # GCP Persistent Disk Snapshot. Only the fully-qualified resource identity
    # is accepted; a short snapshot name is not independently resolvable.
    re.compile(r"^projects/[a-z0-9-]+/global/snapshots/[a-z0-9][-a-z0-9]{0,62}$"),
    # PostgreSQL dump and source-ledger files. Only absolute local paths are
    # accepted so validation can prove existence and hash the bytes.
    re.compile(r"^(?:pg_dump|postgresql-dump):(?:file://)?/(?!/).+$"),
    re.compile(r"^source-ledger:(?:file://)?/(?!/).+$"),
    # System authoritative backup directory path
    re.compile(r"^(?:file://)?/var/backups/(?:postgres|postgresql|database|telemetry)/.+\.(?:sql|sql\.gz|dump|tar|tar\.gz|custom|pgdump|bin|archive)$"),
)

DERIVED_LIFECYCLE_SUBSTRINGS: tuple[str, ...] = (
    "lifecycle",
    "projection",
    "trade_journey",
    "loop_runs",
    "loop_run",
    "event_receipts",
    "read_model",
    "readmodel",
    "/data/bff/",
    "bff/",
)


def is_derived_lifecycle_or_projection(source: str) -> bool:
    """Check if a string references a derived Lifecycle JSON, read model, or secondary projection."""
    s = source.strip().lower()
    for pattern in DERIVED_LIFECYCLE_SUBSTRINGS:
        if pattern in s:
            return True
    return False


def is_valid_authoritative_recovery_source(source: str) -> bool:
    """Return whether a source identity can be independently resolved.

    This is only a syntax/capability check.  A matching URI is not evidence that
    the source exists; ``inspect_authoritative_recovery_source`` performs that
    independent check and is mandatory for a ``complete`` disposition.
    """
    s = source.strip()
    if is_derived_lifecycle_or_projection(s):
        return False
    return any(pattern.match(s) is not None for pattern in AUTHORITATIVE_PROOF_PATTERNS)


def _recovery_source_kind(source: str) -> str:
    if re.match(r"^g(?:s|cs)://", source):
        return "gcs_object"
    if source.startswith("projects/"):
        return "gcp_snapshot"
    if source.startswith(("pg_dump:", "postgresql-dump:", "/var/backups/", "file:///var/backups/")):
        return "postgresql_dump"
    if source.startswith("source-ledger:"):
        return "source_ledger"
    raise ValueError(f"Unsupported or unbound authoritative recovery source identity: {source!r}")


def _local_recovery_source_path(source: str) -> Path:
    for prefix in ("pg_dump:file://", "postgresql-dump:file://", "source-ledger:file://"):
        if source.startswith(prefix):
            return Path(source[len(prefix):])
    for prefix in ("pg_dump:", "postgresql-dump:", "source-ledger:"):
        if source.startswith(prefix):
            return Path(source[len(prefix):])
    if source.startswith("file://"):
        return Path(source[len("file://"):])
    return Path(source)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_json_command(command: Sequence[str]) -> Mapping[str, Any]:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        stderr = getattr(exc, "stderr", "") or ""
        detail = stderr.strip() or str(exc)
        raise ValueError(f"Authoritative recovery source lookup failed: {detail}") from exc
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Authoritative recovery source lookup returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("Authoritative recovery source lookup did not return an object")
    return payload


def inspect_authoritative_recovery_source(source: str) -> dict[str, str]:
    """Resolve a source and derive independently observed immutable identity.

    Local dump/source-ledger bytes are hashed directly. GCS objects must expose
    a 64-hex ``pantheon_sha256`` (or ``sha256``) custom metadata value and are
    bound to generation + metageneration. GCP snapshots are bound to a READY,
    fully-qualified resource and a SHA-256 over immutable describe fields.
    """
    source = source.strip()
    if not is_valid_authoritative_recovery_source(source):
        raise ValueError(f"Unsupported or unbound authoritative recovery source identity: {source!r}")

    source_kind = _recovery_source_kind(source)
    if source_kind in {"postgresql_dump", "source_ledger"}:
        path = _local_recovery_source_path(source)
        if not path.is_absolute():
            raise ValueError(f"Recovery source must resolve to an absolute path: {source!r}")
        if path.is_symlink():
            raise ValueError(f"Recovery source must not be a mutable symlink: {source!r}")
        if not path.is_file():
            raise ValueError(f"Authoritative recovery source does not exist as a regular file: {source!r}")
        size = path.stat().st_size
        return {
            "source_kind": source_kind,
            "source_identity": source,
            "source_version": f"bytes:{size}",
            "immutable_digest_sha256": _sha256_file(path),
        }

    if source_kind == "gcs_object":
        canonical_source = "gs://" + source.split("://", 1)[1]
        match = re.match(r"^gs://([^/]+)/(.+)$", canonical_source)
        assert match is not None
        expected_bucket, expected_name = match.groups()
        payload = _run_json_command(("gcloud", "storage", "objects", "describe", canonical_source, "--format=json"))
        actual_bucket = str(payload.get("bucket", "")).rsplit("/", 1)[-1]
        actual_name = str(payload.get("name", ""))
        generation = str(payload.get("generation", "")).strip()
        metageneration = str(payload.get("metageneration", "")).strip()
        metadata = payload.get("metadata")
        if actual_bucket != expected_bucket or actual_name != expected_name:
            raise ValueError("GCS lookup identity does not match recovery_source")
        if not generation or not metageneration:
            raise ValueError("GCS object is missing immutable generation binding")
        if not isinstance(metadata, Mapping):
            raise ValueError("GCS object is missing SHA-256 metadata")
        digest = str(metadata.get("pantheon_sha256") or metadata.get("sha256") or "").strip()
        if not HEX_SHA256_PATTERN.fullmatch(digest):
            raise ValueError("GCS object is missing valid pantheon_sha256/sha256 metadata")
        return {
            "source_kind": source_kind,
            "source_identity": source,
            "source_version": f"generation:{generation};metageneration:{metageneration}",
            "immutable_digest_sha256": digest.lower(),
        }

    match = re.fullmatch(r"projects/([^/]+)/global/snapshots/([^/]+)", source)
    assert match is not None
    project, snapshot_name = match.groups()
    payload = _run_json_command(
        ("gcloud", "compute", "snapshots", "describe", snapshot_name, "--project", project, "--format=json")
    )
    resource_id = str(payload.get("id", "")).strip()
    self_link = str(payload.get("selfLink", "")).strip()
    if str(payload.get("name", "")) != snapshot_name or not self_link.endswith(f"/{source}"):
        raise ValueError("GCP snapshot lookup identity does not match recovery_source")
    if str(payload.get("status", "")) != "READY" or not resource_id:
        raise ValueError("GCP snapshot is not READY or lacks immutable resource id")
    immutable_fields = {
        key: payload.get(key)
        for key in (
            "id",
            "name",
            "selfLink",
            "sourceDisk",
            "sourceDiskId",
            "diskSizeGb",
            "storageBytes",
            "creationTimestamp",
            "storageLocations",
        )
    }
    digest = hashlib.sha256(
        json.dumps(immutable_fields, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "source_kind": source_kind,
        "source_identity": source,
        "source_version": f"id:{resource_id}",
        "immutable_digest_sha256": digest,
    }


CANONICAL_BASELINE_QUERY = """SELECT
  count(*)::bigint AS row_count,
  min(created_at) AS min_created_at,
  max(created_at) AS max_created_at,
  max(ingested_seq)::bigint AS source_high_watermark
FROM public.telemetry_events;"""


def compute_query_sha256(query_text: str = CANONICAL_BASELINE_QUERY) -> str:
    """Compute the SHA-256 digest of the canonical query string."""
    return hashlib.sha256(query_text.encode("utf-8")).hexdigest()


def _format_rfc3339(dt: datetime.datetime | None) -> str | None:
    """Format a datetime as an RFC3339 UTC string with timezone offset."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt.isoformat()


def _parse_rfc3339(value: str) -> datetime.datetime:
    """Parse an RFC3339 / ISO8601 string, requiring timezone qualification."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp is not timezone-qualified: {value!r}")
    return parsed.astimezone(datetime.timezone.utc)


def validate_recovery_source_attestation(
    attestation: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and independently resolve the proof required for ``complete``.

    The attestation is deliberately strict and self-contained. It binds the
    source identity and independently observed immutable digest/version to the
    exact baseline event range, plus a zero-missing event-id comparison proof.
    """
    if not isinstance(attestation, Mapping):
        raise ValueError("recovery_source_attestation must be an object for complete disposition")

    required_keys = {
        "schema_version",
        "source_kind",
        "source_identity",
        "source_version",
        "immutable_digest_sha256",
        "verified_at",
        "event_range",
        "completeness",
    }
    actual_keys = set(attestation)
    if actual_keys != required_keys:
        missing = sorted(required_keys - actual_keys)
        extra = sorted(actual_keys - required_keys)
        raise ValueError(
            f"recovery_source_attestation keys do not match contract; missing={missing}, extra={extra}"
        )

    if attestation["schema_version"] != RECOVERY_ATTESTATION_SCHEMA_VERSION:
        raise ValueError(
            f"recovery_source_attestation schema_version must be {RECOVERY_ATTESTATION_SCHEMA_VERSION!r}"
        )

    recovery_source = str(baseline["recovery_source"]).strip()
    source_identity = attestation["source_identity"]
    if not isinstance(source_identity, str) or source_identity != recovery_source:
        raise ValueError("recovery_source_attestation source_identity must exactly match recovery_source")
    expected_kind = _recovery_source_kind(recovery_source)
    if attestation["source_kind"] != expected_kind:
        raise ValueError(
            f"recovery_source_attestation source_kind must be {expected_kind!r} for {recovery_source!r}"
        )
    source_version = attestation["source_version"]
    if not isinstance(source_version, str) or not source_version.strip():
        raise ValueError("recovery_source_attestation source_version must be a non-empty string")
    expected_digest = attestation["immutable_digest_sha256"]
    if not isinstance(expected_digest, str) or not HEX_SHA256_PATTERN.fullmatch(expected_digest):
        raise ValueError("recovery_source_attestation immutable_digest_sha256 must be 64 hexadecimal characters")
    verified_at = attestation["verified_at"]
    if not isinstance(verified_at, str) or not verified_at.strip():
        raise ValueError("recovery_source_attestation verified_at must be a non-empty RFC3339 string")
    _parse_rfc3339(verified_at)

    event_range = attestation["event_range"]
    expected_event_range_keys = {
        "row_count",
        "min_created_at",
        "max_created_at",
        "source_high_watermark",
    }
    if not isinstance(event_range, Mapping) or set(event_range) != expected_event_range_keys:
        raise ValueError("recovery_source_attestation event_range must contain the exact baseline range fields")
    for field in sorted(expected_event_range_keys):
        if event_range[field] != baseline[field]:
            raise ValueError(
                f"recovery_source_attestation event_range.{field} does not match baseline {field}"
            )

    completeness = attestation["completeness"]
    expected_completeness_keys = {
        "status",
        "known_history_start",
        "expected_event_count",
        "observed_event_count",
        "missing_event_count",
        "event_id_comparison_sha256",
        "query_sha256",
    }
    if not isinstance(completeness, Mapping) or set(completeness) != expected_completeness_keys:
        raise ValueError(
            "recovery_source_attestation completeness must contain status, history boundary, counts, and comparison hashes"
        )
    if completeness["status"] != "complete":
        raise ValueError("recovery_source_attestation completeness.status must be 'complete'")
    if baseline["known_history_start"] is None or completeness["known_history_start"] != baseline["known_history_start"]:
        raise ValueError(
            "recovery_source_attestation completeness.known_history_start must match the non-null baseline boundary"
        )
    row_count = baseline["row_count"]
    if (
        isinstance(completeness["expected_event_count"], bool)
        or completeness["expected_event_count"] != row_count
        or isinstance(completeness["observed_event_count"], bool)
        or completeness["observed_event_count"] != row_count
        or isinstance(completeness["missing_event_count"], bool)
        or completeness["missing_event_count"] != 0
    ):
        raise ValueError(
            "recovery_source_attestation completeness must bind expected/observed counts to row_count with zero missing events"
        )
    comparison_digest = completeness["event_id_comparison_sha256"]
    if not isinstance(comparison_digest, str) or not HEX_SHA256_PATTERN.fullmatch(comparison_digest):
        raise ValueError(
            "recovery_source_attestation completeness.event_id_comparison_sha256 must be 64 hexadecimal characters"
        )
    if completeness["query_sha256"] != baseline["query_sha256"]:
        raise ValueError("recovery_source_attestation completeness.query_sha256 must match baseline query_sha256")

    observed = inspect_authoritative_recovery_source(recovery_source)
    for field in ("source_kind", "source_identity", "source_version", "immutable_digest_sha256"):
        expected = attestation[field]
        actual = observed[field]
        if field == "immutable_digest_sha256":
            expected = str(expected).lower()
            actual = str(actual).lower()
        if actual != expected:
            raise ValueError(
                f"Independent recovery source verification mismatch for {field}: expected {expected!r}, observed {actual!r}"
            )

    return dict(attestation)


def validate_baseline_artifact(data: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a baseline artifact dictionary against the canonical schema and rules.

    Fails closed (raises ValueError / TypeError) on:
    - Missing required fields
    - Unexpected extra fields
    - Invalid field types
    - Truncated or malformed deployment_sha (must be full 40 hex chars)
    - Invalid query_sha256 (must be 64 hex chars matching the query)
    - Non-canonical source_table (must be 'public.telemetry_events')
    - Invalid history_disposition (must be complete|partial|irrecoverable|unknown)
    - Claim of 'complete' without a verified recovery_source proof reference conforming
      to the authoritative backup / source-ledger contract and a source-bound,
      independently verified recovery_source_attestation
    - Derived Lifecycle JSON references in recovery_source or source_table
    - Invalid timestamps (must be valid RFC3339 strings or null where allowed)
    - Inconsistent row counts or high watermarks
    """
    if not isinstance(data, Mapping):
        raise TypeError(f"Baseline artifact must be a mapping, got {type(data).__name__}")

    required_keys = {
        "captured_at",
        "environment",
        "deployment_sha",
        "source_table",
        "row_count",
        "min_created_at",
        "max_created_at",
        "source_high_watermark",
        "known_history_start",
        "history_disposition",
        "recovery_source",
        "recovery_source_attestation",
        "query_sha256",
        "operator_note",
    }

    actual_keys = set(data.keys())
    missing_keys = required_keys - actual_keys
    if missing_keys:
        raise ValueError(f"Baseline artifact missing required keys: {sorted(missing_keys)}")

    extra_keys = actual_keys - required_keys
    if extra_keys:
        raise ValueError(f"Baseline artifact contains unexpected extra keys: {sorted(extra_keys)}")

    # 1. captured_at
    captured_at = data["captured_at"]
    if isinstance(captured_at, bool) or not isinstance(captured_at, str) or not captured_at.strip():
        raise ValueError(f"captured_at must be a non-empty RFC3339 string, got {captured_at!r}")
    _parse_rfc3339(captured_at)

    # 2. environment
    environment = data["environment"]
    if isinstance(environment, bool) or not isinstance(environment, str) or not environment.strip():
        raise ValueError(f"environment must be a non-empty string, got {environment!r}")

    # 3. deployment_sha (must be full 40-char hex string)
    deployment_sha = data["deployment_sha"]
    if isinstance(deployment_sha, bool) or not isinstance(deployment_sha, str) or not HEX_SHA40_PATTERN.match(deployment_sha):
        raise ValueError(
            f"deployment_sha must be a full 40-character hexadecimal SHA (not truncated), got {deployment_sha!r}"
        )

    # 4. source_table (must strictly equal 'public.telemetry_events')
    source_table = data["source_table"]
    if isinstance(source_table, bool) or not isinstance(source_table, str) or source_table != CANONICAL_SOURCE_TABLE:
        raise ValueError(
            f"source_table must be strictly {CANONICAL_SOURCE_TABLE!r}, got {source_table!r}. "
            f"Derived tables or secondary JSON projections cannot be treated as canonical source truth."
        )
    if is_derived_lifecycle_or_projection(source_table):
        raise ValueError(
            f"source_table cannot reference derived Lifecycle JSON or projection ({source_table!r}). "
            f"source_table must be strictly {CANONICAL_SOURCE_TABLE!r}."
        )

    # 5. row_count (non-negative integer)
    row_count = data["row_count"]
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise ValueError(f"row_count must be a non-negative integer, got {row_count!r}")

    # 6. min_created_at
    min_created_at = data["min_created_at"]
    parsed_min: datetime.datetime | None = None
    if min_created_at is not None:
        if isinstance(min_created_at, bool) or not isinstance(min_created_at, str):
            raise ValueError(f"min_created_at must be an RFC3339 string or null, got {min_created_at!r}")
        parsed_min = _parse_rfc3339(min_created_at)

    # 7. max_created_at
    max_created_at = data["max_created_at"]
    parsed_max: datetime.datetime | None = None
    if max_created_at is not None:
        if isinstance(max_created_at, bool) or not isinstance(max_created_at, str):
            raise ValueError(f"max_created_at must be an RFC3339 string or null, got {max_created_at!r}")
        parsed_max = _parse_rfc3339(max_created_at)

    if row_count > 0:
        if min_created_at is None:
            raise ValueError("min_created_at cannot be null when row_count > 0")
        if max_created_at is None:
            raise ValueError("max_created_at cannot be null when row_count > 0")
        if parsed_min is not None and parsed_max is not None and parsed_min > parsed_max:
            raise ValueError(
                f"min_created_at ({min_created_at!r}) cannot be after max_created_at ({max_created_at!r})"
            )
    else:
        if min_created_at is not None:
            raise ValueError("min_created_at must be null when row_count is 0")
        if max_created_at is not None:
            raise ValueError("max_created_at must be null when row_count is 0")

    # 8. source_high_watermark
    source_high_watermark = data["source_high_watermark"]
    if source_high_watermark is not None:
        if isinstance(source_high_watermark, bool) or not isinstance(source_high_watermark, int) or source_high_watermark < 0:
            raise ValueError(f"source_high_watermark must be a non-negative integer or null, got {source_high_watermark!r}")

    if row_count > 0 and source_high_watermark is None:
        raise ValueError("source_high_watermark cannot be null when row_count > 0")
    if row_count == 0 and source_high_watermark is not None:
        raise ValueError("source_high_watermark must be null when row_count is 0")

    # 9. known_history_start
    known_history_start = data["known_history_start"]
    if known_history_start is not None:
        if isinstance(known_history_start, bool) or not isinstance(known_history_start, str):
            raise ValueError(f"known_history_start must be an RFC3339 string or null, got {known_history_start!r}")
        _parse_rfc3339(known_history_start)

    # 10. history_disposition
    history_disposition = data["history_disposition"]
    if isinstance(history_disposition, bool) or not isinstance(history_disposition, str) or history_disposition not in ALLOWED_DISPOSITIONS:
        raise ValueError(
            f"history_disposition must be one of {sorted(ALLOWED_DISPOSITIONS)}, got {history_disposition!r}"
        )

    # 11. recovery_source
    recovery_source = data["recovery_source"]
    recovery_source_attestation = data["recovery_source_attestation"]
    if history_disposition == "complete":
        if recovery_source is None:
            raise ValueError(
                "history_disposition is 'complete' but recovery_source proof reference is missing (null). "
                "Complete history disposition requires a verifiable authoritative backup/source-ledger proof reference string."
            )
        if isinstance(recovery_source, bool) or not isinstance(recovery_source, str):
            raise ValueError(
                f"recovery_source must be a non-empty string proof reference for complete disposition, "
                f"got {type(recovery_source).__name__}: {recovery_source!r}"
            )
        stripped_source = recovery_source.strip()
        if not stripped_source:
            raise ValueError(
                "history_disposition is 'complete' but recovery_source proof reference is empty whitespace. "
                "Complete history disposition requires a verifiable authoritative backup/source-ledger proof reference string."
            )
        if is_derived_lifecycle_or_projection(stripped_source):
            raise ValueError(
                f"recovery_source cannot reference derived Lifecycle JSON or secondary projection ({recovery_source!r}). "
                "Derived JSON cannot be treated as canonical source truth."
            )
        if not is_valid_authoritative_recovery_source(stripped_source):
            raise ValueError(
                f"recovery_source ({recovery_source!r}) is unsupported or unbound. Complete history disposition "
                "requires an independently resolvable gs:// object, fully-qualified projects/.../global/snapshots/... "
                "resource, absolute pg_dump/backup file, or absolute source-ledger proof file; bare digests and "
                "short logical names are not proof."
            )
        if not isinstance(recovery_source_attestation, Mapping):
            raise ValueError(
                "history_disposition is 'complete' but recovery_source_attestation is missing. "
                "Complete requires source identity, immutable digest/version, event range, and completeness proof."
            )
    else:
        if recovery_source is not None:
            if isinstance(recovery_source, bool) or not isinstance(recovery_source, str):
                raise ValueError(
                    f"recovery_source must be a string or null, got {type(recovery_source).__name__}: {recovery_source!r}"
                )
            stripped_source = recovery_source.strip()
            if is_derived_lifecycle_or_projection(stripped_source):
                raise ValueError(
                    f"recovery_source cannot reference derived Lifecycle JSON or secondary projection ({recovery_source!r}). "
                    "Derived JSON cannot be treated as canonical source truth."
                )
        if recovery_source_attestation is not None:
            raise ValueError("recovery_source_attestation must be null unless history_disposition is 'complete'")

    # 12. query_sha256
    query_sha256 = data["query_sha256"]
    if isinstance(query_sha256, bool) or not isinstance(query_sha256, str) or not HEX_SHA256_PATTERN.match(query_sha256):
        raise ValueError(f"query_sha256 must be a 64-character hexadecimal SHA-256 string, got {query_sha256!r}")

    expected_query_sha256 = compute_query_sha256(CANONICAL_BASELINE_QUERY)
    if query_sha256.lower() != expected_query_sha256.lower():
        raise ValueError(
            f"query_sha256 does not match canonical baseline query hash. Expected {expected_query_sha256}, got {query_sha256}"
        )

    # 13. operator_note
    operator_note = data["operator_note"]
    if isinstance(operator_note, bool) or not isinstance(operator_note, str):
        raise ValueError(f"operator_note must be a string, got {operator_note!r}")

    if history_disposition == "complete":
        validate_recovery_source_attestation(recovery_source_attestation, data)

    return dict(data)


async def execute_telemetry_baseline_query(dsn: str) -> dict[str, Any]:
    """Execute the canonical baseline query against PostgreSQL asynchronously."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(CANONICAL_BASELINE_QUERY)
        if row is None:
            return {
                "row_count": 0,
                "min_created_at": None,
                "max_created_at": None,
                "source_high_watermark": None,
            }
        return {
            "row_count": int(row["row_count"] or 0),
            "min_created_at": _format_rfc3339(row["min_created_at"]),
            "max_created_at": _format_rfc3339(row["max_created_at"]),
            "source_high_watermark": int(row["source_high_watermark"]) if row["source_high_watermark"] is not None else None,
        }
    finally:
        await conn.close()


def capture_telemetry_baseline(
    dsn: str,
    *,
    environment: str = "dev",
    deployment_sha: str,
    history_disposition: str = "partial",
    recovery_source: str | None = None,
    recovery_source_attestation: Mapping[str, Any] | None = None,
    known_history_start: str | None = "2026-08-22T11:48:48+00:00",
    operator_note: str = "",
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Capture and validate canonical telemetry baseline dictionary."""
    if captured_at is None:
        captured_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    query_results = asyncio.run(execute_telemetry_baseline_query(dsn))

    baseline: dict[str, Any] = {
        "captured_at": captured_at,
        "environment": environment,
        "deployment_sha": deployment_sha,
        "source_table": CANONICAL_SOURCE_TABLE,
        "row_count": query_results["row_count"],
        "min_created_at": query_results["min_created_at"],
        "max_created_at": query_results["max_created_at"],
        "source_high_watermark": query_results["source_high_watermark"],
        "known_history_start": known_history_start,
        "history_disposition": history_disposition,
        "recovery_source": recovery_source,
        "recovery_source_attestation": recovery_source_attestation,
        "query_sha256": compute_query_sha256(CANONICAL_BASELINE_QUERY),
        "operator_note": operator_note,
    }

    return validate_baseline_artifact(baseline)


def generate_backup_candidate_inventory(
    *,
    environment: str = "dev",
    deployment_sha: str,
    active_row_count: int = 0,
    known_history_start: str = "2026-08-22T11:48:48+00:00",
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    """Generate the authoritative backup and import candidate inventory."""
    if evaluated_at is None:
        evaluated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return {
        "schema_version": "pantheon.telemetry_backup_candidate_inventory.v1",
        "evaluated_at": evaluated_at,
        "environment": environment,
        "deployment_sha": deployment_sha,
        "candidates": [
            {
                "candidate_id": "gcp_disk_snapshots",
                "category": "cloud_infrastructure",
                "location": "GCP project pantheon-lupin-dev-20260719 disk snapshots",
                "status": "unavailable",
                "qualifies_as_canonical_source": True,
                "event_ids_recovered": 0,
                "disposition": "No GCP disk snapshot, machine image, or persistent disk snapshot exists prior to 2026-08-22 05:21:28+00."
            },
            {
                "candidate_id": "postgresql_pg_dump_backups",
                "category": "database_dumps",
                "location": "/var/backups, /home/lupin, /data, /tmp",
                "status": "unavailable",
                "qualifies_as_canonical_source": True,
                "event_ids_recovered": 0,
                "disposition": "No PostgreSQL pg_dump, WAL archive, or SQL dump of public.telemetry_events was retained for the truncated historical period."
            },
            {
                "candidate_id": "docker_postgres_volume",
                "category": "container_persistent_storage",
                "location": "dev-root_postgres-data / pantheon_postgres-data",
                "status": "partial_active_source",
                "qualifies_as_canonical_source": True,
                "event_ids_recovered": active_row_count,
                "disposition": (
                    f"Active container volume holds canonical public.telemetry_events repopulated post-boundary "
                    f"from {known_history_start} forward ({active_row_count} rows preserved intact). "
                    f"Pre-boundary historical records prior to truncation are irrecoverable in source."
                )
            },
            {
                "candidate_id": "lifecycle_projection_json",
                "category": "derived_read_model",
                "location": "/data/bff/lifecycle-projection/trade_journey_events.json, loop_runs.json",
                "status": "derived_only_rejected_as_source",
                "qualifies_as_canonical_source": False,
                "event_ids_recovered": 0,
                "disposition": (
                    "Derived JSON files contain secondary projection data from historical runs, but per "
                    "Architecture Decision AD-03 and SD-DATA-02, derived read-models cannot be imported or "
                    "synthesized into public.telemetry_events as canonical source truth."
                )
            },
            {
                "candidate_id": "synthetic_test_fixtures",
                "category": "test_fixtures",
                "location": "services/*/fixtures/ (e.g. pnl_drift_telemetry_event.json, order_rejection_spike_telemetry.json)",
                "status": "test_fixtures_rejected_as_source",
                "qualifies_as_canonical_source": False,
                "event_ids_recovered": 0,
                "disposition": (
                    "Synthetic test event fixtures are for unit/integration testing only and are strictly "
                    "prohibited from being imported into canonical production/dev source tables."
                )
            }
        ],
        "overall_history_disposition": "partial",
        "known_history_start": known_history_start,
        "disposition_summary": (
            "Pre-boundary source history prior to 2026-08-22 05:21:28+00 / 11:48:48+00:00 is irrecoverable due "
            "to the historical deploy prune defect (G-01 fixed by PFG-DATA-TELEMETRY-PRUNE-20260822). "
            "Post-boundary telemetry is actively accumulating and preserved intact in public.telemetry_events."
        )
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dsn",
        default=os.getenv(
            "TELEMETRY_DB_DSN",
            os.getenv("DATABASE_URL", "postgresql://pantheon_app:pantheon_app@localhost:15432/pantheon"),
        ),
        help="PostgreSQL connection DSN for telemetry database",
    )
    parser.add_argument(
        "--environment",
        default=os.getenv("PANTHEON_DEPLOY_ENV", "dev"),
        help="Deployment environment label (default: dev)",
    )
    parser.add_argument(
        "--deployment-sha",
        default=os.getenv("PANTHEON_COMMAND_RUNTIME_SHA", os.getenv("GIT_SHA", "")),
        help="Full 40-character hexadecimal deployment git SHA",
    )
    parser.add_argument(
        "--history-disposition",
        choices=sorted(ALLOWED_DISPOSITIONS),
        default="partial",
        help="History disposition status (complete|partial|irrecoverable|unknown)",
    )
    parser.add_argument(
        "--recovery-source",
        default=None,
        help="Independently resolvable recovery source identity (required when history-disposition is complete)",
    )
    parser.add_argument(
        "--recovery-attestation-file",
        type=Path,
        default=None,
        help=(
            "JSON attestation binding source identity/version/digest and event completeness; "
            "required when history-disposition is complete"
        ),
    )
    parser.add_argument(
        "--known-history-start",
        default="2026-08-22T11:48:48+00:00",
        help="RFC3339 timestamp marking start of verified continuous history",
    )
    parser.add_argument(
        "--operator-note",
        default="Observed repopulation boundary after SD-DATA-01 fix; canonical history preserved post-boundary with zero drift.",
        help="Operator note explaining baseline context",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="File path to write baseline JSON output",
    )
    parser.add_argument(
        "--candidate-inventory-out",
        type=Path,
        default=None,
        help="File path to write backup candidate inventory JSON output",
    )
    parser.add_argument(
        "--validate-file",
        type=Path,
        default=None,
        help="Validate an existing baseline JSON file against the canonical contract",
    )

    args = parser.parse_args(argv)

    if args.validate_file is not None:
        try:
            content = json.loads(args.validate_file.read_text(encoding="utf-8"))
            validate_baseline_artifact(content)
            print(f"✓ Baseline artifact {args.validate_file} is valid according to SD-DATA-02 contract.")
            return 0
        except Exception as exc:
            print(f"✗ Baseline validation failed: {exc}", file=sys.stderr)
            return 1

    deployment_sha = args.deployment_sha.strip()
    if not deployment_sha:
        try:
            import subprocess
            proc = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
            deployment_sha = proc.stdout.strip()
        except Exception:
            pass

    if not deployment_sha or not HEX_SHA40_PATTERN.match(deployment_sha):
        print(
            f"ERROR: --deployment-sha must be provided as a full 40-character hexadecimal SHA. Got {deployment_sha!r}",
            file=sys.stderr,
        )
        return 2

    recovery_source_attestation: Mapping[str, Any] | None = None
    if args.recovery_attestation_file is not None:
        try:
            loaded_attestation = json.loads(args.recovery_attestation_file.read_text(encoding="utf-8"))
            if not isinstance(loaded_attestation, Mapping):
                raise ValueError("attestation JSON must be an object")
            recovery_source_attestation = loaded_attestation
        except Exception as exc:
            print(f"ERROR: Failed to load recovery source attestation: {exc}", file=sys.stderr)
            return 2

    try:
        baseline = capture_telemetry_baseline(
            args.dsn,
            environment=args.environment,
            deployment_sha=deployment_sha,
            history_disposition=args.history_disposition,
            recovery_source=args.recovery_source,
            recovery_source_attestation=recovery_source_attestation,
            known_history_start=args.known_history_start,
            operator_note=args.operator_note,
        )
    except Exception as exc:
        print(f"ERROR: Failed to capture telemetry baseline: {exc}", file=sys.stderr)
        return 3

    inventory = generate_backup_candidate_inventory(
        environment=args.environment,
        deployment_sha=deployment_sha,
        active_row_count=baseline["row_count"],
        known_history_start=args.known_history_start,
    )

    baseline_json_str = json.dumps(baseline, indent=2, sort_keys=True)
    inventory_json_str = json.dumps(inventory, indent=2, sort_keys=True)

    print("Canonical Telemetry Baseline:")
    print(baseline_json_str)

    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(baseline_json_str + "\n", encoding="utf-8")
        print(f"Wrote baseline artifact to {args.out}")

    if args.candidate_inventory_out is not None:
        args.candidate_inventory_out.parent.mkdir(parents=True, exist_ok=True)
        args.candidate_inventory_out.write_text(inventory_json_str + "\n", encoding="utf-8")
        print(f"Wrote candidate inventory artifact to {args.candidate_inventory_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
