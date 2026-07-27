# AI Collaboration Guide

Last updated: 2026-05-17
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
2. `ai-status.json`
3. `current-work.md` as a human summary only
4. the active planning session README named by `.orchestrator/planning-state.json` when `discussion_planning` is active
5. the active `planning-session.json` named by `.orchestrator/planning-state.json` when `discussion_planning` is active
6. `TARGET_ARCHITECTURE.md`
7. `CANONICAL_DOCUMENT_MAP.md`
8. `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`
9. `ROADMAP.md`
10. `DEVELOPMENT_WORKBREAKDOWN.md`
11. `WORKBENCH_DELIVERY_BACKLOG.md`
12. `DELIVERY_CLOSURE_AND_LOOP_STATES.md`
13. `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
14. the L1 policy file for the topic you are touching
15. `OSS_INTEGRATION_CHECKLIST.md`
16. L3 supporting docs only when you need rationale or migration history

Canonical truth now uses five layers:

### L0 Collaboration & State

- `AI_COLLABORATION_GUIDE.md`: stable collaboration rules and command usage
- `ai-status.json`: machine-readable live task state, ownership, blockers, handoffs
- `ai-activity-log.jsonl`: append-only activity history
- `.orchestrator/skills/worker-anchor-commit.md`: mid-task anchor
  commit rules for fragile shared worktree surfaces
- `.orchestrator/skills/task-closeout-finalization.md`: owner finalization, commit, and publication rules for `review_approved -> done`

### L0.5 Derived Narrative

- `current-work.md`: generated human-readable sprint snapshot

### L1 Platform Architecture & Policy

- `TARGET_ARCHITECTURE.md`: platform north-star and cross-plane architecture
- `OPENCLAW_RUNTIME_CONTRACT.md`: upstream runtime boundary and adapter contract
- `PERSONA_RUNTIME_MODEL.md`: persona registry/session/runtime model
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`: binding, deployment, and write-owner semantics
- `PAPER_CANARY_LIVE_POLICY.md`: deployment-stage policy and thresholds
- `ROLLBACK_AND_POSITION_SEMANTICS.md`: rollback action semantics and position handling
- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`: lineage and telemetry storage truth model
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`: evolution governance and threshold policy
- `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`: cross-service consistency, outbox/inbox, and saga policy
- `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`: kill switch and safe mode fast-path policy
- `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`: multi-persona synthesis and sponsor-resolution policy
- `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`: telemetry ingest shock-absorption and storage-layer policy
- `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`: database ownership and shared-cluster write-boundary policy
- `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`: event ordering, delivery guarantees, and idempotency policy
- `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md`: cooldown, observation window, and convergence policy
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`: BFF HA, degraded control-plane operation, and operator fallback policy
- `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`: loop trigger model, race-condition resolution, and scheduling boundaries

### L2 Planning & Execution

- `CANONICAL_DOCUMENT_MAP.md`: canonical routing and precedence
- `DOCUMENT_AUTHORITY_AND_RECORD_BOUNDARY.md`: blueprint-vs-record governance rules
- `ROADMAP.md`: phased program plan and critical path
- `DEVELOPMENT_WORKBREAKDOWN.md`: full platform backlog and task decomposition
- `WORKBENCH_DELIVERY_BACKLOG.md`: remaining module-level productization backlog
- `DELIVERY_CLOSURE_AND_LOOP_STATES.md`: truthful closure semantics for packet loops
- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`: evidence ladder for runtime and system proof claims
- `OSS_INTEGRATION_CHECKLIST.md`: upstream OSS integration evidence checklist

Planning sessions remain working records, even when active:

- the active planning session README named by `.orchestrator/planning-state.json`
- the active `planning-session.json` named by `.orchestrator/planning-state.json`

### L3 Supporting Design & Migration

- `CANONICAL_CONTRACT_MIGRATION_DECISION.md`: migration decision and cutover rationale
- `WORK_REBASELINE.md`: historical work-model reset after OSS reinterpretation
- `Pantheon_總索引版系統分析文件.md`: north-star product blueprint
- `Pantheon_資料表_Schema_設計版.md`: future-state data/storage design
- `Pantheon_API_Service_Contract_設計版.md`: future-state service/API design

Layer rules:

