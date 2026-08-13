#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
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
    "scripts/run-supervisor-watchdog.sh",
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
    "event-queue.jsonl",
    "github-bus-state.json",
    "github-relay-state.json",
    "github-webhook-events.jsonl",
    "logs",
    "metrics",
    "provider_capabilities.json",
    "state.json",
    "supervisor.pid",
    "watchdog-state.json",
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


def remove_generated_portable_runtime(target_root: Path) -> None:
    for rel_path in (
        ".orchestrator/state.json",
        ".orchestrator/event-queue.jsonl",
        ".orchestrator/approval-queue.json",
        ".orchestrator/provider_capabilities.json",
        ".orchestrator/claude-approval-broker.mcp.json",
        ".orchestrator/supervisor.pid",
        "docs-site/ai-status.json",
        "docs-site/ai-activity-log.jsonl",
        "docs-site/approval-queue.json",
        "docs-site/current-work.md",
        "docs-site/orchestrator-state.json",
    ):
        (target_root / rel_path).unlink(missing_ok=True)


def generic_state(project_name: str, objective: str) -> dict:
    timestamp = ai_status.iso_now()
    canonical_layers = {
        "L0 Collaboration & State": [
            "AI_COLLABORATION_GUIDE.md",
            "ai-status.json",
            "ai-activity-log.jsonl",
        ],
        "L0.5 Derived Narrative": [
            "current-work.md",
        ],
        "L1 Runtime & Dashboard": [
            "docs-site/index.html",
        ],
    }
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
        "canonical_document_layers": canonical_layers,
        "canonical_files": ai_status.flatten_canonical_document_layers(canonical_layers),
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


def generic_current_work(project_name: str, objective: str) -> str:
    return (
        "# Current Work\n\n"
        f"Project: {project_name}\n\n"
        f"Objective: {objective}\n\n"
        "Canonical task state is stored in `ai-status.json`. This file is a "
        "generated human summary only.\n"
    )


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
2. `ai-status.json`
3. `current-work.md` as a human summary only
4. `ai-activity-log.jsonl` only when targeted recent history is needed
5. `docs-site/index.html`

Layered source of truth:
- `L0 Collaboration & State`: `AI_COLLABORATION_GUIDE.md`, `ai-status.json`, `ai-activity-log.jsonl`
- `L0.5 Derived Narrative`: `current-work.md`
- `L1 Runtime & Dashboard`: `docs-site/index.html`

Generated files must not outrank their machine-readable source.
This bundle only seeds the collaboration/control-plane layer. As the repo gains project-specific architecture, backlog, or policy docs, update this file and `ai-status.json` so the canonical read order matches the new project instead of the source repo.

## 2. Collaboration Model

### Capability Lanes

- `Claude`: execution plane, control plane, governance review
- `Gemini`: cloud/runtime packaging, CI/CD, worker operations
- `Codex`: contracts, state system, schema, acceptance
- `Codex2`: contracts, state system, schema, acceptance, sidecar review
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

Do not edit state files or impersonate an actor with `AI_NAME`. Product-originated
source tasks may enter through the signed dev bridge; explicit local Human/Ops
maintenance uses `scripts/human-ops-status.sh`. Worker lifecycle commands are
accepted only from the exact supervisor-issued worker lease. Workers must not
invoke the Human/Ops wrapper.

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

If an auto worker fails, queue-owned retry handles transient delivery errors;
durably unavailable assignments may use the one bounded recovery reconciler.
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
2. read `ai-status.json`
3. use `current-work.md` only as a human summary
4. read `ai-activity-log.jsonl` only if you need targeted recent history
5. treat generated views as derived from machine-readable state

Working rules:
- use `scripts/ai-status.sh` or `python3 scripts/ai_status.py` for status changes
- do not directly patch `ai-status.json`, `current-work.md`, or `ai-activity-log.jsonl`
- project-specific architecture or backlog docs are declared through `AI_COLLABORATION_GUIDE.md`; do not assume source-repo docs exist here unless the guide points to them
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

Submit a signed assistant dev packet and wait for its durable bridge receipt.
The supervisor materializes and dispatches the resulting canonical task.

## 4. Print the current first prompt

The repo-aware prompt is generated from `ai-status.json`:

```bash
python3 scripts/ai_status.py prompt
```

If you add project-specific docs later, update `AI_COLLABORATION_GUIDE.md`, `FOR_*.md`, and `ai-status.json` canonical layers so the prompt stays aligned with the new repo.

## 5. Optional GitHub bus

The bootstrap leaves GitHub bus disabled by default. When you are ready, update `.orchestrator/config.local.json` with your repo details and enable `github_bus.enabled`.
"""


def llm_onboarding_doc(project_name: str) -> str:
    project = project_title(project_name)
    return f"""# LLM Onboarding

