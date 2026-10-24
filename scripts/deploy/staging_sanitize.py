#!/usr/bin/env python3
"""Sanitized-data preparation and staging verification gates.

Enforces:
- Zero production data in non-production/staging (`source_environment` allowlist).
- Explicit schema table/column allowlist; fail-closed on unknown tables or columns.
- Deterministic PII masking and credential/secret exclusion.
- Forward migration (expand-phase) and old-app/new-schema compatibility verification.
- Backup/restore readability verification.
- Exact-pair application rollback verification.
- Tenant isolation, RBAC, and provider/write/broker-off readback verification.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


SHA256_PREFIXED_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST_RE = re.compile(r"^[a-zA-Z0-9._:/-]+@sha256:[0-9a-f]{64}$")
INTERNAL_STAGING_EMAIL_RE = re.compile(r"^staging-user-[A-Za-z0-9_-]+@pantheon\.internal$")
RAW_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SECRET_VALUE_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
)
DESTRUCTIVE_SQL_RE = re.compile(
    r"\b(?:DROP\s+TABLE|DROP\s+COLUMN|TRUNCATE\s+TABLE|ALTER\s+COLUMN\s+\S+\s+TYPE)\b",
    re.IGNORECASE,
)

ALLOWED_NONPROD_SOURCES = frozenset({
    "dev_sanitized_seed",
    "staging_synthetic",
    "nonprod_seed",
})
FORBIDDEN_PROD_SOURCES = frozenset({
    "prod",
    "production",
    "prod_snapshot",
    "production_backup",
    "live",
})

RETIRED_IDENTIFIERS = (
    "pantheon-lupin-dev-20260719",
    "pantheon-benjamin-20260528",
    "35.201.204.12",
    "35.201.239.38",
    "34.81.75.241",
    "104.155.223.192",
    "/home/lupin",
)

ALLOWED_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "users": frozenset({"id", "email", "full_name", "password_hash", "phone", "national_id", "tenant_id", "role"}),
    "broker_credentials": frozenset({"id", "account_id", "broker_name", "encrypted_secret", "api_key"}),
    "exchange_api_keys": frozenset({"id", "exchange", "key_id", "secret_ciphertext"}),
    "user_sessions": frozenset({"id", "user_id", "session_token", "refresh_token", "expires_at"}),
    "system_settings": frozenset({
        "id",
        "live_broker_enabled",
        "provider_enabled",
        "real_writes_enabled",
        "safe_write_strict",
        "execution_mode",
    }),
    "telemetry_events": frozenset({"id", "event_type", "tenant_id", "raw_payload", "created_at"}),
    "tenants": frozenset({"id", "name", "status"}),
    "rbac_bindings": frozenset({"id", "user_id", "tenant_id", "role"}),
    "schema_migrations": frozenset({"version", "applied_at", "checksum_sha256"}),
}

CREDENTIAL_TABLES = ("broker_credentials", "exchange_api_keys", "user_sessions")
SENSITIVE_PAYLOAD_KEYS = frozenset({
    "token",
    "auth",
    "secret",
    "api_key",
    "apikey",
    "private_key",
    "password",
    "cookie",
    "authorization",
    "broker_secret",
    "tws_password",
    "access_token",
    "refresh_token",
    "client_secret",
})
DUMMY_PASSWORD_HASH = "$2b$12$dummyStagingHashForVerificationPurposesOnly"


class SanitizationError(ValueError):
    """Raised when sanitization or staging gate verification fails closed."""


def _check_no_retired_identifiers(payload: Any) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    for retired in RETIRED_IDENTIFIERS:
        if retired in serialized:
            raise SanitizationError(
                f"input contains retired project/host identifier: {retired!r}"
            )


def validate_sanitized_snapshot_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Validate snapshot provenance, retention, and non-production origin."""
    if not isinstance(metadata, dict):
        raise SanitizationError("snapshot metadata must be a JSON object")
    _check_no_retired_identifiers(metadata)

    required_keys = {
        "snapshot_name",
        "source_environment",
        "schema_migration_sha256",
        "retention_days",
        "pii_masked",
        "secrets_excluded",
    }
    unknown_keys = set(metadata.keys()) - (required_keys | {"created_at", "dataset_sha256"})
    missing_keys = required_keys - set(metadata.keys())
    if missing_keys:
        raise SanitizationError(f"snapshot metadata missing required keys: {sorted(missing_keys)}")
    if unknown_keys:
        raise SanitizationError(f"snapshot metadata contains unknown keys: {sorted(unknown_keys)}")

    source_env = metadata.get("source_environment")
    if not isinstance(source_env, str):
        raise SanitizationError("source_environment must be a string")
    if source_env.lower() in FORBIDDEN_PROD_SOURCES or re.search(
        r"(?:^|[-_])(?:prod|production)(?:$|[-_])", source_env.lower()
    ):
        raise SanitizationError(
            f"production data is strictly forbidden in nonprod/staging (source_environment={source_env!r})"
        )
    if source_env not in ALLOWED_NONPROD_SOURCES:
        raise SanitizationError(
            f"unknown source_environment {source_env!r}; allowed: {sorted(ALLOWED_NONPROD_SOURCES)}"
        )

    snapshot_name = metadata.get("snapshot_name")
    if not isinstance(snapshot_name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", snapshot_name):
        raise SanitizationError(f"invalid snapshot_name: {snapshot_name!r}")
    if re.search(r"(?:^|[-_])(?:prod|production)(?:$|[-_])", snapshot_name.lower()):
        raise SanitizationError(f"snapshot_name must not reference production: {snapshot_name!r}")

    mig_sha = metadata.get("schema_migration_sha256")
    if not isinstance(mig_sha, str) or not SHA256_PREFIXED_RE.fullmatch(mig_sha):
        raise SanitizationError("schema_migration_sha256 must match 'sha256:<64-hex>'")

    retention = metadata.get("retention_days")
    if type(retention) is not int or retention < 1 or retention > 7:
        raise SanitizationError("retention_days must be an integer between 1 and 7")

    if metadata.get("pii_masked") is not True:
        raise SanitizationError("snapshot metadata must confirm pii_masked=True")
    if metadata.get("secrets_excluded") is not True:
        raise SanitizationError("snapshot metadata must confirm secrets_excluded=True")

    return metadata


def _strip_sensitive_json(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise SanitizationError("telemetry payload keys must be strings")
            if k.lower() in SENSITIVE_PAYLOAD_KEYS or any(
                token in k.lower() for token in ("secret", "token", "password", "private_key", "api_key")
            ):
                continue
            cleaned_v = _strip_sensitive_json(v)
            if cleaned_v is not None:
                cleaned[k] = cleaned_v
        return cleaned
    if isinstance(value, list):
        return [_strip_sensitive_json(item) for item in value if item is not None]
    return value


def _validate_dataset_schema(tables: dict[str, Any]) -> None:
    if not isinstance(tables, dict) or not tables:
        raise SanitizationError("tables must be a non-empty object")
    unknown_tables = set(tables.keys()) - set(ALLOWED_TABLE_COLUMNS.keys())
    if unknown_tables:
        raise SanitizationError(
            f"unknown table(s) in sanitization input (fail-closed): {sorted(unknown_tables)}"
        )
    for table_name, rows in tables.items():
        if not isinstance(rows, list):
            raise SanitizationError(f"table {table_name!r} must be a list of row objects")
        allowed_cols = ALLOWED_TABLE_COLUMNS[table_name]
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                raise SanitizationError(f"table {table_name!r} row {idx} must be an object")
            unknown_cols = set(row.keys()) - allowed_cols
            if unknown_cols:
                raise SanitizationError(
                    f"unknown column(s) in table {table_name!r} row {idx} (fail-closed): "
                    f"{sorted(unknown_cols)}"
                )


def sanitize_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitize a non-production dataset and verify zero PII/secret leakage."""
    if not isinstance(payload, dict):
        raise SanitizationError("dataset payload must be a JSON object")
    _check_no_retired_identifiers(payload)

    allowed_top = {"metadata", "tables"}
    if set(payload.keys()) != allowed_top:
        raise SanitizationError("dataset payload must contain exact keys: metadata, tables")

    metadata = copy.deepcopy(payload["metadata"])
    metadata["pii_masked"] = True
    metadata["secrets_excluded"] = True
    validate_sanitized_snapshot_metadata(metadata)

    raw_tables = payload["tables"]
    _validate_dataset_schema(raw_tables)
    tables = copy.deepcopy(raw_tables)

    # 1. Wipe credential and session tables
    for cred_table in CREDENTIAL_TABLES:
        if cred_table in tables:
            tables[cred_table] = []

    # 2. Anonymize user PII
    for row in tables.get("users", []):
        user_id = str(row.get("id", "")).strip()
        if not user_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user_id):
            raise SanitizationError(f"users row has invalid id: {user_id!r}")
        row["email"] = f"staging-user-{user_id}@pantheon.internal"
        row["full_name"] = f"Staging Persona {user_id}"
        row["password_hash"] = DUMMY_PASSWORD_HASH
        if "phone" in row:
            row["phone"] = None
        if "national_id" in row:
            row["national_id"] = None

    # 3. Force fail-closed safety settings
    if not tables.get("system_settings"):
        tables["system_settings"] = [{"id": "default"}]
    for setting in tables["system_settings"]:
        setting["live_broker_enabled"] = False
        setting["provider_enabled"] = False
        setting["real_writes_enabled"] = False
        setting["safe_write_strict"] = True
        setting["execution_mode"] = "paper"

    # 4. Strip sensitive telemetry fields
    for event in tables.get("telemetry_events", []):
        raw_payload = event.get("raw_payload")
        if raw_payload is not None and not isinstance(raw_payload, dict):
            raise SanitizationError("telemetry_events.raw_payload must be an object or null")
        if isinstance(raw_payload, dict):
            event["raw_payload"] = _strip_sensitive_json(raw_payload)

    canonical_tables = json.dumps(tables, sort_keys=True, separators=(",", ":")).encode("utf-8")
    metadata["dataset_sha256"] = "sha256:" + hashlib.sha256(canonical_tables).hexdigest()

    result = {"metadata": metadata, "tables": tables}
    verify_sanitized_dataset(result)
    return result


def verify_sanitized_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """Fail closed if a sanitized dataset still contains PII, secrets, or unsafe flags."""
    if not isinstance(dataset, dict) or set(dataset.keys()) != {"metadata", "tables"}:
        raise SanitizationError("sanitized dataset must contain exact keys: metadata, tables")
    validate_sanitized_snapshot_metadata(dataset["metadata"])
    tables = dataset["tables"]
    _validate_dataset_schema(tables)

    for cred_table in CREDENTIAL_TABLES:
        if tables.get(cred_table):
            raise SanitizationError(f"credential table {cred_table!r} must be empty after sanitization")

    for row in tables.get("users", []):
        email = row.get("email", "")
        if not isinstance(email, str) or not INTERNAL_STAGING_EMAIL_RE.fullmatch(email):
            raise SanitizationError(f"unmasked user email detected: {email!r}")
        full_name = row.get("full_name", "")
        if not isinstance(full_name, str) or not full_name.startswith("Staging Persona "):
            raise SanitizationError(f"unmasked user full_name detected: {full_name!r}")
        if row.get("password_hash") != DUMMY_PASSWORD_HASH:
            raise SanitizationError("user password_hash was not replaced with staging dummy hash")
        if row.get("phone") is not None or row.get("national_id") is not None:
            raise SanitizationError("user phone/national_id must be null in sanitized dataset")

    settings = tables.get("system_settings", [])
    if not settings:
        raise SanitizationError("system_settings table must contain at least one row")
    for row in settings:
        if row.get("live_broker_enabled") is not False:
            raise SanitizationError("system_settings.live_broker_enabled must be False")
        if row.get("provider_enabled") is not False:
            raise SanitizationError("system_settings.provider_enabled must be False")
        if row.get("real_writes_enabled") is not False:
            raise SanitizationError("system_settings.real_writes_enabled must be False")
        if row.get("safe_write_strict") is not True:
            raise SanitizationError("system_settings.safe_write_strict must be True")
        if row.get("execution_mode") != "paper":
            raise SanitizationError("system_settings.execution_mode must be 'paper'")

    for event in tables.get("telemetry_events", []):
        payload = event.get("raw_payload") or {}
        if not isinstance(payload, dict):
            raise SanitizationError("telemetry_events.raw_payload must be an object")
        _assert_no_sensitive_keys(payload)

    serialized = json.dumps(tables, sort_keys=True)
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(serialized):
            raise SanitizationError(f"residual secret value matched pattern {pattern.pattern!r}")
    for email_match in RAW_EMAIL_RE.findall(serialized):
        if not INTERNAL_STAGING_EMAIL_RE.fullmatch(email_match):
            raise SanitizationError(f"residual non-internal email found in dataset: {email_match!r}")

    return {
        "sanitized_verified": True,
        "snapshot_name": dataset["metadata"]["snapshot_name"],
        "source_environment": dataset["metadata"]["source_environment"],
        "dataset_sha256": dataset["metadata"].get("dataset_sha256"),
        "user_count": len(tables.get("users", [])),
    }


def _assert_no_sensitive_keys(obj: Any) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in SENSITIVE_PAYLOAD_KEYS or any(
                token in k.lower() for token in ("secret", "token", "password", "private_key", "api_key")
            ):
                raise SanitizationError(f"sensitive key {k!r} remains in telemetry payload")
            _assert_no_sensitive_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_sensitive_keys(item)


def verify_migration_and_compatibility(plan: dict[str, Any]) -> dict[str, Any]:
    """Verify expand-phase forward migration and old-app/new-schema backward compatibility."""
    if not isinstance(plan, dict):
        raise SanitizationError("migration plan must be a JSON object")
    required = {
        "expected_migration_set_sha256",
        "observed_migration_set_sha256",
        "phase",
        "statements",
        "old_app_schema",
        "new_schema",
        "forward_migration_applied",
        "old_app_smoke_against_new_schema_passed",
    }
    if not required.issubset(set(plan.keys())):
        raise SanitizationError(
            f"migration plan missing required keys: {sorted(required - set(plan.keys()))}"
        )

    expected_sha = plan["expected_migration_set_sha256"]
    observed_sha = plan["observed_migration_set_sha256"]
    if not isinstance(expected_sha, str) or not SHA256_PREFIXED_RE.fullmatch(expected_sha):
        raise SanitizationError("invalid expected_migration_set_sha256")
    if observed_sha != expected_sha:
        raise SanitizationError(
            f"migration_set_sha256 mismatch: expected {expected_sha}, observed {observed_sha}"
        )
    if plan.get("phase") != "expand":
        raise SanitizationError("staging release promotion only permits 'expand' phase migrations")

    statements = plan.get("statements")
    if not isinstance(statements, list) or not statements:
        raise SanitizationError("migration statements must be a non-empty list")
    for stmt in statements:
        if not isinstance(stmt, str) or not stmt.strip():
            raise SanitizationError("migration statement must be a non-empty string")
        if DESTRUCTIVE_SQL_RE.search(stmt):
            raise SanitizationError(
                f"destructive SQL forbidden in expand migration phase: {stmt!r}"
            )

    old_schema = plan.get("old_app_schema")
    new_schema = plan.get("new_schema")
    if not isinstance(old_schema, dict) or not isinstance(new_schema, dict) or not old_schema:
        raise SanitizationError("old_app_schema and new_schema must be non-empty objects")

    for table, old_cols in old_schema.items():
        if table not in new_schema:
            raise SanitizationError(
                f"backward compatibility broken: table {table!r} removed in new_schema"
            )
        new_cols = new_schema[table]
        if not isinstance(old_cols, dict) or not isinstance(new_cols, dict):
            raise SanitizationError(f"schema columns for table {table!r} must be objects")
        for col_name, old_spec in old_cols.items():
            if col_name not in new_cols:
                raise SanitizationError(
                    f"backward compatibility broken: column {table}.{col_name} removed in new_schema"
                )
            new_spec = new_cols[col_name]
            if not isinstance(old_spec, dict) or not isinstance(new_spec, dict):
                raise SanitizationError(f"column spec for {table}.{col_name} must be an object")
            if old_spec.get("type") != new_spec.get("type"):
                raise SanitizationError(
                    f"backward compatibility broken: column {table}.{col_name} changed type "
                    f"from {old_spec.get('type')!r} to {new_spec.get('type')!r}"
                )
        for col_name, new_spec in new_cols.items():
            if col_name not in old_cols:
                if not isinstance(new_spec, dict):
                    raise SanitizationError(f"column spec for {table}.{col_name} must be an object")
                if not (new_spec.get("nullable") is True or new_spec.get("has_default") is True):
                    raise SanitizationError(
                        f"backward compatibility broken: new column {table}.{col_name} is neither "
                        f"nullable nor defaulted, which breaks old application writes"
                    )

    if plan.get("forward_migration_applied") is not True:
        raise SanitizationError("forward_migration_applied must be True")
    if plan.get("old_app_smoke_against_new_schema_passed") is not True:
        raise SanitizationError("old_app_smoke_against_new_schema_passed must be True")

    return {
        "migration_verified": True,
        "migration_set_sha256": observed_sha,
        "old_app_new_schema_compatible": True,
    }


def verify_backup_restore_readability(record: dict[str, Any]) -> dict[str, Any]:
    """Verify backup creation, restore execution, and post-restore data readability."""
    if not isinstance(record, dict):
        raise SanitizationError("backup restore record must be a JSON object")
    required = {
        "backup_id",
        "backup_sha256",
        "expected_migration_set_sha256",
        "restored_migration_set_sha256",
        "restore_completed",
        "read_query_verified",
        "restored_row_counts",
    }
    if not required.issubset(set(record.keys())):
        raise SanitizationError(
            f"backup restore record missing keys: {sorted(required - set(record.keys()))}"
        )
    if not isinstance(record["backup_id"], str) or not record["backup_id"].strip():
        raise SanitizationError("backup_id must be a non-empty string")
    if not isinstance(record["backup_sha256"], str) or not SHA256_PREFIXED_RE.fullmatch(record["backup_sha256"]):
        raise SanitizationError("backup_sha256 must match 'sha256:<64-hex>'")
    if record["expected_migration_set_sha256"] != record["restored_migration_set_sha256"]:
        raise SanitizationError("restored schema migration hash does not match expected migration_set_sha256")
    if record.get("restore_completed") is not True or record.get("read_query_verified") is not True:
        raise SanitizationError("backup restore and read query verification must both be True")

    counts = record.get("restored_row_counts")
    if not isinstance(counts, dict):
        raise SanitizationError("restored_row_counts must be an object")
    if counts.get("users", 0) <= 0 or counts.get("tenants", 0) <= 0:
        raise SanitizationError("restored database must have positive readable rows for users and tenants")
    for cred_table in CREDENTIAL_TABLES:
        if counts.get(cred_table, 0) != 0:
            raise SanitizationError(f"restored {cred_table} must have 0 rows in nonprod")

    return {
        "backup_restore_verified": True,
        "backup_id": record["backup_id"],
        "backup_sha256": record["backup_sha256"],
    }


def verify_exact_pair_rollback(
    rollback_baseline: dict[str, Any],
    rollback_readback: dict[str, Any],
) -> dict[str, Any]:
    """Verify application rollback restores the exact baseline FE/BFF pair within RTO."""
    if not isinstance(rollback_baseline, dict) or not isinstance(rollback_readback, dict):
        raise SanitizationError("rollback_baseline and rollback_readback must be JSON objects")

    required_baseline = {
        "previous_release_id",
        "frontend_commit",
        "frontend_artifact_sha256",
        "backend_commit",
        "backend_operator_bff_image",
    }
    if set(rollback_baseline.keys()) != required_baseline:
        raise SanitizationError("invalid rollback_baseline keys")

    if rollback_readback.get("served_release_id") != rollback_baseline["previous_release_id"]:
        raise SanitizationError("rollback served_release_id does not match previous_release_id")
    if rollback_readback.get("served_frontend_commit") != rollback_baseline["frontend_commit"]:
        raise SanitizationError("rollback served_frontend_commit does not match baseline frontend_commit")
    if rollback_readback.get("served_frontend_artifact_sha256") != rollback_baseline["frontend_artifact_sha256"]:
        raise SanitizationError(
            "rollback served_frontend_artifact_sha256 does not match baseline frontend_artifact_sha256"
        )
    if rollback_readback.get("served_backend_commit") != rollback_baseline["backend_commit"]:
        raise SanitizationError("rollback served_backend_commit does not match baseline backend_commit")
    if rollback_readback.get("fe_manifest_paired_backend_commit") != rollback_baseline["backend_commit"]:
        raise SanitizationError("rollback FE deployment.json paired backend commit mismatch (hybrid state)")
    if rollback_readback.get("served_operator_bff_image") != rollback_baseline["backend_operator_bff_image"]:
        raise SanitizationError("rollback served_operator_bff_image does not match baseline image digest")
    if rollback_readback.get("bff_healthy") is not True:
        raise SanitizationError("rollback BFF health check is not healthy")
    if rollback_readback.get("singleton_worker_owner_unique") is not True:
        raise SanitizationError("rollback singleton worker ownership is not unique")

    duration = rollback_readback.get("rollback_duration_seconds")
    if type(duration) not in (int, float) or isinstance(duration, bool) or duration < 0 or duration > 120:
        raise SanitizationError(
            f"rollback duration must be <= 120 seconds (got {duration!r})"
        )

    return {
        "exact_pair_rollback_verified": True,
        "restored_release_id": rollback_baseline["previous_release_id"],
        "rollback_duration_seconds": duration,
    }


def verify_security_and_safety_readbacks(readback: dict[str, Any]) -> dict[str, Any]:
    """Verify tenant isolation, RBAC, and provider/write/broker-off readbacks."""
    if not isinstance(readback, dict):
        raise SanitizationError("security and safety readback must be a JSON object")

    expected_booleans = {
        "tenant_isolation_enforced": True,
        "cross_tenant_read_rejected": True,
        "cross_tenant_write_rejected": True,
        "unauthenticated_rejected": True,
        "viewer_mutation_rejected": True,
        "role_map_strict": True,
        "provider_enabled": False,
        "real_writes_enabled": False,
        "dev_stub_writes_enabled": False,
        "live_broker_enabled": False,
        "control_vm_broker_credentials_present": False,
    }
    for key, expected in expected_booleans.items():
        actual = readback.get(key)
        if type(actual) is not bool or actual is not expected:
            raise SanitizationError(
                f"security/safety readback failed for {key}: expected {expected!r}, got {actual!r}"
            )

    if readback.get("vite_bff_mode") != "live":
        raise SanitizationError("vite_bff_mode readback must be 'live'")
    if readback.get("vite_bff_fallback") != "strict":
        raise SanitizationError("vite_bff_fallback readback must be 'strict'")
    if readback.get("execution_mode") != "paper":
        raise SanitizationError("execution_mode readback must be 'paper'")

    return {
        "security_and_safety_readback_verified": True,
        "provider_enabled": False,
        "real_writes_enabled": False,
        "live_broker_enabled": False,
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise SanitizationError(f"cannot read JSON from {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SanitizationError(f"JSON root in {path} must be an object")
    return data


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("sanitize", help="Sanitize raw nonprod dataset JSON and write output")
    prep.add_argument("--input", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)

    ver_san = sub.add_parser("verify-sanitized", help="Verify a sanitized dataset JSON file")
    ver_san.add_argument("--input", type=Path, required=True)

    ver_mig = sub.add_parser("verify-migration", help="Verify expand migration and old-app/new-schema compatibility")
    ver_mig.add_argument("--input", type=Path, required=True)

    ver_bak = sub.add_parser("verify-backup-restore", help="Verify backup restore readability")
    ver_bak.add_argument("--input", type=Path, required=True)

    ver_rb = sub.add_parser("verify-rollback", help="Verify exact-pair application rollback")
    ver_rb.add_argument("--baseline", type=Path, required=True)
    ver_rb.add_argument("--readback", type=Path, required=True)

    ver_sec = sub.add_parser("verify-readbacks", help="Verify tenant/RBAC and safe-off readbacks")
    ver_sec.add_argument("--input", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "sanitize":
            sanitized = sanitize_dataset(_load_json(args.input))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(sanitized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps(verify_sanitized_dataset(sanitized), sort_keys=True))
        elif args.command == "verify-sanitized":
            print(json.dumps(verify_sanitized_dataset(_load_json(args.input)), sort_keys=True))
        elif args.command == "verify-migration":
            print(json.dumps(verify_migration_and_compatibility(_load_json(args.input)), sort_keys=True))
        elif args.command == "verify-backup-restore":
            print(json.dumps(verify_backup_restore_readability(_load_json(args.input)), sort_keys=True))
        elif args.command == "verify-rollback":
            print(
                json.dumps(
                    verify_exact_pair_rollback(_load_json(args.baseline), _load_json(args.readback)),
                    sort_keys=True,
                )
            )
        elif args.command == "verify-readbacks":
            print(json.dumps(verify_security_and_safety_readbacks(_load_json(args.input)), sort_keys=True))
        return 0
    except SanitizationError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
