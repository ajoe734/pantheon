# Review: AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4

| Field | Value |
|---|---|
| Reviewer | `Claude` |
| Owner | `Codex` |
| Review date | `2026-06-20` |
| Outcome | **Approved** |

## Scope Compliance

The packet correctly declares `Mutates canonical truth: false` and holds to it.
No L1 canonical docs, OpenAPI specs, capability manifests, BFF runtime code,
registry code, governance implementation, or execute-plans source was changed.
This is a support artifact only, scoped to the declared `bff_handoff_packet`
helper kind.

## Verification Checks (run by Claude)

| Check | Result |
|---|---|
| `pytest test_agora_router.py test_agora_identity_scope.py -q` | 22 passed |
| `/bff/agora/me` exact-route rg vs OpenAPI | Absent from OpenAPI (rg matches in OpenAPI were `/memory` and `/messages`, not `/me`); present only in `bff/agora/router.py` |
| `/bff/agora/capabilities` exact-route rg vs OpenAPI | Absent from OpenAPI; present only in `bff/agora/router.py` |
| `/bff/agora/servant/ensure` exact-route rg vs OpenAPI | Absent from OpenAPI; present in `bff/agora/servant/router.py` (501 stub) |
| `AG-BE-ID-002` status | `blocked`, waiting for `Codex` — confirmed against live `ai_status.py show` |
| `AG-BE-ID-003` status | `todo`, depends on `AG-BE-ID-002` — confirmed |
| `AG-FE-ID-001` status | `todo` — confirmed; parent implementation not started |
| `AgoraApp.tsx` | `MISSING` (expected before parent implementation) |
| `identity.ts` | `MISSING` (expected) |
| `servant.ts` | `MISSING` (expected) |

## Content Review

**§2 Task State Snapshot** is accurate. The dependency chain is correctly
captured: `AG-BE-ID-002` is blocked with design/adapter clarification pending,
`AG-BE-ID-003` is unstarted pending its dependency, and the parent `AG-FE-ID-001`
must not claim a successful servant/session flow while this chain is unresolved.

**§4 BFF Query Gap** table is accurate and appropriately conservative. All three
routes (`/me`, `/capabilities`, `/servant/ensure`) are correctly classified as
interim runtime routes absent from the generated contract surface. The parent
handoff rule for each is correct.

**§5 AG-BE-ID-002 Blocker Impact** is new in this followup and well-reasoned.
The specific gaps identified (missing route catalog entry, missing §5.4 servant
capability set, missing adapter paths) match the confirmed blocker note from
`AG-BE-ID-002` (`next` field). The three implementation options table
(stop-on-blocker / blocked-shell-only / full-success-shell) gives the parent
owner a clear decision fork.

**§6 Frontend Surface Still Missing** accurately reflects the current working
tree. All cited missing artifacts (`AgoraApp.tsx`, `identity.ts`, `servant.ts`,
`identity.ts` routes) were independently confirmed MISSING. The warning about
`bff-v1/paths.ts` bundle leakage correctly carries forward the `AG-FE-000`
lesson.

**§7 Minimal Blocked-Shell Contract** is well-specified. The six-state table
covers the real failure modes the shell must handle without overclaiming. The
requirement that `servant_policy` safety facts are shown as display-only (not
controls) is appropriately conservative.

**§8 Operator Journey** cleanly separates the current honest journey from the
blocked success journey. The text makes it unambiguous that servant
provisioning, OpenClaw reconciliation, and session startup remain unavailable.

**§9 Parent Absorption Checklist** is complete and appropriately strict. The
nine checks cover backend dependency decision, route truth, 501 handling,
strict transport, no page-level direct fetch, narrow imports, bundle isolation,
ask/session gating, and the missing UI spec. The missing UI spec check (§23
not found in local checkout) is a valid addition over prior followups.

**§10 Suggested Verification** provides runnable commands that the parent owner
can execute after writing execute-plans code. The commands match what this
review independently ran for backend checks.

**§11 Sidecar Verification** evidence is accurate: 22 passed, `git diff --check`
passed.

## Approval Notes

This packet is approved for absorption by `AG-FE-ID-001`. The parent owner
(Codex) should:

1. Use §9 as the acceptance gate before closing the parent task.
2. Make an explicit decision: stop on blocker, or implement a strict
   blocked-shell-only scope. The full servant/session success path is not
   available until `AG-BE-ID-002` and `AG-BE-ID-003` clear.
3. Treat `/me`, `/capabilities`, and `/servant/ensure` as accepted interim
   runtime routes — not as contract-complete routes — until OpenAPI/manifest
   reconciliation lands.
4. Gate `AskPersonas` behind the status shell while `AG-BE-ID-003` is todo.
5. Run bundle isolation scan (`§10`) after writing `identity.ts`, `servant.ts`,
   and `AgoraApp.tsx`.
6. Obtain or carry a blocker for the missing §23 UI spec before implementing
   any Agora shell layout or widgets.

No changes requested. The task may close.
