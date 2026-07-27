#!/usr/bin/env python3
"""Inspect or remove only the loop-control rows created by the old test suite.

The command is read-only unless ``--apply`` is present. Apply mode additionally
requires a Human/Ops evidence JSON document bound to the exact dry-run plan
digest. DATABASE_URL is intentionally ignored.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


TASK_ID = "OPS-LOOP-CONTROL-TEST-DB-ISOLATION-001"
CLEANUP_DATABASE_URL_ENV = "PANTHEON_LOOP_CONTROL_CLEANUP_DATABASE_URL"
PRESERVED_CANONICAL_LOOP_IDS = frozenset(
    {"source_ingestion", "strategy_distillation"}
)
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


class CleanupRefused(RuntimeError):
    """Raised before a cleanup can mutate a database."""


@dataclass(frozen=True)
class ContaminationSignature:
    loop_id: str
    tenant_id: str
    environment: str
    controller_ids: tuple[str, ...]


# These are the only rows ever written by the pre-isolation DB tests. The
# controller id is part of each signature so a legitimate row that reuses a
# test-looking loop key is not silently admitted for deletion.
CONTAMINATION_SIGNATURES = (
    ContaminationSignature(
        "test-loop-1", "default", "test", ("ctrl-1",)
    ),
    ContaminationSignature(
        "test-writer-loop", "default", "test", ("ctrl-writer",)
    ),
    ContaminationSignature(
        "test-lease-loop", "default", "test", ("ctrl-lease-1",)
    ),
    ContaminationSignature(
        "test-loop-concurrent",
        "tenant-concurrency",
        "test",
        ("ctrl-concurrent",),
    ),
    ContaminationSignature(
        "test-loop-isolation",
        "tenant-a",
        "dev",
        ("controller-tenant-a-dev",),
    ),
    ContaminationSignature(
        "test-loop-isolation",
        "tenant-b",
        "dev",
        ("controller-tenant-b-dev",),
    ),
    ContaminationSignature(
        "test-loop-isolation",
        "tenant-a",
        "prod",
        ("controller-tenant-a-prod",),
    ),
    ContaminationSignature(
        "test-loop-fenced-generation",
        "tenant-fence",
        "test",
        ("stable-controller-id",),
    ),
)


def _validate_catalog() -> None:
    if not CONTAMINATION_SIGNATURES:
        raise CleanupRefused("contamination signature catalog is empty")
    seen: set[tuple[str, str, str]] = set()
    for signature in CONTAMINATION_SIGNATURES:
        key = (
            signature.loop_id,
            signature.tenant_id,
            signature.environment,
        )
        if key in seen:
            raise CleanupRefused(f"duplicate contamination key: {key!r}")
        seen.add(key)
        if signature.loop_id in PRESERVED_CANONICAL_LOOP_IDS:
            raise CleanupRefused(
                f"protected canonical loop entered cleanup catalog: {key!r}"
            )
        if not signature.loop_id.startswith("test-"):
            raise CleanupRefused(
                f"non-test loop entered cleanup catalog: {key!r}"
            )
        if not signature.controller_ids:
            raise CleanupRefused(
                f"contamination signature lacks controller ids: {key!r}"
            )


def _validate_schema(schema: str) -> str:
    if not _SAFE_IDENTIFIER.fullmatch(schema):
        raise CleanupRefused(f"unsafe schema identifier: {schema!r}")
    return schema


def _quote_identifier(identifier: str) -> str:
    _validate_schema(identifier)
    return f'"{identifier}"'


def build_candidate_predicate(
    signatures: Sequence[ContaminationSignature] = CONTAMINATION_SIGNATURES,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    parameters: list[object] = []
    for signature in signatures:
        first = len(parameters) + 1
        clauses.append(
            "("
            f"loop_id = ${first} AND "
            f"tenant_id = ${first + 1} AND "
            f"environment = ${first + 2} AND "
            f"controller_id = ANY(${first + 3}::text[])"
            ")"
        )
        parameters.extend(
            [
                signature.loop_id,
                signature.tenant_id,
                signature.environment,
                list(signature.controller_ids),
            ]
        )
    if not clauses:
        raise CleanupRefused("refusing cleanup with no exact predicates")
    return " OR ".join(clauses), parameters


def canonical_plan_bytes(plan: Mapping[str, Any]) -> bytes:
    return json.dumps(
        plan,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def cleanup_plan_sha256(plan: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_plan_bytes(plan)).hexdigest()


def load_human_ops_evidence(path: str | os.PathLike[str]) -> dict[str, Any]:
    evidence_path = Path(path)
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CleanupRefused(
            f"cannot read Human/Ops evidence {str(evidence_path)!r}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise CleanupRefused("Human/Ops evidence must be a JSON object")
    return payload


def validate_human_ops_evidence(
    evidence: Mapping[str, Any],
    *,
    expected_plan_sha256: str,
) -> None:
    required = {
        "schema_version",
        "task_id",
        "actor",
        "approved",
        "cleanup_plan_sha256",
    }
    missing = sorted(required - set(evidence))
    if missing:
        raise CleanupRefused(
            f"Human/Ops evidence is missing required fields: {missing!r}"
        )
    if evidence["schema_version"] != 1:
        raise CleanupRefused("Human/Ops evidence schema_version must be 1")
    if evidence["task_id"] != TASK_ID:
        raise CleanupRefused("Human/Ops evidence names the wrong task")
    if evidence["actor"] != "Human/Ops" or evidence["approved"] is not True:
        raise CleanupRefused(
            "live cleanup requires approved evidence from actor Human/Ops"
        )
    if evidence["cleanup_plan_sha256"] != expected_plan_sha256:
        raise CleanupRefused(
            "Human/Ops evidence does not bind the current cleanup plan; "
            "run dry-run again and obtain a fresh approval"
        )


def _stable_candidate(row: Mapping[str, Any]) -> dict[str, str]:
    raw_json = str(row["row_json"])
    return {
        "loop_id": str(row["loop_id"]),
        "tenant_id": str(row["tenant_id"]),
        "environment": str(row["environment"]),
        "controller_id": str(row["controller_id"]),
        "row_sha256": hashlib.sha256(raw_json.encode("utf-8")).hexdigest(),
    }


async def collect_cleanup_plan(
    conn: Any,
    *,
    schema: str,
    lock_rows: bool = False,
) -> dict[str, Any]:
    schema = _validate_schema(schema)
    target = await conn.fetchrow(
        """
        SELECT
            current_database() AS database_name,
            COALESCE(inet_server_addr()::text, 'local-socket') AS server_address,
            inet_server_port() AS server_port,
            current_user AS database_user
        """
    )
    qualified_name = f"{schema}.loop_controller_records"
    table_exists = bool(
        await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", qualified_name)
    )
    candidates: list[dict[str, str]] = []
    if table_exists:
        predicate, parameters = build_candidate_predicate()
        lock_clause = " FOR UPDATE" if lock_rows else ""
        rows = await conn.fetch(
            f"""
            SELECT
                loop_id,
                tenant_id,
                environment,
                controller_id,
                to_jsonb(candidate)::text AS row_json
            FROM {_quote_identifier(schema)}.loop_controller_records AS candidate
            WHERE {predicate}
            ORDER BY loop_id, tenant_id, environment, controller_id
            {lock_clause}
            """,
            *parameters,
        )
        candidates = [_stable_candidate(row) for row in rows]

    return {
        "schema_version": 1,
        "task_id": TASK_ID,
        "target": {
            "database_name": str(target["database_name"]),
            "database_user": str(target["database_user"]),
            "schema": schema,
            "server_address": str(target["server_address"]),
            "server_port": (
                int(target["server_port"])
                if target["server_port"] is not None
                else None
            ),
        },
        "table_exists": table_exists,
        "enumerated_signatures": [
            asdict(signature) for signature in CONTAMINATION_SIGNATURES
        ],
        "candidate_row_count": len(candidates),
        "candidate_rows": candidates,
        "preserved_canonical_loop_ids": sorted(
            PRESERVED_CANONICAL_LOOP_IDS
        ),
    }


async def _delete_candidates(conn: Any, *, schema: str) -> int:
    predicate, parameters = build_candidate_predicate()
    result = await conn.execute(
        f"""
        DELETE FROM {_quote_identifier(schema)}.loop_controller_records
        WHERE {predicate}
        """,
        *parameters,
    )
    try:
        return int(str(result).rsplit(" ", 1)[1])
    except (IndexError, ValueError) as exc:
        raise CleanupRefused(
            f"database returned an unexpected DELETE status: {result!r}"
        ) from exc


async def apply_cleanup(
    conn: Any,
    *,
    schema: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    async with conn.transaction():
        plan = await collect_cleanup_plan(
            conn,
            schema=schema,
            lock_rows=True,
        )
        plan_sha256 = cleanup_plan_sha256(plan)
        validate_human_ops_evidence(
            evidence,
            expected_plan_sha256=plan_sha256,
        )
        deleted = (
            await _delete_candidates(conn, schema=schema)
            if plan["table_exists"]
            else 0
        )
        if deleted != plan["candidate_row_count"]:
            raise CleanupRefused(
                "deleted row count differs from the Human/Ops-approved plan; "
                "the transaction will roll back"
            )
        return {
            "mode": "applied",
            "cleanup_plan_sha256": plan_sha256,
            "deleted_row_count": deleted,
            "plan": plan,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schema",
        default="public",
        help="schema containing loop_controller_records (default: public)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="delete the exact approved candidate set; omitted means dry-run",
    )
    parser.add_argument(
        "--human-ops-evidence",
        help=(
            "JSON approval containing actor=Human/Ops and the current "
            "cleanup_plan_sha256; required with --apply"
        ),
    )
    return parser


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    _validate_catalog()
    schema = _validate_schema(args.schema)
    dsn = str(os.environ.get(CLEANUP_DATABASE_URL_ENV, "") or "").strip()
    if not dsn:
        raise CleanupRefused(
            f"{CLEANUP_DATABASE_URL_ENV} is required; DATABASE_URL is ignored"
        )
    if args.apply and not args.human_ops_evidence:
        raise CleanupRefused(
            "--apply requires --human-ops-evidence from Human/Ops"
        )

    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        if args.apply:
            evidence = load_human_ops_evidence(args.human_ops_evidence)
            return await apply_cleanup(
                conn,
                schema=schema,
                evidence=evidence,
            )
        plan = await collect_cleanup_plan(conn, schema=schema)
        return {
            "mode": "dry_run",
            "cleanup_plan_sha256": cleanup_plan_sha256(plan),
            "plan": plan,
            "apply_requirements": {
                "actor": "Human/Ops",
                "approved": True,
                "evidence_flag": "--human-ops-evidence",
                "task_id": TASK_ID,
            },
        }
    finally:
        await conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args))
    except CleanupRefused as exc:
        raise SystemExit(f"cleanup refused: {exc}") from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
