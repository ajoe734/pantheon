# Task Brief: OPS-DEV-POSTGRES-SHM-20260825

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix dev Postgres container shared-memory floor
- Owner: Antigravity
- Reviewer: Codex2
- Status: todo
- Next: Implement and validate docker-compose PostgreSQL shm_size floor; then rerun strict dev deploy after lease release.

## Summary
修正 dev docker-compose 的 PostgreSQL 容器 shared memory 配置。此次部署在 host 有足夠磁碟與 /dev/shm 的情況下，因容器預設 64MB shm，VACUUM 需要 66379584 bytes 而失敗。將 PostgreSQL shm_size 提升到明確且有限的 256MB，補充反向測試/compose config 驗證；不得改 Source Ingestion continuous pull 或啟用 permissive writes。

## Scope
scope:
- docker-compose.yml
- scripts/test_deploy_nonprod_vm.py
- .orchestrator/task-briefs/ops_dev_postgres_shm_20260825.md

## Acceptance
- docker compose config shows postgres shm_size >= 256m
- regression test fails if shm_size is omitted or below 256m
- strict dev deploy reaches postgres VACUUM without ENOSPC
- Source Ingestion remains reconcile-only/manual

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

