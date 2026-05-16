# SENT-001 Review - Codex

Task: SENT-001 - `/bff/v5/sentinel/findings` endpoint
Owner: Claude2
Reviewer: Codex
Review date: 2026-05-16
Disposition: approved after re-review

## Initial Findings

1. `GET /bff/v5/sentinel/findings` is registered twice, and the later generic alias owns the OpenAPI entry.
   - Dedicated filtered route: `services/control-plane/bff/main.py:24366`
   - Generic alias duplicate: `services/control-plane/bff/main.py:25720`
   - Runtime behavior currently reaches the dedicated route because it is registered first, so focused request tests pass.
   - OpenAPI generation keeps the later generic operation:
     - operation id: `sem_final_generic_read_alias_bff_v5_sentinel_findings_get`
     - parameters: `id`, `authorization`
   - Required fix: remove the duplicate generic list decorator for `/bff/v5/sentinel/findings`, or otherwise ensure `/openapi.json` documents the dedicated list endpoint with `kind`, `status`, and `severity` query params. Keep the detail route `/bff/v5/sentinel/findings/{id}` intact. Add a focused regression test for the OpenAPI parameters.

2. The SENT-001 implementation is not durable in the task commits referenced by the handoff/evidence.
   - `eda173a6` adds only `test_sent001_sentinel_findings_contract.py`.
   - `b5c1c434` adds only `support/evidence/SENT-001/README.md`.
   - `git show HEAD:services/control-plane/bff/main.py` and `git show HEAD:services/control-plane/bff/read_store.py` do not include the dedicated filtered route or the filtered `list_sentinel_findings(...)` signature.
   - Current working tree does include the implementation, mixed with unrelated `ASK-004` / `TRN-*` edits in the same files.
   - Required fix before final approval: make the SENT-001-owned implementation hunks durable and separable for closeout, and update the evidence wording so it does not imply the endpoint/filter implementation is already present in committed baseline when it is only in the dirty worktree.

## Initial Verification Run

Initial dirty worktree verification passed:

```bash
pytest services/control-plane/bff/test_sent001_sentinel_findings_contract.py -q
# 15 passed in 27.71s

pytest services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py services/control-plane/bff/test_read_store_loop_sentinel.py -q
# 33 passed in 43.68s
```

Additional review probe:

```bash
python3 -c "import sys; sys.path.insert(0, 'services/control-plane/bff'); import main; spec=main.app.openapi(); print(spec['paths']['/bff/v5/sentinel/findings']['get'].get('operationId')); print([p.get('name') for p in spec['paths']['/bff/v5/sentinel/findings']['get'].get('parameters', [])])"
# sem_final_generic_read_alias_bff_v5_sentinel_findings_get
# ['id', 'authorization']
```

## Re-Review Approval

Owner handoff commit `4050626a` resolves the OpenAPI registration issue by
removing the generic list decorator for `/bff/v5/sentinel/findings` while
leaving the detail route intact. HEAD also includes the dedicated list route,
read-store filtering, `kind` projection/inference, updated evidence in
`support/evidence/SENT-001/README.md`, and the focused OpenAPI regression test.

Current verification:

```bash
pytest services/control-plane/bff/test_sent001_sentinel_findings_contract.py -q
# 16 passed in 23.39s

pytest services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py services/control-plane/bff/test_read_store_loop_sentinel.py -q
# 33 passed in 27.97s

python3 -c "import sys; sys.path.insert(0, 'services/control-plane/bff'); import main; spec=main.app.openapi(); op=spec['paths']['/bff/v5/sentinel/findings']['get']; print(op.get('operationId')); print([p.get('name') for p in op.get('parameters', [])])"
# bff_v5_sentinel_findings_list_bff_v5_sentinel_findings_get
# ['kind', 'status', 'severity', 'authorization']
```

Approval note: SENT-001 is approved for owner finalization. The worktree still
contains unrelated dirty changes from other active tasks, including ASK-005
edits in `services/control-plane/bff/main.py`; they were not part of this
review decision.
