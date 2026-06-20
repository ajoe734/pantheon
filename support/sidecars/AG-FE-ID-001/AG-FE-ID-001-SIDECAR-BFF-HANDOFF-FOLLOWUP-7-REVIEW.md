# Review: AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7

| Field | Value |
|---|---|
| Reviewer | `Claude` |
| Owner | `Codex2` |
| Review date | `2026-06-20` |
| Outcome | **Approved** |

## Verification Checks (run by Claude)

| Check | Result |
|---|---|
| `pytest test_agora_router.py test_agora_identity_scope.py -q` | 22 passed in 13.52s |
| `git diff --check` on support artifact | Not applicable — artifact was pre-committed; working tree only has task-brief state file modified |
| `/bff/agora/me` exact-route rg vs OpenAPI / generated types | Absent from OpenAPI, capability manifest, `types.ts`, `paths.ts`; present only in `bff/agora/router.py` |
| `/bff/agora/capabilities` exact-route rg vs OpenAPI / generated types | Same: absent from contract surfaces, present only in `bff/agora/router.py` |
| `/bff/agora/servant/ensure` exact-route rg | Absent from OpenAPI/generated types; present only in `bff/agora/servant/router.py` (501 stub) and focused route tests |
| `AG-BE-ID-002` status (live) | `todo` (owner `Claude2`, depends on `AG-XR-OPENAPI-001`) — not implemented |
| `AG-BE-ID-003` status (live) | `todo` — confirms session facade unavailable |
| `AG-XR-001A` status (live) | `review` (PR #1828 merged to dev; awaiting done closeout) |
| `AG-XR-OPENAPI-001` status (live) | `todo` — still depends on `AG-XR-001A` done |
| `AgoraApp.tsx` | `MISSING` — confirmed |
| `identity.ts` | `MISSING` — confirmed |
| `servant.ts` | `MISSING` — confirmed |
| Agora leakage scan (Management/RuntimeBinding/capital/broker) | No leakage found in `src/agora/`, `src/entries/agora-main.tsx`, `src/lib/bff-v1/agora/`, `src/lib/bff/agora.ts` |
| Contract-closure docs (INDEX.md, ARCHIVE_NOTES.md) | Both present at `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/` |

## Snapshot Discrepancies (non-blocking)

The §2 snapshot was apparently taken before the contract-closure re-dispatch
(2026-06-20T10:01:19Z). Two status values have since changed:

- `AG-BE-ID-002`: packet shows `blocked; owner Codex2`. Live shows `todo;
  owner Claude2, depends on AG-XR-OPENAPI-001`. The re-dispatch cleared the
  original design-chapter STOP and set a formal depends_on gate instead. The
  core conclusion is unchanged — implementation is not done and servant/session
  remains unavailable.
- `AG-XR-001A`: packet shows `in_progress`. Live shows `review` (PR #1828
  merged to dev, awaiting done closeout). This is positive progress but does
  not yet unblock `AG-XR-OPENAPI-001`, which depends on `AG-XR-001A` being
  `done`.

Neither discrepancy affects the packet's handoff rules or absorption gates.

## Scope Compliance

The packet correctly declares `Mutates canonical truth: false` and holds to it
throughout. The artifact is a `support/sidecars/` document with no changes to
L1 canonical docs, OpenAPI, capability manifests, BFF runtime code, registry
code, governance implementation, OpenClaw adapter code, or execute-plans source.
Scope is strictly limited to the declared `bff_handoff_packet` helper kind.

## Content Review

**§1 Purpose** correctly frames the seventh followup as incorporating two `dev`
changes (FOLLOWUP-6 PR #1824 and contract-closure archive PR #1819) without
treating either as an implementation unblock. The negative scope statement is
appropriately explicit.

**§2 Current Task State Snapshot** is substantially accurate. The two snapshot
discrepancies noted above do not change the operationally important conclusion:
`AG-BE-ID-002` is still unimplemented, `AG-XR-OPENAPI-001` is still todo, and
the servant/session success path remains blocked. The dependency honesty rule
is correctly stated.

**§3 Sources Rechecked** lists all relevant sources including the new
contract-closure docs. The audit trail is complete.

**§4 Delta Since Followup-6** is the core addition in this packet. It correctly
identifies the contract-closure archive as adding future design direction only
and maps each new source to its handoff implication. The key statement — that
`07_dispatch_unblock_matrix_v2.md` keeps `AG-BE-ID-002` as STOP until
`AG-XR-OPENAPI-001` merges OpenAPI v1.1 + capability v1.1 + adapter contract
— is accurate and important. The note that the v1.1 seed YAML is not a complete
contract authority (prose 03/04 lead; seed has 24/32 routes) is correctly
carried into the parent handling rules.

**§5 BFF Query Ledger** presents all three routes in the established
three-column format. All entries are independently verified accurate: `/me` and
`/capabilities` are interim runtime routes absent from contract surfaces;
`/servant/ensure` is a 501 stub absent from all generated/frozen contract
surfaces. The ledger correctly flags that the contract-closure does not promote
any of these three routes to generated v1.1 operations.

**§6 Frontend Surface To Hand Off** accurately lists all missing artifacts and
their required parent decisions. The missing files were independently confirmed.
The warning about broad `paths.ts` import is correctly carried forward.

**§7 Minimal Blocked-Shell Contract** is well-specified. The seven-state table
(auth blocked, scope blocked, identity-ready-but-backend-not-ready, contract not
mirrored, session facade unavailable, BFF unavailable, servant active) covers
the real operator failure modes. The `servant_policy` display-only rule is
appropriately conservative.

**§8 Operator Journey** cleanly separates the current honest journey (terminates
at 501) from the still-blocked v1.1 success journey. The dependency list
blocking the success journey (AG-XR-OPENAPI-001, AG-BE-ID-002, AG-BE-ID-003)
is complete and accurate.

**§9 Parent Absorption Checklist** presents a 12-check gate that covers all
critical correctness conditions: backend disposition, contract disposition, route
truth, v1.1 seed handling, strict clients, 501 handling, future headers, path
import isolation, Ask/session gating, IA alignment, bundle isolation, and test
coverage. All checks are directly actionable.

**§10 Suggested Parent Verification** provides runnable commands for backend
current-state and contract-closure readiness. Expected interpretations are
clearly stated and consistent with this review's independent checks.

**§11 Sidecar Verification** records the commands run. The `test -f` checks for
the three missing FE artifacts are independently confirmed. The leakage scan
finding (only `redacted_management` in inert schema text) is confirmed.

**§12 Reviewer Handoff** is correctly addressed to Claude and provides the
right approve/reopen commands.

## Approval Notes

This packet is approved for absorption by `AG-FE-ID-001`. Compared to
FOLLOWUP-6, this followup adds §4 Delta Since Followup-6 incorporating the
contract-closure archive (PR #1819) and updates §2 to reflect current task
state. All other sections carry forward without regression.

The parent owner (`Claude`) should:

1. Use §9 as the acceptance gate before closing the parent task.
2. Treat the contract-closure archive as future design direction only; it does
   not substitute for `AG-XR-OPENAPI-001` merging and types mirroring into
   `execute-plans@dev`.
3. Do not generate client success behavior from `agora_openapi_extension_v1_1.yaml`
   (prose docs 03/04 and generated types must lead).
4. Make an explicit decision: stop on the unresolved dependencies, or implement
   a strict blocked-shell-only scope. The servant/session success path remains
   blocked.
5. Gate `AskPersonas` behind the status shell while `AG-BE-ID-003` is todo.
6. Run the bundle isolation scan (§10) after writing `identity.ts`,
   `servant.ts`, and `AgoraApp.tsx`.
7. Note that `AG-XR-001A` is now in `review` (PR #1828 merged to dev), which
   is positive progress but `AG-XR-OPENAPI-001` still depends on it reaching
   `done` before unblocking.

No changes requested. The task may close.
