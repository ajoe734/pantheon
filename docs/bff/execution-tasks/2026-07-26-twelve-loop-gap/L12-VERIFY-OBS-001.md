# L12-VERIFY-OBS-001 — Prove observability and Evolution

Wave 4, lane `verify-observability`, owner `Antigravity`, reviewer `Claude`; depends on Reconciliation, Evolution, BFF Health, and integrated truth.

Outcome: prove runtime anomaly → telemetry → drift → incident → postmortem → EvolutionDecision → real approved action, plus downstream stop → infrastructure incident → recovery.

Scope: `scripts/verify_twelve_loop_observability.py` and this task's evidence directory.

Acceptance: no fabricated identity, one correlated incident, real downstream terminal receipt, retry/compensation, restart/replay, and recovered BFF incident.

Proof: EP3/EP4 service drills, duplicate/concurrency/restart cases, target stop/recovery, and reviewed evidence manifest. The full machine contract is canonical in `tasks.json`.
