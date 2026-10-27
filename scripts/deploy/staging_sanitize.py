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

# Staging carries the full owned application schema, not a hand-picked table list: the allowed
# tables/columns are the catalog read back from the migrated staging Postgres (every non-system
# schema: public, trade_journey_projection, agora, governance, research, ...). A table or column
# that is not in that catalog fails closed; there are no phantom identity/credential/settings
# tables (tenants and RBAC come from BFF JWT claims, safe-off flags from service env).
SYSTEM_SCHEMAS = frozenset({"pg_catalog", "information_schema"})
QUALIFIED_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")
REDACTED = "[REDACTED]"
# Credential-bearing tables must never exist in staging, whatever schema they live in.
FORBIDDEN_TABLES = frozenset({"broker_credentials", "exchange_api_keys", "user_sessions"})
FORBIDDEN_TABLE_RE = re.compile(r"credential|secret|api_?key|password|session_token", re.IGNORECASE)
# Columns whose name says they carry a secret are redacted on every table (e.g. lease_token).
SENSITIVE_COLUMN_RE = re.compile(
    r"password|secret|token|api_?key|private_key|credential|cookie|authorization", re.IGNORECASE
)
# Product durable owner stores (bind-mounted by the staging compose, archived with backups).
OWNER_STORE_PATHS = ("bff", "runtime", "governance")


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
    unknown_keys = set(metadata.keys()) - (
        required_keys | {"created_at", "dataset_sha256", "observed_from_live_vm", "dry_run"}
    )
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


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SENSITIVE_PAYLOAD_KEYS or any(
        token in lowered for token in ("secret", "token", "password", "private_key", "api_key")
    )


def _mask_email(match: "re.Match[str]") -> str:
    email = match.group(0)
    if INTERNAL_STAGING_EMAIL_RE.fullmatch(email):
        return email
    return f"staging-user-{hashlib.sha256(email.lower().encode('utf-8')).hexdigest()[:12]}@pantheon.internal"


def _scrub_string(value: str) -> str:
    for pattern in SECRET_VALUE_PATTERNS:
        value = pattern.sub(REDACTED, value)
    return RAW_EMAIL_RE.sub(_mask_email, value)


