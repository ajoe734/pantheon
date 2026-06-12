"""Archive SA/SD packets to the canonical Pantheon doc locations.

ASST-INTEG-005 — owned by Claude2.

Archive destinations:
  SA/SD bundles  →  docs/04/<slug>/
  Architecture   →  docs/02-architecture/  (only for explicit arch-level docs)
  UI specs       →  docs/05-ui/            (deferred to ASST-INTEG-008)
  Worker briefs  →  .orchestrator/task-briefs/

All written files include source citations in their frontmatter.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .dev_docs_models import (
    ArchiveLocations,
    DevDocPacket,
    ExecutionTask,
    RequirementCapture,
    SystemAnalysis,
    SystemDesign,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(text: str, max_len: int = 40) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in text.lower())
    return safe[:max_len].strip("_")


def _match_parent_permissions(path: Path, *, mode: int) -> None:
    try:
        parent_stat = path.parent.stat()
        os.chown(path, parent_stat.st_uid, parent_stat.st_gid)
    except OSError:
        pass
    try:
        path.chmod(mode)
    except OSError:
        pass


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _match_parent_permissions(path, mode=0o775)


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    _match_parent_permissions(path, mode=0o664)


def _source_citation_block(source_refs: list) -> str:
    if not source_refs:
        return ""
    lines = ["## Source Citations", ""]
    for ref in source_refs:
        if isinstance(ref, dict):
            source_id = ref.get("source_id") or ref.get("sourceId") or "unknown"
            href = ref.get("href", "")
            snapshot = ref.get("snapshot_at") or ref.get("snapshotAt") or ""
            note = ref.get("note", "")
        else:
            source_id = str(getattr(ref, "source_id", None) or "unknown")
            href = str(getattr(ref, "href", None) or "")
            snapshot = str(getattr(ref, "snapshot_at", None) or "")
            note = str(getattr(ref, "note", None) or "")
        line = f"- `{source_id}`"
        if href:
            line += f": {href}"
        if snapshot:
            line += f" (snapshot: {snapshot})"
        if note:
            line += f" — {note}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown serializers
# ---------------------------------------------------------------------------

def requirement_capture_to_md(capture: RequirementCapture) -> str:
    lines = [
        f"# Requirement Capture: {capture.problem[:80]}",
        "",
        f"- **Capture ID**: `{capture.capture_id}`",
        f"- **Conversation ID**: `{capture.conversation_id}`",
        f"- **Generated at**: {capture.generated_at}",
        "",
        "## Problem",
        "",
        capture.problem,
        "",
        "## Actors",
        "",
        *[f"- {a}" for a in (capture.actors or ["(none identified)"])],
        "",
        "## User Intent",
        "",
        capture.user_intent,
        "",
        "## Affected Modules",
        "",
        *[f"- {m}" for m in (capture.affected_modules or ["(none specified)"])],
        "",
        "## Constraints",
        "",
        *[f"- {c}" for c in (capture.constraints or ["(none specified)"])],
        "",
        "## Open Questions",
        "",
        *[f"- {q}" for q in (capture.open_questions or ["None"])],
        "",
        "## Source Turn References",
        "",
    ]
    for ref in (capture.source_turn_refs or []):
        lines.append(f"- turn `{ref.turn_id}` ({ref.role}): {ref.excerpt[:120]}")
    lines += ["", _source_citation_block(capture.source_refs or [])]
    return "\n".join(lines)


def system_analysis_to_md(sa: SystemAnalysis) -> str:
    lines = [
        f"# {sa.title}",
        "",
        f"- **SA ID**: `{sa.sa_id}`",
        f"- **Capture ID**: `{sa.capture_id}`",
        f"- **Generated at**: {sa.generated_at}",
        "",
        "## Current State",
        "",
        sa.current_state,
        "",
        "## Roles",
        "",
        *[f"- {r}" for r in (sa.roles or ["(none)"])],
        "",
        "## Flows",
        "",
        *[f"- {f}" for f in (sa.flows or ["(none)"])],
        "",
        "## Data",
        "",
        *[f"- {d}" for d in (sa.data or ["(none)"])],
        "",
        "## Risk",
        "",
        *[f"- {r}" for r in (sa.risk or ["(none)"])],
        "",
        "## Edge Cases",
        "",
        *[f"- {e}" for e in (sa.edge_cases or ["(none)"])],
        "",
        "## Acceptance Scenarios",
        "",
        *[f"- {a}" for a in (sa.acceptance_scenarios or ["(none)"])],
        "",
        _source_citation_block(sa.source_refs or []),
    ]
    return "\n".join(lines)


def system_design_to_md(sd: SystemDesign) -> str:
    lines = [
        f"# {sd.title}",
        "",
        f"- **SD ID**: `{sd.sd_id}`",
        f"- **SA ID**: `{sd.sa_id}`",
        f"- **Generated at**: {sd.generated_at}",
        "",
        "## Architecture",
        "",
        sd.architecture,
        "",
        "## API Contract",
        "",
        *[f"- {a}" for a in (sd.api_contract or ["(none)"])],
        "",
        "## DB / Migration",
        "",
        *[f"- {d}" for d in (sd.db_migration or ["(none)"])],
        "",
        "## UI Routes / Components",
        "",
        *[f"- {u}" for u in (sd.ui_routes or ["(none)"])],
        "",
        "## Tool / Action Contract",
        "",
        *[f"- {t}" for t in (sd.tool_action_contract or ["(none)"])],
        "",
        "## Tests",
        "",
        *[f"- {t}" for t in (sd.tests or ["(none)"])],
        "",
        "## Rollout",
        "",
        sd.rollout,
        "",
        "## Rollback",
        "",
        sd.rollback,
        "",
        _source_citation_block(sd.source_refs or []),
    ]
    return "\n".join(lines)


def task_brief_to_md(task: ExecutionTask, packet_id: str) -> str:
    lines = [
        f"# Task Brief: {task.task_id}",
        "",
        "This file was generated by the SA/SD generator (ASST-INTEG-005).",
        f"Source packet: `{packet_id}`",
        "",
        "## Task",
        "",
        f"- **Title**: {task.title}",
        f"- **Owner**: {task.owner}",
        f"- **Reviewer**: {task.reviewer}",
    ]
    if task.phase:
        lines.append(f"- **Phase**: {task.phase}")
    lines += [
        "",
        "## Summary",
        "",
        task.summary,
        "",
        "## Dependencies",
        "",
        *[f"- {d}" for d in (task.depends_on or ["(none)"])],
        "",
        "## Artifacts",
        "",
        *[f"- {a}" for a in (task.artifacts or ["(none)"])],
        "",
        "## Acceptance",
        "",
        *[f"- {a}" for a in (task.acceptance or ["(none)"])],
        "",
        _source_citation_block(task.source_refs or []),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Archive writer
# ---------------------------------------------------------------------------

def archive_packet(
    packet: DevDocPacket,
    *,
    repo_root: str,
) -> ArchiveLocations:
    """Write packet artifacts to the canonical doc locations and return paths."""
    root = Path(repo_root)
    slug = _slug(packet.requirement_capture.problem)
    docs_dir = root / "docs"
    docs04_dir = docs_dir / "04"
    _ensure_dir(docs_dir)
    _ensure_dir(docs04_dir)
    bundle_dir = docs04_dir / f"sa_sd_{packet.packet_id}_{slug}"
    _ensure_dir(bundle_dir)

    # Requirement capture
    req_path = bundle_dir / "requirement_capture.md"
    _write_text(req_path, requirement_capture_to_md(packet.requirement_capture))

    # System analysis
    sa_path = bundle_dir / "system_analysis.md"
    _write_text(sa_path, system_analysis_to_md(packet.system_analysis))

    # System design
    sd_path = bundle_dir / "system_design.md"
    _write_text(sd_path, system_design_to_md(packet.system_design))

    # Machine-readable packet
    packet_json_path = bundle_dir / "dev_doc_packet.json"
    _write_text(packet_json_path, packet.model_dump_json(indent=2, by_alias=False))

    # Task briefs
    orchestrator_dir = root / ".orchestrator"
    briefs_dir = orchestrator_dir / "task-briefs"
    _ensure_dir(orchestrator_dir)
    _ensure_dir(briefs_dir)
    brief_paths: list[str] = []
    for task in packet.execution_tasks:
        safe_id = task.task_id.lower().replace("_", "-")
        brief_file = briefs_dir / f"{safe_id}.md"
        _write_text(brief_file, task_brief_to_md(task, packet.packet_id))
        brief_paths.append(str(brief_file.relative_to(root)))

    # Relative paths returned for callers
    return ArchiveLocations(
        requirementCapture=str(req_path.relative_to(root)),
        systemAnalysis=str(sa_path.relative_to(root)),
        systemDesign=str(sd_path.relative_to(root)),
        taskBriefs=brief_paths,
    )


def infer_repo_root(anchor: Optional[str] = None) -> str:
    """Walk up from anchor (or this file) to find the repo root (contains ai-status.json)."""
    start = Path(anchor) if anchor else Path(__file__)
    candidate = start if start.is_dir() else start.parent
    for _ in range(12):
        if (candidate / "ai-status.json").exists():
            return str(candidate)
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    env_root = os.environ.get("PANTHEON_STATUS_ROOT")
    if env_root and Path(env_root, "ai-status.json").exists():
        return env_root
    return str(start if start.is_dir() else start.parent)
