#!/usr/bin/env python3
"""Production release preflight (fail-closed) for ENV-PROD-CONTROL-IMPL-001.

Checks, without touching any cloud resource:
- explicit production configuration (independent project, WIF/SA/IAM/secrets refs,
  disk/network/DNS/HTTPS/IdP, RPO/RTO/retention/on-call/rollback owner). Missing,
  placeholder, retired or staging/dev-shared values fail closed; nothing is guessed.
- the immutable release manifest (outer SHA-256 match, no relabeling).
- `staging_verified` evidence for that exact manifest checksum.
- genuine protected `production` Environment approval (GitHub run-approvals API shape).
  Approver must be a required reviewer of the actual Environment record; distinct from the
  initiator unless that record has prevent_self_review=false (operator policy 2026-10-24).
- rollback baseline presence, or an explicit first-release bootstrap declaration.

Local checks prove controller behavior only; they are never hosted drill evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_manifest  # noqa: E402


class PreflightError(ValueError):
    """Raised when production preflight fails closed."""


REQUIRED_CONFIG = (
    "PROD_GCP_PROJECT_ID",
    "PROD_GCP_ZONE",
    "PROD_WIF_PROVIDER",
    "PROD_SERVICE_ACCOUNT",
    "PROD_IAM_POLICY_REF",
    "PROD_SECRETS_REF",
    "PROD_DATA_DISK",
    "PROD_NETWORK",
    "PROD_BFF_HOST",
    "PROD_FE_HOST",
    "PROD_IDP_ISSUER",
    "PROD_TENANT_ID",
    "PROD_PUBLIC_GCP_IDENTITY_API_KEY",
    "PROD_PUBLIC_GCP_IDENTITY_PROJECT_ID",
    "PROD_PUBLIC_GCP_IDENTITY_AUTH_DOMAIN",
    "PROD_RPO_SECONDS",
    "PROD_RTO_SECONDS",
    "PROD_RETENTION_DAYS",
    "PROD_ONCALL_OWNER",
    "PROD_ROLLBACK_OWNER",
)
POSITIVE_INT_CONFIG = ("PROD_RPO_SECONDS", "PROD_RTO_SECONDS", "PROD_RETENTION_DAYS")
NONPROD_PROJECT_VARS = ("STAGING_GCP_PROJECT_ID", "DEV_GCP_PROJECT_ID")
PLACEHOLDER_RE = re.compile(
    r"(todo|tbd|changeme|replace[-_ ]?me|placeholder|example\.(com|org)|^<.*>$|^\$\{.*\}$|unspecified)",
    re.IGNORECASE,
)
HOST_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
SAFETY_ENV = {
    "PANTHEON_PROVIDER_ENABLED": "false",
    "PANTHEON_LIVE_BROKER_ENABLED": "false",
    "PANTHEON_REAL_WRITES_ENABLED": "false",
    "PANTHEON_CONTROL_VM_BROKER_CREDENTIALS_PRESENT": "false",
}


def check_config(env: Mapping[str, str]) -> dict[str, str]:
    """Validate explicit production configuration; returns the accepted values."""
    problems: list[str] = []
    values: dict[str, str] = {}
    for key in REQUIRED_CONFIG:
        raw = (env.get(key) or "").strip()
        if not raw:
            problems.append(f"{key} is missing")
            continue
        if PLACEHOLDER_RE.search(raw):
            problems.append(f"{key} is a placeholder")
            continue
        for retired in release_manifest.RETIRED_IDENTIFIERS:
            if retired in raw:
                problems.append(f"{key} references retired identifier {retired!r}")
        values[key] = raw
    for key in POSITIVE_INT_CONFIG:
        if key in values and not (values[key].isdigit() and int(values[key]) > 0):
            problems.append(f"{key} must be a positive integer")
    for key in ("PROD_BFF_HOST", "PROD_FE_HOST"):
        if key in values and not HOST_RE.fullmatch(values[key]):
            problems.append(f"{key} must be a DNS hostname (HTTPS only, no scheme/port)")
    if {"PROD_BFF_HOST", "PROD_FE_HOST"} <= values.keys() and values["PROD_BFF_HOST"] == values["PROD_FE_HOST"]:
        problems.append("PROD_BFF_HOST and PROD_FE_HOST must differ")
    if "PROD_IDP_ISSUER" in values and not values["PROD_IDP_ISSUER"].startswith("https://"):
        problems.append("PROD_IDP_ISSUER must be an https:// URL")
    project = values.get("PROD_GCP_PROJECT_ID")
    if project:
        for nonprod in NONPROD_PROJECT_VARS:
            if (env.get(nonprod) or "").strip() == project:
                problems.append(f"PROD_GCP_PROJECT_ID must be independent of {nonprod}")
        if re.search(r"(^|[-_])(dev|stg|staging|nonprod|test)([-_]|$)", project):
            problems.append("PROD_GCP_PROJECT_ID looks like a non-production project")
    for key, expected in SAFETY_ENV.items():
        if (env.get(key) or "false").strip().lower() != expected:
            problems.append(f"{key} must be {expected} (safe default)")
    if problems:
        raise PreflightError("production configuration preflight failed: " + "; ".join(problems))
    return values


def check_staging_evidence(evidence: Any, manifest: dict[str, Any], manifest_sha256: str) -> None:
    """Require staging_verified evidence bound to this exact manifest checksum."""
    if not isinstance(evidence, dict):
        raise PreflightError("staging evidence must be a JSON object")
    if evidence.get("status") != "staging_verified":
        raise PreflightError("staging evidence status must be 'staging_verified'")
    if evidence.get("dry_run") is not False or evidence.get("observed_from_live_vm") is not True:
        raise PreflightError("staging evidence must be a live (non-dry-run) observation")
    if evidence.get("release_id") != manifest["release_id"]:
        raise PreflightError("staging evidence release_id does not match manifest")
    summary = evidence.get("manifest")
    if not isinstance(summary, dict) or summary.get("manifest_sha256") != manifest_sha256:
        raise PreflightError("staging evidence is bound to a different manifest checksum")
    if summary.get("operator_bff_image") != manifest["backend"]["images"]["operator_bff"]:
        raise PreflightError("staging evidence BFF image differs from manifest")
    if summary.get("frontend_artifact_sha256") != manifest["frontend"]["artifact_sha256"]:
        raise PreflightError("staging evidence FE artifact differs from manifest")
    if not str(evidence.get("workflow_run_id") or "").strip():
        raise PreflightError("staging evidence lacks workflow_run_id")
    gates = evidence.get("gates")
    required = {
        "sanitized_data",
        "migration_and_compatibility",
        "backup_restore_readability",
        "exact_pair_rollback",
        "security_and_safety_readbacks",
        "cleanup_dry_run",
    }
    if not isinstance(gates, dict) or not required <= gates.keys():
        raise PreflightError(f"staging evidence gates incomplete; need {sorted(required)}")
    mig = gates["migration_and_compatibility"]
    if (
        not isinstance(mig, dict)
        or mig.get("migration_verified") is not True
        or mig.get("old_app_new_schema_compatible") is not True
        or mig.get("migration_set_sha256") != manifest.get("migration_set_sha256")
    ):
        raise PreflightError("staging migration_and_compatibility evidence does not match manifest migration_set_sha256")


def _environment_policy(env_policy: Any) -> tuple[set[str], bool]:
    """Return (required reviewer user logins, prevent_self_review) from the actual
    GitHub `environments/production` response. Fails closed on anything unreadable."""
    if not isinstance(env_policy, dict) or env_policy.get("name") != "production":
        raise PreflightError("environment policy must be the GitHub 'production' Environment record")
    rule = next(
        (r for r in env_policy.get("protection_rules") or [] if isinstance(r, dict) and r.get("type") == "required_reviewers"),
        None,
    )
    if rule is None:
        raise PreflightError("production Environment has no required_reviewers protection rule")
    prevent = rule.get("prevent_self_review")
    if not isinstance(prevent, bool):
        raise PreflightError("production Environment prevent_self_review is not a boolean")
    logins = {
        ((r.get("reviewer") or {}).get("login") or "").strip().lower()
        for r in rule.get("reviewers") or []
        if isinstance(r, dict) and r.get("type") == "User"
    }
    logins.discard("")
    if not logins:
        raise PreflightError("production Environment lists no required reviewer users")
    return logins, prevent


def check_approval(approvals: Any, initiator: str, env_policy: Any = None) -> str:
    """Require a genuine approved `production` Environment review.

    `approvals` is the GitHub `actions/runs/{id}/approvals` response (list of
    {state, user:{login}, environments:[{name}]}). Without `env_policy` the strict default
    applies: the approver must differ from the initiator. With the actual `production`
    Environment record, the approver must be a listed required reviewer, and may equal the
    initiator only when the Environment really has prevent_self_review=false.
    Returns the approver login.
    """
    if not initiator or not initiator.strip():
        raise PreflightError("initiator identity is missing")
    if not isinstance(approvals, list):
        raise PreflightError("approval record must be a list of run approvals")
    reviewers: set[str] | None = None
    prevent_self = True
    if env_policy is not None:
        reviewers, prevent_self = _environment_policy(env_policy)
    init = initiator.strip().lower()
    for entry in approvals:
        if not isinstance(entry, dict) or entry.get("state") != "approved":
            continue
        envs = entry.get("environments") or []
        if not any(isinstance(e, dict) and e.get("name") == "production" for e in envs):
            continue
        login = ((entry.get("user") or {}).get("login") or "").strip()
        if not login:
            continue
        if reviewers is not None and login.lower() not in reviewers:
            continue
        if prevent_self and login.lower() == init:
            continue
        return login
    raise PreflightError(
        "no approved 'production' Environment review by a permitted required reviewer "
        "(distinct from the initiator unless the Environment allows self review)"
    )


def check_baseline(manifest: dict[str, Any], *, bootstrap: bool, baseline_state: Any) -> str:
    """Decide rollback posture: 'baseline' (healthy retained baseline) or 'bootstrap'."""
    if baseline_state is None:
        if not bootstrap:
            raise PreflightError(
                "no retained production baseline; first release must explicitly declare bootstrap"
            )
        # The staging-qualified manifest carries rollback_baseline as retained/tested rollback ARTIFACT
        # metadata (staging requires it for promotion eligibility). It is not a production incumbent:
        # incumbent posture comes only from genuine on-host baseline state, which is absent here, so
        # bootstrap never treats this reference as a healthy or switchable rollback target.
        rb = manifest.get("rollback_baseline")
        if rb is not None and not isinstance(rb, dict):
            raise PreflightError("manifest rollback_baseline must be an object when present")
        return "bootstrap"
    if bootstrap:
        raise PreflightError("bootstrap declared but a retained baseline exists")
    rb = manifest.get("rollback_baseline")
    if not isinstance(rb, dict):
        raise PreflightError("manifest lacks rollback_baseline for non-bootstrap release")
    if baseline_state.get("healthy") is not True:
        raise PreflightError("retained baseline is not recorded healthy")
    if baseline_state.get("frontend_artifact_sha256") != rb["frontend_artifact_sha256"] or baseline_state.get(
        "operator_bff_image"
    ) != rb["backend_operator_bff_image"]:
        raise PreflightError("retained baseline does not match manifest rollback_baseline")
    return "baseline"


def run_all(
    *,
    env: Mapping[str, str],
    manifest_path: Path,
    expected_sha256: str,
    staging_evidence_path: Path,
    approvals_path: Path | None,
    initiator: str,
    env_policy_path: Path | None = None,
    baseline_path: Path | None,
    bootstrap: bool,
    require_approval: bool = True,
    skip_baseline: bool = False,
) -> dict[str, Any]:
    config = check_config(env)
    manifest = release_manifest.load_manifest_file(manifest_path)
    try:
        release_manifest.validate_manifest(manifest)
        actual = release_manifest.compute_manifest_sha256(manifest)
    except release_manifest.ReleaseManifestError as exc:
        raise PreflightError(str(exc)) from exc
    normalized = expected_sha256 if expected_sha256.startswith("sha256:") else f"sha256:{expected_sha256}"
    if actual != normalized:
        raise PreflightError(f"manifest checksum mismatch: expected {normalized}, got {actual}")
    if manifest["release_state"] not in {"staging_verified", "production_approved", "dev_verified"}:
        raise PreflightError(f"release_state {manifest['release_state']!r} not promotable to production")
    try:
        evidence = json.loads(staging_evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError(f"cannot read staging evidence: {exc}") from exc
    check_staging_evidence(evidence, manifest, actual)
    approver = None
    if require_approval:
        if approvals_path is None:
            raise PreflightError("approval record path is required")
        try:
            approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PreflightError(f"cannot read approval record: {exc}") from exc
        env_policy = None
        if env_policy_path is not None:
            try:
                env_policy = json.loads(env_policy_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PreflightError(f"cannot read environment policy: {exc}") from exc
        approver = check_approval(approvals, initiator, env_policy)
    baseline_state = None
    if baseline_path is not None and baseline_path.exists():
        try:
            baseline_state = json.loads(baseline_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PreflightError(f"baseline state unreadable: {exc}") from exc
    if skip_baseline:
        # Runner-side pass: the retained baseline lives on the control VM, where the
        # controller re-runs this preflight with the real baseline file.
        posture = "deferred_to_control_vm"
    else:
        posture = check_baseline(manifest, bootstrap=bootstrap, baseline_state=baseline_state)
    return {
        "status": "preflight_passed",
        "release_id": manifest["release_id"],
        "manifest_sha256": actual,
        "approver": approver,
        "rollback_posture": posture,
        "project_id": config["PROD_GCP_PROJECT_ID"],
        "frontend_artifact_sha256": manifest["frontend"]["artifact_sha256"],
        "operator_bff_image": manifest["backend"]["images"]["operator_bff"],
    }


# --- Production consumption of the Stage-owned migration contract -------------------------------
# The product has no schema_migrations table. The migration set identity is the one Stage qualifies
# (deploy/staging/pantheon-staging-collect-gates): sha256 over "<file sha256>  <name>\n" lines of the
# versioned SQL files listed in migration-spec.json, sorted by name, recomputed from the bytes actually
# applied. The live database is read back through the same information_schema catalog query Stage uses.


def _staging_sanitize():
    import staging_sanitize  # VM-side only; installed next to this script

    return staging_sanitize


# Same query Stage uses (pantheon-staging-collect-gates live_catalog_sql); stdout is the catalog JSON.
LIVE_CATALOG_SQL = (
    "SELECT COALESCE(json_object_agg(table_name, cols), '{}'::json)::text FROM ("
    "SELECT table_schema || '.' || table_name AS table_name, json_object_agg(column_name, "
    "json_build_object('type', data_type, 'nullable', (is_nullable = 'YES'), "
    "'has_default', (column_default IS NOT NULL))) AS cols FROM information_schema.columns "
    "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') "
    "AND table_schema NOT LIKE 'pg_toast%' AND table_schema NOT LIKE 'pg_temp%' "
    "GROUP BY table_schema, table_name) s;"
)
REQUIRED_RELATIONS = ("public.telemetry_events", "trade_journey_projection.controller")
_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)", re.I
)


class MigrationError(PreflightError):
    pass


def load_verified(manifest_path: str, spec_dir: str) -> list[tuple[str, str]]:
    """Return [(name, sql)] in applied order, after proving the set identity equals the manifest's."""
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    want = manifest.get("migration_set_sha256")
    root = Path(spec_dir)
    try:
        spec = json.loads((root / "migration-spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MigrationError(f"migration-spec.json unreadable in {root}: {exc}")
    if spec.get("migration_set_sha256") != want:
        raise MigrationError("migration-spec.json migration_set_sha256 != manifest")
    files = spec.get("files")
    if not isinstance(files, list) or not files:
        raise MigrationError("migration-spec.json must list the versioned migration files")
    out: list[tuple[str, str]] = []
    lines: list[str] = []
    for entry in sorted(files, key=lambda e: str(e.get("name") if isinstance(e, dict) else "")):
        name = str(entry.get("name") or "") if isinstance(entry, dict) else ""
        if not re.fullmatch(r"[0-9]{3}_[a-z0-9_]+\.sql", name):
            raise MigrationError(f"invalid migration file name {name!r}")
        path = root / "migrations" / name
        if not path.is_file():
            raise MigrationError(f"versioned migration file {path} is missing")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if "sha256:" + digest != str(entry.get("sha256") or ""):
            raise MigrationError(f"{name} digest sha256:{digest} != spec")
        sql = raw.decode("utf-8")
        if _staging_sanitize().DESTRUCTIVE_SQL_RE.search(sql):
            raise MigrationError(f"destructive SQL forbidden in expand migration phase: {name}")
        lines.append(f"{digest}  {name}\n")
        out.append((name, sql))
    observed = "sha256:" + hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    if observed != want:
        raise MigrationError(f"recomputed migration set digest {observed} != manifest {want}")
    return out


# Current product schema contract owned by scripts/db_migrate.sh (the existing telemetry substrate initializer):
# the exact columns the projector/ingest read (SELECT ingested_seq, ingested_at FROM public.telemetry_events ...).
SUBSTRATE_COLUMNS: dict[str, dict[str, tuple[str, bool]]] = {  # column -> (information_schema data_type, has_default)
    "public.telemetry_events": {
        "event_id": ("text", False), "event_type": ("text", False), "created_at": ("timestamp with time zone", False),
        "payload": ("jsonb", False), "ingested_seq": ("bigint", True), "ingested_at": ("timestamp with time zone", True),
    },
    "public.loop_controller_records": {
        "loop_id": ("text", False), "tenant_id": ("text", False), "environment": ("text", False),
        "controller_id": ("text", False), "controller_name": ("text", False), "deployment_sha": ("text", False),
        "evidence_refs": ("jsonb", True), "truth_level": ("text", False), "payload": ("jsonb", True),
        "updated_at": ("timestamp with time zone", True),
    },
}
_SQL_TYPES = {
    "text": "text", "bigint": "bigint", "int": "integer", "integer": "integer", "boolean": "boolean",
    "jsonb": "jsonb", "timestamptz": "timestamp with time zone", "smallint": "smallint",
}
_NON_COLUMN = {"primary", "unique", "constraint", "foreign", "check", "like", "exclude"}
_ADD_COLUMN = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s+"
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_]+)([^;,]*)", re.I
)
_HAS_DEFAULT = re.compile(r"\bDEFAULT\b", re.I)


