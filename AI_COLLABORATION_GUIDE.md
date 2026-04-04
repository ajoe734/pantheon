# AI Collaboration Guide

Last updated: 2026-04-04
Status: canonical collaboration rules for the Pantheon project

## 0. Repository Architecture (2026-04-04 — migration complete)

**You are in the `pantheon` repo.** Migration from the LEAN monorepo is complete.

- **System name:** `Pantheon` — the multi-persona automated trading system we are building
- **OpenClaw** is an upstream OSS framework we integrate (like DSPy, Qlib) — it is NOT the system name

Two repos exist:
- `ajoe734/pantheon` — this repo; all services, scripts, audits, plan docs
- `ajoe734/pantheon-lean` — LEAN fork; mounted as `lean/` submodule here

**Working boundary:**

| Path | Belongs to | Rule |
|---|---|---|
| `lean/` (submodule) | LEAN fork | `git submodule update --init` to populate. C# changes go directly to `ajoe734/pantheon-lean`. |
| `lean/Algorithm.Python/pantheon_algo/` | Pantheon ↔ LEAN bridge | Only place Pantheon Python runs inside LEAN |
| `services/` | Pantheon | All agent work happens here |
| `scripts/`, `audits/`, `*.md` plan files | Pantheon | Work here directly |

**OSS framework rule:** Each framework (DSPy, Qlib, FinRL, imitation, MLflow, OpenClaw) runs in its own Docker container with its own `services/research/<framework>/requirements.txt`. Never merge into a shared requirements file. Fork a framework only if you need to modify its core — otherwise `pip install` the pinned version.

**To initialize after cloning:**
```bash
git clone git@github.com:ajoe734/pantheon.git
cd pantheon
git submodule update --init --recursive
```

## 1. Canonical Truth

Read these in order before starting work:

1. `AI_COLLABORATION_GUIDE.md`
2. `current-work.md`
3. `ai-status.json`
4. `TARGET_ARCHITECTURE.md`
5. `ROADMAP.md`
6. `OSS_INTEGRATION_CHECKLIST.md`
7. `WORK_REBASELINE.md`

Source of truth split:

- `AI_COLLABORATION_GUIDE.md`: stable collaboration rules and command usage
- `ai-status.json`: machine-readable live task state, ownership, blockers, handoffs
- `ai-activity-log.jsonl`: append-only activity history
- `current-work.md`: generated human-readable sprint snapshot
- `TARGET_ARCHITECTURE.md`: target-state product architecture
- `ROADMAP.md`: epic-level delivery plan aligned to the target architecture
- `OSS_INTEGRATION_CHECKLIST.md`: execution checklist for named upstream OSS components
- `WORK_REBASELINE.md`: corrected work breakdown and audit scope after reclassifying upstream OSS components
- `docs-site/index.html`: visual collaboration panel

OSS interpretation rule:

- if a document names a real upstream project such as `OpenClaw`, `DSPy`, `Qlib`, `FinRL`, `TRL`, `imitation`, `MLflow`, `W&B`, `RLlib`, or `Tune`, assume that task means upstream integration unless a local replacement is explicitly stated
- do not silently treat upstream project names as conceptual boxes only

Compatibility-only files:

- `COLLAB.md`
- `PROGRESS.md`
- `dashboard.html`
- root `index.html`
- root `ai-status.sh`
- root `ai-status.py`

These are wrappers, not truth.

## 2. Collaboration Model

Separate stable capability lanes from sprint ownership.

### Capability Lanes

- `Claude`: execution plane, control plane, governance review
- `Gemini`: GCP, CI/CD, runtime packaging, worker operations
- `Codex`: integration contracts, status system, schema, acceptance
- `Grok`: coding assist, research ingestion, external search, spec review, critique

Recommended local mode for `Grok`:

- prefer `VS Code` chat if your Grok is exposed there as a coding assistant
- use `Grok Web` / browser chat as fallback when the task is mainly research or external search
- do not require API automation for normal collaboration
- route it through the same status / handoff system as the other agents

### Sprint Ownership

Current owner, reviewer, dependencies, and next steps live in `ai-status.json`.

Rules:

- each task has exactly one `owner`
- `reviewer` cannot equal `owner`
- blocked tasks must include `waiting_for`
- done transitions must include a checkpoint message
- interface changes should be reflected through status updates, not hidden in chat only

## 3. Status Commands

Use the status script instead of manually editing multiple Markdown tables.

