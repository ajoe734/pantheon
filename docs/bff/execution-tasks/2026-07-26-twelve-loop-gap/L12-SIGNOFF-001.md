# L12-SIGNOFF-001 — Install protected closeout authority

Wave 2, lane `protected-human-ops-signoff`, owner `Claude`, reviewer `Codex2`; depends on `L12-FLEET-001` and external overlap owner `PPL-ALLOC-009`.

Outcome: install a transition-time guard so only an authenticated authorized Human or Ops actor can issue the final verdict, bound to the exact catalog, task, manifest, target and FE/BFF deployment identities.

Scope: governance verdict schema/service, its BFF boundary, `ai_status` and loop closeout guards/tests, and this task's evidence directory.

Acceptance: review-approved and done fail closed for a missing, rejected, revoked, replayed, stale, expired, unauthorized, tampered or mismatched verdict; candidate files and fleet workers cannot self-sign or bypass it.

Proof: authorization/signature/binding/expiry/revocation/concurrency tests, candidate self-sign and direct-state-edit negatives, transition enforcement, and a reviewed evidence manifest. The full machine contract and binding fields are canonical in `tasks.json`.
