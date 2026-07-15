# Immutable audit archive repair bootstrap — 2026-07-15

Status: **external/bootstrap prerequisite; no live mutation is authorized by this packet**.

This packet turns the PR #3652 archive-parser blocker into one narrowly scoped,
supervisor-admitted fleet task.  It is intentionally outside both the primary
49-task materialization transaction and the broken status/audit outbox: the
transaction that cannot parse history must not be used to authorize repair of
that history.

The machine-readable contract is
[archive-audit-repair-bootstrap-task.v1.json](fixtures/archive-audit-repair-bootstrap-task.v1.json).
The operative admission and repair procedure is [RUNBOOK.md](RUNBOOK.md).
The rendered task is [LOOP-PROD-AUDIT-ARCHIVE-REPAIR-001.md](LOOP-PROD-AUDIT-ARCHIVE-REPAIR-001.md).

This packet neither changes `ai-status.json`, active or archived audit bytes,
outbox files, runtime/supervisor code, scheduler state, nor deployment state.
