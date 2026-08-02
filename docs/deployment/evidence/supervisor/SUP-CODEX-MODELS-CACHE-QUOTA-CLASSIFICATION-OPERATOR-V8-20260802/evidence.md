# SUP-CODEX-MODELS-CACHE-QUOTA-CLASSIFICATION-OPERATOR-V8-20260802 evidence

Status: implementation complete; independent exact-head Human/Ops review pending.

## Delivered source contract

The Codex probe now recognizes only the observed `models_cache.json` parser
failure that names the missing `supports_reasoning_summaries` field. On the
bounded pre-lock target path, it verifies the exact configured provider home,
binds the pre-probe pathname to an owned regular single-link inode, hashes the
cache, atomically quarantines that same inode, and performs one fresh probe.
Unsafe, unowned, changed, concurrent-loser, or failed-quarantine paths stay
closed and do not spend the refresh probe.

Codex usage-limit output is now `quota_reached`, never credential revocation.
A validated reset is reduced to one UTC timestamp and passed through the
existing capability and dispatch-pause APIs. Cache compatibility failures are
recoverable capacity/tooling failures, not sticky auth failures.

The supervisor enables cache mutation only during its existing pre-lock,
bounded provider recovery phase. Under runtime admission it consumes the
sanitized result, pauses only the affected configured account identity, and
uses the existing exact-CAS owner/reviewer pair planner. Codex and Codex2 remain
distinct configured groups; the task adds neither same-account inference nor a
mutual-review ban. Existing dependency, priority, capacity, cooldown,
failure-loop, chair-triage, active-lease, explicit-Human/Ops, and exact-head
review gates remain the routing authority.

## Security boundary

The implementation does not read, rewrite, copy, rotate, or emit `auth.json`
payloads. It does not emit auth paths, token-presence fields, API key values, or
API-key variable names in probe metadata. Cache bytes are read only after exact
failure and path-identity checks, solely to bind SHA-256 evidence to the inode
being quarantined.

## Verification

- Provider permissions: 93 tests passed plus 7 subtests.
- Supervisor: 548 tests passed plus 154 subtests.
- Final focused supervisor matrix: 22 tests passed plus 5 subtests.
- `py_compile` and `git diff --check` passed.
- No live probe, provider-home mutation, service restart, deployment, or
  capital-affecting action was performed by this source task.

The machine-readable acceptance matrix, dependency commits, exact commands,
implementation commits, rollout, and rollback are recorded in `evidence.json`.