- L0 state files coordinate work and do not define product semantics by themselves.
- L0.5 derived files help humans navigate but never outrank machine-readable state.
- L1 defines current canonical architecture and policy.
- L2 may sequence work but must not override L1 semantics.
- L3 may explain or motivate decisions but does not override L1/L2.
- `CANONICAL_DOCUMENT_MAP.md` is the lookup table when two docs seem close in scope.
- planning sessions, review docs, and execution artifacts are records unless explicitly promoted.

### State Placement Rules

Do not treat `ai-status.json` as a dump for every mode's internal state.

Use this decision rule:

- if another agent must still see the fact after process restart, worker replacement, or mode switch, it may belong in `ai-status.json`
- if the value is mainly for one active mode's execution loop, debug, retry, approval, or rendering logic, it belongs in that mode's own state file instead

What belongs in `ai-status.json`:

- durable task identity and scope: `id`, `title`, `phase`, `depends_on`, `artifacts`, acceptance summary
- canonical ownership and lifecycle: `owner`, `reviewer`, `status`, `waiting_for`, `terminal_outcome`
- durable coordination facts shared across modes: blocker state, handoff state, review approval, final delivery summary
- concise mode results that other modes must inherit: accepted planning outcome materialized as tasks, finalized delivery commit hash, approved owner/reviewer reassignment

What must not be stored in `ai-status.json`:

- worker/runtime internals such as `pid`, `session_id`, `queue_event_id`, `attempt_count`, `next_retry_at`, `last_heartbeat_at`, `dispatch_pause`, `provider quota`, or raw provider errors
- approval workflow internals such as tool payloads, resume overrides, approval signatures, or broker evidence blobs
- planning baton internals such as round ownership, current draft editor, intermediate objections, or per-round review packet metadata
- dashboard-only derived values such as occupancy summaries, truth mismatches, humanized badges, or stale/runtime reconciliation helpers

State file ownership:

- `ai-status.json`: cross-mode durable execution truth
- `.orchestrator/planning-state.json`: planning mode machine-readable session state
- `.orchestrator/state.json`: supervisor / queue / worker / runtime state
- `.orchestrator/approval-queue.json`: approval queue and approval lifecycle state
- `ai-activity-log.jsonl`: append-only historical events and audit trail
- `current-work.md` and dashboard bundle: derived human-readable summaries only

Size rule:

- prefer storing a short stable summary in `ai-status.json` and keep the heavy payload in the mode-specific file or evidence file
- if a field would grow on every poll / retry / heartbeat, it does not belong in `ai-status.json`
- if removing the field would not change task ownership, blocker truth, review truth, or delivery truth, it does not belong in `ai-status.json`

Compatibility-only files:

- `COLLAB.md`
- `PROGRESS.md`
- `dashboard.html`
- root `index.html`
- root `ai-status.sh`
- root `ai-status.py`

These are wrappers, not truth.

Derived but important:

- `docs-site/index.html`: visual collaboration panel

OSS interpretation rule:

- if a document names a real upstream project such as `OpenClaw`, `DSPy`, `Qlib`, `FinRL`, `TRL`, `imitation`, `MLflow`, `W&B`, `RLlib`, or `Tune`, assume that task means upstream integration unless a local replacement is explicitly stated
- do not silently treat upstream project names as conceptual boxes only

Cutover rule:

- run the supervisor, dashboard, status sync, and GitHub approval bus from the `pantheon` repo only
- do not run `scripts/launch-docs-site.sh`, `scripts/ai-status.sh`, or any orchestrator command from the old `Lean` workspace
- the `Lean` checkout is now execution-side only and should not host Pantheon collaboration state

## 2. Collaboration Model

Separate stable capability lanes from sprint ownership.

### Capability Lanes

- `Claude`: execution plane, control plane, governance review
- `Gemini`: GCP, CI/CD, runtime packaging, worker operations
- `Gemini2`: GCP, CI/CD, runtime packaging, worker operations
- `Codex`: integration contracts, status system, schema, acceptance
- `Codex2`: integration contracts, schema, acceptance, sidecar review
- `Copilot`: coding assist, research ingestion, external search, spec review, critique

Recommended local mode for `Copilot`:

- prefer `VS Code` chat if your Copilot lane is exposed there as a coding assistant
- use a browser-hosted coding/research chat as fallback when the task is mainly research or external search
- do not require API automation for normal collaboration
- route it through the same status / handoff system as the other agents

