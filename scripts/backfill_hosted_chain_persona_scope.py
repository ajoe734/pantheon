#!/usr/bin/env python3
"""Backfill hosted chain Persona test scope metadata through the Persona owner.

Hosted L12 chain runs previously created chain Personas directly at the Persona
owner ``POST /api/personas`` with only ``metadata.requested_by``, leaving fleet
classification as ``purpose=unknown`` and ``data_scope=unknown``.  This one-time
scoped script patches explicit chain Persona IDs via the Persona owner's
``PATCH /api/personas/{id}`` endpoint with only ``actor_id`` and
``metadata: {"data_scope": "test", "persona_class": "trading"}``.

It never calls ``PersonaRegistryHttpWritePort.update_persona`` (which rewrites
``metadata.requested_by`` as a string), never writes a database, and never reads
or prints a secret.  Run inside the dev ``operator-bff`` container via stdin::

    docker exec -i pantheon-operator-bff-1 python - \\
      --persona-id persona-strat-hosted-all12-h12-claude-20261027-4923-src-106b4057d07a \\
      --persona-id persona-strat-hosted-all12-h12-01a11-20261023-9aeb-src-e018ff6d0eb8 \\
      [--apply] < scripts/backfill_hosted_chain_persona_scope.py
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA_VERSION = "pantheon.hosted-chain-persona-scope-backfill.v1"
BACKFILL_METADATA: dict[str, str] = {
    "data_scope": "test",
    "persona_class": "trading",
}
PRESERVED_FIELDS = ("name", "mandate", "status", "lifecycle_state", "owner")
_TENANT_ENVS = (
    "PANTHEON_DEV_BFF_TENANT_ID",
    "PANTHEON_BFF_TENANT_ID",
    "PANTHEON_BFF_DEFAULT_TENANT_ID",
    "PANTHEON_BFF_HEALTH_TENANT_ID",
    "PANTHEON_TENANT_ID",
)
_SAFE_PERSONA_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_SECRET_KEY = re.compile(
    r"token|secret|password|credential|authorization|cookie|private_key|api_key",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"(?i)(\b(?:bearer|token|secret|password|api_key)\s*[:=]\s*|\bbearer\s+)\S+"
)


class BackfillError(RuntimeError):
    """Raised when backfill inputs, tenant scope, or owner readback fail closed."""


def configured_dev_tenant(env: Mapping[str, str] | None = None) -> str:
    """Resolve the container's configured dev tenant; fail closed outside dev."""
    source = os.environ if env is None else env
    environment = str(source.get("PANTHEON_ENV") or "dev").strip().lower()
    if environment != "dev":
        raise BackfillError(
            f"hosted chain persona backfill requires PANTHEON_ENV=dev, got {environment!r}"
        )
    for name in _TENANT_ENVS:
        value = str(source.get(name) or "").strip()
        if value:
            if value == "*":
                raise BackfillError(f"configured tenant in {name} must not be '*'")
            return value
    return "tenant-dev"


