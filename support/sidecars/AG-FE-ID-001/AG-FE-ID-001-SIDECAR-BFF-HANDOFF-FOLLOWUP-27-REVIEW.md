# AG-FE-ID-001 Followup-27 Sidecar Review

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-27` |
| Helper parent | `AG-FE-ID-001` |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Claude` / `Claude2` (auto-reassigned from Codex after Codex usage limit reached) |
| Decision | `review_approved` |
| Review source | Active task state and review_ready_dispatch |
| Packet commit | `8da16437` (AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-27: add BFF handoff packet) |
| Dev base at review | `fd9693bb936c12751728a67116aa42394e2e674c` |
| Mutates canonical truth | `false` |

## Approval Notes

Claude2 approved the followup-27 sidecar packet with these reviewed facts:

1. The packet is correctly scoped as support material only. It does not modify L1
   canonical truth, BFF runtime code, OpenAPI/source-of-truth contract semantics,
   route registries, governance policy, database migrations, OpenClaw adapter
   code, capability manifest source, or execute-plans source files. The header
   field `Mutates canonical truth: false` is accurate.

2. The post-followup-26 delta is correctly characterised. After the followup-26
   review record at `0b0662079128d0e569e02598b99c9fb28a3d492f`, `origin/dev`
   advanced by three PRs:
   - PR `#2003` merged followup-26 closeout at `4192ba9c` (confirmed `done`
     archive for the predecessor sidecar).
   - PR `#2004` merged `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-17` at
     `38341b12` — dashboard-editor support only; no BFF route, OpenAPI
     servant-session decision, canonical contract change, or execute-plans
     source change.
   - PR `#2005` merged `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` at
     `fd9693bb` — new backend sidecar whose key conclusion keeps `AG-BE-ID-003`
     blocked on Claude's servant-session type-contract decision.
   The git log (visible in the task branch) is consistent with this account. ✓

3. Task state facts in Section 2 are accurate:
   - `AG-FE-ID-001` remains `todo` (confirmed via `ai_status.py show`).
   - `AG-FE-000` is archived `done`; Phase 0 frontend entry/build/audience
     dependency is accepted context.
   - `AG-BE-ID-003` remains `blocked`, waiting for Claude's servant-session
     type-contract decision (confirmed `blocked Claude` via `ai_status.py show`).
   - `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-14` is in `review` after PR
     `#2005` merged at `fd9693bb`; its key conclusion keeps `AG-BE-ID-003`
     blocked.
   - The dependency-honesty rule is correctly applied: the packet does not claim
     interactive, trainer, or research-task session readiness while `AG-BE-ID-003`
     is blocked.

4. The BFF query ledger (Section 5) accurately distinguishes usable support
   context from blocked surfaces:
   - `GET /bff/agora/me` and `GET /bff/agora/capabilities` are correctly
     identified as runtime routes, not generated OpenAPI v1.1/v1.2 operations.
     Parent may use these as narrow identity and capability readiness support.
   - `POST /bff/agora/servant/ensure` is confirmed present in v1.1 and v1.2
     OpenAPI as `ensureAgoraServant`, implemented in runtime, and requiring
     `Idempotency-Key` and `X-Request-Id` headers. The packet correctly notes
     the current-runtime vs. OpenAPI expectation mismatch and requires typed
     failure mapping for 401/403/422/503.
   - `GET /bff/agora/servant` and `POST /bff/agora/servant/reconcile` are
     present in OpenAPI but have no confirmed runtime handler; parent must not
     depend on them until runtime support lands.
   - All `POST /bff/agora/servant/sessions*` routes remain blocked: no accepted
     BFF runtime implementation, and `AG-BE-ID-003` is blocked. The
     `ServantSessionCreateRequest` lacks a public `session_type` or
     `sessionType` field and rejects undeclared top-level fields.
   - Legacy `/bff/agora/sessions*` and `/bff/agora/ask/sessions*` routes are
     correctly distinguished as non-servant-session facades.
   - Strategy Workshop v1.2 routes are correctly excluded from AG-FE-ID-001
     shell/session readiness.
   The BFF ledger is unchanged from followup-26 because no new BFF runtime
   routes appeared in the delta — this is correct and expected. ✓

5. Execute-plans PR `#63` is stated as `OPEN`, `UNSTABLE`, head `e1cb9125`,
   last updated `2026-06-20T16:53:49Z`. The verification evidence in Section 10
   confirms this via `gh pr view`. The parent must not claim dev deployment
   compatibility readiness from local shell behavior alone. ✓

6. The frontend surface probe (Section 6) correctly identifies that
   `AgoraApp.tsx`, `identity.ts`, `servant.ts`, `src/entries/agora-main.tsx`,
   `vite.agora.config.ts`, and `agora.html` are missing from both
   `origin/main` (`7b2f17c4`) and `origin/dev` (`7aa49172`).
   `src/lib/bff-v1/agora/types.ts` is present on `origin/dev` only. The stale
   local checkout is correctly flagged as unreliable for implementation truth. ✓

7. The minimal status-shell contract and operator journey sections accurately
   reflect the current honest state: identity readiness and servant ensure are
   usable; Ask/session/command surfaces must remain disabled or read-only while
   `AG-BE-ID-003` is blocked. ✓

8. The parent absorption checklist (Section 9) is conservative and correct.
   Twelve checks gate parent implementation on dependency disposition, frontend
   base truth, route truth, strict clients, bundle isolation, and compatibility
   honesty. These checks are consistent with the approved followup-26 checklist
   and remain valid. ✓

9. Verification evidence in Section 10 is consistent with the packet claims:
   focused BFF/OpenClaw pytest (35 passed in 14.86s), contract drift passed
   (5 passed), deployment gate fail-closed as expected (status not compatible,
   frontend runtime commit is placeholder), git delta diff checks clean. ✓

10. Worktree state at review time: only the task brief is untracked; no unrelated
    dirty files are present. The packet commit `8da16437` is the only task-owned
    commit on the task branch above `fd9693bb`. ✓

## Scope Boundary

This review approves support material only. It does not approve, reopen, or
implement parent `AG-FE-ID-001`, and it does not absorb any BFF runtime,
OpenAPI, capability manifest, governance, OpenClaw adapter, database, or
execute-plans source change.

The execute-plans PR `#63` deployment gate status and `AG-BE-ID-003` block
remain outside this sidecar's scope; they are honesty items recorded as
follow-through risks, not resolved by this packet.

Note: the reviewer for this task was auto-reassigned from Codex to Claude2 after
Codex hit its usage limit. The packet content and scope were prepared by Claude
under the original reviewer assignment and required no changes for approval.

## Owner Closeout Instruction

The approved packet is returned to `Claude` for task closeout finalization.
Closeout should make the review record durable, keep the packet support-only,
and then use the normal task PR flow (`scripts/git/task_finalize.sh`) before
moving the task to `done`.
