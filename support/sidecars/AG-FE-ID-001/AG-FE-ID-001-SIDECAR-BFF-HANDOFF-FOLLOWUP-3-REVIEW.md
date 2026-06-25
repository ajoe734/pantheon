# Review: AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3

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
This is a support artifact only.

## Verification Checks (run by Claude)

All claims in §9 of the packet were independently re-run:

| Check | Result |
|---|---|
| `pytest test_agora_router.py test_agora_identity_scope.py -q` | 22 passed |
| `/bff/agora/me` exact-route rg | Present only in `services/control-plane/bff/agora/router.py` — absent from OpenAPI, capability manifest |
| `/bff/agora/capabilities` exact-route rg | Same: runtime only, absent from contract surfaces |
| `/bff/agora/servant/ensure` exact-route rg | Present in `bff/agora/servant/router.py` (501 stub) and `tests/test_agora_router.py` — absent from OpenAPI |
| `AgoraApp.tsx` | `MISSING` (expected) |
| `identity.ts` | `MISSING` (expected) |
| `servant.ts` | `MISSING` (expected) |

## Content Review

**§2 Task State Snapshot** is accurate. The dependency honesty rule is correctly
framed: `AG-BE-ID-002` is blocked, `AG-BE-ID-003` is todo, and the parent must
not close as a successful servant/session flow while that chain is unresolved.

**§4 BFF Query Gap** correctly represents the gap between runtime truth and
contract/generated coverage for all three routes. The parent handoff rule for
each route is appropriate and conservative.

**§5 Bundle Isolation Lesson** is accurate and actionable. The warning about
importing the broad `bff-v1/paths.ts` object into Agora shell code reflects the
correct lesson from `AG-FE-000`.

**§6 Minimal Blocked-Shell Shape** is well-specified. The five-state table
(auth blocked, scope blocked, identity ready/backend not ready, BFF unavailable,
servant active) covers the real failure modes the parent shell must handle.

**§7 Parent Absorption Checklist** is complete and appropriately strict. All
eight checks are reasonable prerequisites for the parent to absorb this sidecar.
The bundle scan step is particularly important.

**§8 Suggested Verification** provides runnable commands that the parent owner
can execute after writing execute-plans code.

## Approval Notes

This packet is approved for absorption by `AG-FE-ID-001`. The parent owner
(Codex) should:

1. Use §7 as the acceptance gate before closing the parent task.
2. Treat `/me`, `/capabilities`, and `/servant/ensure` as accepted interim
   runtime routes — not as contract-complete routes — until OpenAPI/manifest
   reconciliation lands.
3. Gate `AskPersonas` behind the blocked-shell status shell until
   `AG-BE-ID-003` clears.
4. Run the bundle isolation scan (`§8`) after writing `identity.ts`,
   `servant.ts`, and `AgoraApp.tsx`.

No changes requested. The task may close.
