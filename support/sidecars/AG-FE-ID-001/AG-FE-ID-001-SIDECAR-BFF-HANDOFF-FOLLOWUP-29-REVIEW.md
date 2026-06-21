# AG-FE-ID-001 Followup-29 Sidecar Review

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-29` |
| Helper parent | `AG-FE-ID-001` |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Claude2` / `Claude` |
| Decision | `review_approved` |
| Review source | Active task state and review_ready_dispatch |
| Packet commit | `ff416b5f` (AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-29: add BFF handoff packet) |
| Dev base at packet prep | `eb7e9ee084dee28636c1495a12ca7daa8c9ff07c` |
| Dev base at review | `7bab8c5d` (origin/dev advanced by PRs #2015, #2016 since packet prep) |
| Mutates canonical truth | `false` |

## Approval Notes

Claude approved the followup-29 sidecar packet with these reviewed facts:

1. The packet is correctly scoped as support material only. It does not modify L1
   canonical truth, BFF runtime code, OpenAPI/source-of-truth contract semantics,
   route registries, governance policy, database migrations, OpenClaw adapter code,
   capability manifest source, or execute-plans source files. The header field
   `Mutates canonical truth: false` is accurate.

2. The post-followup-28 dev delta is correctly characterised. `git log --oneline
   b9b8b76e..origin/dev` confirms PRs #2009–#2012 and their commits since the
   followup-28 closeout at `b9b8b76e`. The name-status diff against the checked
   handoff pathset shows only:
   - Strategy workshop BFF files (M): `services/control-plane/bff/agora/strategy_workshop/`
     — `VERSION`/`__all__` additions, ETag CAS/private-content-ref enforcement,
     and new test file. v1.2 workshop/private-content layer only. ✓
   - Followup-28 sidecar artifact additions (A): support artifact files only. ✓
   - `services/control-plane/bff/main.py` (M): `_MGMT_NL_PROVIDER_INLINE_GRACE_DEFAULT_SECONDS`
     from `12.0` to `3.0` (management nl/ask grace tuning, PR #2012). ✓
   No Agora identity/servant runtime, OpenAPI/spec, canonical contract, manifest
   source, or execute-plans source file changed. ✓

3. `origin/dev` advanced from `eb7e9ee0` to `7bab8c5d` between packet preparation
   and this review (PRs #2015 `MGMT-LIVE-EVIDENCE-PREFLIGHT-DIAG` and #2016
   `persona-full-ooda-iterative-validation`). `git diff --name-status eb7e9ee0..origin/dev
   -- services/control-plane/bff/agora/ services/control-plane/bff/main.py` returns
   no output — no Agora BFF changes from these additional PRs. The packet's dev delta
   characterisation remains accurate at review time. ✓

4. Execute-plans delta (Section 4): `GATE-RBAC-NLASK-ALIGN` (PR #65) correctly
   characterised as management-side validation scripting only. `scripts/validate-management-live-deep.mjs`
   updated for nl/ask RBAC and dry-run two-man alignment. No Agora BFF route or
   frontend client implication. ✓

5. `types.ts` stale detection is a new finding correctly documented in this packet.
   `contract-drift-check.mjs` now exits early detecting `src/lib/bff-v1/agora/types.ts`
   stale when `PANTHEON_CONTRACT_ROOT` is set. Previously 5 vitest tests passed
   without the contract root in followup-28. Parent must run `contract:drift:update`
   before reusing generated Agora types. ✓

6. Execute-plans PR `#63` is still `OPEN`. At review time the merge state shows
   `UNSTABLE` (was `UNKNOWN` in the packet's observation, which itself had shifted
   from `UNSTABLE` in followup-28). Head commit `e1cb9125` and timestamp
   `2026-06-20T16:53:49Z` remain unchanged. The oscillation between `UNSTABLE` and
   `UNKNOWN` reflects GitHub's CI re-evaluation window, not a merge-readiness
   improvement. Parent must not claim dev deployment readiness from local shell
   behavior alone. ✓

7. **State change noted at review time — `AG-BE-ID-003`**: The packet records
   `AG-BE-ID-003` as `blocked`, waiting for `Claude`'s servant-session type-contract
   decision. At review time `ai_status.py show AG-BE-ID-003` returns `todo` — the
   task was unblocked when `AG-XR-OPENAPI-003` (the OpenAPI session-type work) moved
   to `in_progress` and the block was cleared. This is a positive advancement: the
   dependency is now assigned and in active work. However, the servant-session
   routes (`POST /bff/agora/servant/sessions*`) are still not implemented. The
   packet's absorption checklist entry ("Parent does not call session routes until
   AG-BE-ID-003 lands and the type decision is approved") remains the correct guard.
   The packet's state snapshot was accurate at preparation time; the state change is
   recorded here as a reviewer observation, not a packet inaccuracy. ✓

8. Frontend surface probe (Section 6): `AgoraApp.tsx`, `identity.ts`, `servant.ts`,
   `src/entries/agora-main.tsx`, `vite.agora.config.ts`, and `agora.html` remain
   absent from both `origin/main` (`7b2f17c4`) and `origin/dev`. `types.ts` is
   present on `origin/dev` only and is now correctly flagged stale. `AskPersonas.tsx`
   and `src/lib/bff/agora.ts` present on both remotes; their guard conditions
   (identity/servant readiness plus AG-BE-ID-003 session decision) are correctly
   stated. The stale local checkout caveat is accurate. ✓

9. BFF query ledger (Section 5): unchanged from followup-28 because no new Agora
   BFF runtime routes appeared in the delta. Distinctions between runtime-only routes
   (`/me`, `/capabilities`), `/servant/ensure` (runtime plus OpenAPI; current-200
   vs. spec expectation mismatch noted), unimplemented servant handlers (`GET
   /bff/agora/servant`, `POST /bff/agora/servant/reconcile`), blocked session
   routes, legacy ask/sessions routes, and strategy workshop v1.2 routes are all
   accurate and correctly gated. ✓

10. Minimal status-shell contract (Section 7) and operator journey (Section 8)
    accurately reflect the honest current state: identity readiness and servant
    ensure are usable; Ask/session/command surfaces must remain disabled or
    read-only while the servant-session facade is not implemented (regardless of
    whether AG-BE-ID-003 is blocked vs. todo — implementation does not exist yet).
    The future session journey section correctly documents what must land before
    enabling session controls. ✓

11. Parent absorption checklist (Section 9): twelve checks remain valid and
    conservative. The `types.ts` stale check is a new addition for this followup
    and is correctly included. Consistent with approved followup-28 checklist. ✓

12. Verification evidence (Section 10): commands and results are internally
    consistent with packet claims. 35 pytest passed (`test_agora_router.py`,
    `test_persona_agent_sync.py`, `test_agora_identity_scope.py`).
    `contract-drift-check.mjs` stale exit is documented. Deployment gate
    fail-closed as expected. Worktree state at packet commit: only the task brief
    is untracked; no unrelated dirty files. ✓

## Scope Boundary

This review approves support material only. It does not approve, reopen, or
implement parent `AG-FE-ID-001`, and it does not absorb any BFF runtime, OpenAPI,
capability manifest, governance, OpenClaw adapter, database, or execute-plans
source change.

The execute-plans PR `#63` deployment gate status and the servant-session facade
gap remain outside this sidecar's scope; they are honesty items recorded as
follow-through risks, not resolved by this packet. The `AG-BE-ID-003` unblocking
observed at review time means the implementation dependency is active, not that
servant-session routes are ready.

## Owner Closeout Instruction

The approved packet is returned to `Claude2` for task closeout finalization.
Closeout should make this review record durable, keep the packet support-only,
and then use the normal task PR flow (`scripts/git/task_finalize.sh`) before
moving the task to `done`.
