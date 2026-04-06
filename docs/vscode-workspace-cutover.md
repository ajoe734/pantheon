# VS Code Workspace Cutover

Pantheon is now the primary development workspace.

## Open This Workspace

Open:

- `pantheon.code-workspace`

This gives you two folders:

- `Pantheon`: primary repo for orchestrator, dashboard, GitHub approval bus, shared state
- `LEAN`: secondary repo for execution / LEAN bridge work only

## What Lives Where

### Pantheon

Use Pantheon for:

- `.orchestrator/`
- `ai-status.json`
- `current-work.md`
- `docs-site/`
- GitHub approval bus
- dashboard and worker orchestration

### LEAN

Use LEAN only for:

- execution-side adapters
- LEAN runtime bridge code
- LEAN engine integration details

Do not run the main supervisor from the LEAN repo anymore.

## Standard Commands

### Start supervisor

```bash
cd /home/ajoe734/code/pantheon
python3 .orchestrator/supervisor.py
```

### Start dashboard

```bash
cd /home/ajoe734/code/pantheon
bash scripts/launch-docs-site.sh
```

### Main dashboard URL

- `http://127.0.0.1:4173/index.html`

## Recommended Editing Pattern

1. Keep `Pantheon` as the active workspace folder.
2. Open `LEAN` files only when you are working on execution-specific code.
3. Treat `Pantheon` as the source of truth for task state and orchestration.
