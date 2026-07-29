# L12-TEACH-001 — Make Persona Teaching tenant-safe and HA

Wave 2, lane `teaching`, owner `Antigravity`, reviewer `Codex`; depends on `L12-FLEET-001`.

Outcome: authenticated teaching mutations with authoritative session/job/replay storage and health that reflects actual evaluation/commit results.

Scope: `services/training-session` and this task's evidence directory.

Acceptance: actor/service/tenant enforcement; HA state; failed evaluation makes no persona mutation; two workers and restart create one terminal commit; health degrades on failure.

Proof: auth/tenant/MFA negatives, two-worker restart, eval-fail no-mutation,
and hosted terminal persona readback. The full machine contract is canonical
in `tasks.json`.

Proof-production boundary: this task must prove a real authoritative-store
terminal persona commit through two workers and restart. The hosted terminal
readback remains a program obligation, but its production is delegated by
`proof-ownership.json` to `L12-VERIFY-LEARN-001` after runtime activation and
is witnessed again by `L12-HOSTED-001`. This avoids requiring a hosted runtime
before the manifest task that creates that runtime can start; it does not
permit local, mock, or seed evidence to be described as hosted.