```bash
AI_NAME=Codex ./scripts/ai-status.sh assign <task-id> <owner> <reviewer> "Optional title"
AI_NAME=Codex ./scripts/ai-status.sh start <task-id> "Started implementation"
AI_NAME=Codex ./scripts/ai-status.sh progress <task-id> "Finished contract draft"
AI_NAME=Codex ./scripts/ai-status.sh handoff <task-id> Gemini "Please review the payload shape"
AI_NAME=Codex ./scripts/ai-status.sh blocker <task-id> "Waiting for broker decision" Gemini
AI_NAME=Codex ./scripts/ai-status.sh done <task-id> "Completed and verified"
./scripts/sync-state.sh
```

Command effects:

1. update `ai-status.json`
2. append `ai-activity-log.jsonl`
3. regenerate `current-work.md`
4. mirror state files into `docs-site/`

Optional environment variables:

- `TASK_PHASE`
- `TASK_TITLE`
- `TASK_BRANCH`
- `TASK_ARTIFACTS`
- `TASK_ACCEPTANCE`
- `TASK_DEPENDS_ON`

## 4. Working Agreement

- prefer one source of truth per concern
- do not create a second collaboration tracker outside the canonical files
- use blockers for waits, not ad-hoc side notes
- use handoffs when another agent now owns the next action
- keep long history in `ai-activity-log.jsonl`, not in `current-work.md`
- if a file is a compatibility wrapper, keep it short and point to the canonical path

## 5. Execution Order

Every collaborating LLM should follow this work order by default:

1. **Review first**
- check whether you are currently the `reviewer` for any task that is in `review`
- complete those reviews before starting new implementation work
- if review fails, write the required changes into status and hand the task back clearly

2. **Then do your own assigned work**
- look for tasks where you are the `owner`
- prefer tasks that are `in_progress` first
- then pick tasks that are `todo` but already unblocked by dependencies
- do not idle if one of your assigned tasks is already safe to start

3. **Then help elsewhere**
- if you have no pending reviews and no unblocked owned work, check whether another task is unowned by active work or stalled but still within your capability lane
- if you can safely move it forward, claim it instead of waiting
- when claiming helper work, reassign yourself as `owner` and set the original owner as `reviewer`
- this keeps authorship clear and gives the original owner a structured chance to accept or correct the work

4. **Only then remain blocked**
- if you truly cannot review, cannot advance your own tasks, and cannot safely help another task, log a blocker
- do not stop at "waiting" if useful work is available

### Helper Claim Rule

When helping with a task that was originally assigned to someone else:

1. use `assign` to move the task to yourself
2. set the original owner as the new reviewer
3. leave a clear message in `progress` saying why you claimed it
4. after implementation, `handoff` back to the original owner for review

Example:

```bash
AI_NAME=Codex bash scripts/ai-status.sh assign REG-001 Codex Gemini
AI_NAME=Codex bash scripts/ai-status.sh progress REG-001 "Claimed REG-001 because it is unblocked and can be advanced now; Gemini will review the updated contract."
AI_NAME=Codex bash scripts/ai-status.sh handoff REG-001 Gemini "Registry contract advanced and ready for original-owner review."
```

### Review Completion Rule

A review is not complete until the reviewer does one of these:

- approves by handing off with explicit acceptance
- writes concrete required changes into `review_notes` or task `next`
- records a blocker if review cannot proceed because files or artifacts are missing

Do not leave review feedback only in chat.

## 6. Dashboard

The internal collaboration panel lives at `docs-site/index.html`.

Start it locally with:

```bash
bash scripts/launch-docs-site.sh
```

The dashboard renders:

- workload split
- agent lanes
- task board
- handoff queue
- blockers
- sprint snapshot
- recent activity

If the panel looks stale:

```bash
bash scripts/sync-state.sh
```

## 7. Prompt Prefix

Use this prefix when handing work to any collaborating LLM:

```text
Please read these files before starting:
- AI_COLLABORATION_GUIDE.md
- current-work.md
- ai-status.json
- TARGET_ARCHITECTURE.md
- ROADMAP.md
- OSS_INTEGRATION_CHECKLIST.md
- WORK_REBASELINE.md

You are [Claude/Gemini/Codex/Grok].
Follow the current owner/reviewer assignments from ai-status.json.
Update progress through scripts/ai-status.sh instead of manually editing multiple Markdown files.
Work in this order: finish assigned reviews first, then your own unblocked tasks, then claim other safe tasks and set the original owner as reviewer.
```
