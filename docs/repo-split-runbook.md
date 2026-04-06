# Repo Split Runbook

Last updated: 2026-04-05
Owner: `ARC-007`

## Goal

Keep `pantheon` as the primary workspace and mount the LEAN fork only through
the `lean/` git submodule. After the split:

- Pantheon services, orchestration, docs, and shared state live in this repo
- LEAN engine code lives in `lean/`
- Pantheon Python inside LEAN lives only in `lean/Algorithm.Python/pantheon_algo/`

## Expected Layout

| Path | Expectation |
|---|---|
| `services/` | Pantheon-owned code lives here |
| `.orchestrator/` | Pantheon-owned runtime and approval bus |
| `lean/` | Git submodule pointing at `ajoe734/pantheon-lean` |
| `lean/Algorithm.Python/pantheon_algo/` | Only in-tree Pantheon LEAN bridge |
| root `pantheon_algo/` | Must not exist |

## Validation Checklist

Run these from the repo root:

```bash
git submodule status
test -d lean/Algorithm.Python/pantheon_algo
test ! -e pantheon_algo
docker compose config >/tmp/pantheon-compose.out
find . -path './lean/.git' -prune -o \( -type d -name '__pycache__' -o -name '*.pyc' \) -print
```

Expected results:

- `git submodule status` shows `lean` initialized instead of missing
- `lean/Algorithm.Python/pantheon_algo/` exists
- root `pantheon_algo/` is absent
- `docker compose config` succeeds with LEAN building from `./lean`
- the `find` command prints nothing outside `lean/.git`

## Compose Notes

`docker-compose.yml` must treat LEAN as a submodule checkout, not as the repo
root. The LEAN service should use:

- `build: ./lean`
- `./lean/Data:/Lean/Data`
- `./lean/Launcher/config.json:/app/config.json`

If `docker compose config` fails, check whether the `lean/` submodule is
initialized and whether `lean/Data` plus `lean/Launcher/config.json` exist.

## Workspace Cutover

Use `pantheon.code-workspace` so VS Code opens both repositories with Pantheon
as the primary folder. The supervisor, dashboard, and shared status files
should run from `pantheon`, not from the LEAN fork.

## Post-Cutover Hygiene

- Keep generated caches and logs out of git
- Mirror shared state changes with `scripts/ai-status.sh` or `python3 scripts/ai_status.py`
- Prefer updating the LEAN bridge in `lean/Algorithm.Python/pantheon_algo/`
  instead of reintroducing duplicate root-level copies
