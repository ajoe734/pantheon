# L12-AGORA-001 — Govern Agora dataset extraction

Wave 1, lane `agora`, owner `Antigravity`, reviewer `Codex`; depends on `L12-FLEET-001` and external overlap owner `PPL-ALLOC-009`.

Outcome: make typed OperatorIdentity, RBAC, tenant scope, idempotency conflict detection, leased extraction, and downstream acknowledgement work end to end.

Scope: BFF Agora dataset extraction and this task's evidence directory.

Acceptance: no tenant/user IDOR; writes require mutation authority; concurrent processors cannot double-own work; DatasetVersion/handoff is acknowledged exactly once.

Proof: real-identity integration, RBAC/tenant negatives, digest-conflict, multi-worker crash/replay, and downstream readback. The full machine contract is canonical in `tasks.json`.
