# Agent Orchestrator

## What This Is

This repo now contains a WSL-native orchestrator under `.orchestrator/` that treats shared-state files as the source of truth:

- `ai-status.json`
- `ai-activity-log.jsonl`
- `current-work.md`
- `docs-site/index.html`

The orchestrator does not rewrite large prompts. It only emits a minimal wake-up event and lets each provider read the shared-state files for full context.

## Why We Do Not Use Windows GUI Automation

This design does not depend on:

- window focus
- clipboard injection
- SendKeys
- browser automation
- desktop clicking

Instead it uses verified local capabilities:

- provider CLIs when they exist
- VS Code workspace files
- Claude hooks plus a local permission broker
- file inbox fallback when no verified shell entrypoint exists

## Runtime Layout

Text form:

1. `.orchestrator/watch_events.py` watches `ai-status.json`
2. It de-duplicates transitions and appends minimal wake jobs to `.orchestrator/event-queue.jsonl`
3. `.orchestrator/supervisor.py` drains that queue, starts provider workers, and tracks runtime state in `.orchestrator/state.json`
4. Claude approvals flow through `.orchestrator/approval_queue.py` and the local MCP permission broker
5. Activity is appended to `ai-activity-log.jsonl`
6. GitHub approval bus mirrors review/blocker state through `.orchestrator/github_bus.py`

The shared-state files remain canonical. Queue, approval, and worker session files under `.orchestrator/` are transient control-plane state.

The supervisor now also understands cross-repo delivery handoffs through `.coordination/*.yaml` and can mirror them into GitHub coordination issues while keeping Pantheon as the runtime authority.

## Start The System

Run the full local orchestrator:

```bash
python3 .orchestrator/supervisor.py
```

One-shot processing pass:

```bash
python3 .orchestrator/supervisor.py --once
```

Watcher-only scan:

```bash
python3 .orchestrator/watch_events.py --once
```

## Test It

Refresh the capability report:

```bash
python3 .orchestrator/doctor.py
```

Queue a manual wake-up:

```bash
python3 .orchestrator/emit_test_event.py --agent claude --task-id TEST-001 --title "Manual wake-up test"
```

Queue and dispatch immediately:

```bash
python3 .orchestrator/emit_test_event.py --agent claude --task-id TEST-001 --title "Manual wake-up test" --dispatch-now
```

Run the approval queue over HTTP:

```bash
python3 .orchestrator/approval_queue.py serve --listen 127.0.0.1:8765
```

Apply provider permission settings:

```bash
python3 .orchestrator/sync_provider_permissions.py --apply
```

GitHub approval bus docs:

```bash
sed -n '1,220p' docs/github-approval-bus.md
```

Delivery coordination bus docs:

```bash
sed -n '1,260p' docs/delivery-coordination-bus.md
```

## Claude Path

- Primary path: `claude -p` with stream-json output, hook events, and a committed MCP config at `.orchestrator/claude-approval-broker.mcp.json`
- Broker path: `.orchestrator/claude_permission_prompt_mcp.py`
- Policy path: `.orchestrator/permission_broker.py`
- Queue path: `.orchestrator/approval_queue.py`

Routine read/search/repo-local edit actions are auto-allowed. Destructive actions are auto-denied. Unknown or high-risk actions are sent to the local approval queue, where you can resolve them with:

```bash
python3 .orchestrator/approval_queue.py list
python3 .orchestrator/approval_queue.py allow <approval-id>
python3 .orchestrator/approval_queue.py deny <approval-id>
```

For Claude deferred tool calls, the broker now resumes with the exact approved tool rule only. It temporarily suppresses conflicting `ask` rules, resumes the session with the approved `allowedTools`, and restores the original policy after the tool finishes.

If the `claude` CLI is not installed, the adapter falls back to `.llm-inbox/claude.md`.

## Copilot Path

There are two Copilot execution routes:

- `copilot_local`: uses Copilot CLI autopilot in the current WSL workspace
- `copilot_cloud`: uses `gh agent-task create` when GitHub CLI and auth are available

`grok` is treated as a Copilot model preference, not a standalone provider. If Copilot CLI is missing, the local adapter falls back to `.llm-inbox/copilot.md` or `.llm-inbox/grok.md`. If the requested model is not available on the authenticated Copilot account, the worker now exits as `failed` and the reason is written to `ai-activity-log.jsonl`.

## Extending To Another Repo

Copy these pieces:

- `.orchestrator/`
- `.llm-inbox/`
- `.github/agents/`
- `.claude/settings.local.example.json`
- `docs/provider-capabilities.md`
- `docs/provider-permissions.md`

Then update `.orchestrator/config.json`:

- shared-state file paths
- task schema mappings
- agent/provider mappings
- provider runtime settings
- approval queue/broker defaults
