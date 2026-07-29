# L12-FLEET-001 — Restore the eight-slot fleet frontier

Wave 0, lane `fleet-capacity`, owner `Codex`, reviewer `Codex2`, target `pantheon:dev`.

Outcome: make the reviewed repository dispatcher policy authoritative over stale live overlays, then prove four eligible Codex and four eligible Codex2 slots without losing active task state.

Scope: the provisioner, drift checker, their tests, and `docs/deployment/evidence/twelve-loop-gap/L12-FLEET-001`.

Acceptance: config parity, governed supervisor/watchdog reload, eight-slot readiness, mutation-free catalog dry-run, and proof that no lease, approval, queue item, or task was lost or duplicated.

Proof must include unit tests, before/after live policy readback, supervisor/watchdog health, provider slot readiness, and the dispatcher dry-run. Restart is forbidden while a deployment lease is active. The full machine contract is canonical in `tasks.json`.
