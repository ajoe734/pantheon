# Task Brief: OPS-RTEL-002

## Task
- Title: Paper runtime fleet reconciler
- Status: done (closeout)
- Owner: Claude
- Reviewer: Codex
- Phase: Runtime Telemetry Hardening
- Last update: 2026-06-09

## Summary
新增 paper runtime fleet reconciler，從 active paper runtime bindings 自動維持一個 worker per binding，取代手動 docker run。

## Dispatch History
- PR #1053: initial paper runtime fleet reconciler
- PR #1056: fetch-failure safety, restart backoff, Dockerfile/requirements
- PR #1076: closeout doc/task-brief sync and binding-scoped signal queue isolation
- PR #1138: task-brief refresh (reopened dispatch revalidation)

## Dependencies
- OPS-RTEL-001: done · Telemetry durability bootstrap

## Acceptance Verified
- stack restart starts workers for all active paper bindings ✓
- killing one worker causes automatic restart ✓
- retired binding stops its worker ✓
- no worker consumes shared Redis signals before isolation ✓

## Artifacts
- `services/execution/runtime-manager/paper_fleet_reconciler.py`
- `services/execution/runtime-manager/test_paper_fleet_reconciler.py`
- `services/execution/runtime-manager/Dockerfile`
- `services/execution/runtime-manager/requirements.txt`
- `docker-compose.yml` — paper-fleet-reconciler service under paper-fleet profile
- `docs/deployment/runtime-telemetry-hardening-2026-06-06.md`

## Verification
```
python3 -m pytest services/execution/runtime-manager/test_paper_fleet_reconciler.py -v
# 25 passed
```

## Closeout Notes
- Deployment doc updated: test count 22→25, monitoring session tests noted
- 25/25 tests pass on this worktree
- Review approval from Codex: PR #1138 merge commit 29bab65f; suite passes 25/25
