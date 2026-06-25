# AG-FE-ID-001 Followup-30 Sidecar Review

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30` |
| Helper parent | `AG-FE-ID-001` |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex` / `Claude2` |
| Decision | `review_approved` |
| Review source | Active task state and review_ready_dispatch |
| Packet commit | `f3c7811b` (AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30: add packet) |
| Dev base at packet prep | `2c797ec065ba1f676ea5b00905790307a1cbf78a` |
| Dev base at review | `2c797ec0` (no additional dev advancement observed at review time) |
| Mutates canonical truth | `false` |

## Approval Notes

Claude2 approves the followup-30 sidecar packet. Verification commands run independently:

1. **Packet scope boundary**: confirmed `mutates_canonical: false` in task record. The packet
   file itself touches only `support/sidecars/AG-FE-ID-001/` — no L1 canonical docs, no BFF
   runtime code, no OpenAPI/source-of-truth contract, no execute-plans source files. ✓

2. **Task state snapshot (Section 2)**: verified independently via
   `AI_NAME=Claude2 python3 scripts/ai_status.py show` for all four tasks:
   - `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-30`: active `review`, owner `Codex`,
     reviewer `Claude2`. ✓
   - `AG-FE-ID-001`: active `blocked`, waiting for `Gemini`; PR `#66` aggregate-gate
     failure is the confirmed blocker; all Agora-specific checks pass (F13 3/3,
     vitest 23/23). ✓
   - `AG-BE-ID-003`: archived `done`, `terminal_outcome: completed`; implementation
     PR `#2025` merged at `aeceba68`; closeout PR `#2029` merged at `8049242d`. ✓
   - `AG-FE-000`: archived `done`. ✓
   The packet's dependency honesty rule (parent no longer blocked on `AG-BE-ID-003`,
   but session UI still cannot claim readiness without frontend client and UI
   acceptance) is accurate. ✓

3. **Agora BFF runtime delta (Section 4)**: `git diff --name-status 2c797ec0..origin/dev
   -- services/control-plane/bff/agora/` returned no output at review time. No Agora BFF
   runtime changes since the packet dev base. The packet's claim of no `services/control-plane/bff/agora/*`
   delta is confirmed. ✓

4. **BFF route ledger (Section 5)**: independently probed
   `services/control-plane/bff/agora/router.py` and
   `services/control-plane/bff/agora/servant/router.py`. Confirmed route presence:
   - `GET /bff/agora/me` (`router.py:84`) ✓
   - `GET /bff/agora/capabilities` (`router.py:115`) ✓
   - `POST /bff/agora/servant/ensure` (`servant/router.py:641`) ✓
   - `POST /bff/agora/servant/sessions` (`servant/router.py:751`) ✓
   - `GET /bff/agora/servant/sessions/{session_id}` (`servant/router.py:851`) ✓
   - `POST /bff/agora/servant/sessions/{session_id}/messages` (`servant/router.py:918`) ✓
   - `POST /bff/agora/servant/sessions/{session_id}/terminate` (`servant/router.py:1076`) ✓
   - `GET /bff/agora/servant/sessions/{session_id}/stream` (`servant/router.py:1156`) ✓
   Confirmed absence: no `GET /bff/agora/servant` handler; no
   `POST /bff/agora/servant/reconcile` handler. The packet correctly marks these as
   unsupported and warns the frontend not to rely on them. ✓

5. **`AG-BE-ID-003` state change vs. followup-29**: followup-29 packet recorded
   `AG-BE-ID-003` as `blocked`, then a reviewer observation noted it moved to `todo`
   when `AG-XR-OPENAPI-003` moved to `in_progress`. This followup-30 packet correctly
   upgrades that fact: `AG-BE-ID-003` is now archived `done` with servant-session routes
   implemented and all focused tests passing. The BFF ledger and absorption checklist
   correctly update the guard from "session routes not implemented" to "frontend still
   needs strict clients and UI acceptance". ✓

6. **PR `#66` gate state (Section 7)**: confirmed via parent task `next` field and
   `review_notes_zh`. Gate 1 (lint — `Gemini`/`Codex`), Gate 2 (contract drift —
   `Codex`), Gate 5 (F05 Sentinel — `Codex`), and Gate 6 (perf+SSE — `Codex2`) remain
   failing on issues outside the AG-FE-ID-001 shell/client scope. F13 Agora passes.
   The packet's gate table accurately attributes gate ownership and does not bury
   aggregate failures in `AG-FE-ID-001` closeout. ✓

7. **PR `#63` (Section 4 and gate note)**: packet correctly reports `OPEN` / `UNSTABLE`,
   head unchanged. The review does not observe any improvement. Continue to treat as
   unresolved follow-through risk. ✓

8. **v1.3/v4 scope separation (Section 4 and BFF ledger)**: the delta table correctly
   identifies new `agora_v1_3.openapi.yaml`, `bundle_index.v1_3.json`, and v4 schema
   files as workshop/trading-room design additions and explicitly states they must not
   be silently absorbed into the Phase 1 status shell unless a separate review task
   explicitly routes that scope. ✓

9. **Frontend surface probe (Section 6)**: packet accurately reports that `AgoraApp.tsx`,
   `identity.ts`, `servant.ts`, and their test files exist only on the PR `#66` branch
   (`de7834b8`), not on `origin/dev`. The `servant.ts` warning about `GET /bff/agora/servant`
   (present as exported helper, no runtime support) is a new correct finding specific
   to this followup. ✓

10. **Minimal operator journey (Section 8)**: the honest journey (identity → capabilities
    → ensure → servant status bar, session controls remain skeleton) is consistent with
    the confirmed runtime route set, the pending frontend merge, and the current frontend
    scope. Session controls correctly remain skeleton or disabled until strict session
    client/UI acceptance lands. ✓

11. **Parent absorption checklist (Section 9)**: all twelve checks remain valid and
    conservative. The checklist entries for `GET /bff/agora/servant` absence, session
    route family separation, v1.3/v4 scope isolation, bundle isolation, and compatibility
    honesty are appropriate follow-through guards for whoever absorbs this into parent
    `AG-FE-ID-001`. ✓

12. **Verification evidence (Section 10)**: commands and results are internally consistent
    with packet claims. 39 pytest passed; compatibility manifest gate fail-closed as
    expected; PR `#66` and PR `#63` facts match independently confirmed states. ✓

No corrections required.

## Scope Boundary

This review approves support material only. It does not approve, reopen, or implement
parent `AG-FE-ID-001`, and it does not absorb any BFF runtime, OpenAPI, capability
manifest, governance, OpenClaw adapter, database, or execute-plans source change.

The execute-plans aggregate gate failures remain assigned to their recorded owners
(`Gemini`, `Codex`, `Codex2`). Parent `AG-FE-ID-001` remains correctly blocked until
the gate clears or the owner records an explicit exception. This sidecar neither changes
nor expedites that gate.

## Owner Closeout Instruction

The approved packet is returned to `Codex` for task closeout finalization. Closeout
should make this review record durable, keep the packet support-only, and then use the
normal task PR flow (`scripts/git/task_finalize.sh`) before moving the task to `done`.
