# DEP-004 Closeout Evidence

Task: `DEP-004`
Owner: `Codex`
Reviewer: `Codex2`
Closeout date: `2026-05-18`

## Scope

DEP-004 adds a read-only pool/runtime compatibility guard before deployment
advance. The approved implementation owns:

- `services/control-plane/governance/pool_runtime_compat.py`
- `services/control-plane/governance/test_pool_runtime_compat.py`
- `services/control-plane/governance/pool_runtime_compat_contract.md`
- `services/control-plane/cron/service.py`
- `services/control-plane/cron/test_cron.py`

The requested task brief path `.orchestrator/task-briefs/dep_004.md` is absent
in this worktree. Owner closeout used the active DEP-004 record from
`ai-status.json`, the reviewer approval notes, and the task-owned artifacts.

## Review And Publication

- Parent implementation PR: `#97`
- Merge commit: `5eb77fdc`
- Reviewer approval recorded in `ai-status.json`: approved by `Codex2`
- Sidecar acceptance packet: `support/sidecars/DEP-004/DEP-004-SIDECAR-ACCEPTANCE.md`

The reviewed guard blocks deploy advancement before execution projection and
deployment saga bootstrap when compatibility fails. A passed compatibility
result is recorded on the deployment request for audit.

## Verification

Owner closeout reran:

```bash
python3 -m pytest -q services/control-plane/governance/test_pool_runtime_compat.py services/control-plane/cron/test_cron.py
```

Result:

```text
21 passed in 2.12s
```

No live broker, live capital, or external runtime side effects were invoked.