### Sprint Ownership

Current owner, reviewer, dependencies, and next steps live in `ai-status.json`.

Rules:

- each task has exactly one `owner`
- `reviewer` cannot equal `owner`
- blocked tasks must include `waiting_for`
- every task must pass through `review -> review_approved -> done`
- direct `done` from `todo` or `in_progress` is not allowed
- only the `reviewer` may move a task into `review_approved`
- only the `owner` may finalize a `review_approved` task into `done`
- done transitions must include a checkpoint message
- only use `supersede` for legacy or duplicate lanes that were explicitly replaced by an accepted consensus packet or a newer execution slice
- interface changes should be reflected through status updates, not hidden in chat only

Lifecycle rule:

- `todo` / `in_progress`: owner implementation work
- `review`: reviewer must either approve or request concrete changes
- `review_approved`: reviewer gate passed; the task returns to the owner for finalization
- `done`: owner has finished final checks, accepted the approved state, and formally closed the task
- `supersede`: exceptional retirement path for obsolete lanes; it closes the task with a terminal note instead of pretending the original scope was fully implemented

### Task Closeout And Publication

`review_approved` is not terminal. It means the reviewer gate passed and the owner must run the closeout checklist before `done`.

Closeout is governed by `.orchestrator/skills/task-closeout-finalization.md`.

Owner finalization requirements:

- re-read the task brief, reviewer approval, and touched artifacts
- update required task-specific records, docs, evidence notes, or handoff / acceptance packets
- run focused verification and record exact commands or evidence
- inspect `git status --short` and keep task-owned changes separate from unrelated dirty worktree changes
- create a task-scoped commit whenever the task changed repo files and an isolated commit is possible
- use `AI_NAME=<Owner> ./scripts/ai-status.sh done <task-id> "<checkpoint message>"` only after closeout is complete

Commit requirements:

- subject includes the task id
- body includes `LLM-Agent: <owner>`, `Task-ID: <task-id>`, and `Reviewer: <reviewer>`
- body includes a concise verification summary when tests or checks were run

Publication rule:

- closeout is not complete until the finished work is published to the configured upstream whenever that is safely possible
- `scripts/ai-status.sh done` records branch, commit, dirty count, remote/upstream, and push status
- after the task-scoped commit, `done` transition, generated state/archive update, and any required state/archive commit, run a normal non-force `git push` to the configured upstream
- if delivery metadata shows `push_status: ahead`, the task is publish-incomplete until pushed or an explicit human hold says not to publish
- chair man must approve a normal non-force `git push` when branch/upstream are clear, closeout commit metadata matches the task or closeout batch, and no human hold is present
- never use force, mirror, delete, all-branch, or tag-wide pushes as routine closeout

### Multi-Branch Integration Policy

**Operational source of truth:** `docs/conventions/GIT_WORKFLOW.md`. This
section is the short pointer; if anything below conflicts with that document,
the document wins.

**AI / Claude session ops notes:** `docs/conventions/CLAUDE_SESSION_NOTES.md`
documents the bash-tool transport workarounds that every Claude / Codex
session needs (use `-F` for commit messages, batch `git add` in 3–4 paths,
avoid jq pipelines, prefer `scripts/git/safe_pr.sh` over hand-rolled
multi-step closeouts). Read it once at start of session.

Topology (2026-05-17 redesign, per-task PR model):

- `master`: canonical / production source. PR-only with branch protection
  (3 required status checks: Commit trailers / Runtime mirror guard /
  Smoke acceptance). Receives `promote/<v>` and `hotfix/<topic>` PRs.
- `dev`: integration line. PR-only (same 3 status checks). Every
  `task/<TASK-ID>` PR auto-merges into `dev` once CI is green.
- `task/<TASK-ID>`: ephemeral per-task branch. One branch per task,
  pushed by one worker, auto-deleted by GitHub when its PR merges.
  Replaces the retired `worker/<name>` permanent branches.
- `publish/v<YYYY>.<MM>.<DD>.<N>`: immutable snapshot cut from `dev` by
  the daily `nightly-publish-cut.yml` workflow (cron 03:00 UTC).
