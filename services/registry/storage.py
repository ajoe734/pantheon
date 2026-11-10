"""
BP5-SVC-002: In-memory storage backend for the registry service.

``RegistryStore`` is an explicit test double: unit tests construct it
directly to exercise ``RegistryService`` without a database. Production
selects the durable Postgres backend in ``services/registry/pg_store.py``
via :func:`build_registry_store` — see architecture-resumption-sa-sd.md §3.1.
Both backends expose the same call surface so ``RegistryService`` (split_api.py)
needs no changes to select between them.
"""
from __future__ import annotations

import dataclasses
from functools import lru_cache
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence

from .models import (
    DeploymentStage,
    DeploymentSummary,
    RegistryEntry,
    RegistryEntryCreate,
    utc_now_iso,
)


# Fields the persona-binding view keeps (current-version selection, lifecycle
# filter and persona extraction read exactly these); everything else in the
# entry metadata, plus evaluation_summary / approval_evidence, is dropped.
PERSONA_BINDING_META_KEYS = (
    "spec_version_id", "tenant_id", "spec_version", "version", "personaIds",
    "persona_ids", "persona_id", "lifecycle_state", "state", "status",
    "title", "name",
)
PERSONA_BINDING_SPEC_KEYS = (
    "persona_ids", "spec_version", "lifecycle_state", "strategy_id", "created_at",
    "title",
)
PERSONA_BINDING_INNER_META_KEYS = ("tenant_id", "persona_id")
# Top-level identity/version/tenant/state fields the count path needs; no
# lineage, storage, evidence or audit fields are carried.
PERSONA_BINDING_ROW_KEYS = (
    "registry_id", "artifact_type", "artifact_state", "strategy_id", "version",
    "owner_tenant", "created_at", "updated_at",
)


def persona_binding_entry(entry: RegistryEntry) -> RegistryEntry:
    """Light projection of a StrategySpec entry for persona-binding counts.

    Same entry and version row; only the heavy spec body, evaluation summary
    and approval evidence are dropped. The Postgres owner computes the same
    shape in SQL (``pg_store.persona_binding_select_sql``).
    """
    meta = entry.metadata if isinstance(entry.metadata, dict) else {}
    light_meta = {k: meta[k] for k in PERSONA_BINDING_META_KEYS if k in meta}
    prov = meta.get("provenance")
    if isinstance(prov, dict) and "created_at" in prov:
        light_meta["provenance"] = {"created_at": prov["created_at"]}
    inner = meta.get("strategy_spec")
    if isinstance(inner, dict):
        light_inner = {k: inner[k] for k in PERSONA_BINDING_SPEC_KEYS if k in inner}
        inner_prov = inner.get("provenance")
        if isinstance(inner_prov, dict) and "created_at" in inner_prov:
            light_inner["provenance"] = {"created_at": inner_prov["created_at"]}
        inner_meta = inner.get("metadata")
        if isinstance(inner_meta, dict):
            light_inner["metadata"] = {
                k: inner_meta[k] for k in PERSONA_BINDING_INNER_META_KEYS if k in inner_meta
            }
        light_meta["strategy_spec"] = light_inner
    return dataclasses.replace(
        entry, metadata=light_meta, evaluation_summary=None, approval_evidence=None,
    )


def persona_binding_row(entry: RegistryEntry) -> dict:
    """Flat minimal row (identity, version, tenant, state, binding metadata)."""
    full = persona_binding_entry(entry).to_dict()
    row = {k: full[k] for k in PERSONA_BINDING_ROW_KEYS if k in full}
    row["metadata"] = full.get("metadata")
    return row


def persona_binding_has_binding(row: dict) -> bool:
    """True when any persona-id source of a flat binding row is truthy."""
    meta = row.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    inner = meta.get("strategy_spec")
    inner = inner if isinstance(inner, dict) else {}
    inner_meta = inner.get("metadata")
    inner_meta = inner_meta if isinstance(inner_meta, dict) else {}
    return bool(
        inner.get("persona_ids") or meta.get("personaIds") or meta.get("persona_ids")
        or meta.get("persona_id") or inner_meta.get("persona_id")
    )


def persona_binding_row_visible(
    row: dict, visible_tenants: Optional[frozenset], artifact_type: Optional[str],
) -> bool:
    """Row is in the verified visible owner population (``None`` tenants = all)."""
    if artifact_type is not None and row.get("artifact_type") != artifact_type:
        return False
    return visible_tenants is None or row.get("owner_tenant") in visible_tenants


def persona_binding_candidate_family_rows(rows: list[dict]) -> list[dict]:
    """Keep every version of each strategy family with any bound version.

    Families whose whole version history is unbound are dropped; rows without a
    ``strategy_id`` are kept. Order is preserved.
    """
    bound = {r.get("strategy_id") for r in rows if persona_binding_has_binding(r)}
    return [r for r in rows if r.get("strategy_id") is None or r.get("strategy_id") in bound]


# ---------------------------------------------------------------------------
# Lineage read projection (REGISTRY-LINEAGE-OWNER-READ-20261104)
# ---------------------------------------------------------------------------
# One flat row per entry carrying only identity/state and the lineage + metadata
# fields an edge is derived from. The Postgres owner computes the same row in
# SQL (``pg_store.lineage_select_sql``) without rebuilding any RegistryEntry.
LINEAGE_ROW_KEYS = (
    "registry_id", "artifact_type", "artifact_state", "strategy_id", "version",
    "owner_tenant", "created_at",
)
LINEAGE_LIST_FIELDS = ("parent_registry_ids", "source_run_ids", "source_dataset_refs")
LINEAGE_SCALAR_FIELDS = ("source_strategy_spec_id",)
LINEAGE_META_FIELDS = ("persona_id", "capital_pool_id", "risk_level")
LINEAGE_DEFAULT_LIMIT = 200
LINEAGE_MAX_LIMIT = 1000
LINEAGE_MAX_DEPTH = 20

_LINEAGE_REGISTRY_TYPES = {"strategy_spec": "Strategy", "execution_bundle": "Artifact"}


def lineage_row(entry: RegistryEntry) -> dict:
    """Flat lineage row of one entry (in-memory twin of the SQL projection)."""
    full = entry.to_dict()
    row = {k: full.get(k) for k in LINEAGE_ROW_KEYS}
    lin = full.get("lineage")
    lin = lin if isinstance(lin, dict) else {}
    for k in LINEAGE_LIST_FIELDS + LINEAGE_SCALAR_FIELDS:
        row[k] = lin.get(k)
    meta = full.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    for k in LINEAGE_META_FIELDS:
        row[k] = meta.get(k)
    return row


# ---------------------------------------------------------------------------
# Execution-bundle catalog read projection (ARTIFACT-CATALOG-REGISTRY-20261104)
# ---------------------------------------------------------------------------
# Flat row per execution_bundle entry carrying only catalog identity, version,
# state, checksum, timestamps, approver, and evaluation_summary.risk_level
# (no full metadata decoded). The Postgres owner computes the same row in SQL
# (``pg_store.strategy_artifact_select_sql``).
STRATEGY_ARTIFACT_ROW_KEYS = (
    "registry_id",
    "strategy_id",
    "version",
    "artifact_state",
    "checksum",
    "created_at",
    "approver",
)
STRATEGY_ARTIFACT_EVAL_KEYS = ("risk_level",)


def strategy_artifact_row(entry: RegistryEntry) -> dict:
    """Flat execution-bundle catalog row (in-memory twin of the SQL projection)."""
    full = entry.to_dict()
    row = {k: full.get(k) for k in STRATEGY_ARTIFACT_ROW_KEYS}
    eval_summary = full.get("evaluation_summary")
    eval_summary = eval_summary if isinstance(eval_summary, dict) else {}
    for k in STRATEGY_ARTIFACT_EVAL_KEYS:
        row[k] = eval_summary.get(k)
    return row


def _lineage_ids(value: Any) -> list[str]:
    """Non-empty string ids of a lineage list; JSON null / non-list yields none."""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return out


