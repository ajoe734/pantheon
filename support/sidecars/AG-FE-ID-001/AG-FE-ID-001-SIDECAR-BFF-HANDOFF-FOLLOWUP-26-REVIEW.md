# AG-FE-ID-001 Followup-26 Sidecar Review

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-26` |
| Helper parent | `AG-FE-ID-001` |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex` / `Claude` |
| Decision | `review_approved` |
| Review source | Active task state and review_ready_dispatch |
| Packet PR | `#1997` merged at `9a5ec4c836f82b1bcbd8ce8d998dceabd6c2d5f5` |
| Dev base at review | `9a5ec4c836f82b1bcbd8ce8d998dceabd6c2d5f5` |
| Mutates canonical truth | `false` |

## Approval Notes

Claude approved the followup-26 sidecar packet with these reviewed facts:

1. The packet is correctly scoped as support material only. It does not modify L1
   canonical truth, BFF runtime code, OpenAPI/source-of-truth contract semantics,
   route registries, governance policy, database migrations, OpenClaw adapter
   code, capability manifest source, or execute-plans source files. The header
   field `Mutates canonical truth: false` is accurate.

2. The post-followup-25 delta is correctly characterised. After PR `#1994` (review
   record for followup-25) merged at `0f88261f`, `origin/dev` advanced by PR `#1996`
   only, which merged `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` at
   `fa489430`. The checked Pantheon handoff pathset diff confirms only
   `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13.md`
   was added — no BFF runtime, OpenAPI/spec, manifest, execute-plans mirror,
   or AG-FE-ID-001 support change.

3. Task state facts in Section 2 are accurate:
   - `AG-FE-ID-001` remains `todo` (confirmed via `ai_status.py show`).
   - `AG-FE-000` is archived `done`; Phase 0 frontend entry/build/audience
     dependency is accepted context.
   - `AG-BE-ID-003` remains `blocked`, waiting for Claude's servant-session
     type-contract decision.
   - `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-13` is in `review` after PR
     `#1996` merged and its key conclusion keeps `AG-BE-ID-003` blocked.
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

5. Execute-plans PR `#63` is confirmed `OPEN`, `UNSTABLE`, and failed
   `integration-gate`. The parent must not claim dev deployment compatibility
   readiness from local shell behavior alone.

6. The frontend surface probe (Section 6) correctly identifies that
   `AgoraApp.tsx`, `identity.ts`, `servant.ts`, `src/entries/agora-main.tsx`,
   `vite.agora.config.ts`, and `agora.html` are missing from both
   `origin/main` (`7b2f17c4`) and `origin/dev` (`7aa49172`).
   `src/lib/bff-v1/agora/types.ts` is present on `origin/dev` only. The stale
   local `/home/lupin/code/execute-plans` checkout (ahead 2, behind 467) is
   correctly flagged as unreliable for implementation truth.

7. The minimal status-shell contract and operator journey sections accurately
   reflect the current honest state: identity readiness and servant ensure are
   usable; Ask/session/command surfaces must remain disabled or read-only while
   `AG-BE-ID-003` is blocked.

8. The parent absorption checklist (Section 9) is conservative and correct.
   Twelve checks gate parent implementation on dependency disposition, frontend
   base truth, route truth, strict clients, bundle isolation, and compatibility
   honesty. These checks are unchanged from followup-25 and remain valid.

9. Verification evidence in Section 10 is consistent with the packet claims:
   focused BFF/OpenClaw pytest (35 passed in 18.05s), contract drift passed
   (20 digests, 17 schemas, 96 operations), deployment gate fail-closed as
   expected (status not compatible, frontend runtime commit is placeholder),
   and private-index diff check clean. Branch CI was reported green.

10. The packet does not modify canonical truth, BFF runtime code, OpenAPI
    contracts, capability manifests, governance policy, OpenClaw adapter code,
    database migrations, or execute-plans source files.

## Scope Boundary

This review approves support material only. It does not approve, reopen, or
implement parent `AG-FE-ID-001`, and it does not absorb any BFF runtime,
OpenAPI, capability manifest, governance, OpenClaw adapter, database, or
execute-plans source change.

The execute-plans PR `#63` deployment gate status and `AG-BE-ID-003` block
remain outside this sidecar's scope; they are honesty items recorded as
follow-through risks, not resolved by this packet.

## Owner Closeout Instruction

The approved packet is returned to `Codex` for task closeout finalization.
Closeout should make the review record durable, keep the packet support-only,
and then use the normal task PR flow before moving the task to `done`.
