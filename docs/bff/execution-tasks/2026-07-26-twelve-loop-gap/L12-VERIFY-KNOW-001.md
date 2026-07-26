# L12-VERIFY-KNOW-001 — Prove knowledge flows

Wave 4, lane `verify-knowledge`, owner `Claude`, reviewer `Antigravity`; depends on Distillation, Alpha, and integrated truth.

Outcome: at real service boundaries, prove Persona requirement → SourceRecord → mutable StrategySpec draft and approved immutable StrategySpec → authoritative ExperimentRun.

Scope: `scripts/verify_twelve_loop_knowledge.py` and this task's evidence directory.

Acceptance: positive paths plus unapproved/immutable negatives, duplicates, concurrency, provider/Registry/research failure, restart, DLQ replay, and matching controller/BFF truth.

Proof: EP3 real-service drill and a reviewed evidence manifest with checksums. The full dependency and machine contract is canonical in `tasks.json`.
