# Review: ASST-KERNEL-007 — Repair-mode worktree workflow

Reviewer: Codex2
Task branch: `task/ASST-KERNEL-007`
Task commit reviewed: `4d12d90c`
Review date: 2026-06-02
Disposition: changes requested

## Findings

1. Blocking: staged rename sources outside `declared_scope` can bypass the
   scope guard.
   - File: `services/openclaw-gateway-adapter/assistant_repair_workflow.py`
   - Lines: 289-315, 535-537
   - Evidence: `git mv outside.txt services/openclaw-gateway-adapter/inside.txt`
     produces `status --porcelain` entry
     `R  outside.txt -> services/openclaw-gateway-adapter/inside.txt` and
     `git diff --cached --name-only` reports only
     `services/openclaw-gateway-adapter/inside.txt`.
   - Current behavior: `_parse_status()` discards the rename source and keeps
     only the destination path. `staged_paths` also sees only the destination.
     If the destination is inside `declared_scope`, the workflow accepts a
     staged deletion/modification of an out-of-scope tracked file.
   - Required fix: include both source and destination paths for renames/copies
     in staged and dirty scope enforcement, or use a diff format that preserves
     both paths and reject if either side is outside `declared_scope`.

2. Blocking: the provider lets request metadata lower the clean-worktree
   requirement.
   - File: `services/openclaw-gateway-adapter/assistant_repair_workflow.py`
   - Lines: 231-236
   - File: `services/openclaw-gateway-adapter/assistant_codex_provider.py`
   - Line: 410
   - Current behavior: `AssistantCodexProvider` calls
     `self._repair_workflow.validate(metadata)`, and `request_from_metadata()`
     honors `metadata["require_clean"]` before the default. A kernel repair
     request can therefore pass `require_clean=false` and enter
     `workspace-write` with existing dirty in-scope files.
   - Required fix: make provider-side repair execution enforce a clean
     worktree regardless of request metadata. If a separate smoke/closeout
     helper needs to inspect dirty task-scope files, keep that override outside
     the provider execution path or make the explicit function parameter
     authoritative over metadata.

## Validation

Focused tests pass on the current implementation:

```bash
pytest services/openclaw-gateway-adapter/tests/test_assistant_repair_workflow.py services/openclaw-gateway-adapter/tests/test_assistant_codex_provider.py -q
```

Result: `15 passed in 2.41s`.

The passing suite does not cover the blocking rename-source bypass or the
provider metadata clean-requirement downgrade described above.
