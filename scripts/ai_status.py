#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATUS_FILE = ROOT / "ai-status.json"
LOG_FILE = ROOT / "ai-activity-log.jsonl"
CURRENT_WORK_FILE = ROOT / "current-work.md"
DOCS_SITE_DIR = ROOT / "docs-site"
PLANNING_STATE_FILE = ROOT / ".orchestrator" / "planning-state.json"
ORCHESTRATOR_STATE_FILE = ROOT / ".orchestrator" / "state.json"
APPROVAL_QUEUE_FILE = ROOT / ".orchestrator" / "approval-queue.json"
DASHBOARD_BUNDLE_FILE = ROOT / "dashboard-bundle.json"
PLANNING_README = "docs/02-architecture/consensus/phase1/README.md"
PLANNING_SESSION_FILE = "docs/02-architecture/consensus/phase1/planning-session.json"
PLANNING_CHECKLIST_FILE = "docs/02-architecture/consensus/phase1/pantheon-backend-completion-checklist.md"

KNOWN_AGENTS = {
    "Claude": {
        "capability_lane": ["execution", "control-plane", "governance-review"],
        "default_branch": "feat/claude-execution-control",
        "target_workload": 15,
    },
    "Gemini": {
        "capability_lane": ["gcp", "ci-cd", "runtime-packaging", "worker-ops"],
        "default_branch": "feat/gemini-research-runtime",
        "target_workload": 30,
    },
    "Codex": {
        "capability_lane": ["integration", "status-system", "schema", "acceptance"],
        "default_branch": "feat/codex-collab-system",
        "target_workload": 30,
    },
    "Qwen": {
        "capability_lane": ["integration", "schema", "acceptance", "code-agent"],
        "default_branch": "feat/qwen-code-agent",
        "target_workload": 15,
    },
    "Copilot": {
        "capability_lane": ["research-ingest", "external-search", "spec-review", "critique"],
        "default_branch": "feat/copilot-research-critique",
        "target_workload": 25,
    },
}

AGENT_ALIASES = {
    "qwen": "Qwen",
    "qwen coder": "Qwen",
    "qwen2.5-coder": "Qwen",
    "qwen3": "Qwen",
    "千問": "Qwen",
    "grok": "Copilot",
    "copilot": "Copilot",
    "copilot host": "Copilot",
    "copilot_host": "Copilot",
}

STATUS_LABELS = {
    "todo": "todo",
    "in_progress": "in_progress",
    "review": "review",
    "review_approved": "review_approved",
    "blocked": "blocked",
    "done": "done",
}

DEPENDENCY_DONE_STATUSES = {"done"}
EXTERNAL_TASK_PREFIXES = {"OC", "RS", "LP", "OSS", "SPIKE"}
TASK_TERMINAL_SUPERSEDED = "superseded"
FIRST_PROMPT_PRIORITY = [
    "AI_COLLABORATION_GUIDE.md",
    "current-work.md",
    "ai-status.json",
    "TARGET_ARCHITECTURE.md",
    "CANONICAL_DOCUMENT_MAP.md",
    "ROADMAP.md",
    "DEVELOPMENT_WORKBREAKDOWN.md",
]
OPTIONAL_CURRENT_WORK_REFERENCES = (
    (PLANNING_README, "Planning mode"),
    ("CANONICAL_DOCUMENT_MAP.md", "Canonical map"),
    ("DEVELOPMENT_WORKBREAKDOWN.md", "Full backlog"),
)


def default_canonical_document_layers() -> dict[str, list[str]]:
    return {
        "L0 Collaboration & State": [
            "AI_COLLABORATION_GUIDE.md",
            "ai-status.json",
            "ai-activity-log.jsonl",
            "current-work.md",
        ],
        "L1 Platform Architecture & Policy": [
            "TARGET_ARCHITECTURE.md",
            "OPENCLAW_RUNTIME_CONTRACT.md",
            "PERSONA_RUNTIME_MODEL.md",
            "BINDING_AND_DEPLOYMENT_SEMANTICS.md",
            "PAPER_CANARY_LIVE_POLICY.md",
            "ROLLBACK_AND_POSITION_SEMANTICS.md",
            "LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md",
            "EVOLUTION_REVIEW_AND_THRESHOLDS.md",
            "CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md",
            "KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md",
            "MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md",
            "TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md",
            "DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md",
            "EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md",
            "EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md",
            "BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md",
            "LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md",
        ],
        "L2 Planning & Execution": [
            PLANNING_README,
            PLANNING_SESSION_FILE,
            "CANONICAL_DOCUMENT_MAP.md",
            "ROADMAP.md",
            "DEVELOPMENT_WORKBREAKDOWN.md",
            "OSS_INTEGRATION_CHECKLIST.md",
        ],
        "L3 Supporting Design & Migration": [
            "CANONICAL_CONTRACT_MIGRATION_DECISION.md",
            "WORK_REBASELINE.md",
            "Pantheon_總索引版系統分析文件.md",
            "Pantheon_資料表_Schema_設計版.md",
            "Pantheon_API_Service_Contract_設計版.md",
        ],
    }


def flatten_canonical_document_layers(layers: dict[str, list[str]]) -> list[str]:
    flattened: list[str] = []
    for documents in layers.values():
        for document in documents:
            if document not in flattened:
                flattened.append(document)
    return flattened


def sync_canonical_document_metadata(state: dict[str, Any]) -> None:
    layers = state.get("canonical_document_layers")
    if not isinstance(layers, dict) or not layers:
        layers = default_canonical_document_layers()
    else:
        normalized_layers: dict[str, list[str]] = {}
        for key, value in layers.items():
            if isinstance(value, list):
                normalized_layers[str(key)] = [str(item) for item in value]
        if not normalized_layers:
            normalized_layers = default_canonical_document_layers()
        layers = normalized_layers
    state["canonical_document_layers"] = layers
    state["canonical_files"] = flatten_canonical_document_layers(layers)


def canonical_file_set(state: dict[str, Any]) -> set[str]:
    sync_canonical_document_metadata(state)
    return {
        str(item)
        for item in state.get("canonical_files", [])
        if str(item).strip()
    }


def canonical_tier_labels(state: dict[str, Any]) -> list[str]:
    sync_canonical_document_metadata(state)
    layers = state.get("canonical_document_layers", {})
    return [f"`{name}`" for name in layers]


def human_join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def build_onboarding_prompt(state: dict[str, Any]) -> str:
    canonical_files = canonical_file_set(state)
    prompt_files = [item for item in FIRST_PROMPT_PRIORITY if item in canonical_files]
    if not prompt_files:
        prompt_files = FIRST_PROMPT_PRIORITY[:3]

    parts = [f"Read {human_join(prompt_files)} first."]
    if "ai-activity-log.jsonl" in canonical_files:
        parts.append("Use ai-activity-log.jsonl when you need recent history.")
    planning_state = load_planning_state()
    if planning_state and planning_state.get("status") in {"active", "human_required"}:
        planning_files = [item for item in (PLANNING_README, PLANNING_SESSION_FILE, PLANNING_CHECKLIST_FILE) if item]
        if planning_files:
            parts.append(
                f"Discussion planning is {planning_state['status']}; read {human_join(planning_files)} before implementation work."
            )
    parts.append("Treat generated views as derived from machine-readable state.")
    parts.append("Follow the canonical lifecycle todo -> in_progress -> review -> review_approved -> done.")
    parts.append("Use scripts/ai-status.sh for every state change.")
    return " ".join(parts)


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_agent_name(name: str | None) -> str:
    if name is None:
        return ""
    trimmed = str(name).strip()
    if not trimmed:
        return ""
    canonical_by_lower = {agent.lower(): agent for agent in KNOWN_AGENTS}
    lowered = trimmed.lower()
    if lowered in canonical_by_lower:
        return canonical_by_lower[lowered]
    alias_target = AGENT_ALIASES.get(lowered)
    if alias_target:
        return alias_target
    return trimmed


