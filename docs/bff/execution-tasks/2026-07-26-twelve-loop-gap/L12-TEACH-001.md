# L12-TEACH-001 — Make Persona Teaching tenant-safe and HA

Wave 2, lane `teaching`, owner `Codex2`, reviewer `Codex`; depends on `L12-FLEET-001`.

Outcome: authenticated teaching mutations with authoritative session/job/replay storage and health that reflects actual evaluation/commit results.

Scope: `services/training-session` and this task's evidence directory.

Acceptance: actor/service/tenant enforcement; HA state; failed evaluation makes no persona mutation; two workers and restart create one terminal commit; health degrades on failure.

Proof: auth/tenant/MFA negatives, two-worker restart, eval-fail no-mutation, and hosted terminal persona readback. The full machine contract is canonical in `tasks.json`.