def _scrub(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(k): "[REDACTED]" if _SECRET_KEY.search(str(k)) else _scrub(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub(r"\1[REDACTED]", value)
    return value


def _record_view(record: Mapping[str, Any]) -> dict[str, Any]:
    from services.control_plane.bff.personas import fleet_classification as fc

    metadata = _scrub(dict(record.get("metadata") or {}))
    view: dict[str, Any] = {
        "persona_id": record.get("persona_id"),
        "tenant_id": record.get("tenant_id"),
        "name": record.get("name"),
        "mandate": record.get("mandate"),
        "status": record.get("status"),
        "lifecycle_state": record.get("lifecycle_state"),
        "owner": record.get("owner"),
        "metadata": metadata,
    }
    view["classification"] = fc.persona_classification(view)
    return view


def _already_backfilled(metadata: Mapping[str, Any]) -> bool:
    return all(metadata.get(k) == v for k, v in BACKFILL_METADATA.items())


def _validate_existing_record(
    persona_id: str,
    record: Any,
    *,
    expected_tenant_id: str,
) -> dict[str, Any]:
    if not isinstance(record, Mapping) or record.get("persona_id") != persona_id:
        raise BackfillError(f"unknown persona_id: {persona_id!r}")
    tenant_id = str(record.get("tenant_id") or "").strip()
    if tenant_id != expected_tenant_id:
        raise BackfillError(
            f"persona {persona_id!r} belongs to tenant {tenant_id!r}, "
            f"expected configured dev tenant {expected_tenant_id!r}"
        )
    metadata = record.get("metadata")
    if metadata is not None and not isinstance(metadata, Mapping):
        raise BackfillError(f"persona {persona_id!r} metadata is not an object")
    meta_dict = dict(metadata or {})
    for key, target in BACKFILL_METADATA.items():
        existing = meta_dict.get(key)
        if existing not in (None, "", target):
            raise BackfillError(
                f"persona {persona_id!r} already declares metadata.{key}={existing!r} "
                f"(expected unset or {target!r})"
            )
    return copy.deepcopy(dict(record))


def _verify_unchanged_fields(
    persona_id: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    expected_tenant_id: str,
) -> None:
    if after.get("persona_id") != persona_id or after.get("tenant_id") != expected_tenant_id:
        raise BackfillError(f"persona {persona_id!r} readback identity or tenant mismatch")
    for field in PRESERVED_FIELDS:
        if after.get(field) != before.get(field):
            raise BackfillError(
                f"persona {persona_id!r} field {field!r} changed from "
                f"{before.get(field)!r} to {after.get(field)!r}"
            )
    before_meta = dict(before.get("metadata") or {})
    after_meta = dict(after.get("metadata") or {})
    if after_meta.get("requested_by") != before_meta.get("requested_by"):
        raise BackfillError(
            f"persona {persona_id!r} metadata.requested_by changed from "
            f"{before_meta.get('requested_by')!r} to {after_meta.get('requested_by')!r}"
        )
    for key, expected in before_meta.items():
        if key not in BACKFILL_METADATA and after_meta.get(key) != expected:
            raise BackfillError(f"persona {persona_id!r} metadata.{key} changed unexpectedly")
    if not _already_backfilled(after_meta):
        raise BackfillError(
            f"persona {persona_id!r} readback metadata missing {BACKFILL_METADATA!r}"
        )


def patch_body(actor_id: str) -> dict[str, Any]:
    """Exact PATCH /api/personas/{id} request body for the Persona owner."""
    clean_actor = str(actor_id or "").strip()
    if not clean_actor:
        raise BackfillError("persona owner service actor_id is required")
    return {
        "actor_id": clean_actor,
        "metadata": dict(BACKFILL_METADATA),
    }


def _patch_persona_metadata(owner: Any, persona_id: str, *, actor_id: str) -> None:
    quoted = urllib.parse.quote(persona_id, safe="")
    body = patch_body(actor_id)
    updated = owner._request(
        "PATCH",
        f"/api/personas/{quoted}",
        dependency="persona_registry_write_owner",
        body=body,
        write=True,
    )
    if not isinstance(updated, Mapping) or updated.get("persona_id") != persona_id:
        raise BackfillError(f"persona owner returned invalid PATCH response for {persona_id!r}")


def execute(
    *,
    owner: Any,
    persona_ids: Sequence[str],
    apply: bool = False,
    expected_tenant_id: str | None = None,
    actor_id: str | None = None,
) -> dict[str, Any]:
    clean_ids = _normalize_persona_ids(persona_ids)
    tenant_id = expected_tenant_id if expected_tenant_id is not None else configured_dev_tenant()
    if not tenant_id or tenant_id == "*":
        raise BackfillError("expected_tenant_id must be a concrete dev tenant")
    resolved_actor = str(
        actor_id if actor_id is not None else getattr(owner, "_service_actor_id", "")
    ).strip()
    if not resolved_actor:
        raise BackfillError("persona owner service actor_id is not configured")

    # Pre-read and validate every requested record before any write so an
    # unknown ID or wrong-tenant record fails closed without partial updates.
    existing_by_id: dict[str, dict[str, Any]] = {}
    for pid in clean_ids:
        raw = owner.get_persona(pid)
        existing_by_id[pid] = _validate_existing_record(
            pid, raw, expected_tenant_id=tenant_id
        )

    records_out: list[dict[str, Any]] = []
    for pid in clean_ids:
        before_raw = existing_by_id[pid]
        before_meta = dict(before_raw.get("metadata") or {})
        already_done = _already_backfilled(before_meta)
        before_view = _record_view(before_raw)

        if not apply:
            projected_raw = {
                **before_raw,
                "metadata": {**before_meta, **BACKFILL_METADATA},
            }
            after_view = _record_view(projected_raw)
            _verify_unchanged_fields(
                pid, before_raw, projected_raw, expected_tenant_id=tenant_id
            )
            records_out.append(
                {
                    "persona_id": pid,
                    "changed": not already_done,
                    "before": before_view,
                    "after": after_view,
                }
            )
            continue

        if already_done:
            _verify_unchanged_fields(
                pid, before_raw, before_raw, expected_tenant_id=tenant_id
            )
            records_out.append(
                {
                    "persona_id": pid,
                    "changed": False,
                    "before": before_view,
                    "after": copy.deepcopy(before_view),
                }
            )
            continue

        _patch_persona_metadata(owner, pid, actor_id=resolved_actor)
        readback = owner.get_persona(pid)
        validated_after = _validate_existing_record(
            pid, readback, expected_tenant_id=tenant_id
        )
        _verify_unchanged_fields(
            pid, before_raw, validated_after, expected_tenant_id=tenant_id
        )
        records_out.append(
            {
                "persona_id": pid,
                "changed": True,
                "before": before_view,
                "after": _record_view(validated_after),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "applied" if apply else "dry_run",
        "dry_run": not apply,
        "apply": bool(apply),
        "tenant_id": tenant_id,
        "actor_id": resolved_actor,
        "persona_ids": clean_ids,
        "records": records_out,
    }


def _normalize_persona_ids(persona_ids: Sequence[str] | None) -> list[str]:
    if not persona_ids:
        raise BackfillError("at least one explicit --persona-id is required")
    ordered: list[str] = []
    for raw in persona_ids:
        clean = str(raw or "").strip()
        if not clean or not _SAFE_PERSONA_ID.match(clean):
            raise BackfillError(f"invalid persona_id: {raw!r}")
        if clean not in ordered:
            ordered.append(clean)
    return ordered


def _build_runtime() -> tuple[Any, str]:
    from services.control_plane.bff.ports.persona_write_owner import (
        create_persona_registry_write_owner,
    )

    owner = create_persona_registry_write_owner()
    if not getattr(owner, "configured", False):
        raise BackfillError("persona owner client is not configured")
    return owner, configured_dev_tenant()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--persona-id",
        dest="persona_ids",
        action="append",
        required=True,
        help="Explicit chain persona_id to backfill (repeatable, required)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Send PATCH /api/personas/{id} to the Persona owner (default is dry-run)",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print each record before and after without writing (default)",
    )
    args = parser.parse_args(argv)
    try:
        args.persona_ids = _normalize_persona_ids(args.persona_ids)
    except BackfillError as exc:
        parser.error(str(exc))
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        owner, tenant_id = _build_runtime()
        result = execute(
            owner=owner,
            persona_ids=args.persona_ids,
            apply=bool(args.apply),
            expected_tenant_id=tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 - structured fail-closed CLI output
        error_payload = {
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "dry_run": not bool(args.apply),
            "apply": bool(args.apply),
            "persona_ids": list(args.persona_ids),
            "error": f"{exc.__class__.__name__}: {exc}",
        }
        print(json.dumps(_scrub(error_payload), indent=2, sort_keys=True))
        return 1
    print(json.dumps(_scrub(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