def _lineage_id(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def build_lineage_graph(
    rows: list[dict], *, root_id: Optional[str] = None, depth: int = 3, limit: int = LINEAGE_DEFAULT_LIMIT,
) -> dict:
    """Bounded lineage graph over visible flat lineage rows.

    Edges come only from ``lineage.parent_registry_ids`` / ``source_strategy_spec_id``
    / ``source_dataset_refs`` / ``source_run_ids`` and ``metadata.persona_id`` /
    ``capital_pool_id``; an absent or JSON-null field yields no edge. ``root_id`` may be
    a registry id, a strategy id or a persona id. Without a root the traversal starts
    from the most recent ``execution_bundle`` rows, never the whole population.
    Traversal is breadth-first over ``depth`` hops (nearest edges win the ``limit`` cap).
    """
    depth = max(1, min(int(depth), LINEAGE_MAX_DEPTH))
    limit = max(1, min(int(limit), LINEAGE_MAX_LIMIT))
    by_id = {r["registry_id"]: r for r in rows if r.get("registry_id")}
    roles: dict[str, str] = {}  # node id -> type for ids that are not registry rows
    edges: list[tuple[str, str, str]] = []
    seen_edges: set = set()

    def add(frm: str, to: str, label: str, to_type: Optional[str] = None, frm_type: Optional[str] = None) -> None:
        if (frm, to, label) in seen_edges:
            return
        seen_edges.add((frm, to, label))
        edges.append((frm, to, label))
        if frm_type:
            roles.setdefault(frm, frm_type)
        if to_type:
            roles.setdefault(to, to_type)

    for rid in sorted(by_id):
        r = by_id[rid]
        for p in _lineage_ids(r.get("parent_registry_ids")):
            add(p, rid, "parent")
        spec = _lineage_id(r.get("source_strategy_spec_id"))
        if spec:
            add(spec, rid, "source_strategy_spec", frm_type="Strategy")
        for d in _lineage_ids(r.get("source_dataset_refs")):
            add(d, rid, "source_dataset", frm_type="Dataset")
        for run in _lineage_ids(r.get("source_run_ids")):
            add(run, rid, "source_run", frm_type="Experiment")
        persona = _lineage_id(r.get("persona_id"))
        if persona:
            add(rid, persona, "persona", to_type="Persona")
        pool = _lineage_id(r.get("capital_pool_id"))
        if pool:
            add(rid, pool, "capital_pool", to_type="CapitalPool")

    adjacency: dict[str, list[int]] = {}
    for i, (frm, to, _label) in enumerate(edges):
        adjacency.setdefault(frm, []).append(i)
        adjacency.setdefault(to, []).append(i)

    has_more = False
    seeds: list[str]
    if root_id:
        wanted = root_id.strip()
        matched = {rid for rid, r in by_id.items() if r.get("strategy_id") == wanted}
        if wanted in by_id or wanted in adjacency:
            matched.add(wanted)
        seeds = sorted(matched)
    else:
        bundles = sorted(
            (r for r in by_id.values()
             if r.get("artifact_type") == "execution_bundle" and r["registry_id"] in adjacency),
            key=lambda r: (str(r.get("created_at") or ""), r["registry_id"]), reverse=True,
        )
        has_more = len(bundles) > limit
        seeds = [r["registry_id"] for r in bundles[:limit]]

    visited = set(seeds)
    frontier = list(seeds)
    picked: list[int] = []
    picked_set: set[int] = set()
    for _ in range(depth):
        nxt: list[str] = []
        for node in sorted(frontier):
            for i in adjacency.get(node, []):
                if i in picked_set:
                    continue
                picked_set.add(i)
                picked.append(i)
                for end in edges[i][:2]:
                    if end not in visited:
                        visited.add(end)
                        nxt.append(end)
        frontier = nxt
        if not frontier:
            break
    if len(picked) > limit:
        picked = picked[:limit]
        has_more = True

    node_ids: list[str] = []
    seen_nodes: set[str] = set()
    for sid in seeds:
        if sid not in seen_nodes:
            seen_nodes.add(sid)
            node_ids.append(sid)
    for i in picked:
        for end in edges[i][:2]:
            if end not in seen_nodes:
                seen_nodes.add(end)
                node_ids.append(end)

    nodes: list[dict] = []
    for nid in node_ids:
        node: dict = {"id": nid, "label": nid}
        r = by_id.get(nid)
        if r is not None:
            ntype = _LINEAGE_REGISTRY_TYPES.get(r.get("artifact_type"), r.get("artifact_type"))
            if ntype:
                node["type"] = ntype
            if r.get("artifact_state"):
                node["state"] = r["artifact_state"]
            risk = _lineage_id(r.get("risk_level"))
            if risk:
                node["risk"] = risk
        elif nid in roles:
            node["type"] = roles[nid]
        nodes.append(node)
    return {
        "root_id": root_id or None,
        "depth": depth,
        "limit": limit,
        "nodes": nodes,
        "edges": [
            {"from": edges[i][0], "to": edges[i][1], "label": edges[i][2]} for i in picked
        ],
        "has_more": has_more,
    }


_BINDING_LIFECYCLE = {
    "draft": "draft", "candidate": "candidate", "approved": "approved",
    "retired": "retired", "active": "approved",
}


def _binding_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


@lru_cache(maxsize=8192)
def _binding_utc_str(value: str) -> datetime:
    raw = value.strip()
    if not raw:
        return datetime.min
    try:
        dt = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except (ValueError, TypeError):
        return datetime.min
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo is not None else dt


def _binding_utc(value: Any) -> datetime:
    """Naive-UTC parse of an RFC3339 string; ``datetime.min`` when absent/invalid."""
    if not value or not isinstance(value, str):
        return datetime.min
    return _binding_utc_str(value)


@lru_cache(maxsize=512)
def _parse_semver_key(semver: str) -> tuple[int, ...]:
    return tuple(int(p) for p in semver.lstrip("v").split(".") if p.isdigit())


def _binding_version(row: dict) -> Optional[tuple]:
    """``(sort_key, state, persona_ids)`` of one binding row, or ``None`` if unidentifiable."""
    meta = _binding_dict(row.get("metadata"))
    inner = _binding_dict(meta.get("strategy_spec"))
    inner_meta = _binding_dict(inner.get("metadata"))
    reg_id = str(row.get("registry_id") or meta.get("spec_version_id") or "").strip()
    semver = str(
        row.get("version") or inner.get("spec_version") or meta.get("spec_version")
        or meta.get("version") or ""
    ).strip()
    if not reg_id or not semver:
        return None
    prov = (
        inner["provenance"] if isinstance(inner.get("provenance"), dict)
        else _binding_dict(meta.get("provenance"))
    )
    created_at = row.get("created_at") or prov.get("created_at")
    persona_ids = list(
        inner.get("persona_ids") or meta.get("personaIds") or meta.get("persona_ids")
        or ([meta["persona_id"]] if meta.get("persona_id") else (
            [inner_meta["persona_id"]] if inner_meta.get("persona_id") else []))
    )
    state = (
        row.get("artifact_state") or inner.get("lifecycle_state") or meta.get("lifecycle_state")
        or meta.get("state") or meta.get("status") or "draft"
    )
    key = (
        _parse_semver_key(semver),
        _binding_utc(created_at), reg_id,
    )
    return key, _BINDING_LIFECYCLE.get(str(state or "").strip().lower(), "draft"), persona_ids


def persona_binding_counts(rows: list[dict]) -> dict[str, int]:
    """Per-persona count of non-retired strategies whose current version binds it.

    ``rows`` are the visible-then-family persona-binding rows. The current version of
    a family is its highest ``(semver, created_at, registry_id)``; a retired current
    version contributes nothing; a strategy counts once per distinct persona id.
    """
    current: dict[Any, tuple] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("artifact_type") not in (None, "strategy_spec"):
            raise ValueError("Invalid strategy registry entry")
        sid = str(row.get("strategy_id") or _binding_dict(
            _binding_dict(row.get("metadata")).get("strategy_spec")).get("strategy_id") or "").strip()
        version = _binding_version(row)
        if not sid or version is None:
            raise ValueError("Invalid strategy registry entry")
        if sid not in current or version[0] > current[sid][0]:
            current[sid] = version
    counts: dict[str, int] = {}
    for _key, state, persona_ids in current.values():
        if state == "retired":
            continue
        for persona_id in {str(v).strip() for v in persona_ids if str(v).strip()}:
            counts[persona_id] = counts.get(persona_id, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Strategy summary page read projection (BFF-STRATEGY-READ-REAL-SCALE-20261106)
# ---------------------------------------------------------------------------
STRATEGY_SUMMARY_ROW_KEYS = (
    "registry_id", "artifact_type", "artifact_state", "strategy_id", "version",
    "owner_tenant", "created_at", "updated_at",
)
STRATEGY_SUMMARY_META_KEYS = (
    "spec_version_id", "tenant_id", "spec_version", "version", "personaIds",
    "persona_ids", "persona_id", "lifecycle_state", "state", "status",
    "title", "name", "risk", "owner", "requested_by", "capital_pool_id",
    "capitalPoolId", "source_kind",
)
STRATEGY_SUMMARY_SPEC_KEYS = (
    "strategy_id", "spec_version", "lifecycle_state", "state", "status", "title",
    "name", "risk", "persona_ids", "created_at", "updated_at",
    "last_modified_at", "created_by", "source_kind", "capital_pool_id",
    "capitalPoolId",
)
STRATEGY_SUMMARY_INNER_META_KEYS = (
    "tenant_id", "persona_id", "capital_pool_id", "capitalPoolId", "owner", "risk",
    "lifecycle_state", "state", "status",
)
STRATEGY_SUMMARY_PROV_KEYS = ("created_at", "created_by", "source_kind")
STRATEGY_SUMMARY_GOV_KEYS = ("risk_level", "risk", "owner", "capital_pool_id", "capitalPoolId")
STRATEGY_SUMMARY_MARKET_KEYS = ("alpha", "market", "frequency", "symbols")
STRATEGY_SUMMARY_EXEC_KEYS = ("capital_pool_id", "capitalPoolId", "risk_level", "execution_mode_hint")

# Candidate-index keys for Phase 1 of strategy_summary_page: only identity,
# version, lifecycle, persona-binding, and timestamp fields required for
# current-version selection, state_counts, state/exclude_state/persona_id
# filtering, and last_modified_at sorting before page slicing.
STRATEGY_SUMMARY_INDEX_ROW_KEYS = (
    "registry_id", "artifact_type", "artifact_state", "strategy_id", "version",
    "created_at", "updated_at",
)
STRATEGY_SUMMARY_INDEX_META_KEYS = (
    "spec_version_id", "spec_version", "version", "personaIds",
    "persona_ids", "persona_id", "lifecycle_state", "state", "status",
)
STRATEGY_SUMMARY_INDEX_SPEC_KEYS = (
    "strategy_id", "spec_version", "lifecycle_state", "state", "status",
    "persona_ids", "created_at", "updated_at", "last_modified_at",
)
STRATEGY_SUMMARY_INDEX_INNER_META_KEYS = (
    "persona_id", "lifecycle_state", "state", "status",
)
STRATEGY_SUMMARY_INDEX_PROV_KEYS = ("created_at",)

_STRATEGY_SUMMARY_DTO_LIFECYCLE_MAP: dict[str, str] = {
    "draft": "draft",
    "candidate": "review",
    "review": "review",
    "approved": "approved",
    "active": "deployed",
    "deployed": "deployed",
    "paused": "paused",
    "retired": "retired",
    "archived": "retired",
    "rejected": "retired",
    "superseded": "retired",
    "paper": "paper_running",
    "paper_running": "paper_running",
    "canary": "canary_running",
    "canary_running": "canary_running",
    "canary_authorized_not_started": "canary_authorized_not_started",
    "live": "live_running",
    "live_running": "live_running",
    "needs_human_approval": "needs_human_approval",
    "rollback_required": "rollback_required",
    "stopped": "stopped",
    "failed": "failed",
    "provisioning": "provisioning",
    "provisioning_failed": "failed",
    "research_only": "research_only",
    "consultable": "consultable",
    "paper_owner": "paper_owner",
    "live_owner": "live_owner",
    "frozen": "frozen",
    "suspended": "suspended",
}

STRATEGY_SUMMARY_DEFAULT_STATE_COUNT_KEYS: tuple[str, ...] = (
    "draft",
    "review",
    "approved",
    "paper_running",
    "canary_running",
    "live_running",
    "deployed",
    "paused",
    "retired",
)


def _extract_strategy_summary_raw_lifecycle(row: dict) -> str:
    """Resolve the stored lifecycle value on a strategy summary row without
    collapsing unknown lifecycle values to ``draft``."""
    meta = _binding_dict(row.get("metadata"))
    inner = _binding_dict(meta.get("strategy_spec"))
    inner_meta = _binding_dict(inner.get("metadata"))
    art_state = str(row.get("artifact_state") or "").strip()
    art_lower = art_state.lower()
    spec_state = str(
        inner.get("lifecycle_state")
        or inner.get("state")
        or inner.get("status")
        or meta.get("lifecycle_state")
        or meta.get("state")
        or meta.get("status")
        or inner_meta.get("lifecycle_state")
        or inner_meta.get("state")
        or inner_meta.get("status")
        or ""
    ).strip()
    spec_lower = spec_state.lower()
    if art_lower not in {"", "draft", "candidate", "approved", "retired"}:
        return art_state
    if art_lower in {"", "draft"}:
        if spec_state and spec_lower != "draft":
            return spec_state
        return "draft"
    if spec_state and spec_lower not in {"draft", "candidate", "review", "approved", "retired"}:
        if art_lower == "retired":
            return "retired"
        return spec_state
    return art_lower


def compute_strategy_spec_state_counts(
    current: dict[str, tuple[tuple, str, list, dict]],
) -> dict[str, int]:
    """Count current tenant-visible strategies per normalized DTO state value."""
    counts: dict[str, int] = {k: 0 for k in STRATEGY_SUMMARY_DEFAULT_STATE_COUNT_KEYS}
    for _key, raw_state, _persona_ids, _row in current.values():
        raw_clean = str(raw_state or "").strip().lower()
        dto_state = _STRATEGY_SUMMARY_DTO_LIFECYCLE_MAP.get(raw_clean, "draft")
        counts[dto_state] = counts.get(dto_state, 0) + 1
    return counts


def strategy_spec_summary_row(entry: RegistryEntry) -> dict:
    """Flat light row for StrategySpec list summary projection (in-memory twin of SQL projection)."""
    row: dict[str, Any] = {
        "registry_id": entry.registry_id,
        "artifact_type": (
            entry.artifact_type.value
            if hasattr(entry.artifact_type, "value")
            else entry.artifact_type
        ),
        "artifact_state": (
            entry.artifact_state.value
            if hasattr(entry.artifact_state, "value")
            else entry.artifact_state
        ),
        "strategy_id": entry.strategy_id,
        "version": entry.version,
        "owner_tenant": entry.owner_tenant,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }
    eval_summary = entry.evaluation_summary
    if isinstance(eval_summary, dict):
        row["evaluation_summary"] = {
            k: eval_summary[k] for k in ("risk_level",) if k in eval_summary
        }
    meta = entry.metadata
    if isinstance(meta, dict):
        light_meta = {k: meta[k] for k in STRATEGY_SUMMARY_META_KEYS if k in meta}
        for sub_name, sub_keys in (
            ("provenance", STRATEGY_SUMMARY_PROV_KEYS),
            ("governance", STRATEGY_SUMMARY_GOV_KEYS),
            ("market_scope", STRATEGY_SUMMARY_MARKET_KEYS),
            ("execution_profile", STRATEGY_SUMMARY_EXEC_KEYS),
        ):
            sub_val = meta.get(sub_name)
            if isinstance(sub_val, dict):
                light_meta[sub_name] = {k: sub_val[k] for k in sub_keys if k in sub_val}
        inner = meta.get("strategy_spec")
        if isinstance(inner, dict):
            light_inner = {k: inner[k] for k in STRATEGY_SUMMARY_SPEC_KEYS if k in inner}
            for sub_name, sub_keys in (
                ("provenance", STRATEGY_SUMMARY_PROV_KEYS),
                ("governance", STRATEGY_SUMMARY_GOV_KEYS),
                ("market_scope", STRATEGY_SUMMARY_MARKET_KEYS),
                ("execution_profile", STRATEGY_SUMMARY_EXEC_KEYS),
                ("metadata", STRATEGY_SUMMARY_INNER_META_KEYS),
            ):
                sub_val = inner.get(sub_name)
                if isinstance(sub_val, dict):
                    light_inner[sub_name] = {k: sub_val[k] for k in sub_keys if k in sub_val}
            light_meta["strategy_spec"] = light_inner
        row["metadata"] = light_meta
    else:
        row["metadata"] = {}
    return row


def strategy_spec_summary_index_row(entry: RegistryEntry) -> dict:
    """Minimal candidate-index row for StrategySpec summary selection, counts, filtering, and sorting.

    Extracts only the fields read before page slicing; heavy summary fields
    (titles, governance, market_scope, execution_profile, evaluation_summary,
    full metadata) are deferred to Phase 2 for the returned page slice alone.
    """
    row: dict[str, Any] = {
        "registry_id": entry.registry_id,
        "artifact_type": (
            entry.artifact_type.value
            if hasattr(entry.artifact_type, "value")
            else entry.artifact_type
        ),
        "artifact_state": (
            entry.artifact_state.value
            if hasattr(entry.artifact_state, "value")
            else entry.artifact_state
        ),
        "strategy_id": entry.strategy_id,
        "version": entry.version,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }
    meta = entry.metadata
    if isinstance(meta, dict):
        light_meta: dict[str, Any] = {
            k: meta[k] for k in STRATEGY_SUMMARY_INDEX_META_KEYS if k in meta
        }
        meta_prov = meta.get("provenance")
        if isinstance(meta_prov, dict):
            light_meta["provenance"] = {
                k: meta_prov[k] for k in STRATEGY_SUMMARY_INDEX_PROV_KEYS if k in meta_prov
            }
        inner = meta.get("strategy_spec")
        if isinstance(inner, dict):
            light_inner: dict[str, Any] = {
                k: inner[k] for k in STRATEGY_SUMMARY_INDEX_SPEC_KEYS if k in inner
            }
            inner_prov = inner.get("provenance")
            if isinstance(inner_prov, dict):
                light_inner["provenance"] = {
                    k: inner_prov[k] for k in STRATEGY_SUMMARY_INDEX_PROV_KEYS if k in inner_prov
                }
            inner_meta = inner.get("metadata")
            if isinstance(inner_meta, dict):
                light_inner["metadata"] = {
                    k: inner_meta[k]
                    for k in STRATEGY_SUMMARY_INDEX_INNER_META_KEYS
                    if k in inner_meta
                }
            light_meta["strategy_spec"] = light_inner
        row["metadata"] = light_meta
    else:
        row["metadata"] = {}
    return row


def _strategy_summary_last_modified_at(row: dict) -> Optional[str]:
    meta = _binding_dict(row.get("metadata"))
    inner = _binding_dict(meta.get("strategy_spec"))
    prov = (
        inner["provenance"]
        if isinstance(inner.get("provenance"), dict)
        else _binding_dict(meta.get("provenance"))
    )
    val = (
        row.get("updated_at")
        or row.get("created_at")
        or inner.get("updated_at")
        or inner.get("last_modified_at")
        or inner.get("created_at")
        or prov.get("created_at")
    )
    return str(val) if val is not None else None


def _build_strategy_spec_summary_item(
    sid: str,
    row: dict,
    version_count: int,
    current_state: str,
    persona_list: list[str],
    last_modified_at: Optional[str],
) -> dict[str, Any]:
    meta = _binding_dict(row.get("metadata"))
    inner = _binding_dict(meta.get("strategy_spec"))
    inner_meta = _binding_dict(inner.get("metadata"))
    eval_summary = _binding_dict(row.get("evaluation_summary"))
    prov = (
        inner["provenance"]
        if isinstance(inner.get("provenance"), dict)
        else _binding_dict(meta.get("provenance"))
    )
    reg_id = str(row.get("registry_id") or meta.get("spec_version_id") or "").strip()
    semver = str(
        row.get("version")
        or inner.get("spec_version")
        or meta.get("spec_version")
        or meta.get("version")
        or ""
    ).strip()
    title = (
        inner.get("title")
        or inner.get("name")
        or meta.get("title")
        or meta.get("name")
        or sid
    )
    inner_gov = _binding_dict(inner.get("governance"))
    meta_gov = _binding_dict(meta.get("governance"))
    governance = dict(inner_gov or meta_gov)
    risk = (
        meta.get("risk")
        or inner.get("risk")
        or eval_summary.get("risk_level")
        or inner_gov.get("risk_level")
        or meta_gov.get("risk_level")
        or inner_gov.get("risk")
        or meta_gov.get("risk")
    )
    if risk and "risk_level" not in governance:
        governance["risk_level"] = risk
    market_scope = dict(_binding_dict(inner.get("market_scope")) or _binding_dict(meta.get("market_scope")))
    execution_profile = dict(
        _binding_dict(inner.get("execution_profile")) or _binding_dict(meta.get("execution_profile"))
    )
    source_kind = str(
        inner.get("source_kind")
        or prov.get("source_kind")
        or meta.get("source_kind")
        or ""
    )
    created_at = (
        row.get("created_at")
        or prov.get("created_at")
        or inner.get("created_at")
        or row.get("updated_at")
    )
    entry_tenant = str(
        row.get("owner_tenant")
        or meta.get("tenant_id")
        or inner_meta.get("tenant_id")
        or ""
    ).strip()
    merged_meta = {
        **inner_meta,
        **{k: v for k, v in meta.items() if k != "strategy_spec"},
    }
    capital_pool_id = str(
        execution_profile.get("capital_pool_id")
        or execution_profile.get("capitalPoolId")
        or governance.get("capital_pool_id")
        or governance.get("capitalPoolId")
        or inner.get("capital_pool_id")
        or inner.get("capitalPoolId")
        or meta.get("capital_pool_id")
        or meta.get("capitalPoolId")
        or inner_meta.get("capital_pool_id")
        or inner_meta.get("capitalPoolId")
        or ""
    ).strip()
    item: dict[str, Any] = {
        "object_ref": {"type": "StrategySpec", "id": reg_id},
        "strategy_id": sid,
        "current_spec_version_id": reg_id,
        "current_spec_version": semver,
        "title": title,
        "name": title,
        "lifecycle_state": current_state,
        "source_kind": source_kind,
        "hypothesis_excerpt": None,
        "version_count": version_count,
        "last_modified_at": last_modified_at,
        "route_href": f"/knowledge/strategy-specs/{sid}",
        "persona_ids": persona_list,
        "governance": governance,
        "market_scope": market_scope,
        "execution_profile": execution_profile,
        "allowedActions": {
            "canSubmitForApproval": current_state == "draft",
            "canRetire": current_state in {"candidate", "approved"},
            "canCompare": current_state in {"candidate", "approved", "retired"},
        },
        "risk": risk,
        "created_at": created_at,
        "tenant_id": entry_tenant,
        "strategy_spec_registry_id": reg_id,
    }
    owner = meta.get("owner") or inner_gov.get("owner") or meta_gov.get("owner") or inner_meta.get("owner")
    if owner:
        item["owner"] = owner
    if capital_pool_id:
        item["capital_pool_id"] = capital_pool_id
    if merged_meta:
        item["metadata"] = merged_meta
    return item


def _validate_strategy_summary_page_args(
    *,
    state: Optional[str] = None,
    exclude_state: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = 20,
) -> tuple[bool, bool, int, int]:
    has_state_filter = state is not None and str(state).strip() != ""
    has_exclude_state_filter = exclude_state is not None and str(exclude_state).strip() != ""
    if has_state_filter and has_exclude_state_filter:
        raise ValueError("state and exclude_state cannot be combined")

    effective_page_size = max(1, min(int(page_size or 20), 200))
    start = 0
    if page_token is not None and str(page_token).strip() != "":
        try:
            start = int(str(page_token).strip())
        except ValueError as exc:
            raise ValueError("Invalid page_token") from exc
        if start < 0:
            raise ValueError("Invalid page_token")
    return has_state_filter, has_exclude_state_filter, effective_page_size, start


def _extract_strategy_summary_persona_ids(row: dict) -> list[str]:
    meta = _binding_dict(row.get("metadata"))
    inner = _binding_dict(meta.get("strategy_spec"))
    inner_meta = _binding_dict(inner.get("metadata"))
    raw = (
        inner.get("persona_ids")
        or meta.get("personaIds")
        or meta.get("persona_ids")
        or (
            [meta["persona_id"]]
            if meta.get("persona_id")
            else ([inner_meta["persona_id"]] if inner_meta.get("persona_id") else [])
        )
    )
    return [str(v) for v in raw if str(v).strip()]


def project_strategy_spec_summary_page(
    rows: list[dict],
    *,
    artifact_state: Optional[str] = None,
    state: Optional[str] = None,
    exclude_state: Optional[str] = None,
    persona_id: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = 20,
    full_row_loader: Optional[Callable[[list[str]], Mapping[str, dict]]] = None,
) -> dict[str, Any]:
    """Owner-side current-version selection, filtering, sorting, and pagination over summary rows."""
    (
        has_state_filter,
        has_exclude_state_filter,
        effective_page_size,
        start,
    ) = _validate_strategy_summary_page_args(
        state=state,
        exclude_state=exclude_state,
        page_token=page_token,
        page_size=page_size,
    )

    wanted_artifact_state = str(artifact_state).strip() if artifact_state else None
    winning_key: dict[str, tuple[tuple[int, ...], datetime, str]] = {}
    winning_row: dict[str, dict] = {}
    version_counts: dict[str, int] = {}

    for row in rows:
        if not isinstance(row, dict) or row.get("artifact_type") not in (None, "strategy_spec"):
            raise ValueError("Invalid strategy registry entry")
        if wanted_artifact_state is not None and row.get("artifact_state") != wanted_artifact_state:
            continue
        sid_raw = row.get("strategy_id")
        reg_raw = row.get("registry_id")
        ver_raw = row.get("version")
        created_at = row.get("created_at")
        if not (sid_raw and reg_raw and ver_raw and created_at):
            meta = _binding_dict(row.get("metadata"))
            inner = _binding_dict(meta.get("strategy_spec"))
            sid_raw = sid_raw or inner.get("strategy_id")
            reg_raw = reg_raw or meta.get("spec_version_id")
            ver_raw = ver_raw or inner.get("spec_version") or meta.get("spec_version") or meta.get("version")
            if not created_at:
                prov = (
                    inner["provenance"]
                    if isinstance(inner.get("provenance"), dict)
                    else _binding_dict(meta.get("provenance"))
                )
                created_at = prov.get("created_at")
        sid = str(sid_raw or "").strip()
        reg_id = str(reg_raw or "").strip()
        semver = str(ver_raw or "").strip()
        if not sid or not reg_id or not semver:
            raise ValueError("Invalid strategy registry entry")
        key = (_parse_semver_key(semver), _binding_utc(created_at), reg_id)
        version_counts[sid] = version_counts.get(sid, 0) + 1
        prev_key = winning_key.get(sid)
        if prev_key is None or key > prev_key:
            winning_key[sid] = key
            winning_row[sid] = row

    state_counts: dict[str, int] = {k: 0 for k in STRATEGY_SUMMARY_DEFAULT_STATE_COUNT_KEYS}

    raw_state_filter = str(state).strip().lower() if has_state_filter else ""
    wanted_dto_state = (
        _STRATEGY_SUMMARY_DTO_LIFECYCLE_MAP.get(raw_state_filter, raw_state_filter)
        if has_state_filter
        else None
    )
    raw_exclude_filter = str(exclude_state).strip().lower() if has_exclude_state_filter else ""
    excluded_dto_state = (
        _STRATEGY_SUMMARY_DTO_LIFECYCLE_MAP.get(raw_exclude_filter, raw_exclude_filter)
        if has_exclude_state_filter
        else None
    )

    wanted_persona = (
        str(persona_id).strip()
        if persona_id is not None and str(persona_id).strip() != ""
        else None
    )

    matched: list[tuple[datetime, str, dict, int, str, list[str], Optional[str]]] = []
    for sid, row in winning_row.items():
        current_state = _extract_strategy_summary_raw_lifecycle(row)
        raw_clean = str(current_state or "").strip().lower()
        is_known_state = (not raw_clean) or (raw_clean in _STRATEGY_SUMMARY_DTO_LIFECYCLE_MAP)
        dto_state = _STRATEGY_SUMMARY_DTO_LIFECYCLE_MAP.get(raw_clean, "draft")
        state_counts[dto_state] = state_counts.get(dto_state, 0) + 1
        is_true_draft = (not raw_clean) or (raw_clean == "draft")

        if has_state_filter and wanted_dto_state == "retired":
            if not ((is_known_state and dto_state == "retired") or raw_clean == raw_state_filter):
                continue
        else:
            if is_known_state and dto_state == "retired":
                continue
            if has_state_filter:
                if raw_state_filter == "draft":
                    if not is_true_draft:
                        continue
                elif not ((is_known_state and dto_state == wanted_dto_state) or raw_clean == raw_state_filter):
                    continue
            elif has_exclude_state_filter:
                if raw_exclude_filter == "draft":
                    if is_true_draft:
                        continue
                elif (is_known_state and dto_state == excluded_dto_state) or raw_clean == raw_exclude_filter:
                    continue

        persona_list = _extract_strategy_summary_persona_ids(row)
        if wanted_persona is not None and wanted_persona not in persona_list:
            continue
        last_mod = _strategy_summary_last_modified_at(row)
        matched.append(
            (_binding_utc(last_mod), sid, row, version_counts[sid], current_state, persona_list, last_mod)
        )

    matched.sort(key=lambda item: (item[0], item[1]), reverse=True)
    total = len(matched)
    page_rows = matched[start : start + effective_page_size]
    next_offset = start + len(page_rows)
    next_page_token = str(next_offset) if (next_offset < total and page_rows) else None

    full_rows_by_id: Mapping[str, dict] = {}
    if page_rows and full_row_loader is not None:
        selected_ids = [
            str(
                row.get("registry_id")
                or _binding_dict(row.get("metadata")).get("spec_version_id")
                or ""
            ).strip()
            for (_dt, _sid, row, _vcount, _current_state, _persona_list, _last_mod) in page_rows
        ]
        full_rows_by_id = full_row_loader([rid for rid in selected_ids if rid]) or {}

    items = [
        _build_strategy_spec_summary_item(
            sid,
            full_rows_by_id.get(
                str(
                    row.get("registry_id")
                    or _binding_dict(row.get("metadata")).get("spec_version_id")
                    or ""
                ).strip(),
                row,
            ),
            vcount,
            current_state,
            persona_list,
            last_mod,
        )
        for (_dt, sid, row, vcount, current_state, persona_list, last_mod) in page_rows
    ]
    return {
        "items": items,
        "total": total,
        "page_size": effective_page_size,
        "next_page_token": next_page_token,
        "state_counts": state_counts,
        "page_info": {
            "total": total,
            "page_size": effective_page_size,
            "next_page_token": next_page_token,
        },
    }


class RegistryUniqueViolationError(ValueError):
    """Raised when a create would collide on a composite ``unique_fields``
    identity (e.g. (strategy_id, version, artifact_type)) already held by a
    *different* registry_id. Mirrors the equivalent Postgres-backend error so
    ``RegistryService`` can catch one exception type regardless of backend."""

    def __init__(
        self,
        registry_id: str,
        unique_fields: tuple[str, ...],
        other_registry_id: str,
    ) -> None:
        super().__init__(
            f"Registry entry {registry_id} collides on {unique_fields} with the "
            f"already-registered registry_id={other_registry_id}."
        )
        self.registry_id = registry_id
        self.unique_fields = unique_fields
        self.other_registry_id = other_registry_id


class RegistryConcurrentUpdateError(ValueError):
    """Raised when a CAS-guarded update's base snapshot no longer matches the
    durable entry. Mirrors ``pg_store.RegistryConcurrentUpdateError`` so
    ``RegistryService`` can catch one exception type regardless of backend."""

    def __init__(self, registry_id: str) -> None:
        super().__init__(
            f"Registry entry {registry_id} was modified by another writer since it was read; "
            "re-read the current version before retrying."
        )
        self.registry_id = registry_id


class RegistryStore:
    """Thread-safe in-memory registry entry store."""

    def __init__(self) -> None:
        # Admission callbacks execute under the owner CAS lock and may perform
        # read-only lineage lookups in this same store. Reentrancy lets that
        # thread read its approved parent while still serializing other writers.
        self._lock = threading.RLock()
        # registry_id -> RegistryEntry
        self._entries: dict[str, RegistryEntry] = {}
        # strategy_id -> list of registry_ids (for index)
        self._strategy_index: dict[str, list[str]] = {}
        # command_key -> {"new_metadata": ...} idempotent-replay ledger
        self._command_receipts: dict[str, dict] = {}
        # registry_id -> original entry snapshot at first successful creation
        # (reviewer finding 5 — see PostgresRegistryStore._store_creation_receipt)
        self._creation_receipts: dict[str, dict] = {}
        # Observable counters for full summary leaf extractions (used by tests to
        # verify that strategy_summary_page only extracts full summary fields for
        # the rows it returns).
        self.full_summary_rows_extracted: int = 0
        self.full_summary_row_ids_extracted: tuple[str, ...] = ()

    # -- Write operations -------------------------------------------------

    def _put_unlocked(self, entry: RegistryEntry) -> None:
        self._entries[entry.registry_id] = entry
        self._strategy_index.setdefault(entry.strategy_id, [])
        if entry.registry_id not in self._strategy_index[entry.strategy_id]:
            self._strategy_index[entry.strategy_id].append(entry.registry_id)

    @staticmethod
    def _new_entry(
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict] = None,
    ) -> RegistryEntry:
        now = utc_now_iso()
        return RegistryEntry(
            registry_id=registry_id,
            artifact_type=payload.artifact_type,
            strategy_id=payload.strategy_id,
            version=payload.version,
            artifact_state=payload.artifact_state,
            lineage=payload.lineage,
            storage_ref=payload.storage_ref,
            checksum=payload.checksum,
            producer_run_id=payload.producer_run_id,
            evaluation_summary=payload.evaluation_summary,
            rollback_target=payload.rollback_target,
            metadata=payload.metadata,
            created_at=now,
            updated_at=now,
            last_actor=actor,
            owner_tenant=str((actor or {}).get("tenant") or "").strip() or None,
        )

    def put(self, entry: RegistryEntry) -> None:
        with self._lock:
            self._put_unlocked(entry)

    def create(
        self,
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict] = None,
        unique_fields: tuple[str, ...] = (),
    ) -> RegistryEntry:
        """Create a new entry, atomically reserving ``unique_fields`` if given.

        Reviewer finding 3: the plain (non-``create_if_absent``) create path
        used to insert unconditionally with no composite-identity check at
        all, so two concurrent ``register()`` calls for the same
        (strategy_id, version, artifact_type) under two different
        caller-generated ``registry_id``s could both commit — this closes
        that race the same way ``create_if_absent`` does, under the same
        lock held for the whole check-then-insert sequence.
        """
        entry = self._new_entry(payload, registry_id, actor=actor)
        with self._lock:
            if unique_fields:
                for other in self._entries.values():
                    if other.registry_id == registry_id:
                        continue
                    if all(
                        getattr(payload, field, None) == getattr(other, field, None)
                        for field in unique_fields
                    ):
                        raise RegistryUniqueViolationError(
                            registry_id, unique_fields, other.registry_id,
                        )
            self._put_unlocked(entry)
        return RegistryEntry.from_dict(entry.to_dict())

    def create_if_absent(
        self,
        payload: RegistryEntryCreate,
        registry_id: str,
        *,
        actor: Optional[dict] = None,
        unique_fields: tuple[str, ...] = (),
    ) -> tuple[RegistryEntry, bool]:
        """Atomically create an entry, or return the existing entry unchanged.

        ``unique_fields`` (e.g. ``("strategy_id", "version", "artifact_type")``)
        reserves a composite identity in addition to ``registry_id`` — see
        architecture-resumption-sa-sd.md §3.2's "immutable revision identity"
        requirement: two different caller-supplied ``registry_id``s must not
        both succeed at the same (strategy_id, version, artifact_type) tuple.
        The whole scan-then-insert sequence runs under ``self._lock`` so this
        mirrors the table-locked semantics of
        ``PostgresJsonOwnerStore.insert_if_absent``.
        """
        with self._lock:
            existing = self._entries.get(registry_id)
            if existing is not None:
                # Returns the live current row; callers use
                # get_creation_receipt() for the immutable-content
                # comparison instead (reviewer finding 5) — a replayed
                # create is not a request to revert real progress (e.g. an
                # approval) back to its original draft state.
                return RegistryEntry.from_dict(existing.to_dict()), False
            if unique_fields:
                for other in self._entries.values():
                    if all(
                        getattr(payload, field, None) == getattr(other, field, None)
                        for field in unique_fields
                    ):
                        return RegistryEntry.from_dict(other.to_dict()), False
            entry = self._new_entry(payload, registry_id, actor=actor)
            self._put_unlocked(entry)
            self._creation_receipts[registry_id] = entry.to_dict()
            return RegistryEntry.from_dict(entry.to_dict()), True

    def update(
        self,
        entry: RegistryEntry,
        *,
        expected: Optional[dict] = None,
        actor: Optional[dict] = None,
    ) -> RegistryEntry:
        """Commit a mutation, optionally CAS-guarded against the caller's base snapshot.

        ``expected`` must be the ``to_dict()`` snapshot the caller read via
        :meth:`get` before mutating ``entry`` — this mirrors the Postgres
        backend's CAS contract so both backends reject the same stale-write
        pattern the same way.
        """
        with self._lock:
            if expected is not None:
                current = self._entries.get(entry.registry_id)
                if current is None or current.to_dict() != expected:
                    raise RegistryConcurrentUpdateError(entry.registry_id)
            entry.updated_at = utc_now_iso()
            if actor is not None:
                entry.last_actor = actor
            self._put_unlocked(entry)
            return RegistryEntry.from_dict(entry.to_dict())

    # -- Read operations --------------------------------------------------

    def get(self, registry_id: str) -> Optional[RegistryEntry]:
        with self._lock:
            entry = self._entries.get(registry_id)
            return RegistryEntry.from_dict(entry.to_dict()) if entry is not None else None

    def list_by_strategy(self, strategy_id: str) -> list[RegistryEntry]:
        with self._lock:
            ids = self._strategy_index.get(strategy_id, [])
            return [
                RegistryEntry.from_dict(self._entries[rid].to_dict())
                for rid in ids
                if rid in self._entries
            ]

    def list_all_entries(self) -> list[RegistryEntry]:
        with self._lock:
            return [
                RegistryEntry.from_dict(entry.to_dict())
                for entry in self._entries.values()
            ]

    def list_persona_binding_rows(
        self, *, visible_tenants: Optional[frozenset] = None, artifact_type: Optional[str] = None,
    ) -> list[dict]:
        rows = [persona_binding_row(entry) for entry in self.list_all_entries()]
        return persona_binding_candidate_family_rows(
            [r for r in rows if persona_binding_row_visible(r, visible_tenants, artifact_type)]
        )

    def list_lineage_rows(self, *, visible_tenants: Optional[frozenset] = None) -> list[dict]:
        rows = [lineage_row(entry) for entry in self.list_all_entries()]
        return [r for r in rows if persona_binding_row_visible(r, visible_tenants, None)]

    def list_strategy_artifact_rows(
        self, *, visible_tenants: Optional[frozenset] = None,
    ) -> list[dict]:
        return [
            strategy_artifact_row(entry)
            for entry in self.list_all_entries()
            if persona_binding_row_visible(
                entry.to_dict(), visible_tenants, "execution_bundle"
            )
        ]

    def list_strategy_spec_summary_rows(
        self,
        *,
        visible_tenants: Optional[frozenset] = None,
        artifact_type: Optional[str] = "strategy_spec",
    ) -> list[dict]:
        with self._lock:
            entries = list(self._entries.values())
        rows: list[dict] = []
        extracted_ids: list[str] = []
        for entry in entries:
            art_type = entry.artifact_type.value if hasattr(entry.artifact_type, "value") else entry.artifact_type
            if artifact_type is not None and art_type != artifact_type:
                continue
            if visible_tenants is not None and entry.owner_tenant not in visible_tenants:
                continue
            extracted_ids.append(entry.registry_id)
            rows.append(strategy_spec_summary_row(entry))
        self.full_summary_rows_extracted = len(rows)
        self.full_summary_row_ids_extracted = tuple(extracted_ids)
        return rows

    def list_strategy_spec_summary_index_rows(
        self,
        *,
        visible_tenants: Optional[frozenset] = None,
        artifact_type: Optional[str] = "strategy_spec",
    ) -> list[dict]:
        """Return lightweight candidate-index rows for Phase 1 of strategy_summary_page."""
        self.full_summary_rows_extracted = 0
        self.full_summary_row_ids_extracted = ()
        with self._lock:
            entries = list(self._entries.values())
        rows: list[dict] = []
        for entry in entries:
            art_type = entry.artifact_type.value if hasattr(entry.artifact_type, "value") else entry.artifact_type
            if artifact_type is not None and art_type != artifact_type:
                continue
            if visible_tenants is not None and entry.owner_tenant not in visible_tenants:
                continue
            rows.append(strategy_spec_summary_index_row(entry))
        return rows

    def get_strategy_spec_summary_rows_by_ids(
        self,
        registry_ids: Sequence[str],
        *,
        visible_tenants: Optional[frozenset] = None,
        artifact_type: Optional[str] = "strategy_spec",
    ) -> dict[str, dict]:
        """Extract full summary fields only for the requested ``registry_ids`` (Phase 2)."""
        target_ids = [str(rid).strip() for rid in (registry_ids or ()) if str(rid).strip()]
        if not target_ids:
            self.full_summary_rows_extracted = 0
            self.full_summary_row_ids_extracted = ()
            return {}
        target_set = set(target_ids)
        with self._lock:
            matched_entries = [
                self._entries[rid]
                for rid in target_ids
                if rid in self._entries
            ]
            if len(matched_entries) < len(target_set):
                seen_rids = {e.registry_id for e in matched_entries}
                for entry in self._entries.values():
                    if entry.registry_id in seen_rids:
                        continue
                    meta = entry.metadata if isinstance(entry.metadata, dict) else {}
                    spec_ver_id = str(meta.get("spec_version_id") or "").strip()
                    if spec_ver_id and spec_ver_id in target_set:
                        matched_entries.append(entry)
        out: dict[str, dict] = {}
        extracted_ids: list[str] = []
        for entry in matched_entries:
            art_type = entry.artifact_type.value if hasattr(entry.artifact_type, "value") else entry.artifact_type
            if artifact_type is not None and art_type != artifact_type:
                continue
            if visible_tenants is not None and entry.owner_tenant not in visible_tenants:
                continue
            summary_row = strategy_spec_summary_row(entry)
            rid = str(
                summary_row.get("registry_id")
                or _binding_dict(summary_row.get("metadata")).get("spec_version_id")
                or entry.registry_id
            ).strip()
            extracted_ids.append(rid)
            out[rid] = summary_row
        self.full_summary_rows_extracted = len(out)
        self.full_summary_row_ids_extracted = tuple(extracted_ids)
        return out

    def resolve_latest_approved(self, strategy_id: str) -> Optional[RegistryEntry]:
        """Return the newest approved entry for a strategy family (semver comparison)."""
        entries = self.list_by_strategy(strategy_id)
        approved = [e for e in entries if e.artifact_state.value == "approved"]
        if not approved:
            return None
        # Sort by semver descending
        def _parse_ver(v: str) -> tuple[int, ...]:
            return tuple(int(x) for x in v.split("."))
        approved.sort(key=lambda e: _parse_ver(e.version), reverse=True)
        return approved[0]

    def get_command_receipt(
        self,
        command_key: str,
        registry_id: str,
        *,
        actor: Optional[dict] = None,
        command_type: str = "metadata",
    ) -> Optional[dict[str, Any]]:
        """In-memory mirror of PostgresRegistryStore.get_command_receipt."""
        from .pg_store import PostgresRegistryStore

        scoped_key = PostgresRegistryStore.receipt_key(
            command_key, registry_id, actor=actor, command_type=command_type,
        )
        with self._lock:
            receipt = self._command_receipts.get(scoped_key)
            if receipt is None and command_type == "create":
                scoped_key_reg = PostgresRegistryStore.receipt_key(
                    command_key, "register_entry", actor=actor, command_type="create",
                )
                receipt = self._command_receipts.get(scoped_key_reg)
                if receipt is not None:
                    committed = receipt.get("committed_entry")
                    if committed is not None and committed.get("registry_id") != registry_id:
                        return None
            return dict(receipt) if receipt is not None else None

    def create_with_receipt(
        self,
        payload_factory: Callable[[], tuple[RegistryEntryCreate, str]],
        *,
        command_key: str,
        actor: Optional[dict] = None,
        unique_fields: tuple[str, ...] = (),
        request_fingerprint: object = None,
        strategy_id: Optional[str] = None,
        validate_lineage: Optional[Callable[[list[RegistryEntry]], None]] = None,
    ) -> tuple[RegistryEntry, bool]:
        """Atomically create-or-replay a caller-scoped idempotent creation.

        Reviewer finding 4: the generic create routes (e.g. the name-only
        draft path) previously had no idempotency concept at all — a
        retried identical request under the same ``Idempotency-Key``
        synthesized a *fresh* random identity every time instead of
        returning the originally-created entry. ``payload_factory`` is only
        invoked when this is genuinely the first request under this
        tenant/actor-scoped key; a replay never generates a new identity,
        it returns the entry snapshot committed the first time.

        ``request_fingerprint`` is a JSON-serializable normalized
        representation of the caller's request (see
        ``PostgresRegistryStore.create_with_receipt`` for the reviewer
        finding 3 rationale): a same-key replay whose fingerprint differs
        from the one originally committed is a divergent request, not a
        replay, and must fail closed instead of silently returning the
        original entry under a different requested identity/content.
        """
        from .pg_store import DivergentCommandReplayError, PostgresRegistryStore, _request_digest

        scoped_key = PostgresRegistryStore.receipt_key(
            command_key, "register_entry", actor=actor, command_type="create",
        )
        request_digest = _request_digest({"request": request_fingerprint})
        with self._lock:
            receipt = self._command_receipts.get(scoped_key)
            if receipt is not None:
                if receipt.get("request_digest") != request_digest:
                    raise DivergentCommandReplayError(command_key)
                return RegistryEntry.from_dict(receipt["committed_entry"]), True

            payload, registry_id = payload_factory()
            target_strategy_id = strategy_id or getattr(payload, "strategy_id", None)
            if validate_lineage is not None and target_strategy_id:
                existing = [e for e in self._entries.values() if e.strategy_id == target_strategy_id]
                validate_lineage(existing)

            if unique_fields:
                for other in self._entries.values():
                    if other.registry_id == registry_id:
                        continue
                    if all(
                        getattr(payload, field, None) == getattr(other, field, None)
                        for field in unique_fields
                    ):
                        raise RegistryUniqueViolationError(
                            registry_id, unique_fields, other.registry_id,
                        )
            entry = self._new_entry(payload, registry_id, actor=actor)
            self._put_unlocked(entry)
            committed = entry.to_dict()
            finalized = {
                "command_key": command_key,
                "registry_id": entry.registry_id,
                "receipt_key": scoped_key,
                "request_digest": request_digest,
                "committed_entry": committed,
                "committed_at": committed.get("created_at") or committed.get("updated_at") or utc_now_iso(),
            }
            self._command_receipts[scoped_key] = finalized
            by_reg_id_key = PostgresRegistryStore.receipt_key(
                command_key, entry.registry_id, actor=actor, command_type="create",
            )
            if by_reg_id_key != scoped_key:
                by_reg_finalized = dict(finalized)
                by_reg_finalized["receipt_key"] = by_reg_id_key
                self._command_receipts[by_reg_id_key] = by_reg_finalized
            return RegistryEntry.from_dict(committed), False

    def register_strategy_spec_revision(
        self,
        *,
        strategy_id: str,
        registry_id: str,
        payload: RegistryEntryCreate,
        validate_lineage: Callable[[list[RegistryEntry]], None],
        actor: Optional[dict] = None,
        unique_fields: tuple[str, ...] = (),
    ) -> tuple[RegistryEntry, bool]:
        """In-memory mirror of ``PostgresRegistryStore.register_strategy_spec_revision``.

        ``self._lock`` already serializes the whole read-validate-write
        sequence for every caller against this process-local store (there is
        no separate advisory-lock primitive needed in-process), so this
        closes the same TOCTOU race the Postgres backend closes with a
        per-strategy_id advisory transaction lock.
        """
        with self._lock:
            existing_entries = [
                RegistryEntry.from_dict(entry.to_dict())
                for entry in self._entries.values()
                if entry.strategy_id == strategy_id
            ]
            validate_lineage(existing_entries)
            existing = self._entries.get(registry_id)
            if existing is not None:
                return RegistryEntry.from_dict(existing.to_dict()), False
            if unique_fields:
                for other in self._entries.values():
                    if all(
                        getattr(payload, field, None) == getattr(other, field, None)
                        for field in unique_fields
                    ):
                        return RegistryEntry.from_dict(other.to_dict()), False
            entry = self._new_entry(payload, registry_id, actor=actor)
            self._put_unlocked(entry)
            self._creation_receipts[registry_id] = entry.to_dict()
            return RegistryEntry.from_dict(entry.to_dict()), True

    def get_creation_receipt(self, registry_id: str) -> Optional[RegistryEntry]:
        """In-memory mirror of ``PostgresRegistryStore.get_creation_receipt``."""
        with self._lock:
            original = self._creation_receipts.get(registry_id)
            return RegistryEntry.from_dict(original) if original is not None else None

    # -- Deployment summary update (called by deployment service) -----------

    def update_deployment_summary(
        self,
        registry_id: str,
        *,
        current_stage: DeploymentStage,
        deployment_plan_id: Optional[str] = None,
        runtime_binding_id: Optional[str] = None,
        actor: Optional[dict] = None,
    ) -> Optional[RegistryEntry]:
        """
        Updates the derived deployment_summary on a registry entry.

        This is a read-model projection — the authoritative deployment stage
        lives in DeploymentPlan / RuntimeBinding, not in the registry entry.
        """
        with self._lock:
            entry = self._entries.get(registry_id)
            if entry is None:
                return None

            entry.deployment_summary = DeploymentSummary(
                current_stage=current_stage,
                deployment_plan_id=deployment_plan_id,
                runtime_binding_id=runtime_binding_id,
                last_transition_at=utc_now_iso(),
            )
            entry.updated_at = utc_now_iso()
            if actor is not None:
                entry.last_actor = actor
            return RegistryEntry.from_dict(entry.to_dict())

    # -- Artifact-state CAS update (parity with PostgresRegistryStore) -----

    def commit_artifact_state_cas(
        self,
        *,
        registry_id: str,
        base_snapshot: dict,
        target_state: Any,
        approved_at: Optional[str] = None,
        approver: Optional[str] = None,
        approval_decision_id: Optional[str] = None,
        command_key: Optional[str] = None,
        actor: Optional[dict] = None,
        validate: Optional[Callable[[RegistryEntry], None]] = None,
        expected_artifact_state: Optional[str] = None,
        expected_version: Optional[str] = None,
        expected_updated_at: Optional[str] = None,
    ) -> tuple[RegistryEntry, bool]:
        """In-memory mirror of ``PostgresRegistryStore.commit_artifact_state_cas``
        — same CAS + idempotent-replay + divergent-key-rejection contract as
        ``commit_metadata_cas``, applied to the artifact_state transition
        (reviewer finding 5). ``validate`` runs only on the non-replay path —
        see the Postgres backend's docstring for why re-validating a
        transition against a replay's already-post-transition current state
        would always (and wrongly) fail.

        ``expected_artifact_state``/``expected_version``/``expected_updated_at``
        mirror the Postgres backend's caller-bound-base digest fields
        (reviewer finding 6) — ``base_snapshot`` itself already carries the
        caller's claimed base merged in by the caller
        (``RegistryService.advance_artifact_state``), so the actual CAS
        enforcement is the ``current.to_dict() != base_snapshot`` check
        below; these are threaded through only so a same command_key
        resubmitted with a genuinely different claimed base is detected as
        divergent rather than silently replayed.
        """
        from .pg_store import DivergentCommandReplayError, PostgresRegistryStore, _request_digest

        scoped_key = (
            PostgresRegistryStore.receipt_key(
                command_key, registry_id, actor=actor, command_type="advance",
            )
            if command_key
            else None
        )
        target_value = target_state.value if hasattr(target_state, "value") else target_state
        request_digest = _request_digest({
            "registry_id": registry_id,
            "target_state": target_value,
            "approver": approver,
            "approval_decision_id": approval_decision_id,
            "expected_artifact_state": expected_artifact_state,
            "expected_version": expected_version,
            "expected_updated_at": expected_updated_at,
        })
        with self._lock:
            if scoped_key:
                receipt = self._command_receipts.get(scoped_key)
                if receipt is not None:
                    # Deliberately excludes the entry's current
                    # artifact_state from the comparison — see the Postgres
                    # backend's docstring: there is no separate "expected
                    # base state" precondition here, and the base
                    # legitimately differs between the original call and a
                    # later replay issued after that call already committed.
                    if (
                        receipt.get("target_state") != target_value
                        or receipt.get("approver") != approver
                        or receipt.get("approval_decision_id") != approval_decision_id
                        or receipt.get("expected_artifact_state") != expected_artifact_state
                        or receipt.get("expected_version") != expected_version
                        or receipt.get("expected_updated_at") != expected_updated_at
                    ):
                        raise DivergentCommandReplayError(command_key)
                    return RegistryEntry.from_dict(receipt["committed_entry"]), True

            current = self._entries.get(registry_id)
            if current is None or current.to_dict() != base_snapshot:
                raise RegistryConcurrentUpdateError(registry_id)
            entry = RegistryEntry.from_dict(base_snapshot)
            if validate is not None:
                validate(entry)
            entry.artifact_state = target_state
            entry.updated_at = utc_now_iso()
            if approved_at is not None:
                entry.approved_at = approved_at
            if approver:
                entry.approver = approver
            if approval_decision_id:
                entry.approval_decision_id = approval_decision_id
            if actor is not None:
                entry.last_actor = actor
            self._put_unlocked(entry)
            committed = entry.to_dict()
            if scoped_key:
                self._command_receipts[scoped_key] = {
                    "command_key": command_key,
                    "receipt_key": scoped_key,
                    "registry_id": registry_id,
                    "request_digest": request_digest,
                    "target_state": target_value,
                    "approver": approver,
                    "approval_decision_id": approval_decision_id,
                    "expected_artifact_state": expected_artifact_state,
                    "expected_version": expected_version,
                    "expected_updated_at": expected_updated_at,
                    "committed_entry": committed,
                    "committed_at": committed.get("updated_at") or utc_now_iso(),
                }
            return RegistryEntry.from_dict(committed), False

    # -- Metadata CAS update (parity with PostgresRegistryStore) -----------

    def commit_metadata_cas(
        self,
        *,
        registry_id: str,
        base_snapshot: dict,
        new_metadata: Optional[dict],
        command_key: Optional[str] = None,
        actor: Optional[dict] = None,
        validate: Optional[Callable[[RegistryEntry], None]] = None,
    ) -> tuple[RegistryEntry, bool]:
        """In-memory mirror of ``PostgresRegistryStore.commit_metadata_cas``.

        The receipt ledger here is process-local (not durable across
        restarts), which is why this backend is a test double only — the
        contract this method proves (CAS + idempotent replay + divergent-key
        rejection + returning the *originally* committed entry snapshot on
        replay, never whatever the row has become since) is identical to the
        Postgres backend so ``RegistryService`` can be tested against either.
        The receipt key is scoped by tenant/actor/registry_id/command_key
        (mirroring ``PostgresRegistryStore.receipt_key``) so the same
        client-chosen ``command_key`` cannot collide across tenants/actors/
        aggregates.
        """
        from .pg_store import DivergentCommandReplayError, PostgresRegistryStore, _request_digest

        scoped_key = (
            PostgresRegistryStore.receipt_key(
                command_key, registry_id, actor=actor, command_type="metadata",
            )
            if command_key
            else None
        )
        request_digest = _request_digest({
            "registry_id": registry_id,
            "expected_metadata": base_snapshot.get("metadata"),
            "metadata": new_metadata,
        })
        with self._lock:
            if scoped_key:
                receipt = self._command_receipts.get(scoped_key)
                if receipt is not None:
                    # Compare the full normalized request — including the
                    # caller's precondition (expected_metadata) — not just
                    # the target metadata, so a same-key retry with a
                    # *changed* precondition is treated as divergent even
                    # when the target metadata happens to match (reviewer
                    # finding 6; mirrors PostgresRegistryStore).
                    if (
                        receipt.get("request_digest") != request_digest
                        and (
                            receipt.get("new_metadata") != new_metadata
                            or receipt.get("expected_metadata") != base_snapshot.get("metadata")
                        )
                    ):
                        raise DivergentCommandReplayError(command_key)
                    # Return the entry snapshot as it was at the moment this
                    # command_key originally committed — not a fresh read of
                    # the (possibly since-mutated-under-a-different-key) row.
                    return RegistryEntry.from_dict(receipt["committed_entry"]), True

            current = self._entries.get(registry_id)
            if current is None or current.to_dict() != base_snapshot:
                raise RegistryConcurrentUpdateError(registry_id)
            if validate is not None:
                validate(RegistryEntry.from_dict(base_snapshot))
            entry = RegistryEntry.from_dict(base_snapshot)
            entry.metadata = new_metadata
            entry.updated_at = utc_now_iso()
            if actor is not None:
                entry.last_actor = actor
            self._put_unlocked(entry)
            committed = entry.to_dict()
            if scoped_key:
                self._command_receipts[scoped_key] = {
                    "command_key": command_key,
                    "receipt_key": scoped_key,
                    "registry_id": registry_id,
                    "request_digest": request_digest,
                    "new_metadata": new_metadata,
                    "expected_metadata": base_snapshot.get("metadata"),
                    "committed_entry": committed,
                    "committed_at": committed.get("updated_at") or utc_now_iso(),
                }
            return RegistryEntry.from_dict(committed), False


