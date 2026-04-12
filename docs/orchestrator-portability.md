# Portable Orchestrator Bundle

This repo now includes a reusable bundle for the local `supervisor + auto worker + dashboard` system.

## What Gets Packaged

The bundle includes:
- `.orchestrator/` runtime code, adapters, templates, and tests
- `scripts/` helpers for status sync, discussion planning, dashboard, supervisor launch, and LLM CLI setup
- `docs-site/` dashboard assets
- `docs/02-architecture/consensus/phase1/` planning artifacts and templates
- generic shared-rule docs:
  - `AI_COLLABORATION_GUIDE.md`
  - `LLM_ONBOARDING.md`
  - `FOR_CLAUDE.md`
  - `FOR_GEMINI.md`
  - `FOR_CODEX.md`
  - `FOR_COPILOT.md`
  - `FOR_GROK.md` as a legacy alias note
- fresh canonical state files:
  - `ai-status.json`
  - `ai-activity-log.jsonl`
  - `current-work.md`

It does **not** carry over Pantheon runtime junk such as:
- `.orchestrator/state.json`
- `.orchestrator/event-queue.jsonl`
- `.orchestrator/approval-queue.json`
- `.orchestrator/planning-state.json`
- `.orchestrator/logs/`
- `.orchestrator/backups/`
- generated dashboard mirror JSON files

## Option A: Bootstrap Directly Into A New Repo

From this repo:

```bash
cd /home/ajoe734/code/pantheon
python3 scripts/orchestrator_bundle.py bootstrap \
  --target-repo /path/to/new-project \
  --project-name "New Project" \
  --objective "Stand up the New Project delivery system with shared supervisor, auto workers, and dashboard."
```

Then inside the new repo:

```bash
cd /path/to/new-project
bash scripts/setup-llm-cli.sh
bash scripts/run-supervisor.sh --verbose
bash scripts/run-dashboard.sh
```

Immediately after bootstrap, replace any source-repo assumptions with new-project semantics:
- update `AI_COLLABORATION_GUIDE.md` to point at the new repo's real canonical docs
- adjust `FOR_*.md` briefs if the lane descriptions need new-project context
- keep `ai-status.json` canonical layers aligned with the files that actually exist in the new repo

## Option B: Export A Tarball

```bash
cd /home/ajoe734/code/pantheon
python3 scripts/orchestrator_bundle.py export \
  --output /tmp/orchestrator-bundle.tar.gz \
  --project-name "New Project" \
  --objective "Stand up the New Project delivery system with shared supervisor, auto workers, and dashboard."
```

Then extract it into the new repo root:

```bash
mkdir -p /path/to/new-project
cd /path/to/new-project
tar -xzf /tmp/orchestrator-bundle.tar.gz
bash scripts/setup-llm-cli.sh
```

## How To Let LLM CLIs Run In The New Repo

### Claude Code

1. Run:
   ```bash
   bash scripts/setup-llm-cli.sh
   ```
2. Start Claude Code from the new repo root.
3. Use the repo-aware first prompt printed by:
   ```bash
   python3 scripts/ai_status.py prompt
   ```

### Codex CLI

1. Start in the new repo root.
2. Use the same repo-aware prompt from `python3 scripts/ai_status.py prompt`.
3. Keep supervisor running in a separate terminal:
   ```bash
   bash scripts/run-supervisor.sh --verbose
   ```

### Gemini CLI / Copilot

- run `bash scripts/setup-llm-cli.sh` first so local settings are synchronized
- start the tool from the new repo root
- point it at the prompt from `python3 scripts/ai_status.py prompt`, which follows the canonical files currently declared in `ai-status.json`

## Recommended First Smoke Test In The New Repo

```bash
cd /path/to/new-project
AI_NAME=Codex TASK_PHASE="Foundation" TASK_SUMMARY_ZH="建立第一個遷移後任務。" ./scripts/ai-status.sh assign DEMO-001 Codex Claude "First migrated task"
AI_NAME=Codex ./scripts/ai-status.sh start DEMO-001 "Started the first migrated task"
./scripts/sync-state.sh
```

Then confirm:
- dashboard shows the task
- supervisor terminal shows heartbeat and queue activity
- `current-work.md` updates automatically

Optional planning-mode smoke test:

```bash
cd /path/to/new-project
./scripts/planning-state.sh start phase1 "Kick off discussion planning"
./scripts/planning-state.sh readout Codex submitted "Codex readout is ready"
./scripts/planning-state.sh consensus ready_for_human "Consensus packet drafted"
./scripts/sync-state.sh
```

## Important Notes

- the bundle defaults `github_bus.enabled = false`
- enable GitHub bus only after the new repo has its own GitHub destination configured
- `review_approved` is not final completion; the owner must still finalize it to `done`
- run everything from the new repo root only