def current_actor(default: str = "Codex") -> str:
    return canonical_agent_name(os.environ.get("AI_NAME", default))


def default_state() -> dict[str, Any]:
    timestamp = iso_now()
    canonical_layers = default_canonical_document_layers()
    return {
        "project": "pantheon",
        "sprint": "2026-04-09-canonical-adoption-platform-plan",
        "objective": (
            "Adopt the layered canonical document system, align architecture and planning truth, and publish the "
            "full Pantheon platform backlog without overwriting historical collaboration records."
        ),
        "updated_at": timestamp,
        "canonical_document_layers": canonical_layers,
        "canonical_files": flatten_canonical_document_layers(canonical_layers),
        "agents": [
            {
                "name": name,
                "capability_lane": meta["capability_lane"],
                "status": "idle",
                "current_task_ids": [],
                "branch": meta["default_branch"],
                "next": "",
                "last_update": None,
            }
            for name, meta in KNOWN_AGENTS.items()
        ],
        "tasks": [
            {
                "id": "OPS-001",
                "title": "Canonicalize collaboration rules",
                "phase": "Foundation",
                "owner": "Codex",
                "reviewer": "Claude",
                "status": "done",
                "depends_on": [],
                "artifacts": ["AI_COLLABORATION_GUIDE.md", "current-work.md"],
                "acceptance": [
                    "canonical rules defined",
                    "current-work reduced to sprint snapshot",
                ],
                "next": "Keep guide stable while downstream work starts",
                "last_update": timestamp,
            },
            {
                "id": "OPS-002",
                "title": "Build JSON status pipeline",
                "phase": "Foundation",
                "owner": "Codex",
                "reviewer": "Gemini",
                "status": "done",
                "depends_on": ["OPS-001"],
                "artifacts": ["ai-status.json", "ai-activity-log.jsonl", "scripts/ai-status.sh"],
                "acceptance": [
                    "state updates through script",
                    "history append-only",
                    "current-work regenerated from JSON state",
                ],
                "next": "Use script-driven updates only",
                "last_update": timestamp,
            },
            {
                "id": "OPS-003",
                "title": "Build collaboration dashboard",
                "phase": "Foundation",
                "owner": "Codex",
                "reviewer": "Claude",
                "status": "done",
                "depends_on": ["OPS-002"],
                "artifacts": ["docs-site/index.html", "docs-site/script.js", "docs-site/style.css"],
                "acceptance": [
                    "dashboard renders workload",
                    "dashboard renders board, blockers, handoffs, recent activity",
                ],
                "next": "Collect visual feedback and adjust layout if needed",
                "last_update": timestamp,
            },
            {
                "id": "P1-001",
                "title": "Define SignalStoreClient contract",
                "phase": "Phase 1",
                "owner": "Codex",
                "reviewer": "Gemini",
                "status": "todo",
                "depends_on": [],
                "artifacts": ["services/signal-store/client.py"],
                "acceptance": [
                    "interface documented",
                    "example payload added",
                    "consumer assumptions listed",
                ],
                "next": "Lock interface for downstream work",
                "last_update": None,
            },
            {
                "id": "P2-001",
                "title": "Define signal JSON schema",
                "phase": "Phase 2",
                "owner": "Gemini",
                "reviewer": "Claude",
                "status": "todo",
                "depends_on": ["P1-001"],
                "artifacts": ["services/research/schema.json"],
                "acceptance": [
                    "payload keys documented",
                    "worker contract references same schema",
                ],
                "next": "Publish worker payload contract",
                "last_update": None,
            },
            {
                "id": "P3-001",
                "title": "Wire LEAN runtime signal consumer",
                "phase": "Phase 3",
                "owner": "Claude",
                "reviewer": "Gemini",
                "status": "todo",
                "depends_on": ["P1-001", "P2-001"],
                "artifacts": ["services/execution/lean-runtime/"],
                "acceptance": [
                    "runtime can consume signal payload",
                    "broker config edge documented",
                ],
                "next": "Connect signal intake to execution plane",
                "last_update": None,
            },
            {
                "id": "P4-001",
                "title": "Draft control-plane routing contract",
                "phase": "Phase 4",
                "owner": "Claude",
                "reviewer": "Codex",
                "status": "todo",
                "depends_on": ["P2-001"],
                "artifacts": ["services/control-plane/router/"],
                "acceptance": [
                    "router contract documented",
                    "monitoring handoff documented",
                ],
                "next": "Define router and monitoring handoff",
                "last_update": None,
            },
        ],
        "handoffs": [],
        "blockers": [],
        "workload": {name: meta["target_workload"] for name, meta in KNOWN_AGENTS.items()},
    }


def load_state() -> dict[str, Any]:
    if not STATUS_FILE.exists() or STATUS_FILE.read_text(encoding="utf-8").strip() == "":
        return default_state()
    state = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    sync_canonical_document_metadata(state)
    normalize_state_agents(state)
    return state