- `hotfix/<topic>`: cut from `master`, dual-PR back into both `master`
  and `dev`. Auto-deleted when both PRs merge.

Cadence:

- **Continuous** per-task PR → `dev` auto-merge (no weekly wave gate).
- **Nightly** publish cut from `dev` if `dev` advanced since the last
  `release/v*` tag.
- Publish snapshots auto-promote to `master` after **1-day soak** via
  `.github/workflows/publish-promote.yml`, gated by the `regression/<v>`
  issue label.
- A `task/<id>` PR open > 24 h without merging is a process violation
  and chair-review must surface it.

Branch retirement:

- `task/*` and `hotfix/*` are auto-deleted by GitHub on PR merge; no
  manual retirement needed.
- For any other branch, tag `archive/<branch>-<YYYY-MM-DD>` with a
  message stating where the work landed.
- After the archive tag is pushed, delete the remote branch with `git
  push origin --delete <branch>` (non-force, non-mirror).
- Do not delete a branch still ahead of its target without explicit
  acceptance from chair-review.

Working tree durability:

- uncommitted worktree diffs are fragile and must not be treated as
  durable handoff state
- before reassignment, interruption, or task switching, commit any
  non-trivial design work on its `task/<TASK-ID>` branch or state that
  the remaining diff is disposable
- docs, `.orchestrator/skills/*`, config/workflow files, and supervisor
  dispatch or routing contact points must use task branch + anchor
  commit + PR; do not leave them as session-only diffs
- when parallel workers touch the same file at different layers, the
  anchor commit message must identify the owned layer and any boundary
  it intentionally leaves unchanged
- auto workers execute inside task-specific git worktree leases; the
  supervisor/dashboard root is not a shared execution cwd, and
  `PANTHEON_STATUS_ROOT` keeps state updates centralized
- if `dev` advances, rebase or merge the task branch as committed work;
  do not make `git stash pop` the normal preservation path

Cross-repo FE task PRs (`ajoe734/execute-plans`):

- `execute-plans` mirrors the same `task/<TASK-ID>` → `dev` model as
  this repo. Its GitHub-configured default branch is `dev`, not `main`;
  `.orchestrator/multi_repo_registry.py` records this as
  `default_branch` for the `execute_plans` repo id, which is what
  `scripts/ai_status.py`'s `done`-finalize gate uses to verify a task
  branch's HEAD merged into the correct target before allowing closeout.
- open FE task PRs with `--base dev`, the same as a pantheon task PR.
  Do not PR a `task/<TASK-ID>` branch straight into `execute-plans`
  `main` — that bypasses the integration line and is the dev/main drift
  root cause fixed by `OPS-EP-BRANCH-TARGETING-001` (a stale `main`
  value in the registry previously made the `done` gate check ancestry
  against the wrong branch).
- `execute-plans` `main` is promoted separately and is not a valid
  direct target for a task PR.

### Shared Deploy Workflow Ownership

Shared CI/CD workflows (`nonprod-deploy.yml` and its execute-plans
counterpart) are fleet infrastructure, not something a task owns. No task
may run `gh workflow disable` against one or `gh run cancel` against a run
it did not dispatch, even to protect its own proof run. Use the workflow's
`concurrency:` group or the dev environment lease
(`scripts/dev_environment_lease.py`) instead. Full rule, rationale, and the
`scripts/check_shared_deploy_workflow_disabled.py` detection guard:
`docs/conventions/GIT_WORKFLOW.md` § 9.1.

### Discussion Planning Mode

`discussion_planning` is additive. It does not replace the current execution lifecycle.

Use it before materializing execution tasks when we still need written consensus on:

- architecture or source-of-truth boundaries
- delivery order / wave order
- task slicing and reviewer assignment

Planning mode now follows two stages:

1. `document_reconciliation`
2. `execution_planning`

That means the session must first identify whether canonical blueprint or planning docs are insufficient, and either:

- update the canonical docs
- or explicitly conclude that no canonical doc change is needed

Only then may the session move to final human approval and execution materialization.

Canonical planning workspace:

- `docs/02-architecture/consensus/phase1/README.md`
- `docs/02-architecture/consensus/phase1/planning-session.json`
- `docs/02-architecture/consensus/phase1/starter-draft.md`
- `docs/02-architecture/consensus/phase1/consensus-packet.md`
- `docs/02-architecture/consensus/phase1/*-readout.md`
- `docs/02-architecture/consensus/phase1/review-round-*.md`

