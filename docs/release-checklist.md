# Pantheon Local Release Checklist

This checklist turns the local repo into a repeatable release artifact without changing the public BFF contract.

## 1. Build Dependencies

Pantheon uses a dedicated local virtualenv for BFF verification.

```bash
python3 scripts/verify_bff_local_release.py
```

What this does:
- creates or reuses `.venv-bff/`
- prefers stdlib `venv`, and if `ensurepip` is unavailable it bootstraps `pip` into the venv with `get-pip.py`
- installs `services/control-plane/bff/requirements.txt`
- runs `py_compile`
- runs unit and app-in-process smoke tests
- runs a socket-level HTTP smoke test through `uvicorn` + `httpx`

## 2. Cleanup Gate

Before packaging, the repo must be free of dirty tracked noise and must not keep generated runtime state under version control.

```bash
python3 scripts/check_release_cleanliness.py
```

This gate fails when:
- generated runtime artifacts are still tracked
- generated runtime artifacts are dirty in the worktree
- non-generated tracked files are dirty before release

## 3. Canonical vs Generated Files

Canonical tracked files:
- `AI_COLLABORATION_GUIDE.md`
- `ai-status.json`
- `ai-activity-log.jsonl`
- `current-work.md`
- source code
- canonical docs
- dashboard static assets

Generated ephemera that should not ship in the release tarball:
- `.orchestrator/state.json`
- `.orchestrator/approval-queue.json`
- `.orchestrator/event-queue.jsonl`
- `.orchestrator/github-bus-state.json`
- `.orchestrator/provider_capabilities.json`
- `.orchestrator/supervisor.pid`
- `dashboard-bundle.json`
- `docs-site/ai-status.json`
- `docs-site/ai-activity-log.jsonl`
- `docs-site/approval-queue.json`
- `docs-site/current-work.md`
- `docs-site/dashboard-bundle.json`
- `docs-site/orchestrator-state.json`
- `.venv-bff/`
- `dist/`
- `.qwen/`
- Python bytecode and `:Zone.Identifier` sidecars

## 4. Packaging

Create the local release tarball:

```bash
python3 scripts/create_local_release.py
```

This flow:
1. checks the worktree is release-clean
2. verifies the BFF locally
3. regenerates docs-site mirrors
4. copies only tracked canonical files into a staging tree
5. writes `RELEASE_MANIFEST.json`
6. writes `VERIFICATION.md`
7. emits `dist/pantheon-local-release-<timestamp>.tar.gz`

## 5. Artifact Contents

Primary artifact:
- `dist/pantheon-local-release-<timestamp>.tar.gz`

Companion artifacts:
- `dist/pantheon-local-release-<timestamp>-RELEASE_MANIFEST.json`
- `dist/pantheon-local-release-<timestamp>-VERIFICATION.md`

The manifest records:
- git commit
- canonical docs revision set
- BFF verification result
- docs-site sync result
- included and excluded artifact categories

## 6. Unpack and Re-Verify

After extracting the tarball:

```bash
tar -xzf dist/pantheon-local-release-<timestamp>.tar.gz
cd pantheon-local-release-<timestamp>
python3 scripts/verify_bff_local_release.py
```

That command should rebuild `.venv-bff/` in the unpacked tree and rerun the same verification lane.
