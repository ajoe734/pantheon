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
    "Grok": {
        "capability_lane": ["research-ingest", "external-search", "spec-review", "critique"],
        "default_branch": "feat/grok-research-critique",
        "target_workload": 25,
    },
}

STATUS_LABELS = {
    "todo": "todo",
    "in_progress": "in_progress",
    "review": "review",
    "blocked": "blocked",
    "done": "done",
}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_state() -> dict[str, Any]:
    timestamp = iso_now()
    return {
        "project": "pantheon",
        "sprint": "2026-04-01-collab-foundation",
        "objective": (
            "Stabilize the Pantheon — multi-persona automated trading system operating system so Claude, Gemini, "
            "Codex, and Grok share one task board, one history feed, and one visual dashboard."
        ),
        "updated_at": timestamp,
        "canonical_files": [
            "AI_COLLABORATION_GUIDE.md",
            "current-work.md",
            "ai-status.json",
            "ai-activity-log.jsonl",
        ],
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
    return json.loads(STATUS_FILE.read_text(encoding="utf-8"))


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


def save_state(state: dict[str, Any]) -> None:
    STATUS_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_log(entry: dict[str, Any]) -> None:
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def ensure_agent(name: str) -> dict[str, Any]:
    if name not in KNOWN_AGENTS:
        raise SystemExit(f"Unknown agent: {name}")
    return KNOWN_AGENTS[name]


def get_agent(state: dict[str, Any], name: str) -> dict[str, Any]:
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


def validate_state(state: dict[str, Any]) -> None:
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
        queued = [task for task in owned if task["status"] == "todo"]
        ready = [
            task
            for task in queued
            if all(task_map.get(dep_id, {"status": "done"}).get("status") == "done" for dep_id in task.get("depends_on", []))
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
        summary[name] = {"total": 0, "active": 0, "blocked": 0, "done": 0, "review": 0, "todo": 0}

    for task in state["tasks"]:
        owner = task["owner"]
        bucket = summary[owner]
        bucket["total"] += 1
        bucket[task["status"] if task["status"] in bucket else "todo"] += 1
        if task["status"] in {"in_progress", "review", "blocked"}:
            bucket["active"] += 1

    state["workload"] = {name: KNOWN_AGENTS[name]["target_workload"] for name in KNOWN_AGENTS}
    state["workload_summary"] = summary


def write_current_work(state: dict[str, Any], logs: list[dict[str, Any]]) -> None:
    def cell(value: Any) -> str:
        text = "-" if value is None or value == "" else str(value)
        return text.replace("|", "\\|").replace("\n", "<br>")

    current_logs = logs[-20:]
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
        f"- Sprint: `{state['sprint']}`",
        "- Canonical files: " + ", ".join(f"`{item}`" for item in state["canonical_files"]),
        "- Dashboard: `docs-site/index.html`",
        "",
        "## Active Slices",
        "",
    ]

    for agent in state["agents"]:
        next_text = agent.get("next") or "No active assignment"
        lines.append(f"- `{agent['name']}`: {', '.join(agent['capability_lane'])}; next: {next_text}")

    lines.extend(["", "## Task Board", "", "| ID | Phase | Task | 中文說明 | Owner | Reviewer | Status | Depends On | Last Update | Next |", "|---|---|---|---|---|---|---|---|---|---|"])

    for task in state["tasks"]:
        depends = ", ".join(f"`{item}`" for item in task.get("depends_on", [])) or "-"
        lines.append(
            "| `{id}` | {phase} | {title} | {summary} | {owner} | {reviewer} | {status} | {depends} | {last_update} | {next} |".format(
                id=cell(task["id"]),
                phase=cell(task["phase"]),
                title=cell(task["title"]),
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


def sync_docs_site() -> None:
    DOCS_SITE_DIR.mkdir(parents=True, exist_ok=True)
    for path in [STATUS_FILE, LOG_FILE, CURRENT_WORK_FILE]:
        if path.exists():
            shutil.copy2(path, DOCS_SITE_DIR / path.name)


def sync_all(state: dict[str, Any]) -> None:
    validate_state(state)
    normalize_handoffs(state)
    recompute_agents(state)
    recompute_workload(state)
    state["updated_at"] = iso_now()
    save_state(state)
    logs = load_logs()
    write_current_work(state, logs)
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
        if task and task.get("status") in {"in_progress", "blocked", "done"}:
            for handoff in pending:
                handoff["status"] = "done"
                handoff["resolved_at"] = iso_now()
            continue

        for handoff in pending[:-1]:
            handoff["status"] = "done"
            handoff["resolved_at"] = iso_now()


def command_assign(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: assign <task-id> <owner> <reviewer> [title]")
    task_id, owner, reviewer = args[0], args[1], args[2]
    title = args[3] if len(args) > 3 else os.environ.get("TASK_TITLE")
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
        state["tasks"].append(task)
    else:
        task["owner"] = owner
        task["reviewer"] = reviewer
        if title:
            task["title"] = title
        task["last_update"] = timestamp
        task["next"] = "Ownership updated"

    agent = get_agent(state, owner)
    if os.environ.get("TASK_BRANCH"):
        agent["branch"] = os.environ["TASK_BRANCH"]

    append_log(
        {
            "ts": timestamp,
            "agent": os.environ.get("AI_NAME", "Codex"),
            "type": "assign",
            "task_id": task_id,
            "message": f"Assigned {task_id} to {owner} with reviewer {reviewer}",
        }
    )


def command_start(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 2:
        raise SystemExit("Usage: start <task-id> <message>")
    task_id, message = args[0], args[1]
    actor = os.environ.get("AI_NAME", "Codex")
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
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
    actor = os.environ.get("AI_NAME", "Codex")
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    timestamp = iso_now()
    if task["status"] == "todo":
        task["status"] = "in_progress"
    task["last_update"] = timestamp
    task["next"] = message
    mark_handoffs_done_for_actor(state, task_id, actor)
    append_log({"ts": timestamp, "agent": actor, "type": "progress", "task_id": task_id, "message": message})


def command_handoff(state: dict[str, Any], args: list[str]) -> None:
    if len(args) < 3:
        raise SystemExit("Usage: handoff <task-id> <to-agent> <message>")
    task_id, to_agent, message = args[0], args[1], args[2]
    actor = os.environ.get("AI_NAME", "Codex")
    ensure_agent(actor)
    ensure_agent(to_agent)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
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
    task_id, message, waiting_for = args[0], args[1], args[2]
    actor = os.environ.get("AI_NAME", "Codex")
    ensure_agent(actor)
    ensure_agent(waiting_for)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
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
    actor = os.environ.get("AI_NAME", "Codex")
    ensure_agent(actor)
    task = get_task(state, task_id)
    if task is None:
        raise SystemExit(f"Unknown task: {task_id}")
    timestamp = iso_now()
    task["status"] = "done"
    task["last_update"] = timestamp
    task["next"] = message
    task.pop("waiting_for", None)
    mark_blockers_resolved(state, task_id)
    mark_handoffs_done(state, task_id)
    append_log({"ts": timestamp, "agent": actor, "type": "done", "task_id": task_id, "message": message})


def command_sync(state: dict[str, Any], _args: list[str]) -> None:
    return None


def main(argv: list[str]) -> int:
    state = load_state()
    command = argv[1] if len(argv) > 1 else "sync"
    args = argv[2:]

    commands = {
        "assign": command_assign,
        "start": command_start,
        "progress": command_progress,
        "handoff": command_handoff,
        "blocker": command_blocker,
        "done": command_done,
        "sync": command_sync,
    }

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