def _scrub_value(value: Any) -> Any:
    """Recursively drop secret-bearing keys, redact secret values and mask raw emails."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise SanitizationError("JSON column keys must be strings")
            if _is_sensitive_key(k):
                continue
            cleaned_v = _scrub_value(v)
            if cleaned_v is not None:
                cleaned[k] = cleaned_v
        return cleaned
    if isinstance(value, list):
        return [_scrub_value(item) for item in value if item is not None]
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def _column_type(spec: Any) -> str:
    return str(spec.get("type") if isinstance(spec, dict) else spec or "").lower()


def _validate_catalog(catalog: Any) -> dict[str, dict[str, Any]]:
    """The migrated staging catalog: {"schema.table": {column: {"type": ...}}} for every app schema."""
    if not isinstance(catalog, dict) or not catalog:
        raise SanitizationError("catalog must be a non-empty object of schema.table entries")
    for name, columns in catalog.items():
        if not isinstance(name, str) or not QUALIFIED_TABLE_RE.fullmatch(name):
            raise SanitizationError(f"catalog entry {name!r} must be a schema-qualified table name")
        schema, table = name.split(".", 1)
        if schema in SYSTEM_SCHEMAS or schema.startswith(("pg_toast", "pg_temp")):
            raise SanitizationError(f"catalog must not include system schema table {name!r}")
        if table in FORBIDDEN_TABLES or FORBIDDEN_TABLE_RE.search(table):
            raise SanitizationError(f"credential table(s) must not exist in staging (fail-closed): {[name]}")
        if not isinstance(columns, dict) or not columns:
            raise SanitizationError(f"catalog entry {name!r} must list its columns")
    return catalog


def _validate_dataset_schema(tables: Any, catalog: dict[str, dict[str, Any]]) -> None:
    if not isinstance(tables, dict) or not tables:
        raise SanitizationError("tables must be a non-empty object")
    for name in tables:
        short = name.rsplit(".", 1)[-1] if isinstance(name, str) else ""
        if short in FORBIDDEN_TABLES or FORBIDDEN_TABLE_RE.search(short):
            raise SanitizationError(f"credential table(s) must not exist in staging (fail-closed): {[name]}")
    unknown_tables = set(tables.keys()) - set(catalog.keys())
    if unknown_tables:
        raise SanitizationError(
            f"unknown table(s) in sanitization input (fail-closed): {sorted(unknown_tables)}"
        )
    for table_name, rows in tables.items():
        if not isinstance(rows, list):
            raise SanitizationError(f"table {table_name!r} must be a list of row objects")
        allowed_cols = set(catalog[table_name])
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                raise SanitizationError(f"table {table_name!r} row {idx} must be an object")
            unknown_cols = set(row.keys()) - allowed_cols
            if unknown_cols:
                raise SanitizationError(
                    f"unknown column(s) in table {table_name!r} row {idx} (fail-closed): "
                    f"{sorted(unknown_cols)}"
                )


def _redacted_form(value: Any, col_type: str) -> Any:
    if col_type == "bytea":
        return None
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    if isinstance(value, str):
        return REDACTED
    return None


def sanitize_dataset(payload: dict[str, Any]) -> dict[str, Any]:
    """Sanitize a non-production dataset covering every owned schema and verify zero PII/secrets."""
    if not isinstance(payload, dict):
        raise SanitizationError("dataset payload must be a JSON object")
    _check_no_retired_identifiers(payload)

    required_top = {"metadata", "catalog", "tables"}
    allowed_top = required_top | {"observed_from_live_vm", "dry_run"}
    if not required_top.issubset(payload.keys()) or not set(payload.keys()).issubset(allowed_top):
        raise SanitizationError("dataset payload must contain exact keys: metadata, catalog, tables")

    metadata = copy.deepcopy(payload["metadata"])
    metadata["pii_masked"] = True
    metadata["secrets_excluded"] = True
    validate_sanitized_snapshot_metadata(metadata)

    catalog = _validate_catalog(payload["catalog"])
    raw_tables = payload["tables"]
    _validate_dataset_schema(raw_tables, catalog)
    tables = copy.deepcopy(raw_tables)

    # Every column of every table in every schema is scrubbed: secret-named columns and bytea are
    # redacted, JSON structures lose secret-bearing keys, strings lose secret values and raw emails.
    for table_name, rows in tables.items():
        for row in rows:
            for column, value in list(row.items()):
                if value is None:
                    continue
                col_type = _column_type(catalog[table_name][column])
                if col_type == "bytea" or SENSITIVE_COLUMN_RE.search(column):
                    row[column] = _redacted_form(value, col_type)
                else:
                    row[column] = _scrub_value(value)

    canonical_tables = json.dumps(tables, sort_keys=True, separators=(",", ":")).encode("utf-8")
    metadata["dataset_sha256"] = "sha256:" + hashlib.sha256(canonical_tables).hexdigest()

    result = {"metadata": metadata, "catalog": catalog, "tables": tables}
    verify_sanitized_dataset(result)
    return result


def verify_sanitized_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """Fail closed if a sanitized dataset still contains PII, secrets, or unsafe flags."""
    required_top = {"metadata", "catalog", "tables"}
    allowed_top = required_top | {"observed_from_live_vm", "dry_run"}
    if (
        not isinstance(dataset, dict)
        or not required_top.issubset(dataset.keys())
        or not set(dataset.keys()).issubset(allowed_top)
    ):
        raise SanitizationError("sanitized dataset must contain exact keys: metadata, catalog, tables")
    validate_sanitized_snapshot_metadata(dataset["metadata"])
    catalog = _validate_catalog(dataset["catalog"])
    tables = dataset["tables"]
    _validate_dataset_schema(tables, catalog)

    for table_name, rows in tables.items():
        for row in rows:
            for column, value in row.items():
                if value is None:
                    continue
                col_type = _column_type(catalog[table_name][column])
                if col_type == "bytea":
                    raise SanitizationError(f"binary column {table_name}.{column} must be empty in staging")
                if SENSITIVE_COLUMN_RE.search(column) and value not in (REDACTED, {}, []):
                    raise SanitizationError(f"secret-named column {table_name}.{column} holds an unredacted value")
                _assert_no_sensitive_keys(value)

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
        "row_counts": {name: len(rows) for name, rows in sorted(tables.items())},
        "schemas": sorted({name.split(".", 1)[0] for name in catalog}),
    }


def _assert_no_sensitive_keys(obj: Any) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _is_sensitive_key(k):
                raise SanitizationError(f"sensitive key {k!r} remains in sanitized JSON column")
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

    add_col_re = re.compile(
        r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
        re.IGNORECASE,
    )
    for stmt in statements:
        for match in add_col_re.finditer(stmt):
            target_table, target_col = match.group(1), match.group(2)
            if "." not in target_table:
                target_table = f"public.{target_table}"
            if target_table not in new_schema or target_col not in new_schema.get(target_table, {}):
                raise SanitizationError(
                    f"migration statement added column {target_table}.{target_col}, "
                    f"but post-migration catalog new_schema does not contain it"
                )

    if plan.get("forward_migration_applied") is not True:
        raise SanitizationError("forward_migration_applied must be True")
    if plan.get("old_app_smoke_against_new_schema_passed") is not True:
        raise SanitizationError("old_app_smoke_against_new_schema_passed must be True")

    if plan.get("observed_from_live_vm") is True:
        if plan.get("catalog_observed") is not True:
            raise SanitizationError("live migration verification requires catalog_observed=True")
        if plan.get("old_app_readyz_status") != 200:
            raise SanitizationError(
                f"old-app /bff/readyz against migrated schema must return 200 (got {plan.get('old_app_readyz_status')!r})"
            )
        if plan.get("old_app_me_read_status") != 200:
            raise SanitizationError(
                f"old-app authenticated GET /bff/me against migrated schema must return 200 (got {plan.get('old_app_me_read_status')!r})"
            )
        old_app_commit = plan.get("old_app_version_commit_observed")
        if not isinstance(old_app_commit, str) or not COMMIT_SHA_RE.fullmatch(old_app_commit):
            raise SanitizationError("old_app_version_commit_observed must be a 40-hex commit SHA")
        old_app_img = plan.get("old_app_image_observed")
        if not isinstance(old_app_img, str) or not IMAGE_DIGEST_RE.fullmatch(old_app_img):
            raise SanitizationError("old_app_image_observed must be a digest-pinned image reference")

    return {
        "migration_verified": True,
        "migration_set_sha256": observed_sha,
        "old_app_new_schema_compatible": True,
    }


def verify_backup_restore_readability(record: dict[str, Any]) -> dict[str, Any]:
    """Verify Postgres + durable owner-store backup, restore, and post-restore readability.

    Identity is the real catalog (information_schema columns hashed before the backup and after
    the restore) plus exact row counts of the actual product tables and the file digest manifest of
    the durable owner stores (`/data/bff`, `/data/runtime`, `/data/governance`); there is no
    synthetic `schema_migrations` row.
    """
    if not isinstance(record, dict):
        raise SanitizationError("backup restore record must be a JSON object")
    required = {
        "backup_id",
        "backup_sha256",
        "expected_catalog_sha256",
        "restored_catalog_sha256",
        "restore_completed",
        "read_query_verified",
        "live_row_counts",
        "restored_row_counts",
        "owner_store_backup_sha256",
        "owner_store_manifest_sha256",
        "restored_owner_store_manifest_sha256",
        "owner_store_dirs",
        "catalog_tables",
    }
    if not required.issubset(set(record.keys())):
        raise SanitizationError(
            f"backup restore record missing keys: {sorted(required - set(record.keys()))}"
        )
    if not isinstance(record["backup_id"], str) or not record["backup_id"].strip():
        raise SanitizationError("backup_id must be a non-empty string")
    for key in ("backup_sha256", "owner_store_backup_sha256"):
        if not isinstance(record[key], str) or not SHA256_PREFIXED_RE.fullmatch(record[key]):
            raise SanitizationError(f"{key} must match 'sha256:<64-hex>'")
    for key in ("expected_catalog_sha256", "restored_catalog_sha256"):
        if not isinstance(record[key], str) or not SHA256_PREFIXED_RE.fullmatch(record[key]):
            raise SanitizationError(f"{key} must match 'sha256:<64-hex>'")
    if record["expected_catalog_sha256"] != record["restored_catalog_sha256"]:
        raise SanitizationError("restored catalog hash does not match the pre-backup catalog hash")
    if record.get("restore_completed") is not True or record.get("read_query_verified") is not True:
        raise SanitizationError("backup restore and read query verification must both be True")

    counts = record["restored_row_counts"]
    live_counts = record["live_row_counts"]
    if not isinstance(counts, dict) or not isinstance(live_counts, dict):
        raise SanitizationError("restored_row_counts and live_row_counts must be objects")
    catalog_tables = record["catalog_tables"]
    if not isinstance(catalog_tables, list) or not catalog_tables:
        raise SanitizationError("catalog_tables must list every owned schema.table")
    if "public.telemetry_events" not in counts:
        raise SanitizationError("restored database must expose the public.telemetry_events table")
    bad = [n for n in counts if not isinstance(n, str) or not QUALIFIED_TABLE_RE.fullmatch(n)]
    if bad:
        raise SanitizationError(f"restored_row_counts has unknown table(s): {sorted(map(str, bad))}")
    if sorted(counts.keys()) != sorted(catalog_tables):
        raise SanitizationError(
            "restored_row_counts must cover exactly the full owned catalog (every schema.table)"
        )
    for name, value in counts.items():
        if type(value) is not int or value < 0:
            raise SanitizationError(f"restored row count for {name!r} must be a non-negative integer")
    if counts != live_counts:
        raise SanitizationError("restored row counts differ from pre-backup row counts")

    for key in ("owner_store_manifest_sha256", "restored_owner_store_manifest_sha256"):
        if not isinstance(record[key], str) or not SHA256_PREFIXED_RE.fullmatch(record[key]):
            raise SanitizationError(f"{key} must match 'sha256:<64-hex>'")
    if record["owner_store_manifest_sha256"] != record["restored_owner_store_manifest_sha256"]:
        raise SanitizationError("restored owner-store file manifest differs from the pre-backup manifest")
    if record["owner_store_dirs"] != list(OWNER_STORE_PATHS):
        raise SanitizationError(f"owner store backup must cover exactly {list(OWNER_STORE_PATHS)}")

    return {
        "backup_restore_verified": True,
        "backup_id": record["backup_id"],
        "backup_sha256": record["backup_sha256"],
        "owner_store_backup_sha256": record["owner_store_backup_sha256"],
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
    optional_baseline = {
        "backend_images",
        "config_schema_version",
    }
    baseline_keys = set(rollback_baseline.keys())
    if not required_baseline.issubset(baseline_keys) or not baseline_keys.issubset(
        required_baseline | optional_baseline
    ):
        raise SanitizationError("invalid rollback_baseline keys")

    if rollback_readback.get("served_release_id") != rollback_baseline["previous_release_id"]:
        raise SanitizationError("rollback served_release_id does not match previous_release_id")
    if rollback_readback.get("served_frontend_commit") != rollback_baseline["frontend_commit"]:
        raise SanitizationError("rollback served_frontend_commit does not match baseline frontend_commit")
    if rollback_readback.get("served_frontend_artifact_sha256") != rollback_baseline["frontend_artifact_sha256"]:
        raise SanitizationError(
            "rollback served_frontend_artifact_sha256 does not match baseline frontend_artifact_sha256"
        )
    if (
        rollback_readback.get("observed_from_live_vm") is True
        or "verified_baseline_archive_sha256" in rollback_readback
    ):
        if (
            rollback_readback.get("verified_baseline_archive_sha256")
            != rollback_baseline["frontend_artifact_sha256"]
        ):
            raise SanitizationError(
                "rollback verified_baseline_archive_sha256 does not match baseline frontend_artifact_sha256"
            )
    if rollback_readback.get("served_backend_commit") != rollback_baseline["backend_commit"]:
        raise SanitizationError("rollback served_backend_commit does not match baseline backend_commit")
    if rollback_readback.get("fe_manifest_paired_backend_commit") != rollback_baseline["backend_commit"]:
        raise SanitizationError("rollback FE deployment.json paired backend commit mismatch (hybrid state)")
    if rollback_readback.get("served_operator_bff_image") != rollback_baseline["backend_operator_bff_image"]:
        raise SanitizationError("rollback served_operator_bff_image does not match baseline image digest")

    required_owner_keys = {
        "operator_bff",
        "runtime_manager",
        "governance",
        "deployment",
        "lifecycle_projector",
    }
    if (
        rollback_readback.get("observed_from_live_vm") is True
        or "served_backend_images" in rollback_readback
        or "backend_images" in rollback_baseline
    ):
        served_imgs = rollback_readback.get("served_backend_images")
        if served_imgs is not None or rollback_readback.get("observed_from_live_vm") is True:
            if not isinstance(served_imgs, dict) or set(served_imgs.keys()) != required_owner_keys:
                raise SanitizationError(
                    "rollback served_backend_images must report all 5 owner services: "
                    "operator_bff, runtime_manager, governance, deployment, lifecycle_projector"
                )
            for svc_key, img_val in served_imgs.items():
                if not isinstance(img_val, str) or not IMAGE_DIGEST_RE.fullmatch(img_val):
                    raise SanitizationError(
                        f"rollback served_backend_images[{svc_key!r}] is not a digest-pinned image"
                    )
            if served_imgs["operator_bff"] != rollback_baseline["backend_operator_bff_image"]:
                raise SanitizationError(
                    "rollback served_backend_images['operator_bff'] does not match backend_operator_bff_image"
                )
            baseline_imgs = rollback_baseline.get("backend_images")
            if isinstance(baseline_imgs, dict):
                for svc_key, expected_img in baseline_imgs.items():
                    if served_imgs.get(svc_key) != expected_img:
                        raise SanitizationError(
                            f"rollback served_backend_images[{svc_key!r}] does not match baseline image {expected_img!r}"
                        )

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

    status_control_keys = {
        "same_tenant_operator_read_status",
        "same_tenant_operator_read_allowed",
        "same_tenant_viewer_read_status",
        "same_tenant_viewer_read_allowed",
        "same_tenant_operator_write_status",
        "same_tenant_operator_write_allowed",
        "unauthenticated_status",
        "unauthenticated_error_code",
        "cross_tenant_read_status",
        "cross_tenant_read_error_code",
        "cross_tenant_read_precondition",
        "cross_tenant_write_status",
        "cross_tenant_write_error_code",
        "cross_tenant_write_precondition",
        "viewer_mutation_status",
        "viewer_mutation_error_code",
        "viewer_mutation_precondition",
    }
    if readback.get("observed_from_live_vm") is True or bool(status_control_keys & set(readback.keys())):
        if readback.get("same_tenant_operator_read_allowed") is not True or readback.get("same_tenant_operator_read_status") != 200:
            raise SanitizationError(
                "positive control failed: same-tenant operator GET /bff/me must return HTTP 200"
            )
        if readback.get("same_tenant_viewer_read_allowed") is not True or readback.get("same_tenant_viewer_read_status") != 200:
            raise SanitizationError(
                "positive control failed: same-tenant viewer GET /bff/me must return HTTP 200"
            )
        if readback.get("same_tenant_operator_write_allowed") is not True or readback.get("same_tenant_operator_write_status") != 200:
            raise SanitizationError(
                "positive control failed: same-tenant operator POST /bff/switch-tenant must return HTTP 200"
            )
        if readback.get("unauthenticated_status") != 401 or readback.get("unauthenticated_error_code") != "AUTH_REQUIRED":
            raise SanitizationError(
                f"unauthenticated GET /bff/me must return 401 AUTH_REQUIRED (got status={readback.get('unauthenticated_status')!r}, code={readback.get('unauthenticated_error_code')!r})"
            )
        if (
            readback.get("cross_tenant_read_status") != 403
            or readback.get("cross_tenant_read_error_code") != "FORBIDDEN"
            or readback.get("cross_tenant_read_precondition") != "tenant_scope"
        ):
            raise SanitizationError(
                f"cross-tenant read must return 403 FORBIDDEN (precondition_failed='tenant_scope'); "
                f"got status={readback.get('cross_tenant_read_status')!r}, code={readback.get('cross_tenant_read_error_code')!r}, "
                f"precondition={readback.get('cross_tenant_read_precondition')!r}"
            )
        if (
            readback.get("cross_tenant_write_status") != 403
            or readback.get("cross_tenant_write_error_code") != "FORBIDDEN"
            or readback.get("cross_tenant_write_precondition") != "tenant_scope"
        ):
            raise SanitizationError(
                f"cross-tenant write must return 403 FORBIDDEN (precondition_failed='tenant_scope'); "
                f"got status={readback.get('cross_tenant_write_status')!r}, code={readback.get('cross_tenant_write_error_code')!r}, "
                f"precondition={readback.get('cross_tenant_write_precondition')!r}"
            )
        if (
            readback.get("viewer_mutation_status") != 403
            or readback.get("viewer_mutation_error_code") != "FORBIDDEN"
            or readback.get("viewer_mutation_precondition") != "role_check"
        ):
            raise SanitizationError(
                f"viewer mutation must return 403 FORBIDDEN (precondition_failed='role_check'); "
                f"got status={readback.get('viewer_mutation_status')!r}, code={readback.get('viewer_mutation_error_code')!r}, "
                f"precondition={readback.get('viewer_mutation_precondition')!r}"
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