Rules:

- only the current baton owner edits `starter-draft.md`
- reviewers write cited comments in the current round file instead of directly rewriting the shared draft
- `planning-session.json` is the machine-readable source of truth for planning state
- `.orchestrator/planning-state.json` is derived for dashboard rendering
- document reconciliation must be completed before `ready_for_human`, `human-gate approved`, or `materialize`
- execution tasks still live in `ai-status.json`; planning drafts should not be inserted there prematurely

Typical flow:

1. all lanes read L0 -> L1 -> L2 canonical docs
2. each lane writes an independent readout
3. `Codex` creates the first starter draft
4. `Codex2 -> Gemini -> Copilot -> Claude` run cited cross-review rounds
5. unresolved semantic conflicts become explicit `human_required` items
6. `Claude` synthesizes the final `consensus-packet.md`
7. after human acceptance, convert the agreed slices into execution tasks through `scripts/ai-status.sh`

## 3. Status Commands

Use the status script instead of manually editing multiple Markdown tables.

```bash
AI_NAME=Codex ./scripts/ai-status.sh assign <task-id> <owner> <reviewer> "Optional title"
AI_NAME=Codex ./scripts/ai-status.sh start <task-id> "Started implementation"
AI_NAME=Codex ./scripts/ai-status.sh progress <task-id> "Finished contract draft"
AI_NAME=Codex ./scripts/ai-status.sh handoff <task-id> Gemini "Please review the payload shape"
AI_NAME=Gemini REVIEW_FILE=path/to/review.md REVIEW_NOTES_ZH="審查通過||後續追蹤事項" ./scripts/ai-status.sh approve <task-id> "Review approved and returned to the owner for finalization"
AI_NAME=Codex ./scripts/ai-status.sh progress <task-id> "Owner picked up the approved task for final checks"
AI_NAME=Codex ./scripts/ai-status.sh blocker <task-id> "Waiting for broker decision" Gemini
AI_NAME=Codex ./scripts/ai-status.sh done <task-id> "Owner finalized approved task and closed it"
AI_NAME=Codex ./scripts/ai-status.sh supersede <task-id> "Superseded by the accepted execution slice; retire this legacy lane." <replacement-task-id>
./scripts/sync-state.sh
```

Planning commands:

