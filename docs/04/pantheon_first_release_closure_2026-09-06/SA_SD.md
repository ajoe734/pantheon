# Current-State SA/SD Summary — First Release Closure

This is a synthesis for readers who should not need workstation/`/tmp` access.
It is not a new architecture decision; it restates, with links, the current
approved position already recorded in
[archive/APPROVAL_RELEASE_SA_SD.md](archive/APPROVAL_RELEASE_SA_SD.md)
(the operator-approved plan) and the six merged audit documents at
[docs/04/pantheon_current_full_gap_audit_2026-09-03/](../pantheon_current_full_gap_audit_2026-09-03/).
Where this document and a signed source disagree, the signed source in
`archive/` is authoritative — this file may lag a later operator revision.

## 1. Approved authority slice ordering (before Overlay)

Root cause (see `archive/APPROVAL_RELEASE_SA_SD.md` §2): the real Persona
coordinator provisioning path runs Governance propose → review → decide →
Registry advance before it can continue. Overlay's forward acceptance needs
that capability. The legacy ordering — which placed the approval-authority
fix at the *end* of the Domain corrective, behind Router → Test → Journal →
CW, which itself depends on Overlay — is a genuine functional dependency
cycle, not something removable by deleting tests, faking `approved`, or
recasting the note as a dependency.

The fix approved by the operator: pull the exact approval-authority slice
forward as `GOV-APPROVAL-AUTHORITY-PREREQUISITE-001`, sequenced after the
Registry successor (`REGISTRY-STRATEGY-UNIFIED-CONTRACT-001`, the only
Registry task actually executing) and before the rest of Domain resumes.
Domain's original task IDs and full remaining scope are preserved; they
consume the merged capability rather than re-implementing it.

| Owner | Sole write responsibility |
| --- | --- |
| Registry | draft/family metadata, validated artifacts, immutable versions, artifact-state, its own receipt |
| Governance | proposal/review/approval/revocation, its authorization/validity, durable decision receipt |
| Deployment / Runtime | plan/binding/dispatch/execution state, gated on verified real owner references |
| BFF / Agora / FE | typed command coordination and verified read projection only; no writable approval/spec truth of its own |

## 2. Existing external hold vs. actual scheduler edges

Where formal tooling cannot express an additional dependency edge, the
existing authentic external capability hold remains the mechanism —
explicitly labeled as a hold, not a new declared edge. No canonical task/queue
JSON is hand-edited, no owner lease is forged, and Overlay is not superseded
just because a downstream item is blocked. This preserves every downstream
dependent instead of silently breaking it.

## 3. Full remaining Domain work (unchanged scope)

Domain's original broad scope — Governance, Persona, Runtime, Deployment,
and everything downstream of them — is not reduced by this closure. Only the
approval-responsibility slice named above is pulled forward. The Domain
reviewer must still confirm downstream integrates exactly one completed
owner for that slice; all other Domain requirements, signed acceptance
criteria, and dependencies are unchanged. Command-ingress/receipt retirement,
source/research callers, and Runtime internal capabilities remain full
Domain requirements.

## 4. Source-join before hosted

Per `archive/APPROVAL_RELEASE_SA_SD.md` §6–7, first-release source retirement
(`STRUCT-RETIRE-001`) must complete *before* the three hosted tasks
(`DEV-RELEASE-HOSTED-001`, `L12-HOSTED-001`, `MGMT-AGORA-E2E-001`) proceed —
this is a reordering of when compatibility layers retire, not a removal of
the hosted acceptance requirements themselves. All three hosted tasks retain
their full original scope and still require a legitimate one-shot
MFA-backed admission; nothing in this documentation task authorizes that
admission or performs any hosted/provider/broker/capital mutation.

## 5. What this task is not

This task (`DOC-FIRST-RELEASE-PLAN-DELIVERY-001`) is documentation delivery
of previously-uncommitted planning sources. It is not:

- a new task materializer (it creates no canonical task/queue JSON entries),
- a declaration that any product gap referenced by these documents is fixed,
- a rewrite of history to make older partial work appear passed,
- an implementation of `GOV-APPROVAL-AUTHORITY-PREREQUISITE-001` or
  `STRUCT-RETIRE-001` (those remain separate tasks with their own owners,
  reviewers, and acceptance criteria — see `EXECUTION_ORDER.md`).

See `TRACEABILITY.md` for the original 20-requirement / 12-loop /
Management / Agora / OpenClaw / dead-tail / duplicate-group / test-file
traceability, and `STATUS.md` for current per-item status.
