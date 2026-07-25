# Task Brief: OPS-DEV-LIFECYCLE-FRESHNESS-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Persist managed-dev lifecycle freshness budget
- Status: review_approved
- Owner: Claude2
- Reviewer: Antigravity
- Next: Independent review approved and implementation PR #4043 merged to `dev`; owner finalizing closeout.

## Summary
把 lifecycle projector 在大型 dev 資料量下的 150 到 180 秒輪詢納入受管 dev readiness 設定；只重建 BFF 時不得重啟 projector，並避免 FE gate 因 120 秒過短窗口反覆 503。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Closeout Evidence
- Delivery: Pantheon PR #4043 merged to `dev` at merge commit `406abcad90421bc262961adb6cb3b6ab89c04962` on 2026-07-24.
- Approved implementation commit:
  - `530c5697d3b671410387fb5a1beae872f68cc0e5` — anchor dev freshness budget: thread a managed-dev lifecycle projector freshness budget through the deploy workflow, deploy script, and focused compose contract tests.
- Approved scope remained limited to the three declared artifacts:
  - `.github/workflows/nonprod-deploy.yml`
  - `scripts/deploy_nonprod_vm.sh`
  - `services/trade_journey/test_lifecycle_projector_compose.py`
- Delivered behavior:
  - `DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS` defaults to `300` for managed dev deploys and is threaded workflow env → deploy script → `docker compose config` and both the root and BFF compose-up invocations as `LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS`.
  - The generic compose default stays fail-closed at 120 seconds; only managed dev deploys widen the freshness window to allow one 150–180 second projector tick plus scheduling headroom, so the FE readiness gate no longer flaps 503 and a BFF-only rebuild does not restart the projector.
  - Not changed: projector implementation, strict auth, or source-SHA gates.
- Owner finalization verification (re-run in this worktree at closeout):
  - `python -m pytest services/trade_journey/test_lifecycle_projector_compose.py` → `6 passed`.
  - `bash -n scripts/deploy_nonprod_vm.sh` → clean.
  - `git diff --check` over the approved commit → clean.
- PR checks passed on merge: Commit trailers, Runtime mirror guard, and Smoke acceptance.
- No canonical architecture document changed; this brief is the task-scoped closeout record for the already-approved implementation.
