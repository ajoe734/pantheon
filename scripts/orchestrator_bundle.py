#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
ROOT = THIS_DIR.parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import ai_status

PORTABLE_SCRIPT_FILES = [
    "scripts/ai_status.py",
    "scripts/ai-status.sh",
    "scripts/sync-state.sh",
    "scripts/dashboard_server.py",
    "scripts/launch-docs-site.sh",
    "scripts/orchestrator_bundle.py",
    "scripts/run-supervisor.sh",
    "scripts/run-dashboard.sh",
    "scripts/setup-llm-cli.sh",
]

ORCHESTRATOR_EXCLUDES = {
    "__pycache__",
    "approval-queue.json",
    "approval-queue.lock",
    "backups",
    "claude-approval-broker.mcp.json",
    "config.json",
    "config.local.json",
    "config.test-backup.json",
    "event-queue.jsonl",
    "github-bus-state.json",
    "github-relay-state.json",
    "github-webhook-events.jsonl",
    "logs",
    "provider_capabilities.json",
    "state.json",
    "supervisor.pid",
}

DOCS_SITE_EXCLUDES = {
    "ai-status.json",
    "ai-activity-log.jsonl",
    "approval-queue.json",
    "current-work.md",
    "orchestrator-state.json",
}

PORTABLE_DIRS = [
    (".orchestrator", ORCHESTRATOR_EXCLUDES),
    ("docs-site", DOCS_SITE_EXCLUDES),
]

PORTABLE_DIR_STUBS = [
    ".claude",
    ".github/agents",
    ".llm-inbox",
    ".vscode",
    "docs",
]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "project"


def project_title(project_name: str) -> str:
    return project_name.strip() or "Project"


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str, executable: bool = False) -> None:
    ensure_parent(path)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def write_json(path: Path, payload: object) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def copy_file(src: Path, dest: Path) -> None:
    ensure_parent(dest)
    shutil.copy2(src, dest)


