# BFF-FINAL-SIDECAR-COPILOT-SPEC-TRACE

Owner: Copilot
Reviewer: Codex
Depends on: BFF-FINAL-001
Parent: BFF-FINAL-010
Mutates canonical code: no

## Scope

Trace the final BFF contract requirements to the BFF-FINAL execution tasks. This is support-only spec review work. Do not edit `services/control-plane/bff/*` or canonical contract files.

## Deliverable

Update this file with:

1. Contract requirements grouped by response envelope, command admission, preconditions, action catalog, SSE, MCP import, evidence redaction, Agora patching, and interventions.
2. The BFF-FINAL task that owns each requirement.
3. Any ambiguity, duplicate ownership, or missing acceptance point.
4. A short checklist for `BFF-FINAL-010`.

## Acceptance

- Trace is useful for final review and handoff.
- Ambiguities are explicit instead of silently resolved.
- No canonical code edits are made.

## Trace mapping (contract clause → owning task)

- Command admission / Idempotency & CommandResponse envelope → BFF-FINAL-002
  - Acceptance pointers: Idempotency-Key header, body idempotencyKey rejected, CommandResponse<T> shape. Tests referenced in artifact and commit notes.

- Precondition error semantics (non-2xx BffErrorEnvelope, token/approval enforcement) → BFF-FINAL-003
  - Acceptance pointers: 428/409 semantics, no downstream command on fail.

- Backend canonical action catalog (descriptor ↔ action) → BFF-FINAL-004
  - Acceptance pointers: catalog artifact, governance metadata, frontend mapping stability.

- SSE approval & ask channels (replay metadata & degradation semantics) → BFF-FINAL-005
  - Acceptance pointers: SSE tests, channel presence, replay metadata.

- MCP import / tool action import semantics → BFF-FINAL-006
  - Acceptance pointers: import-tools endpoint functional, standalone create absent.

- Evidence redaction contract (EvidenceKind, RedactedEvidenceRef) → BFF-FINAL-007
  - Acceptance pointers: read surfaces include redacted refs, no leakage.

- Agora journal JSON Merge Patch facade → BFF-FINAL-008
  - Acceptance pointers: merge-patch content type, audit diff emitted.

- v5 interventions & two-man semantics → BFF-FINAL-009
  - Acceptance pointers: v5 route, remediation guards, two-man tests.

- Overall verification & handoff → BFF-FINAL-010
  - Acceptance pointers: all BFF tests pass, cleanup pass, delivery note and coordination response.

## Open ambiguities / potential gaps

1. Test artifacts locations: some acceptance criteria reference "tests" (e.g., SSE tests, idempotency tests). Confirm canonical test paths and whether sidecar should list test commands or merely reference artifacts.
2. Push/publication: ensure BFF-FINAL-010 closeout will include branch push; record push_status during finalization.
3. Overlapping ownership: BFF-FINAL-002 and BFF-FINAL-006 both touch command admission surface — confirm which task owns final adapter-level admission validation versus tool-import admission guard.

## Recommended short checklist for BFF-FINAL-010 owner

- Re-read consensus packet and each task artifact listed above.
- Confirm each acceptance pointer has an executable test or an explicit artifact reference.
- Run focused verification commands (example):
  - pytest services/control-plane/bff/tests::test_idempotency -q
  - pytest services/control-plane/bff/tests::test_sse_channels -q
- Capture commit hashes for any task-scoped commits and stage only task files.
- Produce final delivery note and coordinate response in .coordination/responses/.

## Notes for reviewer (Codex)

- This file is a support artifact only; do not accept any canonical code edits here.
- Please review the listed ambiguities and indicate whether to re-dispatch a follow-up small task for clarifications.

