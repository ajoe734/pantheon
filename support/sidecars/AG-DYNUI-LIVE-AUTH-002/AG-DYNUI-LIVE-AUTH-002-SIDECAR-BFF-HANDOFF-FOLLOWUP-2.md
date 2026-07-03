# AG-DYNUI-LIVE-AUTH-002 BFF and Frontend Handoff Packet - Follow-up 2

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-LIVE-AUTH-002` |
| Parent title | Fix live Agora Trading Room auth headers |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar task | `AG-DYNUI-LIVE-AUTH-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Sidecar owner / reviewer | `Codex2` / `Claude` |
| Prior sidecar | `AG-DYNUI-LIVE-AUTH-002-SIDECAR-BFF-HANDOFF` (review approved, PR #2807 merged to `dev` as `813568c7a1e2db36f11cedd18de46b11d15c71bc`) |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-03` |
| Mutates canonical | `false` |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend code, registry behavior,
or governance behavior. The parent owner and reviewer decide whether and how
to absorb this packet into the mainline fix.

---

## 1. Why This Follow-up Exists

The prior handoff packet correctly identified the live symptom:

- `/bff/me` succeeded in the authenticated browser session;
- `/bff/agora/trading-room` and `/bff/agora/trading-room/decision-events`
  returned `401 AUTH_REQUIRED`;
- unauthenticated direct curl to `/bff/agora/trading-room` also returned
  `401 AUTH_REQUIRED`, which is the correct fail-closed posture.

The prior packet's working diagnosis was that the `execute-plans`
`tradingRoom.ts` client bypassed shared bearer/tenant/correlation header
helpers. Parent investigation in PR #2808 supersedes that diagnosis on the
facts:

1. `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` already sends
   `credentials: "include"` on Trading Room reads and writes.
2. The live browser's successful `/bff/me` and
   `/bff/management/shell-summary` calls prove the session can authenticate
   through the `pantheon_session` cookie in this dev environment.
3. The Trading Room BFF router was only forwarding the `Authorization` header
   into `extract_identity()`, while the working routes use
   `_extract_identity(authorization, session_cookie=...)`.

This follow-up exists to record the corrected handoff: the immediate parent
gap is a **BFF route cookie-session propagation gap**, not a frontend client
header propagation gap. Frontend live verification is still required after the
parent PR merges and deploys, but no `tradingRoom.ts` change is required by
the evidence reviewed here.

---

## 2. Current Parent State

`AI_NAME=Codex2 ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-002` reports
the parent task is `review_approved` after PR #2808 merged:

| Parent evidence | Finding |
|---|---|
| PR | `https://github.com/ajoe734/pantheon/pull/2808` |
| PR title | `AG-DYNUI-LIVE-AUTH-002: accept cookie session in trading-room BFF` |
| Head branch / target | `task/AG-DYNUI-LIVE-AUTH-002` -> `dev` |
| Head SHA | `144abd335e4224f41415478134b70be4d20831ed` |
| Merge commit | `056f5cd8f2ca2a05b4bd577d479ae5e3736ef067` at `2026-07-03T07:23:10Z` |
| Files changed | `services/control-plane/bff/agora/trading_room/router.py`; `services/control-plane/bff/agora/trading_room/test_trading_room.py` |
| Parent verification | `python3 -m pytest services/control-plane/bff/agora/trading_room/test_trading_room.py services/control-plane/tests/agora/test_agora_isolation_matrix.py -q` -> `100 passed` |
| PR checks | Commit trailers, runtime mirror guard, smoke acceptance, and orchestrator sync passed. |

Live deployment still needs explicit closeout evidence from the parent owner:

| Live endpoint | Result |
|---|---|
| `GET https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | `execute-plans` commit `6556534b937e433b40cf94d87b8ab25a792aed35`, `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`; this frontend deployment is unchanged and does not prove the BFF merge was deployed. |
| `GET https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | `{"status":"ok","service":"operator-bff","version":"0.2.0", ...}` |
| Unauthenticated `GET /bff/agora/trading-room` | `401 AUTH_REQUIRED`, still correct for requests with no bearer and no session cookie |

---

## 3. Corrected BFF Query Gap Matrix

The route family itself remains the correct BFF surface. The gap is that every
route must accept the same browser session credential posture as `/bff/me`:
`Authorization` header when present, or `pantheon_session` cookie when the
browser session is cookie-backed.

| Route family | Parent PR #2808 delta | Frontend handoff implication |
|---|---|---|
| `GET /bff/agora/trading-room` | Adds `pantheon_session: Optional[str] = Cookie(default=None)` and passes `session_cookie=pantheon_session` into `extract_identity()` | Existing `credentials: "include"` should authenticate cookie-backed browser sessions after deploy. |
| `GET /bff/agora/trading-room/strategies/{strategy_id}` | Same cookie forwarding | No frontend route or DTO change required. |
| `POST /bff/agora/strategies/{strategy_id}/trading-room/proposals` and proposal detail/accept routes | Same cookie forwarding | Proposal generation and accept flows should use the existing client transport; preserve idempotency and typed errors. |
| `GET/PATCH/POST /bff/agora/trading-room/workspaces[...]` for workspace read, layout, views, widgets, versions, rollback | Same cookie forwarding | Existing workspace/grid/widget flows should now share the authenticated session posture; keep `If-Match` and idempotency unchanged. |
| `GET /bff/agora/trading-room/decision-events` and `GET /decision-events/{id}` | Same cookie forwarding | The initial event queue load should stop returning `401` for valid cookie sessions. |
| `POST /bff/agora/trading-room/decision-events/{id}/decisions` | Same cookie forwarding | Decision writes still require `If-Match`, `Idempotency-Key`, and `X-Request-Id`; this remains decision-support only. |
| `GET /bff/agora/trading-room/stream` | Same cookie forwarding | SSE auth posture aligns with the rest of the Trading Room route family; this does not make SSE a completion proof by itself. |
| `GET/POST /bff/agora/trading-intents[...]` handoff/withdraw routes | Same cookie forwarding | Request-only governed handoff remains non-ordering and non-capital-binding. |

The frontend should not compensate for this backend gap by manually reading or
reposting cookies, by adding route-specific bearer hacks, or by falling back to
fixtures in strict live mode. `credentials: "include"` is the correct browser
transport for the cookie-backed path; the BFF router must consume it.

---

## 4. Operator Journey Delta

### Journey A: Initial Trading Room Load

1. Operator opens `/agora/trading-room` in the Pantheon dev frontend.
2. Browser sends the existing session cookie because the client uses
   `credentials: "include"`.
3. `GET /bff/agora/trading-room` and
   `GET /bff/agora/trading-room/decision-events` forward that
   `pantheon_session` cookie into identity extraction.
4. BFF returns `200` for the authenticated session.
5. The page renders the AGORA Trading Room shell and queue/empty state; it
   must not render `Failed to load Trading Room`.

### Journey B: Workspace Proposal And Grid

1. Proposal generation, proposal read/accept, workspace read, layout patch,
   version list/rollback, view/widget mutation, and widget revision calls
   authenticate through either bearer headers or the same cookie session.
2. `401` remains valid only when both bearer and session cookie are absent or
   invalid.
3. `403`, `404`, `409`, `412`, `422`, and `501` should keep the existing typed
   client semantics. Do not collapse these into a generic auth failure.

### Journey C: Decision Event Write

1. Operator submits a decision event action with the event ETag,
   idempotency key, and request id.
2. Route auth uses bearer or cookie session, but write guards remain unchanged:
   exact `If-Match`, `Idempotency-Key`, and `X-Request-Id` are still required.
3. The route still records decision-support intent only. It does not place
   orders, create RuntimeBinding, bind capital, or approve promotion.

---

## 5. Parent Review Notes

For the parent review of PR #2808, focus on the backend auth boundary rather
than the original frontend-header hypothesis:

1. Confirm every Trading Room route that calls `extract_identity()` accepts
   `pantheon_session: Optional[str] = Cookie(default=None)` and passes
   `session_cookie=pantheon_session`.
2. Confirm the no-credential path still returns `401 AUTH_REQUIRED`; cookie
   support must not turn unauthenticated curl into success.
3. Confirm cookie-only regression coverage exists at least for the two live
   failing reads: aggregate and decision-events.
4. Confirm decision writes and governed handoff/withdraw routes still require
   their existing concurrency and idempotency headers.
5. Treat any missing cookie forwarding on the wider workspace/proposal/widget
   route family as the same class of bug, because the live operator can enter
   those flows from the same Trading Room page.

---

## 6. Live Verification Checklist After Merge And Deploy

After PR #2808's merge commit is deployed to the dev BFF:

1. Confirm the BFF deploy includes the parent merge commit. The exact command
   may depend on the deployment record available to the parent owner, but the
   evidence should name the deployed Pantheon commit or image digest.
2. Confirm the frontend deployment is still strict live:

   ```bash
   curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json
   ```

   Expected: `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and the
   frontend commit remains the intended `execute-plans` commit unless a
   separate frontend PR is deliberately introduced.

3. Run a browser probe against:

   ```text
   https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room
   ```

   Expected DOM/network result:

   - old white Trading Desk markers remain absent;
   - `AGORA`, `Servant`, and `Trading Room` remain present;
   - `Failed to load Trading Room` is absent;
   - browser responses for `/bff/agora/trading-room` and
     `/bff/agora/trading-room/decision-events` are `200`;
   - captured request evidence proves session auth was present without
     committing raw cookie or bearer token values.

4. Keep unauthenticated curl fail-closed:

   ```bash
   curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room
   ```

   Expected: `401 AUTH_REQUIRED` when no bearer header and no session cookie
   are supplied.

Do not close parent acceptance on network-idle-only evidence. The proof needs
both DOM text and the two Trading Room BFF response statuses.

---

## 7. Verification Performed For This Sidecar

| Command | Result |
|---|---|
| `git status -sb` / `git branch --show-current` / `git remote -v` | Worktree on `task/AG-DYNUI-LIVE-AUTH-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`; remote `origin` is `https://github.com/ajoe734/pantheon.git`; only initial dirty item was the generated task brief for this task. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Confirmed sidecar is active `in_progress`, owner `Codex2`, reviewer `Claude`, artifact path is this packet. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-002` | Confirmed parent is `review_approved`; review notes approve PR #2808 and ask owner Claude to complete closeout plus post-deploy live Playwright probe. |
| `gh pr view 2808 --repo ajoe734/pantheon --json ...` | Confirmed PR #2808 merged at `2026-07-03T07:23:10Z` as `056f5cd8f2ca2a05b4bd577d479ae5e3736ef067`; all visible checks passed. |
| `gh pr diff 2808 --repo ajoe734/pantheon --name-only` | Confirmed parent PR changes only `router.py` and `test_trading_room.py`. |
| `git diff --stat origin/dev...FETCH_HEAD` after fetching parent branch | Confirmed parent diff is 2 files, 93 insertions, 30 deletions. |
| `curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Confirmed live FE is still `execute-plans` commit `6556534b937e433b40cf94d87b8ab25a792aed35`, strict live BFF mode. |
| `curl -fsS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | BFF healthy. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room` | Unauthenticated request still returns `401 AUTH_REQUIRED`, as expected. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned, per
the task-scoped read-order instruction. No runtime code, canonical truth, or
frontend code was modified by this sidecar.

---

## 8. Handoff Status

This packet is ready for `Claude` review as a support-only update to
`AG-DYNUI-LIVE-AUTH-002`. It records that the parent PR's backend
cookie-session fix supersedes the prior frontend-header implementation
guidance while preserving the same live operator acceptance proof.

If the parent deploy or live browser probe contradicts this packet, reviewer
should ask for a narrow correction packet tied to that new evidence. No
additional sidecar runtime work is needed from Codex2 unless reviewer finds
the handoff inaccurate.