def copy_tree_filtered(src: Path, dest: Path, excludes: set[str]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for path in src.rglob("*"):
        rel = path.relative_to(src)
        if any(part in excludes for part in rel.parts):
            continue
        if path.name in excludes:
            continue
        if path.is_dir():
            (dest / rel).mkdir(parents=True, exist_ok=True)
            continue
        if path.suffix == ".pyc":
            continue
        copy_file(path, dest / rel)


def generic_state(project_name: str, objective: str) -> dict:
    timestamp = ai_status.iso_now()
    agents = []
    for name, meta in ai_status.KNOWN_AGENTS.items():
        agents.append(
            {
                "name": name,
                "capability_lane": meta["capability_lane"],
                "status": "idle",
                "current_task_ids": [],
                "branch": meta["default_branch"],
                "next": "",
                "last_update": None,
            }
        )

    return {
        "project": slugify(project_name),
        "sprint": f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-bootstrap",
        "objective": objective,
        "updated_at": timestamp,
        "canonical_files": [
            "AI_COLLABORATION_GUIDE.md",
            "current-work.md",
            "ai-status.json",
            "ai-activity-log.jsonl",
        ],
        "agents": agents,
        "tasks": [],
        "handoffs": [],
        "blockers": [],
        "workload": {name: meta["target_workload"] for name, meta in ai_status.KNOWN_AGENTS.items()},
        "workload_summary": {
            name: {
                "total": 0,
                "active": 0,
                "blocked": 0,
                "done": 0,
                "review": 0,
                "review_approved": 0,
                "todo": 0,
            }
            for name in ai_status.KNOWN_AGENTS
        },
    }


def generic_collaboration_guide(project_name: str, objective: str) -> str:
    project = project_title(project_name)
    return f"""# AI Collaboration Guide

Last updated: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
Status: canonical collaboration rules for the {project} project

## 0. Repository Scope

You are in the `{slugify(project_name)}` repo.

This repository uses the portable orchestrator bundle:
- `.orchestrator/` for watcher, supervisor, permission broker, and worker adapters
- `scripts/` for status sync and dashboard helpers
- `docs-site/` for the local dashboard

The system objective is:

> {objective}

## 1. Canonical Truth

Read these in order before starting work:

1. `AI_COLLABORATION_GUIDE.md`
2. `current-work.md`
3. `ai-status.json`
4. `ai-activity-log.jsonl`
5. `docs-site/index.html`

Source of truth split:
- `AI_COLLABORATION_GUIDE.md`: stable collaboration rules
- `ai-status.json`: machine-readable live task state
- `ai-activity-log.jsonl`: append-only activity history
- `current-work.md`: generated human-readable snapshot
- `docs-site/index.html`: local dashboard

## 2. Collaboration Model

### Capability Lanes

- `Claude`: execution plane, control plane, governance review
- `Gemini`: cloud/runtime packaging, CI/CD, worker operations
- `Codex`: contracts, state system, schema, acceptance
- `Copilot`: coding assist, research ingestion, external search, critique

### Task Ownership

Rules:
- each task has exactly one `owner`
- `reviewer` cannot equal `owner`
- blocked tasks must include `waiting_for`
- every task must pass through `review -> review_approved -> done`
- direct `done` from `todo` or `in_progress` is not allowed
- only the `reviewer` may move a task into `review_approved`
- only the `owner` may finalize a `review_approved` task into `done`

Lifecycle:
- `todo` / `in_progress`: owner implementation work
- `review`: reviewer must either approve or request concrete changes
- `review_approved`: reviewer gate passed; the task returns to the owner for finalization
- `done`: owner has formally closed the task

## 3. Status Commands

Use the status script instead of editing multiple files manually.

```bash
AI_NAME=Codex ./scripts/ai-status.sh assign <task-id> <owner> <reviewer> "Optional title"
AI_NAME=Codex ./scripts/ai-status.sh start <task-id> "Started implementation"
AI_NAME=Codex ./scripts/ai-status.sh progress <task-id> "Updated progress"
AI_NAME=Codex ./scripts/ai-status.sh handoff <task-id> Claude "Ready for review"
AI_NAME=Claude REVIEW_NOTES_ZH="審查通過||回到 owner 收尾" ./scripts/ai-status.sh approve <task-id> "Review approved and returned to the owner for finalization"
AI_NAME=Codex ./scripts/ai-status.sh done <task-id> "Owner finalized approved task and closed it"
./scripts/sync-state.sh
```

## 4. Local Runtime

Run locally from the repository root only:

```bash
bash scripts/setup-llm-cli.sh
bash scripts/run-supervisor.sh --verbose
bash scripts/run-dashboard.sh
```

The dashboard will be served at `http://127.0.0.1:4173/index.html` unless you override `PORT`.

## 5. Work Order For Every LLM

1. review first
2. then finalize any `review_approved` tasks you own
3. then continue your `in_progress` tasks
4. then pick unblocked `todo` tasks you own
5. only then remain idle or log a blocker

If an auto worker repeatedly fails, the supervisor may retry, fallback, or reassign ownership/review to another lane.
"""


def agent_brief(agent_name: str, capability_lane: list[str], project_name: str) -> str:
    project = project_title(project_name)
    lane_text = " · ".join(capability_lane)
    return f"""# FOR_{agent_name.upper()}

Repo: `{slugify(project_name)}`
System: `{project}`
Lane: {lane_text}

Before doing anything:
1. read `AI_COLLABORATION_GUIDE.md`
2. read `current-work.md`
3. read `ai-status.json`
4. treat those files as the only shared truth

Working rules:
- use `scripts/ai-status.sh` or `python3 scripts/ai_status.py` for status changes
- do not directly patch `ai-status.json`, `current-work.md`, or `ai-activity-log.jsonl`
- if you are the reviewer, finish `review` tasks first
- if you are the owner of a `review_approved` task, finalize it to `done`
- if review fails, write concrete changes and return the task to the owner
"""


def copilot_alias_brief(project_name: str) -> str:
    return f"""# FOR_GROK

`Copilot` is the canonical fourth lane name in `{project_title(project_name)}`.
Use [FOR_COPILOT.md](FOR_COPILOT.md) as the active brief.
"""


def quickstart_doc(project_name: str) -> str:
    project = project_title(project_name)
    return f"""# Orchestrator Quickstart

This repo has been bootstrapped with the reusable supervisor + auto-worker + dashboard bundle.

## 1. Prepare local LLM/IDE integration

```bash
bash scripts/setup-llm-cli.sh
```

That will:
- sync provider permission settings
- regenerate local dashboard mirrors
- create repo-local Claude settings if needed

## 2. Start the local runtime

Supervisor:
```bash
bash scripts/run-supervisor.sh --verbose
```

Dashboard:
```bash
bash scripts/run-dashboard.sh
```

## 3. Seed the first task

```bash
AI_NAME=Codex ./scripts/ai-status.sh assign DEMO-001 Codex Claude "First migrated task"
AI_NAME=Codex TASK_PHASE="Foundation" TASK_SUMMARY_ZH="把新 repo 的第一個協作任務建立起來。" ./scripts/ai-status.sh assign DEMO-001 Codex Claude "First migrated task"
AI_NAME=Codex ./scripts/ai-status.sh start DEMO-001 "Started the first migrated task"
./scripts/sync-state.sh
```

## 4. Tell each LLM CLI how to behave

Recommended first prompt for each CLI:

```text
Read AI_COLLABORATION_GUIDE.md, current-work.md, ai-status.json, and ai-activity-log.jsonl first. Follow the canonical lifecycle todo -> in_progress -> review -> review_approved -> done. Use scripts/ai-status.sh for every state change.
```

## 5. Optional GitHub bus

The bootstrap leaves GitHub bus disabled by default. When you are ready, update `.orchestrator/config.local.json` with your repo details and enable `github_bus.enabled`.
"""


def workspace_settings() -> dict:
    return {
        "claudeCode.initialPermissionMode": "acceptEdits",
        "claudeCode.allowDangerouslySkipPermissions": False,
        "github.copilot.chat.backgroundAgent.enabled": True,
        "github.copilot.chat.cloudAgent.enabled": True,
        "github.copilot.chat.claudeAgent.enabled": True,
        "github.copilot.chat.claudeAgent.allowDangerouslySkipPermissions": False,
        "github.copilot.chat.reviewAgent.enabled": True,
        "geminicodeassist.enable": True,
        "geminicodeassist.agentYoloMode": False,
    }


def generic_config() -> dict:
    config = json.loads((ROOT / ".orchestrator" / "config.example.json").read_text(encoding="utf-8"))
    config.setdefault("github_bus", {})["enabled"] = False
    config["github_bus"]["repo"] = None
    config["github_bus"]["auto_request_reviewers"] = False
    config["github_bus"]["close_resolved_issues"] = False
    return config


def portable_manifest(project_name: str) -> dict:
    return {
        "bundle": "portable-orchestrator",
        "version": iso_now(),
        "source_repo": str(ROOT),
        "project_name": project_name,
        "portable_dirs": [path for path, _ in PORTABLE_DIRS],
        "portable_scripts": PORTABLE_SCRIPT_FILES,
    }


def ensure_clean_targets(target_root: Path, force: bool) -> None:
    candidates = [
        target_root / ".orchestrator",
        target_root / "docs-site",
        target_root / "scripts" / "ai_status.py",
        target_root / "scripts" / "dashboard_server.py",
        target_root / "AI_COLLABORATION_GUIDE.md",
        target_root / "FOR_CLAUDE.md",
        target_root / "FOR_GEMINI.md",
        target_root / "FOR_CODEX.md",
        target_root / "FOR_COPILOT.md",
        target_root / "FOR_GROK.md",
        target_root / "ORCHESTRATOR_QUICKSTART.md",
        target_root / "ai-status.json",
        target_root / "ai-activity-log.jsonl",
        target_root / "current-work.md",
    ]
    existing = [path for path in candidates if path.exists()]
    if existing and not force:
        joined = "\n".join(f"- {path}" for path in existing)
        raise SystemExit(
            "Refusing to overwrite existing orchestrator bundle paths without --force:\n" + joined
        )


def write_bootstrap_files(target_root: Path, project_name: str, objective: str) -> None:
    for rel_path in PORTABLE_SCRIPT_FILES:
        copy_file(ROOT / rel_path, target_root / rel_path)

    for rel_path, excludes in PORTABLE_DIRS:
        copy_tree_filtered(ROOT / rel_path, target_root / rel_path, excludes)

    for stub in PORTABLE_DIR_STUBS:
        (target_root / stub).mkdir(parents=True, exist_ok=True)

    write_json(target_root / ".orchestrator" / "config.json", generic_config())
    write_json(target_root / ".vscode" / "settings.json", workspace_settings())
    write_json(target_root / "ai-status.json", generic_state(project_name, objective))
    write_text(target_root / "ai-activity-log.jsonl", "")
    write_text(target_root / "AI_COLLABORATION_GUIDE.md", generic_collaboration_guide(project_name, objective))
    write_text(target_root / "FOR_CLAUDE.md", agent_brief("Claude", ai_status.KNOWN_AGENTS["Claude"]["capability_lane"], project_name))
    write_text(target_root / "FOR_GEMINI.md", agent_brief("Gemini", ai_status.KNOWN_AGENTS["Gemini"]["capability_lane"], project_name))
    write_text(target_root / "FOR_CODEX.md", agent_brief("Codex", ai_status.KNOWN_AGENTS["Codex"]["capability_lane"], project_name))
    write_text(target_root / "FOR_COPILOT.md", agent_brief("Copilot", ai_status.KNOWN_AGENTS["Copilot"]["capability_lane"], project_name))
    write_text(target_root / "FOR_GROK.md", copilot_alias_brief(project_name))
    write_text(target_root / "ORCHESTRATOR_QUICKSTART.md", quickstart_doc(project_name))
    write_json(target_root / ".orchestrator" / "bundle-manifest.json", portable_manifest(project_name))

    subprocess.run(
        ["python3", str(target_root / "scripts" / "ai_status.py"), "sync"],
        cwd=str(target_root),
        check=True,
        capture_output=True,
        text=True,
    )


def bootstrap(target_repo: Path, project_name: str, objective: str, force: bool) -> None:
    target_repo.mkdir(parents=True, exist_ok=True)
    ensure_clean_targets(target_repo, force)
    write_bootstrap_files(target_repo, project_name, objective)


def export_bundle(output_path: Path, project_name: str, objective: str, force: bool) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not force:
        raise SystemExit(f"Output already exists: {output_path}. Use --force to overwrite.")

    with tempfile.TemporaryDirectory(prefix="orchestrator-bundle-") as temp_dir:
        staging_root = Path(temp_dir) / "orchestrator-bundle"
        bootstrap(staging_root, project_name, objective, force=True)
        with tarfile.open(output_path, "w:gz") as archive:
            archive.add(staging_root, arcname=".")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export or bootstrap the portable supervisor/auto-worker/dashboard bundle.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap_parser = subparsers.add_parser("bootstrap", help="Copy the bundle into a target repository.")
    bootstrap_parser.add_argument("--target-repo", required=True, help="Path to the target repository root.")
    bootstrap_parser.add_argument("--project-name", help="Human-readable project name. Defaults to target directory name.")
    bootstrap_parser.add_argument(
        "--objective",
        help="Project objective shown in ai-status.json and the dashboard.",
    )
    bootstrap_parser.add_argument("--force", action="store_true", help="Overwrite existing orchestrator bundle paths.")

    export_parser = subparsers.add_parser("export", help="Build a tar.gz bundle that can be extracted into another repository.")
    export_parser.add_argument("--output", required=True, help="Target .tar.gz path.")
    export_parser.add_argument("--project-name", default="Project", help="Human-readable project name baked into the exported bundle.")
    export_parser.add_argument(
        "--objective",
        default="Stand up a shared multi-LLM delivery system with a supervisor, auto workers, and a live dashboard.",
        help="Project objective baked into the exported bundle.",
    )
    export_parser.add_argument("--force", action="store_true", help="Overwrite the output archive if it already exists.")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "bootstrap":
        target_repo = Path(args.target_repo).resolve()
        project_name = args.project_name or target_repo.name
        objective = args.objective or f"Stand up the {project_title(project_name)} delivery system with shared supervisor, auto workers, and dashboard."
        bootstrap(target_repo, project_name, objective, args.force)
        print(json.dumps({
            "ok": True,
            "mode": "bootstrap",
            "target_repo": str(target_repo),
            "project_name": project_name,
            "objective": objective,
            "next": [
                "bash scripts/setup-llm-cli.sh",
                "bash scripts/run-supervisor.sh --verbose",
                "bash scripts/run-dashboard.sh",
            ],
        }, indent=2, ensure_ascii=False))
        return 0

    output_path = Path(args.output).resolve()
    export_bundle(output_path, args.project_name, args.objective, args.force)
    print(json.dumps({
        "ok": True,
        "mode": "export",
        "output": str(output_path),
        "project_name": args.project_name,
        "objective": args.objective,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
