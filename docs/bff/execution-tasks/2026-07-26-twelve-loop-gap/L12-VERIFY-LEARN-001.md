# L12-VERIFY-LEARN-001 — Prove learning flows

Wave 4, lane `verify-learning`, owner `Codex`, reviewer `Codex2`; depends on Teaching, Imitation, Consultation, and integrated truth.

Outcome: prove teaching eval/commit, Agora dataset, real-data imitation candidate, and Consultation memo/handoff across service boundaries without runtime mutation.

Scope: `scripts/verify_twelve_loop_learning.py` and this task's evidence directory.

Acceptance: positive terminal authorities plus auth/tenant/RBAC negatives, duplicate/multi-worker/restart/DLQ behavior, no seed fallback, and no runtime effect.

Proof: EP3 service drill, tenant matrix, restart/replay, and reviewed evidence manifest. The full dependency and machine contract is canonical in `tasks.json`.