```bash
./scripts/planning-state.sh start phase1 "Kick off the discussion planning session"
./scripts/planning-state.sh readout Codex submitted "Codex readout is ready"
./scripts/planning-state.sh baton Codex2 Gemini "Baton moved to Codex2 for cited cross-review"
./scripts/planning-state.sh round 1 open "Opened review round 1"
./scripts/planning-state.sh issue DIV-001 high human_required "Ownership/source-of-truth conflict"
./scripts/planning-state.sh consensus ready_for_human "Consensus packet drafted and waiting for human acceptance"
./scripts/planning-state.sh human-gate approved "Human accepted the planning packet"
./scripts/planning-state.sh propose-task W3-001A Codex2 Claude "Callcenter & CTI correlation baseline"
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
- `REVIEW_FILE`
- `REVIEW_NOTES_ZH` (`||` separated when setting multiple notes)

### Python Test Environment Provisioning

`services.*`, `scripts.*`, and `integrations.*` are top-level import names, so
Python resolves them only from `sys.path`. Running the suite from the
repository root, or exporting `PYTHONPATH`, happens to work — but it makes every
test result depend on where the interpreter was started, and it is why dotted
`unittest` and direct-file execution used to fail from any other directory.

The repository is therefore installable. Provision it once per checkout before
running tests:

```bash
# auto worker, inside your task worktree
python3 scripts/dev/provision_python_distribution.py
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
"$PANTHEON_PY" -m pytest -q services/telemetry
```

Rules:

- **Use the script, never a bare `pip install -e .`.** An editable install
  writes an absolute mapping to one checkout. Every auto worker gets its own
  git worktree while sharing the host interpreter, so a bare install into that
  shared interpreter silently rebinds `services` for every other worker. The
  script installs into a checkout-scoped `.venv-pantheon` instead and fails
  closed if the mapping ever points somewhere else.
- **Provisioning does not dirty your worktree.** `.venv-pantheon/` matches the
  existing `.venv-*/` ignore rule, and no install artifact is written into the
  tree.
- **Provisioning installs import paths only.** `pyproject.toml` declares no
  dependencies; `requirements.txt` and the per-service requirements files stay
  the dependency source of truth. The dependencies themselves are inherited
  from a separately selected interpreter — see below.
- `--mode current --allow-system-interpreter` installs into the running
  interpreter. It is for disposable single-checkout containers only — dev CI
  uses it in the `Python packaging provision` job of `branch-ci.yml`, which is
  the same entry point, so CI and workers install the same distribution. In this
  mode the running interpreter must already have the dependencies, which is why
  the CI job installs `requirements.txt` in the step before.
- `--check-only` verifies an existing provision without installing.
- `--recreate` rebuilds `.venv-pantheon` from scratch.

#### Where the dependencies come from

You do not have to have pytest installed before you run the command above, and
you should not assume you do: on an auto-worker host `command -v python3` is
`/usr/bin/python3`, which has neither pytest nor any service dependency.

Provisioning therefore resolves a **dependency interpreter** separately from the
interpreter you started it with, and probes each candidate for `pytest` before
accepting it. In order:

1. `--dependency-python`
2. `$PANTHEON_DEPENDENCY_PYTHON`
3. the interpreter you ran the script with
4. `$VIRTUAL_ENV`
5. `<checkout>/.venv`
6. `<main worktree>/.venv` — derived with `git rev-parse --git-common-dir`, so a
   task worktree finds the main checkout's environment. **This is the one that
   normally answers for an auto worker.**

`.venv-pantheon` is then created *by* that interpreter and inherits its
packages, and provisioning ends by re-proving that the interpreter it hands back
can import pytest from a foreign working directory with no `PYTHONPATH`.

Two consequences worth knowing:

- **Provisioning fails closed rather than returning an unusable interpreter.**
  If no candidate has the dependencies, the script exits non-zero and prints the
  whole candidate table with the reason each entry was rejected. A run that
  succeeds is a run whose `"$PANTHEON_PY" -m pytest` will not die on
  `No module named pytest`.
- **An interpreter you name explicitly is authoritative.** `--dependency-python`
  and `$PANTHEON_DEPENDENCY_PYTHON` are never silently replaced by a fallback,
  because a silent fallback is how the wrong environment gets used unnoticed.
  Use them when a host has several environments and you want a specific one:

  ```bash
  PANTHEON_DEPENDENCY_PYTHON=/path/to/python \
    python3 scripts/dev/provision_python_distribution.py
  ```

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
- first finalize any task where you are the `owner` and the task is already `review_approved`
- then continue tasks that are `in_progress`
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

### Provider Failure Reassignment

- if an auto worker repeatedly fails on the same task, the supervisor should not keep hammering the same provider forever
- after repeated provider failures, the supervisor may reassign:
  - `review` tasks by swapping the `reviewer`
  - `todo` / `in_progress` tasks by swapping the `owner`
- reassignment should be written back into `ai-status.json` so every LLM sees the new owner/reviewer assignment from the canonical task board
- inbox fallback is still allowed when no safe alternate owner/reviewer exists, but reassignment is preferred over repeatedly retrying a provider that is already failing

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

- approves with `approve`, which moves the task to `review_approved` and hands it back to the owner for finalization
- uses `reopen` plus concrete required changes when the task must return to implementation
- records a blocker if review cannot proceed because files or artifacts are missing

Do not leave review feedback only in chat.
Do not use `done` as the reviewer.

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
- CANONICAL_DOCUMENT_MAP.md
- ROADMAP.md
- DEVELOPMENT_WORKBREAKDOWN.md
- OSS_INTEGRATION_CHECKLIST.md
- the L1 policy document that matches your task
- L3 supporting docs only if you need rationale or migration history

You are [Claude/Claude2/Gemini/Gemini2/Codex/Codex2/Copilot].
Follow the current owner/reviewer assignments from ai-status.json.
Update progress through scripts/ai-status.sh instead of manually editing multiple Markdown files.
Work in this order: finish assigned reviews first, then finalize your own `review_approved` tasks, then continue your own unblocked tasks, then claim other safe tasks and set the original owner as reviewer.
```
