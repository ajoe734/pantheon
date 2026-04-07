# Portable Orchestrator Bundle

This repo now includes a reusable bundle for the local `supervisor + auto worker + dashboard` system.

## What Gets Packaged

The bundle includes:
- `.orchestrator/` runtime code, adapters, templates, and tests
- `scripts/` helpers for status sync, dashboard, supervisor launch, and LLM CLI setup
- `docs-site/` dashboard assets
- generic shared-rule docs:
  - `AI_COLLABORATION_GUIDE.md`
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
3. Use this first prompt:
   ```text
   Read AI_COLLABORATION_GUIDE.md, current-work.md, ai-status.json, and ai-activity-log.jsonl first. Follow the canonical lifecycle todo -> in_progress -> review -> review_approved -> done. Use scripts/ai-status.sh for every state change.
   ```

### Codex CLI

1. Start in the new repo root.
2. Use the same first prompt as above.
3. Keep supervisor running in a separate terminal:
   ```bash
   bash scripts/run-supervisor.sh --verbose
   ```

### Gemini CLI / Copilot

- run `bash scripts/setup-llm-cli.sh` first so local settings are synchronized
- start the tool from the new repo root
- point it at the same canonical files and same lifecycle rules

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

## Important Notes

- the bundle defaults `github_bus.enabled = false`
- enable GitHub bus only after the new repo has its own GitHub destination configured
- `review_approved` is not final completion; the owner must still finalize it to `done`
- run everything from the new repo root only