This file is the first-stop onboarding guide for any LLM working inside `{project}`.

## 1. Read Order

Start with these files in order:

1. `AI_COLLABORATION_GUIDE.md`
2. `ai-status.json`
3. `current-work.md` as a human summary only
4. `ai-activity-log.jsonl` only when you need targeted recent history

If the repo later adds project-specific architecture, backlog, or policy docs, `AI_COLLABORATION_GUIDE.md` and `ai-status.json` should be updated to point at them explicitly.

## 2. First Prompt

Print the repo-aware prompt with:

```bash
python3 scripts/ai_status.py prompt
```

Use that output as the first prompt in Claude Code, Codex CLI, Gemini CLI, Copilot, or any other connected coding LLM.

## 3. Shared Truth Rules

- `ai-status.json` is the machine-readable source of truth for tasks, ownership, blockers, and handoffs
- `ai-activity-log.jsonl` is append-only history
- `current-work.md` is generated from state and is not the write source
- `docs-site/` is a read-only dashboard mirror, not the place to edit status

## 4. Status Commands

Do not edit collaboration files or use `AI_NAME` as authorization. Product task
creation may use the signed dev bridge; explicit local Human/Ops maintenance
uses `scripts/human-ops-status.sh`. Worker lifecycle writes require the exact
active worker lease, and workers must not invoke the Human/Ops wrapper.

Lifecycle:
- `todo -> in_progress -> review -> review_approved -> done`

Guardrails:
- `reviewer` cannot equal `owner`
- only the reviewer may move a task to `review_approved`
- only the owner may close a `review_approved` task to `done`

## 5. Work Order

Every LLM should follow this order:

1. Review tasks assigned to you as reviewer
2. Finalize your own `review_approved` tasks
3. Continue your `in_progress` tasks
4. Pick unblocked `todo` tasks assigned to you

## 6. Local Runtime

Prepare the local environment:

```bash
bash scripts/setup-llm-cli.sh
```

Run the local collaboration control plane from the repo root:

```bash
bash scripts/run-supervisor.sh --verbose
bash scripts/run-dashboard.sh
```

## 7. First Smoke Test

Submit one signed dev packet, wait for the durable materialization receipt, and
let the supervisor launch the exact task generation.

Then verify:
- `ai-status.json` contains `DEMO-001`
- `current-work.md` refreshed
- dashboard shows the task
- supervisor shows heartbeat or queue activity
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
        target_root / "LLM_ONBOARDING.md",
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


def assert_noncanonical_bundle_target(target_root: Path) -> Path:
    resolved_target = target_root.expanduser().resolve()
    protected_roots = {ROOT.resolve()}
    configured_status_root = str(os.environ.get("PANTHEON_STATUS_ROOT") or "").strip()
    if configured_status_root:
        protected_roots.add(Path(configured_status_root).expanduser().resolve())
    if resolved_target in protected_roots:
        raise SystemExit(
            "Refusing to bootstrap over the active Pantheon repository/status root; "
            "portable bundle writes are not protocol-v1 canonical writers."
        )
    return resolved_target


def write_bootstrap_files(target_root: Path, project_name: str, objective: str) -> None:
    target_root = assert_noncanonical_bundle_target(target_root)
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
    write_text(target_root / "current-work.md", generic_current_work(project_name, objective))
    write_text(target_root / "AI_COLLABORATION_GUIDE.md", generic_collaboration_guide(project_name, objective))
    write_text(target_root / "FOR_CLAUDE.md", agent_brief("Claude", ai_status.KNOWN_AGENTS["Claude"]["capability_lane"], project_name))
    write_text(target_root / "FOR_GEMINI.md", agent_brief("Gemini", ai_status.KNOWN_AGENTS["Gemini"]["capability_lane"], project_name))
    write_text(target_root / "FOR_CODEX.md", agent_brief("Codex", ai_status.KNOWN_AGENTS["Codex"]["capability_lane"], project_name))
    write_text(target_root / "FOR_COPILOT.md", agent_brief("Copilot", ai_status.KNOWN_AGENTS["Copilot"]["capability_lane"], project_name))
    write_text(target_root / "FOR_GROK.md", copilot_alias_brief(project_name))
    write_text(target_root / "LLM_ONBOARDING.md", llm_onboarding_doc(project_name))
    write_text(target_root / "ORCHESTRATOR_QUICKSTART.md", quickstart_doc(project_name))
    write_json(target_root / ".orchestrator" / "bundle-manifest.json", portable_manifest(project_name))
    subprocess.run(["git", "init"], cwd=str(target_root), check=True, capture_output=True)

    remove_generated_portable_runtime(target_root)


def bootstrap(target_repo: Path, project_name: str, objective: str, force: bool) -> None:
    target_repo = assert_noncanonical_bundle_target(target_repo)
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
