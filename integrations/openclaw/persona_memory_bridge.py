"""Canonical Memory Plane to OpenClaw workspace materialization.

OpenClaw workspace files are a bounded prompt cache. The canonical truth stays in
the Pantheon Memory Plane; workspace files must carry source ids and must not be
used as an unaudited direct-write path back into memory.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


DEFAULT_MEMORY_QUERY = "recent lessons, preferences, risks, and institutional context for this persona"
DEFAULT_MEMORY_SCOPE = "both"
DEFAULT_MEMORY_LIMIT = 8
MEMORY_CONTEXT_SCHEMA_VERSION = "openclaw_memory_context.v1"
WRITEBACK_CANDIDATE_SCHEMA_VERSION = "openclaw_memory_writeback_candidate.v1"
CANONICAL_WRITEBACK_ENDPOINT = "POST /api/memory/writebacks/persona"


@dataclass(frozen=True)
class MemoryMaterializationResult:
    persona_id: str
    workspace: str
    generated_at: str
    context_path: str
    markdown_path: str
    hit_count: int
    source_refs: List[Dict[str, str]] = field(default_factory=list)
    rejected_hits: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "workspace": self.workspace,
            "generated_at": self.generated_at,
            "context_path": self.context_path,
            "markdown_path": self.markdown_path,
            "hit_count": self.hit_count,
            "source_refs": list(self.source_refs),
            "rejected_hits": list(self.rejected_hits),
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fetch_canonical_memory(
    memory_api_url: str,
    *,
    persona_id: str,
    actor_id: str,
    actor_roles: Iterable[str],
    session_id: str,
    scope: str = DEFAULT_MEMORY_SCOPE,
    query: str = DEFAULT_MEMORY_QUERY,
    limit: int = DEFAULT_MEMORY_LIMIT,
    timeout_seconds: float = 5.0,
    auth_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Call the canonical Memory Plane retrieval facade."""
    base = memory_api_url.rstrip("/")
    params: Dict[str, Any] = {
        "actor_id": actor_id,
        "actor_roles": [str(role) for role in actor_roles],
        "session_id": session_id,
        "persona_id": persona_id,
        "scope": scope,
        "query": query,
        "limit": int(limit),
    }
    url = f"{base}/api/memory/retrieve?{urllib.parse.urlencode(params, doseq=True)}"
    headers = {"Accept": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def materialize_persona_memory_from_api(
    *,
    memory_api_url: str,
    persona_id: str,
    workspace: str,
    actor_id: str = "openclaw-persona-memory-bridge",
    actor_roles: Iterable[str] = ("operator",),
    session_id: Optional[str] = None,
    scope: str = DEFAULT_MEMORY_SCOPE,
    query: str = DEFAULT_MEMORY_QUERY,
    limit: int = DEFAULT_MEMORY_LIMIT,
    timeout_seconds: float = 5.0,
    auth_token: Optional[str] = None,
    generated_at: Optional[str] = None,
) -> MemoryMaterializationResult:
    payload = fetch_canonical_memory(
        memory_api_url,
        persona_id=persona_id,
        actor_id=actor_id,
        actor_roles=actor_roles,
        session_id=session_id or f"openclaw-memory-sync-{persona_id}",
        scope=scope,
        query=query,
        limit=limit,
        timeout_seconds=timeout_seconds,
        auth_token=auth_token,
    )
    return materialize_openclaw_memory_context(
        persona_id=persona_id,
        workspace=workspace,
        retrieval_payload=payload,
        query=query,
        scope=scope,
        limit=limit,
        generated_at=generated_at,
    )


def materialize_openclaw_memory_context(
    *,
    persona_id: str,
    workspace: str,
    retrieval_payload: Mapping[str, Any],
    query: str = DEFAULT_MEMORY_QUERY,
    scope: str = DEFAULT_MEMORY_SCOPE,
    limit: int = DEFAULT_MEMORY_LIMIT,
    generated_at: Optional[str] = None,
) -> MemoryMaterializationResult:
    """Render canonical retrieval hits into OpenClaw workspace cache files."""
    generated = generated_at or utc_now()
    workspace_path = Path(workspace)
    memory_dir = workspace_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    hits, rejected = normalize_retrieval_hits(retrieval_payload, persona_id=persona_id, max_hits=limit)
    context = {
        "schema_version": MEMORY_CONTEXT_SCHEMA_VERSION,
        "persona_id": persona_id,
        "source": "canonical_memory_plane",
        "generated_at": generated,
        "retrieval": {
            "scope": scope,
            "query": query,
            "limit": int(limit),
            "count": len(hits),
        },
        "mutation_policy": {
            "workspace_is_cache": True,
            "canonical_write_endpoint": CANONICAL_WRITEBACK_ENDPOINT,
            "direct_session_writes": False,
            "writeback_flow": "stage_candidate_then_authorized_memory_service_writeback",
        },
        "hits": hits,
        "rejected_hits": rejected,
    }
    context_path = memory_dir / "context.json"
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown_path = workspace_path / "MEMORY.md"
    markdown_path.write_text(render_memory_markdown(context), encoding="utf-8")

    candidates_dir = memory_dir / "writeback-candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    (candidates_dir / "README.md").write_text(_writeback_readme(), encoding="utf-8")

    return MemoryMaterializationResult(
        persona_id=persona_id,
        workspace=str(workspace_path),
        generated_at=generated,
        context_path=str(context_path),
        markdown_path=str(markdown_path),
        hit_count=len(hits),
        source_refs=[{"type": hit["type"], "ref": hit["canonical_ref"]} for hit in hits],
        rejected_hits=rejected,
    )


def normalize_retrieval_hits(
    retrieval_payload: Mapping[str, Any],
    *,
    persona_id: str,
    max_hits: int = DEFAULT_MEMORY_LIMIT,
) -> tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    hits: List[Dict[str, Any]] = []
    rejected: List[Dict[str, str]] = []
    for raw_hit in list(retrieval_payload.get("hits") or [])[:max_hits]:
        if not isinstance(raw_hit, Mapping):
            continue
        hit_type = str(raw_hit.get("type") or "").strip()
        entry = raw_hit.get("entry") if isinstance(raw_hit.get("entry"), Mapping) else {}
        if hit_type == "persona":
            source_id = str(entry.get("memory_id") or "").strip()
            entry_persona = str(entry.get("persona_id") or "").strip()
            if entry_persona != persona_id:
                rejected.append({
                    "type": "persona",
                    "source_id": source_id or "unknown",
                    "reason": "persona_scope_mismatch",
                })
                continue
            normalized = _normalize_persona_hit(raw_hit, entry)
        elif hit_type == "institutional":
            normalized = _normalize_institutional_hit(raw_hit, entry)
        else:
            rejected.append({
                "type": hit_type or "unknown",
                "source_id": str(entry.get("memory_id") or entry.get("entry_id") or "unknown"),
                "reason": "unsupported_hit_type",
            })
            continue
        hits.append(normalized)
    return hits, rejected


def _normalize_persona_hit(raw_hit: Mapping[str, Any], entry: Mapping[str, Any]) -> Dict[str, Any]:
    content = entry.get("content") if isinstance(entry.get("content"), Mapping) else {}
    source_id = str(entry.get("memory_id") or "").strip()
    return {
        "type": "persona",
        "source_id": source_id,
        "canonical_ref": f"persona_memory:{source_id}",
        "relevance_score": float(raw_hit.get("relevance_score") or 0.0),
        "persona_id": str(entry.get("persona_id") or ""),
        "memory_type": str(entry.get("memory_type") or ""),
        "summary": str(content.get("summary") or ""),
        "tags": _string_list(content.get("tags")),
        "source_event_type": str(entry.get("source_event_type") or ""),
        "source_event_id": str(entry.get("source_event_id") or ""),
        "written_at": str(entry.get("written_at") or ""),
        "relevance_scope": str(entry.get("relevance_scope") or ""),
    }


def _normalize_institutional_hit(raw_hit: Mapping[str, Any], entry: Mapping[str, Any]) -> Dict[str, Any]:
    content = entry.get("content") if isinstance(entry.get("content"), Mapping) else {}
    source_id = str(entry.get("entry_id") or "").strip()
    return {
        "type": "institutional",
        "source_id": source_id,
        "canonical_ref": f"institutional_memory:{source_id}",
        "relevance_score": float(raw_hit.get("relevance_score") or 0.0),
        "knowledge_type": str(entry.get("knowledge_type") or ""),
        "headline": str(content.get("headline") or ""),
        "body": str(content.get("body") or ""),
        "tags": _string_list(content.get("tags")),
        "source_event_type": str(entry.get("source_event_type") or ""),
        "source_event_id": str(entry.get("source_event_id") or ""),
        "written_at": str(entry.get("written_at") or ""),
        "scope": str(entry.get("scope") or ""),
        "scope_filter": str(entry.get("scope_filter") or ""),
    }


def render_memory_markdown(context: Mapping[str, Any]) -> str:
    lines = [
        f"# OpenClaw Memory Context - {context.get('persona_id')}",
        "",
        f"Generated: {context.get('generated_at')}",
        "Source: Pantheon canonical Memory Plane.",
        "",
        "This file is a materialized cache, not the source of truth. Do not edit",
        "it to create durable memory. Stage writeback candidates under",
        "`memory/writeback-candidates/`; canonical writes must go through",
        "`POST /api/memory/writebacks/persona`.",
        "",
    ]
    persona_hits = [hit for hit in context.get("hits", []) if isinstance(hit, Mapping) and hit.get("type") == "persona"]
    institutional_hits = [
        hit for hit in context.get("hits", []) if isinstance(hit, Mapping) and hit.get("type") == "institutional"
    ]
    lines.extend(_render_section("Persona Memory", persona_hits))
    lines.extend(_render_section("Institutional Memory", institutional_hits))
    return "\n".join(lines).rstrip() + "\n"


def stage_openclaw_memory_writeback_candidate(
    *,
    workspace: str,
    persona_id: str,
    summary: str,
    source_event_id: str,
    memory_type: str = "episodic",
    source_event_type: str = "session_end",
    tags: Optional[Iterable[str]] = None,
    generated_at: Optional[str] = None,
    candidate_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Stage a candidate only; this never mutates canonical memory."""
    clean_summary = summary.strip()
    if not clean_summary:
        raise ValueError("summary is required for memory writeback candidate")
    generated = generated_at or utc_now()
    cid = candidate_id or f"openclaw-memory-candidate-{persona_id}-{_compact_timestamp(generated)}"
    candidates_dir = Path(workspace) / "memory" / "writeback-candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    candidate = {
        "schema_version": WRITEBACK_CANDIDATE_SCHEMA_VERSION,
        "candidate_id": cid,
        "status": "staged_for_governed_writeback",
        "persona_id": persona_id,
        "generated_at": generated,
        "canonical_write_endpoint": CANONICAL_WRITEBACK_ENDPOINT,
        "direct_session_writes": False,
        "required_write_authority": "persona-memory-svc",
        "payload": {
            "memory_id": cid,
            "persona_id": persona_id,
            "memory_type": memory_type,
            "content": {
                "summary": clean_summary,
                "tags": _string_list(tags),
            },
            "source_event_type": source_event_type,
            "source_event_id": source_event_id,
            "written_at": generated,
            "write_authority": "persona-memory-svc",
        },
    }
    path = candidates_dir / f"{cid}.json"
    path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**candidate, "path": str(path)}


def _render_section(title: str, hits: List[Mapping[str, Any]]) -> List[str]:
    lines = [f"## {title}", ""]
    if not hits:
        lines.extend(["No canonical entries materialized.", ""])
        return lines
    for hit in hits:
        if hit.get("type") == "persona":
            summary = hit.get("summary") or "(empty summary)"
        else:
            summary = hit.get("headline") or "(empty headline)"
        lines.append(
            f"- [{hit.get('canonical_ref')}] score={hit.get('relevance_score')} "
            f"written_at={hit.get('written_at')} source_event={hit.get('source_event_id')}: {summary}"
        )
    lines.append("")
    return lines


def _writeback_readme() -> str:
    return (
        "# OpenClaw Memory Writeback Candidates\n\n"
        "Files here are staged candidates only. They are not canonical memory. "
        "A Pantheon service with write authority must review and submit them to "
        "`POST /api/memory/writebacks/persona`.\n"
    )


def _string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Iterable):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _compact_timestamp(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum())