# Module-level singleton for API layer to share
_default_store: Optional["RegistryStore"] = None
_store_lock = threading.Lock()


def build_registry_store():
    """Select the registry storage backend from environment configuration.

    ``REGISTRY_STORE_BACKEND=postgres`` selects the durable production owner
    store (``pg_store.PostgresRegistryStore``). ``REGISTRY_STORE_BACKEND=memory``
    is an explicit, documented test/local-dev opt-in into the in-memory test
    double (see ``conftest.py``, which sets it for this whole package's unit
    test run) — never a silent fallback when nothing was configured at all.

    Reviewer finding 7 (gen-8 review): an earlier revision of this function
    defaulted an unset ``REGISTRY_STORE_BACKEND`` to memory in "dev posture"
    (no enforced ``PANTHEON_ENV``/``PANTHEON_PERSISTENCE_POSTURE``) — but that
    let the actual mounted app silently serve real writes against an
    in-memory store with zero configuration at all, which is exactly the
    "missing-config fallback" architecture-resumption-sa-sd.md §3.1 forbids:
    "memory is explicitly injected test-only, never missing-config/
    connection/schema fallback". ``REGISTRY_STORE_BACKEND`` must now always
    be explicitly set to ``memory`` or ``postgres`` — dev/test callers opt in
    to memory the same explicit way ``conftest.py`` already does, they do not
    rely on an implicit default.

    Backend selection additionally fails closed the same way
    ``services.foundation.persistence_posture`` already does for every other
    service: in an enforced posture (``PANTHEON_ENV``/``PANTHEON_PERSISTENCE_POSTURE``
    in {stage, staging, prod, production, ...}), ``REGISTRY_STORE_BACKEND``
    must resolve to ``postgres`` or this raises ``RuntimeError`` before a
    request is ever served — an explicit ``postgres`` selection with no
    reachable database then fails closed a second time inside
    ``build_postgres_registry_store()`` instead of silently downgrading here.
    """
    from services.foundation.persistence_posture import require_persistence_posture

    require_persistence_posture(
        "registry",
        backend_env_vars={"REGISTRY_STORE_BACKEND": "memory"},
        require_object_store=False,
    )

    backend_raw = os.getenv("REGISTRY_STORE_BACKEND")
    if backend_raw is None or not backend_raw.strip():
        raise RuntimeError(
            "REGISTRY_STORE_BACKEND is not set. The registry never silently defaults to "
            "the in-memory test double — set REGISTRY_STORE_BACKEND=memory to explicitly "
            "opt into it (tests/local dev only; see conftest.py) or "
            "REGISTRY_STORE_BACKEND=postgres for a durable deployment."
        )
    backend = backend_raw.strip().lower()
    if backend == "memory":
        # A configured Postgres DSN is a strong signal the deployer intended
        # the durable backend; silently returning the in-memory test double
        # in that case would mean every write vanishes on process exit while
        # looking identical to a working deployment. Fail closed instead of
        # guessing (architecture-resumption-sa-sd.md §3.1).
        if os.getenv("REGISTRY_STORE_DSN") or os.getenv("DATABASE_URL"):
            raise RuntimeError(
                "REGISTRY_STORE_DSN/DATABASE_URL is configured but REGISTRY_STORE_BACKEND="
                "memory; refusing to silently select the in-memory test double over an "
                "apparently-intended Postgres connection. Set REGISTRY_STORE_BACKEND=postgres."
            )
        return RegistryStore()
    if backend != "postgres":
        raise ValueError("REGISTRY_STORE_BACKEND must be memory or postgres")
    from .pg_store import build_postgres_registry_store

    return build_postgres_registry_store()


def get_store():
    global _default_store
    with _store_lock:
        if _default_store is None:
            _default_store = build_registry_store()
        return _default_store


def reset_store() -> None:
    """Reset the singleton — useful in tests."""
    global _default_store
    with _store_lock:
        _default_store = None