def load_logs() -> list[dict[str, Any]]:
    if not LOG_FILE.exists():
        return []
    logs: list[dict[str, Any]] = []
    for line_no, line in enumerate(LOG_FILE.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            logs.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(
                f"Warning: skipping malformed ai-activity-log.jsonl line {line_no}: {exc}",
                file=sys.stderr,
            )
    return logs


def load_planning_state() -> dict[str, Any] | None:
    if not PLANNING_STATE_FILE.exists():
        return None
    try:
        payload = json.loads(PLANNING_STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return deepcopy(default)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return deepcopy(default)


def save_state(state: dict[str, Any]) -> None:
    STATUS_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_log(entry: dict[str, Any]) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def ensure_agent(name: str) -> dict[str, Any]:
    canonical = canonical_agent_name(name)
    if canonical not in KNOWN_AGENTS:
        raise SystemExit(f"Unknown agent: {name}")
    return KNOWN_AGENTS[canonical]


def get_agent(state: dict[str, Any], name: str) -> dict[str, Any]:
    name = canonical_agent_name(name)
    ensure_agent(name)
    for agent in state["agents"]:
        if agent["name"] == name:
            return agent
    meta = KNOWN_AGENTS[name]
    agent = {
        "name": name,
        "capability_lane": meta["capability_lane"],
        "status": "idle",
        "current_task_ids": [],
        "branch": meta["default_branch"],
        "next": "",
        "last_update": None,
    }
    state["agents"].append(agent)
    return agent


def get_task(state: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for task in state["tasks"]:
        if task["id"] == task_id:
            return task
    return None


def parse_csv_env(name: str) -> list[str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_delimited_env(name: str, delimiter: str = "||") -> list[str]:
    value = os.environ.get(name, "").strip()
    if not value:
        return []
    return [item.strip() for item in value.split(delimiter) if item.strip()]


def parse_json_env(name: str) -> dict[str, Any]:
    value = os.environ.get(name, "").strip()
    if not value:
        return {}
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise SystemExit(f"{name} must decode to a JSON object")
    return payload


def parse_bool_env(name: str) -> bool | None:
    value = os.environ.get(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{name} must be a boolean-like string")


def task_metadata_from_env() -> dict[str, Any]:
    metadata = parse_json_env("TASK_METADATA_JSON")
    explicit_fields = {
        "task_class": os.environ.get("TASK_CLASS", "").strip() or None,
        "helper_parent": os.environ.get("TASK_HELPER_PARENT", "").strip() or None,
        "helper_kind": os.environ.get("TASK_HELPER_KIND", "").strip() or None,
        "auto_created_by": os.environ.get("TASK_AUTO_CREATED_BY", "").strip() or None,
    }
    for key, value in explicit_fields.items():
        if value is not None:
            metadata[key] = value

    for env_name, field_name in (
        ("TASK_AUTO_GENERATED", "auto_generated"),
        ("TASK_MUTATES_CANONICAL", "mutates_canonical"),
    ):
        parsed = parse_bool_env(env_name)
        if parsed is not None:
            metadata[field_name] = parsed

    return metadata


def dependency_is_satisfied(task_map: dict[str, dict[str, Any]], dep_id: str) -> bool:
    dependency = task_map.get(dep_id)
    if dependency is None:
        return True
    return dependency.get("status") in DEPENDENCY_DONE_STATUSES


def ensure_review_finalize_handoff(
    state: dict[str, Any],
    task: dict[str, Any],
    *,
    from_agent: str,
    timestamp: str,
    message: str | None = None,
) -> None:
    owner = canonical_agent_name(task.get("owner"))
    if not owner:
        return
    pending_owner_handoff = next(
        (
            handoff
            for handoff in state.get("handoffs", [])
            if handoff.get("task_id") == task.get("id")
            and handoff.get("to") == owner
            and handoff.get("status") != "done"
        ),
        None,
    )
    if pending_owner_handoff:
        if message:
            pending_owner_handoff["message"] = message
        return

    state.setdefault("handoffs", []).append(
        {
            "task_id": task.get("id"),
            "from": canonical_agent_name(from_agent),
            "to": owner,
            "message": message or "Review approved. Owner must finalize this task to move it from review_approved to done.",
            "status": "pending",
            "created_at": timestamp,
        }
    )


def validate_state(state: dict[str, Any]) -> None:
    sync_canonical_document_metadata(state)
    normalize_state_agents(state)
    for task in state["tasks"]:
        ensure_agent(task["owner"])
        ensure_agent(task["reviewer"])
        if task["owner"] == task["reviewer"]:
            raise SystemExit(f"Task {task['id']} has identical owner and reviewer")
        if task["status"] == "blocked" and not task.get("waiting_for"):
            raise SystemExit(f"Blocked task {task['id']} is missing waiting_for")

    for blocker in state.get("blockers", []):
        ensure_agent(blocker["owner"])
        ensure_agent(blocker["waiting_for"])

    for handoff in state.get("handoffs", []):
        ensure_agent(handoff["from"])
        ensure_agent(handoff["to"])


def normalize_state_agents(state: dict[str, Any]) -> None:
    for task in state.get("tasks", []):
        task["owner"] = canonical_agent_name(task.get("owner"))
        task["reviewer"] = canonical_agent_name(task.get("reviewer"))
        if task.get("waiting_for"):
            task["waiting_for"] = canonical_agent_name(task.get("waiting_for"))

    for blocker in state.get("blockers", []):
        blocker["owner"] = canonical_agent_name(blocker.get("owner"))
        blocker["waiting_for"] = canonical_agent_name(blocker.get("waiting_for"))

    for handoff in state.get("handoffs", []):
        handoff["from"] = canonical_agent_name(handoff.get("from"))
        handoff["to"] = canonical_agent_name(handoff.get("to"))

    for agent in state.get("agents", []):
        agent["name"] = canonical_agent_name(agent.get("name"))


def recompute_agents(state: dict[str, Any]) -> None:
    deduped_agents: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for agent in state.get("agents", []):
        name = agent.get("name")
        if not name or name in seen_names:
            continue
        deduped_agents.append(agent)
        seen_names.add(name)
    state["agents"] = deduped_agents

    by_owner: dict[str, list[dict[str, Any]]] = {name: [] for name in KNOWN_AGENTS}
    task_map = {task["id"]: task for task in state["tasks"]}
    for task in state["tasks"]:
        by_owner.setdefault(task["owner"], []).append(task)

    for name in KNOWN_AGENTS:
        agent = get_agent(state, name)
        owned = by_owner.get(name, [])
        active = [task for task in owned if task["status"] in {"in_progress", "review", "blocked"}]
        approved = [task for task in owned if task["status"] == "review_approved"]
        queued = [task for task in owned if task["status"] == "todo"]
        ready = [
            task
            for task in queued
            if all(dependency_is_satisfied(task_map, dep_id) for dep_id in task.get("depends_on", []))
        ]
        waiting = [task for task in queued if task not in ready]

        if any(task["status"] == "blocked" for task in active):
            agent["status"] = "blocked"
            agent["current_task_ids"] = [task["id"] for task in active]
        elif any(task["status"] == "in_progress" for task in active):
            agent["status"] = "working"
            agent["current_task_ids"] = [task["id"] for task in active]
        elif any(task["status"] == "review" for task in active):
            agent["status"] = "reviewing"
            agent["current_task_ids"] = [task["id"] for task in active]
        elif approved:
            agent["status"] = "finalize"
            agent["current_task_ids"] = [task["id"] for task in approved]
        elif ready:
            agent["status"] = "ready"
            agent["current_task_ids"] = [task["id"] for task in ready]
        elif waiting:
            agent["status"] = "waiting"
            agent["current_task_ids"] = [task["id"] for task in waiting[:3]]
        else:
            agent["status"] = "idle"
            agent["current_task_ids"] = []

        if active:
            latest = sorted(
                active,
                key=lambda task: task.get("last_update") or "",
                reverse=True,
            )[0]
            agent["next"] = latest.get("next", "")
            agent["last_update"] = latest.get("last_update")
        elif approved:
            agent["next"] = approved[0].get("next", "")
            agent["last_update"] = approved[0].get("last_update")
        elif ready:
            agent["next"] = ready[0].get("next", "")
            agent["last_update"] = ready[0].get("last_update")
        elif waiting:
            agent["next"] = waiting[0].get("next", "")
            if not agent.get("last_update"):
                agent["last_update"] = waiting[0].get("last_update")
        elif queued:
            agent["next"] = queued[0].get("next", "")
        elif not agent.get("last_update"):
            agent["last_update"] = None


def recompute_workload(state: dict[str, Any]) -> None:
    summary: dict[str, dict[str, int]] = {}
    for name in KNOWN_AGENTS:
        summary[name] = {
            "total": 0,
            "active": 0,
            "blocked": 0,
            "done": 0,
            "review": 0,
            "review_approved": 0,
            "todo": 0,
        }

    for task in state["tasks"]:
        owner = task["owner"]
        bucket = summary[owner]
        bucket["total"] += 1
        bucket[task["status"] if task["status"] in bucket else "todo"] += 1
        if task["status"] in {"in_progress", "review", "blocked"}:
            bucket["active"] += 1

    state["workload"] = {name: KNOWN_AGENTS[name]["target_workload"] for name in KNOWN_AGENTS}
    state["workload_summary"] = summary


def task_delivery_layer(task: dict[str, Any]) -> str:
    explicit = str(task.get("delivery_layer") or "").strip().lower()
    if explicit in {"primary", "project"}:
        return "primary"
    if explicit in {"external", "upstream"}:
        return "external"
    prefix = task["id"].split("-", 1)[0]
    if prefix in EXTERNAL_TASK_PREFIXES:
        return "external"
    return "primary"


def display_task_title(task: dict[str, Any]) -> str:
    title = str(task.get("title") or "")
    if task.get("task_class") != "sidecar":
        return title

    markers = ["[Sidecar]"]
    if task.get("auto_generated"):
        markers.append("[Auto]")
    if task.get("helper_parent"):
        markers.append(f"[Parent {task['helper_parent']}]")
    marker_text = " ".join(markers)
    if title:
        return f"{marker_text} {title}"
    return marker_text


def write_current_work(state: dict[str, Any], logs: list[dict[str, Any]]) -> None:
    def cell(value: Any) -> str:
        text = "-" if value is None or value == "" else str(value)
        return text.replace("|", "\\|").replace("\n", "<br>")

    def append_layer_table(lines: list[str], tasks: list[dict[str, Any]]) -> None:
        lines.extend(
            [
                "| ID | Phase | Task | Owner | Status | Depends On | 中文說明 |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        if not tasks:
            lines.append("| _(none)_ | - | - | - | - | - | - |")
            return
        for task in tasks:
            depends = ", ".join(f"`{item}`" for item in task.get("depends_on", [])) or "-"
            lines.append(
                "| `{id}` | {phase} | {title} | {owner} | {status} | {depends} | {summary} |".format(
                    id=cell(task["id"]),
                    phase=cell(task["phase"]),
                    title=cell(display_task_title(task)),
                    owner=cell(task["owner"]),
                    status=cell(task["status"]),
                    depends=cell(depends),
                    summary=cell(task.get("summary_zh") or "-"),
                )
            )

    current_logs = logs[-20:]
    canonical_files = canonical_file_set(state)
    tier_labels = canonical_tier_labels(state)
    planning_state = load_planning_state()
    active_tasks = [task for task in state["tasks"] if task.get("status") != "done"]
    primary_tasks = [task for task in active_tasks if task_delivery_layer(task) == "primary"]
    external_tasks = [task for task in active_tasks if task_delivery_layer(task) == "external"]
    current_sprint_lines = [
        f"- Sprint: `{state['sprint']}`",
        "- Canonical files: " + ", ".join(f"`{item}`" for item in state["canonical_files"]),
        "- Canonical tiers: " + (", ".join(tier_labels) if tier_labels else "-"),
    ]
    for path, label in OPTIONAL_CURRENT_WORK_REFERENCES:
        if path in canonical_files:
            current_sprint_lines.append(f"- {label}: `{path}`")
    current_sprint_lines.append("- Dashboard: `docs-site/index.html`")

    lines: list[str] = [
        "# Current Work",
        "",
        "This file is generated from `ai-status.json` and `ai-activity-log.jsonl`.",
        "Do not treat this file as the machine-readable source of truth.",
        "",
        f"Last updated: {state['updated_at']}",
        "",
        "## Objective",
        "",
        state["objective"],
        "",
        "## Current Sprint",
        "",
        *current_sprint_lines,
        "",
    ]

    if planning_state:
        gate = planning_state.get("switch_gate", {})
        lines.extend(
            [
                "## Discussion Planning",
                "",
                f"- Session: `{planning_state.get('session_id', '-')}`",
                f"- Status: `{planning_state.get('status', '-')}`",
                f"- Baton owner: `{planning_state.get('baton_owner', '-')}`",
                f"- Current round: `{planning_state.get('current_round', 0)}`",
                f"- Consensus: `{planning_state.get('consensus_status', '-')}`",
                f"- Human gate: `{planning_state.get('human_gate_status', '-')}`",
                f"- Ready for human: `{gate.get('ready_for_human', False)}`",
                f"- Ready to materialize execution: `{gate.get('ready_to_materialize', False)}`",
                "",
            ]
        )

    lines.extend(
        [
        "## Active Slices",
        "",
        ]
    )

    for agent in state["agents"]:
        next_text = agent.get("next") or "No active assignment"
        lines.append(f"- `{agent['name']}`: {', '.join(agent['capability_lane'])}; next: {next_text}")

    lines.extend(
        [
            "",
            "## Delivery Layers",
            "",
            "### Primary Project Work",
            "",
        ]
    )
    append_layer_table(lines, primary_tasks)
    lines.extend(
        [
            "",
            "### External / Upstream Integration Work",
            "",
        ]
    )
    append_layer_table(lines, external_tasks)

    lines.extend(["", "## Task Board", "", "| ID | Phase | Task | 中文說明 | Owner | Reviewer | Status | Depends On | Last Update | Next |", "|---|---|---|---|---|---|---|---|---|---|"])

    for task in state["tasks"]:
        depends = ", ".join(f"`{item}`" for item in task.get("depends_on", [])) or "-"
        lines.append(
            "| `{id}` | {phase} | {title} | {summary} | {owner} | {reviewer} | {status} | {depends} | {last_update} | {next} |".format(
                id=cell(task["id"]),
                phase=cell(task["phase"]),
                title=cell(display_task_title(task)),
                summary=cell(task.get("summary_zh") or "-"),
                owner=cell(task["owner"]),
                reviewer=cell(task["reviewer"]),
                status=cell(task["status"]),
                depends=cell(depends),
                last_update=cell(task.get("last_update") or "-"),
                next=cell(task.get("next") or "-"),
            )
        )

    lines.extend(["", "## Handoff Queue", "", "| Task | From | To | Message | Status | Created At |", "|---|---|---|---|---|---|"])
    pending_handoffs = [handoff for handoff in state.get("handoffs", []) if handoff.get("status") != "done"]
    if pending_handoffs:
        for handoff in pending_handoffs:
            lines.append(
                f"| `{handoff['task_id']}` | {handoff['from']} | {handoff['to']} | {handoff['message']} | {handoff['status']} | {handoff['created_at']} |"
            )
    else:
        lines.append("| _(none)_ | - | - | - | - | - |")

    lines.extend(["", "## Blockers", "", "| Task | Owner | Waiting For | Message | Status |", "|---|---|---|---|---|"])
    open_blockers = [blocker for blocker in state.get("blockers", []) if blocker.get("status") == "open"]
    if open_blockers:
        for blocker in open_blockers:
            lines.append(
                f"| `{blocker['task_id']}` | {blocker['owner']} | {blocker['waiting_for']} | {blocker['message']} | {blocker['status']} |"
            )
    else:
        lines.append("| _(none)_ | - | - | - | - |")

    lines.extend(["", "## Review Notes", "", "| Task | Reviewer | 修正重點 | Review File |", "|---|---|---|---|"])
    review_tasks = [task for task in state["tasks"] if task.get("review_notes_zh")]
    if review_tasks:
        for task in review_tasks:
            note_html = "<br>".join(task.get("review_notes_zh", []))
            lines.append(
                f"| `{task['id']}` | {cell(task['reviewer'])} | {cell(note_html)} | {cell(task.get('review_file') or '-')} |"
            )
    else:
        lines.append("| _(none)_ | - | - | - |")

    lines.extend(["", "## Latest Checkpoints", ""])
    if current_logs:
        for entry in current_logs:
            task_id = f" `{entry['task_id']}`" if entry.get("task_id") else ""
            lines.append(f"- {entry['ts']} {entry['agent']}:{task_id} {entry['message']}")
    else:
        lines.append("- No checkpoints yet.")

    CURRENT_WORK_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def normalize_worker_actor(worker: dict[str, Any]) -> str:
    for candidate in (worker.get("agent_id"), worker.get("target_agent"), worker.get("provider")):
        canonical = canonical_agent_name(candidate)
        if canonical:
            return canonical
        lowered = str(candidate or "").strip().lower()
        if lowered in {"grok", "copilot"}:
            return "Copilot"
    return str(worker.get("agent_id") or worker.get("provider") or "").strip()


def expected_task_actor(task: dict[str, Any]) -> str:
    if str(task.get("status") or "").lower() == "review":
        return canonical_agent_name(task.get("reviewer"))
    return canonical_agent_name(task.get("owner"))


def normalize_runtime_workers(state: dict[str, Any], orchestrator_state: dict[str, Any]) -> list[dict[str, Any]]:
    task_map = {task["id"]: task for task in state.get("tasks", [])}
    rows: list[dict[str, Any]] = []
    for run_id, worker in (orchestrator_state.get("workers", {}) or {}).items():
        task = task_map.get(worker.get("task_id"))
        task_status = str(task.get("status") or "") if task else None
        worker_status = str(worker.get("status") or "")
        if worker_status in {"superseded", "reassigned"}:
            bucket = "transition"
        elif task_status == "done" or worker_status in {"completed", "failed"}:
            bucket = "completed"
        elif worker_status in {"running", "started"}:
            bucket = "running"
        else:
            bucket = "pending"
        rows.append(
            {
                "run_id": run_id,
                "task_id": worker.get("task_id"),
                "queue_event_id": worker.get("queue_event_id"),
                "actor": normalize_worker_actor(worker),
                "provider": worker.get("provider"),
                "status": worker_status,
                "bucket": bucket,
                "task_status": task_status,
                "reason": worker.get("reason") or (worker.get("request_snapshot") or {}).get("reason"),
                "mode": worker.get("mode"),
                "last_event_at": worker.get("last_event_at"),
                "started_at": worker.get("started_at"),
                "last_error": worker.get("last_error"),
            }
        )
    rows.sort(key=lambda item: str(item.get("last_event_at") or ""), reverse=True)
    return rows


def normalize_runtime_queue(orchestrator_state: dict[str, Any]) -> list[dict[str, Any]]:
    queue_records = ((orchestrator_state.get("queue") or {}).get("events") or {})
    workers_by_event: dict[str, dict[str, Any]] = {}
    for run_id, worker in (orchestrator_state.get("workers", {}) or {}).items():
        queue_event_id = worker.get("queue_event_id")
        if queue_event_id:
            workers_by_event[str(queue_event_id)] = {"run_id": run_id, **worker}
    rows: list[dict[str, Any]] = []
    for event_id, event in queue_records.items():
        linked_worker = workers_by_event.get(str(event_id), {})
        rows.append(
            {
                "id": event_id,
                "task_id": event.get("task_id") or linked_worker.get("task_id"),
                "status": event.get("status"),
                "agent": canonical_agent_name(event.get("target_display_name") or event.get("target_agent") or linked_worker.get("agent_id")),
                "provider": event.get("provider") or linked_worker.get("provider"),
                "reason": event.get("reason") or linked_worker.get("reason") or (linked_worker.get("request_snapshot") or {}).get("reason"),
                "run_id": event.get("run_id") or linked_worker.get("run_id"),
                "last_event_at": event.get("last_event_at") or event.get("processed_at") or event.get("last_attempt_at") or linked_worker.get("last_event_at"),
            }
        )
    rows.sort(key=lambda item: str(item.get("last_event_at") or ""), reverse=True)
    return rows


def mismatch_resolution_hint(item: dict[str, Any]) -> str:
    mismatch_type = str(item.get("type") or "")
    if mismatch_type == "worker_without_task":
        return "先檢查 dispatch/request snapshot 是否漏掉 task_id；如果是舊 worker，應重派成帶 task_id 的新 run。"
    if mismatch_type == "worker_task_missing":
        return "先確認 task 是否被移除或改名；若 task 已失效，應停掉 worker，否則重建對應 task。"
    if mismatch_type == "worker_assignment_mismatch":
        return "先對齊 owner/reviewer 與 runtime actor；若已改派，先把 task board assignment 寫回，再重新 dispatch。"
    if mismatch_type == "running_worker_on_todo":
        return "先把 task 狀態推成 in_progress；若 worker 是誤派，則回退 queue 或直接停掉該 run。"
    if mismatch_type == "running_worker_on_done":
        return "先確認這是不是殘留 worker；若 task 已確定 done，應停掉 worker 並清理 queue record。"
    if mismatch_type == "active_task_without_worker":
        return "要嘛重新 dispatch expected actor，要嘛把 task 狀態降回 todo/blocking truth，避免假 active。"
    if mismatch_type == "queue_started_without_worker":
        return "先檢查 queue record 是否卡在 started；如果 worker 已消失，重設 queue 或重新 dispatch。"
    if mismatch_type == "approval_missing_task":
        return "先清掉 stale approval，或先恢復 task board 中的 task，再進行批准。"
    return "先對齊 task board、queue、runtime 三者的真相，再決定是重派、回退，還是清理殘留記錄。"


def detect_truth_mismatches(
    state: dict[str, Any],
    workers: list[dict[str, Any]],
    queue_events: list[dict[str, Any]],
    approval_state: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    task_map = {task["id"]: task for task in state.get("tasks", [])}
    live_workers = [worker for worker in workers if worker.get("bucket") in {"running", "pending"}]
    live_workers_by_task: dict[str, list[dict[str, Any]]] = {}
    mismatches: list[dict[str, Any]] = []
    seen: set[str] = set()

    def push(payload: dict[str, Any]) -> None:
        key = str(payload.get("id") or f"{payload.get('type')}:{payload.get('task_id')}:{payload.get('worker_run_id')}:{payload.get('queue_event_id')}")
        if key in seen:
            return
        payload.setdefault("resolution_hint", mismatch_resolution_hint(payload))
        seen.add(key)
        mismatches.append(payload)

    for worker in live_workers:
        task_id = str(worker.get("task_id") or "").strip()
        if task_id:
            live_workers_by_task.setdefault(task_id, []).append(worker)
        else:
            push(
                {
                    "id": f"worker-without-task:{worker.get('run_id')}",
                    "type": "worker_without_task",
                    "severity": "medium",
                    "title": "Live worker 沒有綁到 task",
                    "summary": f"{worker.get('actor') or '-'} 的 worker 已在跑，但沒有 task_id。",
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )
            continue

        task = task_map.get(task_id)
        if task is None:
            push(
                {
                    "id": f"worker-task-missing:{worker.get('run_id')}",
                    "type": "worker_task_missing",
                    "severity": "high",
                    "title": "Live worker 指向不存在的 task",
                    "summary": f"{worker.get('actor') or '-'} 的 worker 綁到 {task_id}，但 task board 找不到這個 task。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )
            continue

        task_status = str(task.get("status") or "").lower()
        expected_actor = expected_task_actor(task)
        actual_actor = canonical_agent_name(worker.get("actor"))
        if expected_actor and actual_actor and expected_actor != actual_actor:
            push(
                {
                    "id": f"worker-assignment:{worker.get('run_id')}",
                    "type": "worker_assignment_mismatch",
                    "severity": "medium" if task_status == "review" else "high",
                    "title": "Live worker 與 task 指派對不上",
                    "summary": f"{task_id} 目前應由 {expected_actor} 接手，但 live worker 來自 {actual_actor}。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "expected_actor": expected_actor,
                    "actual_actor": actual_actor,
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )

        if worker.get("bucket") == "running" and task_status == "todo":
            push(
                {
                    "id": f"running-worker-on-todo:{worker.get('run_id')}",
                    "type": "running_worker_on_todo",
                    "severity": "medium",
                    "title": "Worker 已在跑，但 task 還是 todo",
                    "summary": f"{task_id} 有 live running worker，但 task status 仍是 todo。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )

        if worker.get("bucket") == "running" and task_status == "done":
            push(
                {
                    "id": f"running-worker-on-done:{worker.get('run_id')}",
                    "type": "running_worker_on_done",
                    "severity": "high",
                    "title": "Task 已完成，但 worker 仍在跑",
                    "summary": f"{task_id} 已是 done，但還有 live running worker。",
                    "task_id": task_id,
                    "worker_run_id": worker.get("run_id"),
                    "detected_at": worker.get("last_event_at") or worker.get("started_at"),
                }
            )

    for task in state.get("tasks", []):
        task_status = str(task.get("status") or "").lower()
        if task_status not in {"in_progress", "review"}:
            continue
        if live_workers_by_task.get(task["id"]):
            continue
        push(
            {
                "id": f"active-task-without-worker:{task['id']}",
                "type": "active_task_without_worker",
                "severity": "medium",
                "title": "Active task 沒有 live worker",
                "summary": f"{task['id']} 在 task board 上是 {task_status}，但目前沒有對應的 live worker。",
                "task_id": task["id"],
                "expected_actor": expected_task_actor(task),
                "detected_at": task.get("last_update"),
            }
        )

    live_queue_ids = {str(worker.get("queue_event_id") or "") for worker in live_workers if worker.get("queue_event_id")}
    for event in queue_events:
        event_status = str(event.get("status") or "").lower()
        if event_status not in {"started", "manual_pending"}:
            continue
        if str(event.get("id") or "") in live_queue_ids:
            continue
        push(
            {
                "id": f"queue-without-worker:{event.get('id')}",
                "type": "queue_started_without_worker",
                "severity": "medium",
                "title": "Queue record 已啟動，但找不到 live worker",
                "summary": f"{event.get('task_id') or event.get('id')} 的 queue record 已是 {event_status}，但 runtime 沒有對應 worker。",
                "task_id": event.get("task_id"),
                "queue_event_id": event.get("id"),
                "detected_at": event.get("last_event_at"),
            }
        )

    for approval in (approval_state.get("pending") or []):
        task_id = str(approval.get("task_id") or "").strip()
        if not task_id or task_id in task_map:
            continue
        push(
            {
                "id": f"approval-missing-task:{approval.get('id') or approval.get('approval_id') or task_id}",
                "type": "approval_missing_task",
                "severity": "medium",
                "title": "Approval queue 指向不存在的 task",
                "summary": f"待批准項目 {task_id} 已不在 task board 中。",
                "task_id": task_id,
                "detected_at": approval.get("created_at"),
            }
        )

    severity_order = {"high": 0, "medium": 1, "low": 2}
    mismatches.sort(
        key=lambda item: (
            severity_order.get(str(item.get("severity") or "medium"), 9),
            str(item.get("detected_at") or ""),
        )
    )
    return live_workers, mismatches


def build_dashboard_bundle(
    state: dict[str, Any],
    planning_state: dict[str, Any] | None,
    orchestrator_state: dict[str, Any] | None,
    approval_state: dict[str, Any] | None,
) -> dict[str, Any]:
    planning = planning_state or {}
    orchestrator = orchestrator_state or {}
    approvals = approval_state or {}
    task_map = {task["id"]: task for task in state.get("tasks", [])}
    workers = normalize_runtime_workers(state, orchestrator)
    queue_events = [
        event
        for event in normalize_runtime_queue(orchestrator)
        if str(event.get("status") or "").lower() not in {"completed", "failed"}
        and str(task_map.get(str(event.get("task_id") or ""), {}).get("status") or "").lower() != "done"
    ]
    live_workers, mismatches = detect_truth_mismatches(state, workers, queue_events, approvals)

    live_workers_by_task: dict[str, list[dict[str, Any]]] = {}
    for worker in live_workers:
        task_id = str(worker.get("task_id") or "").strip()
        if task_id:
            live_workers_by_task.setdefault(task_id, []).append(worker)

    ready_now = 0
    in_progress = 0
    in_review = 0
    blocked = 0
    review_approved = 0
    done = 0
    for task in state.get("tasks", []):
        status = str(task.get("status") or "").lower()
        if status == "todo" and all(
            str(task_map.get(dep_id, {}).get("status") or "done").lower() in DEPENDENCY_DONE_STATUSES
            for dep_id in task.get("depends_on", [])
        ):
            ready_now += 1
        elif status == "in_progress":
            in_progress += 1
        elif status == "review":
            in_review += 1
        elif status == "blocked":
            blocked += 1
        elif status == "review_approved":
            review_approved += 1
        elif status == "done":
            done += 1

    worker_task_links: list[dict[str, Any]] = []
    mismatch_index: dict[tuple[str, str], list[str]] = {}
    mismatch_detail_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for mismatch in mismatches:
        task_id = str(mismatch.get("task_id") or "")
        run_id = str(mismatch.get("worker_run_id") or "")
        mismatch_index.setdefault((task_id, run_id), []).append(str(mismatch.get("type") or "mismatch"))
        mismatch_detail_index.setdefault((task_id, run_id), []).append(mismatch)
    queue_map = {str(event.get("id") or ""): event for event in queue_events}
    for worker in live_workers:
        task_id = str(worker.get("task_id") or "")
        task = task_map.get(task_id, {})
        queue_event = queue_map.get(str(worker.get("queue_event_id") or ""), {})
        linked_mismatches = mismatch_detail_index.get((task_id, str(worker.get("run_id") or "")), [])
        worker_task_links.append(
            {
                "task_id": task_id or None,
                "task_title": task.get("title"),
                "task_summary": task.get("summary_zh"),
                "task_next": task.get("next"),
                "task_status": task.get("status"),
                "owner": task.get("owner"),
                "reviewer": task.get("reviewer"),
                "expected_actor": expected_task_actor(task) if task else None,
                "worker_run_id": worker.get("run_id"),
                "queue_event_id": worker.get("queue_event_id"),
                "queue_status": queue_event.get("status"),
                "queue_last_event_at": queue_event.get("last_event_at"),
                "actor": worker.get("actor"),
                "provider": worker.get("provider"),
                "worker_status": worker.get("status"),
                "runtime_bucket": worker.get("bucket"),
                "dispatch_reason": worker.get("reason"),
                "mode": worker.get("mode"),
                "last_event_at": worker.get("last_event_at"),
                "last_error": worker.get("last_error"),
                "mismatch_flags": mismatch_index.get((task_id, str(worker.get("run_id") or "")), []),
                "mismatch_count": len(linked_mismatches),
                "resolution_hints": [str(item.get("resolution_hint") or "") for item in linked_mismatches if str(item.get("resolution_hint") or "")],
            }
        )

    proposed = planning.get("proposed_execution_tasks") or []
    materialized_count = sum(1 for item in proposed if str(item.get("id") or "") in task_map)
    runtime_mode = str(planning.get("runtime_mode") or "supervisor_managed_execution")
    planning_status = str(planning.get("status") or "inactive")
    focus_mode = "planning" if planning_status in {"active", "human_required"} else "execution" if runtime_mode == "supervisor_managed_execution" else "execution"

    lanes: dict[str, dict[str, int]] = {}
    for worker in workers:
        actor = str(worker.get("actor") or "-")
        lane = lanes.setdefault(actor, {"running": 0, "pending": 0, "transition": 0, "completed": 0, "failed": 0})
        bucket = str(worker.get("bucket") or "pending")
        lane[bucket] = lane.get(bucket, 0) + 1
        if worker.get("status") == "failed":
            lane["failed"] += 1

    return {
        "generated_at": iso_now(),
        "focus_mode": focus_mode,
        "runtime_summary": {
            "supervisor_pid": (orchestrator.get("supervisor") or {}).get("pid"),
            "heartbeat_at": (orchestrator.get("supervisor") or {}).get("last_heartbeat_at") or orchestrator.get("last_heartbeat_at"),
            "queue_depth": len(queue_events),
            "pending_approvals": len(approvals.get("pending") or []),
            "running_workers": sum(1 for worker in live_workers if worker.get("bucket") == "running"),
            "pending_workers": sum(1 for worker in live_workers if worker.get("bucket") == "pending"),
            "mismatch_count": len(mismatches),
            "lanes": lanes,
        },
        "execution_summary": {
            "ready_now": ready_now,
            "in_progress": in_progress,
            "in_review": in_review,
            "blocked": blocked,
            "review_approved": review_approved,
            "done": done,
            "live_attached": sum(1 for linked in live_workers_by_task.values() if any(worker.get("bucket") == "running" for worker in linked)),
            "mismatch_count": len(mismatches),
        },
        "planning_summary": {
            "status": planning.get("status"),
            "consensus_status": planning.get("consensus_status"),
            "human_gate_status": planning.get("human_gate_status"),
            "counts": planning.get("counts") or {},
            "materialized_count": materialized_count,
            "proposed_execution_tasks": len(proposed),
        },
        "worker_task_links": worker_task_links,
        "truth_mismatches": mismatches,
    }


def write_dashboard_bundle(state: dict[str, Any]) -> None:
    planning_state = load_planning_state()
    orchestrator_state = load_json_file(ORCHESTRATOR_STATE_FILE, {})
    approval_state = load_json_file(APPROVAL_QUEUE_FILE, {"pending": [], "history": []})
    bundle = build_dashboard_bundle(state, planning_state, orchestrator_state, approval_state)
    DASHBOARD_BUNDLE_FILE.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sync_docs_site() -> None:
    DOCS_SITE_DIR.mkdir(parents=True, exist_ok=True)
    mirror_files = [
        STATUS_FILE,
        LOG_FILE,
        CURRENT_WORK_FILE,
        DASHBOARD_BUNDLE_FILE,
        ROOT / ".orchestrator" / "state.json",
        ROOT / ".orchestrator" / "approval-queue.json",
        ROOT / ".orchestrator" / "planning-state.json",
    ]
    rename_map = {
        "state.json": "orchestrator-state.json",
        "approval-queue.json": "approval-queue.json",
    }
    for path in mirror_files:
        if path.exists():
            target_name = rename_map.get(path.name, path.name)
            shutil.copy2(path, DOCS_SITE_DIR / target_name)


def sync_all(state: dict[str, Any]) -> None:
    sync_canonical_document_metadata(state)
    normalize_state_agents(state)
    validate_state(state)
    normalize_handoffs(state)
    recompute_agents(state)
    recompute_workload(state)
    state["updated_at"] = iso_now()
    save_state(state)
    logs = load_logs()
    write_current_work(state, logs)
    write_dashboard_bundle(state)
    sync_docs_site()


def mark_blockers_resolved(state: dict[str, Any], task_id: str) -> None:
    for blocker in state.get("blockers", []):
        if blocker["task_id"] == task_id and blocker["status"] == "open":
            blocker["status"] = "resolved"
            blocker["resolved_at"] = iso_now()


def mark_handoffs_done(state: dict[str, Any], task_id: str) -> None:
    for handoff in state.get("handoffs", []):
        if handoff["task_id"] == task_id and handoff["status"] != "done":
            handoff["status"] = "done"
            handoff["resolved_at"] = iso_now()


def mark_handoffs_done_for_actor(state: dict[str, Any], task_id: str, actor: str) -> None:
    for handoff in state.get("handoffs", []):
        if handoff["task_id"] == task_id and handoff.get("to") == actor and handoff["status"] != "done":
            handoff["status"] = "done"
            handoff["resolved_at"] = iso_now()


def normalize_handoffs(state: dict[str, Any]) -> None:
    task_map = {task["id"]: task for task in state["tasks"]}
    pending_by_task: dict[str, list[dict[str, Any]]] = {}
    for handoff in state.get("handoffs", []):
        if handoff.get("status") == "done":
            continue
        pending_by_task.setdefault(handoff["task_id"], []).append(handoff)

    for task_id, pending in pending_by_task.items():
        task = task_map.get(task_id)
        if task:
            task_status = task.get("status")
            if task_status in {"in_progress", "blocked", "done"}:
                for handoff in pending:
                    handoff["status"] = "done"
                    handoff["resolved_at"] = iso_now()
                continue
            if task_status == "review_approved":
                owner = canonical_agent_name(task.get("owner"))
                owner_handoffs = [handoff for handoff in pending if handoff.get("to") == owner]
                for handoff in pending:
                    if handoff not in owner_handoffs:
                        handoff["status"] = "done"
                        handoff["resolved_at"] = iso_now()
                if not owner_handoffs:
                    ensure_review_finalize_handoff(
                        state,
                        task,
                        from_agent=canonical_agent_name(task.get("reviewer")),
                        timestamp=iso_now(),
                        message=task.get("next"),
                    )
                continue

        for handoff in pending[:-1]:
            handoff["status"] = "done"
            handoff["resolved_at"] = iso_now()

    for task in state.get("tasks", []):
        if task.get("status") != "review_approved":
            continue
        task_id = task.get("id")
        owner = canonical_agent_name(task.get("owner"))
        pending = [
            handoff
            for handoff in state.get("handoffs", [])
            if handoff.get("task_id") == task_id and handoff.get("status") != "done"
        ]
        owner_handoffs = [handoff for handoff in pending if handoff.get("to") == owner]
        for handoff in pending:
            if handoff not in owner_handoffs:
                handoff["status"] = "done"
                handoff["resolved_at"] = iso_now()
        if not owner_handoffs:
            ensure_review_finalize_handoff(
                state,
                task,
                from_agent=canonical_agent_name(task.get("reviewer")),
                timestamp=iso_now(),
                message=task.get("next"),
            )


def command_assign(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: assign <task-id> <owner> <reviewer> [title]")
    task_id, owner, reviewer = args[0], canonical_agent_name(args[1]), canonical_agent_name(args[2])
    title = args[3] if len(args) > 3 else os.environ.get("TASK_TITLE")
    summary_zh = os.environ.get("TASK_SUMMARY_ZH")
    metadata = task_metadata_from_env()
    ensure_agent(owner)
    ensure_agent(reviewer)
    if owner == reviewer:
        raise SystemExit("Reviewer cannot equal owner")

    task = get_task(state, task_id)
    timestamp = iso_now()
    if task is None:
        task = {
            "id": task_id,
            "title": title,
            "summary_zh": summary_zh,
            "phase": os.environ.get("TASK_PHASE", "Unassigned"),
            "owner": owner,
            "reviewer": reviewer,
            "status": "todo",
            "depends_on": parse_csv_env("TASK_DEPENDS_ON"),
            "artifacts": parse_csv_env("TASK_ARTIFACTS"),
            "acceptance": parse_csv_env("TASK_ACCEPTANCE"),
            "next": "Assignment created",
            "last_update": timestamp,
        }
        task.update(metadata)
        state["tasks"].append(task)
    else:
        task["owner"] = owner
        task["reviewer"] = reviewer
        if title:
            task["title"] = title
        if summary_zh:
            task["summary_zh"] = summary_zh
        if metadata:
            task.update(metadata)
        task["last_update"] = timestamp
        task["next"] = "Ownership updated"

    agent = get_agent(state, owner)
    if os.environ.get("TASK_BRANCH"):
        agent["branch"] = os.environ["TASK_BRANCH"]

    append_log(
        {
            "ts": timestamp,
            "agent": current_actor(),
            "type": "assign",
            "task_id": task_id,
            "message": f"Assigned {task_id} to {owner} with reviewer {reviewer}",
        }
    )


def command_start(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: start <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can start {task_id}")
    timestamp = iso_now()
    task["status"] = "in_progress"
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    mark_blockers_resolved(state, task_id)
    append_log({"ts": timestamp, "agent": actor, "type": "start", "task_id": task_id, "message": message})


def command_progress(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: progress <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can progress {task_id}")
    timestamp = iso_now()
    if task["status"] in {"todo", "review_approved"}:
        task["status"] = "in_progress"
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    append_log({"ts": timestamp, "agent": actor, "type": "progress", "task_id": task_id, "message": message})


def command_note(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: note <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    timestamp = iso_now()
    task["last_update"] = timestamp
    task["next"] = message
    append_log({"ts": timestamp, "agent": actor, "type": "note", "task_id": task_id, "message": message})


def command_reopen(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: reopen <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    owner = canonical_agent_name(task.get("owner"))
    reviewer = canonical_agent_name(task.get("reviewer"))
    if actor not in {owner, reviewer}:
        raise SystemExit(f"Only the owner ({owner}) or reviewer ({reviewer}) can reopen {task_id}")
    timestamp = iso_now()
    task["status"] = "in_progress"
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("waiting_for", None)
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done(state, task_id)
    if actor == reviewer and owner and owner != reviewer:
        state.setdefault("handoffs", []).append(
            {
                "task_id": task_id,
                "from": reviewer,
                "to": owner,
                "message": message,
                "status": "pending",
                "created_at": timestamp,
            }
        )
    append_log({"ts": timestamp, "agent": actor, "type": "reopen", "task_id": task_id, "message": message})


def command_handoff(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: handoff <task-id> <to-agent> <message>")
    task_id, to_agent, message = args[0], canonical_agent_name(args[1]), args[2]
    actor = current_actor()
    ensure_agent(actor)
    ensure_agent(to_agent)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can hand off {task_id} for review")
    if task.get("reviewer") != to_agent:
        raise SystemExit(
            f"{task_id} handoff target must match the assigned reviewer ({task.get('reviewer')}); reassign reviewer first if needed"
        )
    timestamp = iso_now()
    task["status"] = "review"
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    mark_blockers_resolved(state, task_id)
    state.setdefault("handoffs", []).append(
        {
            "task_id": task_id,
            "from": actor,
            "to": to_agent,
            "message": message,
            "status": "pending",
            "created_at": timestamp,
        }
    )
    append_log({"ts": timestamp, "agent": actor, "type": "handoff", "task_id": task_id, "message": f"Handoff to {to_agent}: {message}"})


def command_blocker(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: blocker <task-id> <message> <waiting-for>")
    task_id, message, waiting_for = args[0], args[1], canonical_agent_name(args[2])
    actor = current_actor()
    ensure_agent(actor)
    ensure_agent(waiting_for)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can block {task_id}")
    timestamp = iso_now()
    task["status"] = "blocked"
    task["waiting_for"] = waiting_for
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    state.setdefault("blockers", []).append(
        {
            "task_id": task_id,
            "owner": actor,
            "waiting_for": waiting_for,
            "message": message,
            "status": "open",
            "created_at": timestamp,
        }
    )
    append_log({"ts": timestamp, "agent": actor, "type": "blocker", "task_id": task_id, "message": f"Blocked on {waiting_for}: {message}"})


def command_done(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: done <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("owner") != actor:
        raise SystemExit(f"Only the owner ({task.get('owner')}) can finalize {task_id} to done")
    if task.get("status") != "review_approved":
        raise SystemExit(f"{task_id} must be review_approved before it can move to done")
    timestamp = iso_now()
    task["status"] = "done"
    task["terminal_outcome"] = "completed"
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("waiting_for", None)
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done(state, task_id)
    append_log({"ts": timestamp, "agent": actor, "type": "done", "task_id": task_id, "message": message})


def command_supersede(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: supersede <task-id> <message> [replacement-task-id]")
    task_id, message = args[0], args[1]
    replacement_task_id = args[2].strip() if len(args) > 2 and args[2].strip() else ""
    actor = current_actor()
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    owner = canonical_agent_name(task.get("owner"))
    reviewer = canonical_agent_name(task.get("reviewer"))
    if actor not in {owner, reviewer}:
        raise SystemExit(f"Only the owner ({owner}) or reviewer ({reviewer}) can supersede {task_id}")
    timestamp = iso_now()
    task["status"] = "done"
    task["terminal_outcome"] = TASK_TERMINAL_SUPERSEDED
    task["last_update"] = timestamp
    task["next"] = message
    if replacement_task_id:
        task["superseded_by"] = replacement_task_id
    task.pop("waiting_for", None)
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done(state, task_id)
    append_log(
        {
            "ts": timestamp,
            "agent": actor,
            "type": "superseded",
            "task_id": task_id,
            "message": message,
            **({"replacement_task_id": replacement_task_id} if replacement_task_id else {}),
        }
    )


def command_approve(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: approve <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = current_actor()
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    if task.get("reviewer") != actor:
        raise SystemExit(f"Only the reviewer ({task.get('reviewer')}) can approve {task_id}")
    if task.get("status") != "review":
        raise SystemExit(f"{task_id} must be in review before it can move to review_approved")

    timestamp = iso_now()
    task["status"] = "review_approved"
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("waiting_for", None)

    review_notes = parse_delimited_env("REVIEW_NOTES_ZH")
    if review_notes:
        task["review_notes_zh"] = review_notes

    review_file = os.environ.get("REVIEW_FILE", "").strip()
    if review_file:
        task["review_file"] = review_file

    mark_blockers_resolved(state, task_id)
    mark_handoffs_done_for_actor(state, task_id, actor)
    ensure_review_finalize_handoff(
        state,
        task,
        from_agent=actor,
        timestamp=timestamp,
        message=message,
    )
    append_log({"ts": timestamp, "agent": actor, "type": "review_approved", "task_id": task_id, "message": message})


def command_sync(state: dict[str, Any], _args: list[str]) -> None:
    return None


def command_prompt(state: dict[str, Any], _args: list[str]) -> None:
    print(build_onboarding_prompt(state))


def main(argv: list[str]) -> int:
    state = load_state()
    command = argv[1] if len(argv) > 1 else "sync"
    args = argv[2:]

    read_only_commands = {
        "prompt": command_prompt,
    }

    commands = {
        "assign": command_assign,
        "start": command_start,
        "progress": command_progress,
        "note": command_note,
        "reopen": command_reopen,
        "handoff": command_handoff,
        "blocker": command_blocker,
        "done": command_done,
        "supersede": command_supersede,
        "approve": command_approve,
        "sync": command_sync,
    }

    if command in read_only_commands:
        read_only_commands[command](state, args)
        return 0

    if command not in commands:
        raise SystemExit(f"Unknown command: {command}")

    state_before = deepcopy(state)
    commands[command](state, args)
    try:
        sync_all(state)
    except Exception:
        save_state(state_before)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