def _qualify(rel: str) -> str:
    return rel if "." in rel else f"public.{rel}"


def _split_top_level(body: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in body:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def expected_columns(migrations: list[tuple[str, str]]) -> dict[str, dict[str, str | None]]:
    """relation -> {column: expected information_schema data_type or None (unknown type: presence only)}."""
    return {rel: {c: t for c, (t, _) in cols.items()} for rel, cols in _parse_columns(migrations).items()}


def expected_defaults(migrations: list[tuple[str, str]]) -> dict[str, set[str]]:
    """relation -> columns the verified SQL declares with a DEFAULT (an existing table never regains one)."""
    return {rel: {c for c, (_, d) in cols.items() if d} for rel, cols in _parse_columns(migrations).items()}


def _parse_columns(migrations: list[tuple[str, str]]) -> dict[str, dict[str, tuple[str | None, bool]]]:
    """relation -> {column: (expected data_type or None, declares DEFAULT)}.

    Parsed from CREATE TABLE bodies and ALTER TABLE ... ADD COLUMN in the verified SQL (independent of the
    relation-name regex); the substrate contract above is added explicitly."""
    out: dict[str, dict[str, tuple[str | None, bool]]] = {
        rel: dict(cols) for rel, cols in SUBSTRATE_COLUMNS.items()
    }
    for _, sql in migrations:
        text = re.sub(r"--[^\n]*", "", sql)
        for m in _CREATE_TABLE.finditer(text):
            start = text.find("(", m.end())
            if start < 0:
                continue
            depth, i = 0, start
            while i < len(text):
                depth += (text[i] == "(") - (text[i] == ")")
                if depth == 0:
                    break
                i += 1
            cols = out.setdefault(_qualify(m.group(1)), {})
            for item in _split_top_level(text[start + 1:i]):
                toks = item.split()
                if not toks or toks[0].lower() in _NON_COLUMN:
                    continue
                cols[toks[0].strip('"').lower()] = (
                    _SQL_TYPES.get(toks[1].lower()) if len(toks) > 1 else None, bool(_HAS_DEFAULT.search(item)))
        for m in _ADD_COLUMN.finditer(text):
            out.setdefault(_qualify(m.group(1)), {})[m.group(2).lower()] = (
                _SQL_TYPES.get(m.group(3).lower()), bool(_HAS_DEFAULT.search(m.group(4))))
    return out


def check_catalog(catalog: object, migrations: list[tuple[str, str]]) -> None:
    """Live information_schema catalog must satisfy the product schema/read contract, not just relation names."""
    if not isinstance(catalog, dict) or not catalog:
        raise MigrationError("production live catalog is empty: migrations not applied")
    expected = set(REQUIRED_RELATIONS)
    for _, sql in migrations:
        for m in _CREATE_TABLE.finditer(sql):
            expected.add(_qualify(m.group(1)))
    missing = sorted(r for r in expected if r not in catalog)
    if missing:
        raise MigrationError(f"production live catalog lacks relations from the verified SQL: {missing}")
    problems: list[str] = []
    defaults = expected_defaults(migrations)
    for rel, cols in sorted(expected_columns(migrations).items()):
        live = catalog.get(rel)
        if not isinstance(live, dict):
            problems.append(f"{rel}: relation missing")
            continue
        for col, want_type in sorted(cols.items()):
            obs = live.get(col)
            if not isinstance(obs, dict):
                problems.append(f"{rel}.{col}: column missing")
            elif want_type and obs.get("type") != want_type:
                problems.append(f"{rel}.{col}: type {obs.get('type')!r} != {want_type!r}")
        for col in sorted(defaults.get(rel, ())):
            obs = live.get(col)
            if isinstance(obs, dict) and obs.get("has_default") is not True:
                problems.append(f"{rel}.{col}: default missing")
    if problems:
        raise MigrationError("production live catalog violates the product schema contract: " + "; ".join(problems))


def render_material(manifest_path: str, src_dir: str, out_dir: str) -> None:
    """Deterministically build migration-spec.json + migrations/*.sql from the checked-out versioned SQL with the
    Stage recipe, then prove the set equals the manifest (fails closed if the checkout is not the qualified set)."""
    src, out = Path(src_dir), Path(out_dir)
    files = []
    (out / "migrations").mkdir(parents=True, exist_ok=True)
    for path in sorted(src.glob("*.sql")):
        raw = path.read_bytes()
        (out / "migrations" / path.name).write_bytes(raw)
        files.append({"name": path.name, "sha256": "sha256:" + hashlib.sha256(raw).hexdigest()})
    lines = "".join(f"{f['sha256'][7:]}  {f['name']}\n" for f in files)
    spec = {
        "phase": "expand", "files": files,
        "migration_set_sha256": "sha256:" + hashlib.sha256(lines.encode("utf-8")).hexdigest(),
    }
    (out / "migration-spec.json").write_text(json.dumps(spec, indent=2, sort_keys=True), encoding="utf-8")
    load_verified(manifest_path, out_dir)


def _migration_cli(cmd: str, manifest: str, spec_dir: str, src_dir: str = "") -> int:
    try:
        if cmd == "render":
            render_material(manifest, src_dir, spec_dir)
            return 0
        migrations = load_verified(manifest, spec_dir)
        if cmd == "catalog":
            check_catalog(json.loads(sys.stdin.read() or "null"), migrations)
        elif cmd == "names":
            print("\n".join(n for n, _ in migrations))
        elif cmd == "catalog-sql":
            print(LIVE_CATALOG_SQL)
        elif cmd != "verify":
            raise MigrationError(f"unknown migration command {cmd!r}")
    except (PreflightError, ValueError) as exc:
        print(str(exc))
        return 1
    return 0


def _cli() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="validate production configuration from the environment")
    a = sub.add_parser("all", help="full production preflight")
    a.add_argument("--manifest", type=Path, required=True)
    a.add_argument("--expected-sha256", required=True)
    a.add_argument("--staging-evidence", type=Path, required=True)
    a.add_argument("--approvals", type=Path, default=None)
    a.add_argument("--initiator", default="")
    a.add_argument("--env-policy", type=Path, default=None, help="GitHub production Environment record")
    a.add_argument("--baseline", type=Path, default=None)
    a.add_argument("--bootstrap", action="store_true")
    a.add_argument("--skip-baseline", action="store_true", help="runner-side: baseline is checked on the control VM")
    a.add_argument("--skip-approval", action="store_true", help="pre-approval validation only")
    mg = sub.add_parser("migration", help="verify Stage-qualified versioned SQL / live catalog (VM-side)")
    mg.add_argument("action", choices=["verify", "names", "catalog", "catalog-sql", "render"])
    mg.add_argument("manifest")
    mg.add_argument("spec_dir", help="material dir (render: output dir)")
    mg.add_argument("--src", default="", help="render: directory of versioned *.sql")
    args = p.parse_args()
    if args.command == "migration":
        return _migration_cli(args.action, args.manifest, args.spec_dir, args.src)
    try:
        if args.command == "config":
            check_config(os.environ)
            print(json.dumps({"status": "config_ok"}))
            return 0
        result = run_all(
            env=os.environ,
            manifest_path=args.manifest,
            expected_sha256=args.expected_sha256,
            staging_evidence_path=args.staging_evidence,
            approvals_path=args.approvals,
            initiator=args.initiator,
            env_policy_path=args.env_policy,
            baseline_path=args.baseline,
            bootstrap=args.bootstrap,
            require_approval=not args.skip_approval,
            skip_baseline=args.skip_baseline,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (PreflightError, release_manifest.ReleaseManifestError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
