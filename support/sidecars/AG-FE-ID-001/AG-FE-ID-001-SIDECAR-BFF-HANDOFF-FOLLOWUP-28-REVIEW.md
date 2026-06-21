# AG-FE-ID-001 Followup-28 Sidecar Review

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-28` |
| Helper parent | `AG-FE-ID-001` |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Claude2` / `Claude` |
| Decision | `review_approved` |
| Review source | Active task state and review_ready_dispatch |
| Packet commit | `12baeb65` (AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-28: add BFF handoff packet) |
| Dev base at review | `b9b8b76e6aacb27f235eb1ef3cce8d9d7e653e6b` |
| Mutates canonical truth | `false` |

## Approval Notes

Claude approved the followup-28 sidecar packet with these reviewed facts:

1. The packet is correctly scoped as support material only. It does not modify L1
   canonical truth, BFF runtime code, OpenAPI/source-of-truth contract semantics,
   route registries, governance policy, database migrations, OpenClaw adapter
   code, capability manifest source, or execute-plans source files. The header
   field `Mutates canonical truth: false` is accurate.

2. The post-followup-27 delta is correctly characterised. `git log --oneline
   8da16437..origin/dev` and `git diff --name-status 8da16437..origin/dev`
   confirm that `origin/dev` advanced only by followup-27 closeout artifacts:
   - PR `#2006` merged the followup-27 packet at `e4626fc3`.
   - Commit `7de20631` added the review record (reviewer was `Claude2` after
     auto-reassignment from Codex).
   - `4d6197f2` merged `origin/dev` into the followup-27 task branch.
   - `bac133a9` recorded the followup-27 closeout.
   - PR `#2007` merged the full closeout at `b9b8b76e` (current `origin/dev` tip).
   The name-status diff shows only:
   - `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-27-REVIEW.md` (A)
   - `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-27.md` (M)
   No BFF runtime, OpenAPI/spec, canonical contract, manifest source, or
   execute-plans source file changed. ✓

3. Task state facts in Section 2 are accurate (verified via `ai_status.py show`):
   - `AG-FE-ID-001` remains `todo`; parent implementation has not started.
   - `AG-FE-000` is archived `done`; Phase 0 entry/build/audience dependency
     is accepted context.
   - `AG-BE-ID-003` is `blocked`, waiting for `Claude`'s servant-session
     type-contract decision (confirmed `blocked Claude` from `ai_status.py show`).
   - The dependency-honesty rule is correctly applied: the packet does not claim
     interactive, trainer, or research-task session readiness while `AG-BE-ID-003`
     is blocked. ✓

4. The BFF query ledger (Section 5) is unchanged from followup-27 because no new
   BFF runtime routes appeared in the delta. The ledger accurately distinguishes:
   - `GET /bff/agora/me` and `GET /bff/agora/capabilities` as runtime routes
     (not generated OpenAPI v1.1/v1.2 operations); usable as narrow identity and
     capability readiness support.
   - `POST /bff/agora/servant/ensure` as present in v1.1/v1.2 OpenAPI, implemented
     in runtime, requiring `Idempotency-Key` and `X-Request-Id`; the current-runtime
     vs. OpenAPI expectation mismatch is correctly noted.
   - `GET /bff/agora/servant` and `POST /bff/agora/servant/reconcile` as present in
     OpenAPI but without confirmed runtime handlers; parent must not depend on them.
   - All `POST /bff/agora/servant/sessions*` routes as blocked: no accepted BFF
     runtime implementation and `AG-BE-ID-003` still blocked.
   - Legacy `/bff/agora/sessions*` and `/bff/agora/ask/sessions*` correctly
     distinguished as non-servant-session facades.
   - Strategy Workshop v1.2 routes correctly excluded from AG-FE-ID-001 shell/session
     readiness. ✓

5. Execute-plans PR `#63` is stated as `OPEN`, `UNSTABLE`, head `e1cb9125`,
   last updated `2026-06-20T16:53:49Z`. The verification evidence in Section 10
   confirms this via `gh pr view`. Timestamp and head are unchanged from the
   followup-27 review observation. Parent must not claim dev deployment readiness
   from local shell behavior alone. ✓

6. The frontend surface probe (Section 6) correctly identifies:
   - `AgoraApp.tsx`, `identity.ts`, `servant.ts`, `src/entries/agora-main.tsx`,
     `vite.agora.config.ts`, and `agora.html` are missing from both
     `origin/main` (`7b2f17c4`) and `origin/dev` (`7aa49172`).
   - `src/lib/bff-v1/agora/types.ts` is present on `origin/dev` only.
   - The stale local checkout is correctly flagged as unreliable for implementation
     truth. ✓

7. The minimal status-shell contract and operator journey sections accurately reflect
   the current honest state: identity readiness and servant ensure are usable;
   Ask/session/command surfaces must remain disabled or read-only while `AG-BE-ID-003`
   is blocked. ✓

8. The parent absorption checklist (Section 9) is conservative and correct. Twelve
   checks gate parent implementation on dependency disposition, frontend base truth,
   route truth, strict clients, bundle isolation, and compatibility honesty. Consistent
   with the approved followup-27 checklist and still valid. ✓

9. Verification evidence in Section 10 is consistent with packet claims: focused
   BFF/OpenClaw pytest (35 passed in 14.64s), contract drift passed (5 passed),
   deployment gate fail-closed as expected (status not compatible, frontend runtime
   commit is placeholder), git delta diff clean. ✓

10. Worktree state at review time: only the task brief is untracked; no unrelated
    dirty files present. The packet commit `12baeb65` is the only task-owned commit
    on the task branch above `b9b8b76e`. ✓

## Scope Boundary

This review approves support material only. It does not approve, reopen, or implement
parent `AG-FE-ID-001`, and it does not absorb any BFF runtime, OpenAPI, capability
manifest, governance, OpenClaw adapter, database, or execute-plans source change.

The execute-plans PR `#63` deployment gate status and `AG-BE-ID-003` block remain
outside this sidecar's scope; they are honesty items recorded as follow-through risks,
not resolved by this packet.

## Owner Closeout Instruction

The approved packet is returned to `Claude2` for task closeout finalization.
Closeout should make this review record durable, keep the packet support-only,
and then use the normal task PR flow (`scripts/git/task_finalize.sh`) before
moving the task to `done`.
