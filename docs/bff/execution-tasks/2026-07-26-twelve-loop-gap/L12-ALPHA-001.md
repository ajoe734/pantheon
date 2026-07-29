# L12-ALPHA-001 — Dispatch approved Alpha replication

Wave 1, lane `alpha`, owner `Codex`, reviewer `Codex2`; depends on `L12-FLEET-001`.

Outcome: use one StrategySpec identifier and tenant key from approved immutable review through a leased queue into authoritative ExperimentTask/ExperimentRun state.

Scope: Alpha replication, experiment orchestrator/services, and this task's evidence directory.

Acceptance: only approved reviewed specs enter; claims expire safely; DLQ/replay is deterministic; no stub/local run is accepted; duplicate review and restart converge once.

Proof: gate negatives, tenant collision, claim-expiry/crash recovery, and real research service-boundary readback. The full machine contract is canonical in `tasks.json`.
