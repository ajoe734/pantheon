# Review: AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5

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
The artifact is strictly a support document scoped to the declared
`bff_handoff_packet` helper kind.

## Verification Checks (run by Claude)

| Check | Result |
|---|---|
| `pytest test_agora_router.py test_agora_identity_scope.py -q` | 22 passed in 11.89s |
| `git diff --check` on support artifact | Passed — no whitespace errors |
| `/bff/agora/me` exact-route rg vs OpenAPI / generated types | Absent from OpenAPI, capability manifest, `types.ts`, `paths.ts`; present only in `bff/agora/router.py` |
| `/bff/agora/capabilities` exact-route rg vs OpenAPI / generated types | Same: absent from contract surfaces, present only in `bff/agora/router.py` |
| `/bff/agora/servant/ensure` exact-route rg | Absent from OpenAPI/generated types; present only in `bff/agora/servant/router.py` (501 stub) and focused route tests |
| `AG-BE-ID-002` status (live) | `blocked`, waiting for `Codex` — confirmed |
| `AG-BE-ID-003` status (live) | `todo`, depends on `AG-BE-ID-002` — confirmed |
| `AG-FE-ID-001` status | `todo` — parent implementation not started |
| `AgoraApp.tsx` | `MISSING` (expected before parent implementation) |
| `identity.ts` | `MISSING` (expected) |
| `servant.ts` | `MISSING` (expected) |

## Content Review

**§2 Current Task State Snapshot** is accurate. The dependency chain
(`AG-BE-ID-002` blocked → `AG-BE-ID-003` todo → `AG-FE-ID-001` blocked) is
correctly captured. The parent dependency honesty rule is clear.

**§3 Sources Rechecked** is a useful addition over prior followups. Listing
every source examined makes the packet easier to audit and confirms the author
did not synthesize facts from memory.

**§4 BFF Query Ledger** presents all three routes in a concise three-column
table. Each entry correctly states implementation status, contract/generated
status (absent from OpenAPI, manifest, generated types, and `paths.ts`), and
the correct handoff rule. The note that `/me` and `/capabilities` may be used
only as accepted interim runtime routes — not contract-complete routes — is the
right conservative framing.

**§5 Frontend Surface To Hand Off** accurately describes the current working
tree. All missing artifacts (`AgoraApp.tsx`, `identity.ts`, `servant.ts`) were
independently confirmed MISSING. The warning to avoid importing `paths.ts` into
Agora shell code correctly carries forward the bundle isolation lesson from
`AG-FE-000`.

**§6 Minimal Blocked-Shell Contract** is well-specified. The six-state model
(auth blocked, scope/audience blocked, identity ready but backend not ready,
session facade unavailable, BFF unavailable, servant active) covers the real
failure modes without overclaiming. The requirement that `servant_policy` safety
facts are display-only and must not become operator controls is appropriately
conservative.

**§7 Operator Journey** cleanly separates the current honest journey (ends at
501 from the servant stub) from the blocked success journey. The text makes it
unambiguous that servant provisioning, OpenClaw reconciliation, and session
startup remain unavailable until `AG-BE-ID-002` and `AG-BE-ID-003` resolve.

**§8 Parent Absorption Checklist** is complete and appropriately strict. The
nine checks cover backend dependency decision, route truth (interim, not
contract-complete), strict client transport, 501 handling (maps to
`backend_not_ready`), no broad path import, ask/session gating, bundle
isolation, missing §23 UI spec, and frontend test coverage. All checks are
directly actionable.

**§9 Suggested Parent Verification** provides runnable backend and
post-implementation frontend commands. The commands match what this review
independently executed for backend checks. The expected current interpretation
is stated clearly to avoid false confidence about what is and is not implemented.

**§10 Sidecar Verification** evidence is accurate: 22 passed,
`git diff --check` passed, route-search results confirmed as described.

## Approval Notes

This packet is approved for absorption by `AG-FE-ID-001`. Compared to the
prior FOLLOWUP-4 packet, this followup:

- adds an explicit Sources Rechecked section (§3) for auditability
- refines the BFF query table format for clarity
- consolidates frontend surface and missing-artifact checks into a single
  section (§5)
- retains the full blocked-shell contract and absorption checklist from
  FOLLOWUP-4 without regression

The parent owner (Codex) should:

1. Use §8 as the acceptance gate before closing the parent task.
2. Make an explicit decision: stop on the backend blocker, or implement a
   strict blocked-shell-only scope. The full servant/session success path is
   not available until `AG-BE-ID-002` and `AG-BE-ID-003` clear.
3. Treat `/me`, `/capabilities`, and `/servant/ensure` as accepted interim
   runtime routes — not contract-complete routes — until OpenAPI/manifest
   reconciliation lands.
4. Gate `AskPersonas` behind the status shell while `AG-BE-ID-003` is todo.
5. Run the bundle isolation scan (§9) after writing `identity.ts`,
   `servant.ts`, and `AgoraApp.tsx`.
6. Obtain or carry a blocker for the missing §23 UI spec before implementing
   any Agora shell layout or widgets.

No changes requested. The task may close.
