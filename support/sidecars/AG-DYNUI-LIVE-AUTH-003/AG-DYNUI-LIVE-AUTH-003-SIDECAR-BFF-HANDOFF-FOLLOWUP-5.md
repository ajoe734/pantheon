# AG-DYNUI-LIVE-AUTH-003 BFF and Frontend Handoff Follow-up 5

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-LIVE-AUTH-003` |
| Parent title | Agora Trading Room frontend BFF auth headers |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar task | `AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Sidecar owner / reviewer | `Claude2` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-03` |
| Mutates canonical | `false` |
| Previous packet | `AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Related fix PRs | `ajoe734/pantheon#2834` (identity 500 fix, merged `2dd82311d`); `ajoe734/execute-plans#168` (bearer fallback, merged `ffbc2357f`) |

This is a support artifact only. It does not change canonical truth, L1
contracts, BFF runtime code, `execute-plans` frontend runtime code, registry
behavior, governance behavior, RuntimeBinding, capital, broker authority, or
parent `AG-DYNUI-LIVE-AUTH-003` acceptance. The parent owner and reviewer decide
whether and how to absorb this packet into the mainline closeout.

---

## 1. Purpose

Follow-up 4 left the handoff at: the frontend auth-header patch
(`execute-plans` PR #148, merged as `63437558...`) was deployed, the BFF was
healthy and fail-closed for unauthenticated requests, but the parent owner
still needed authenticated live browser proof before AUTH-003 could be marked
`done`.

Between follow-up 4 and this packet, two more fixes landed and the parent
finally obtained that live browser proof:

- `ajoe734/pantheon` PR `#2834` (`AG-DYNUI-LIVE-AUTH-003-BFF-500-TRADING-ROOM`)
  fixed a BFF `500 INTERNAL_ERROR` on `GET /bff/agora/trading-room` and related
  write routes: production `extract_identity()` returns a pydantic
  `OperatorIdentity` (attribute access only), but several `trading_room/router.py`
  handlers called `identity.get(...)` as if it were a dict, raising an
  unhandled `AttributeError` once a real bearer-authenticated identity reached
  them. The fix routes every identity field read through the existing
  `_workspace_scope()` helper, which already supported both dict- and
  attribute-style identities. Merged commit `2dd82311dcd95b9ebe4ed33a8d16666ecbb82791`.
- `ajoe734/execute-plans` PR `#168` (`AG-DYNUI-LIVE-AUTH-004`) added a shared
  BFF bearer-token fallback reused by the Trading Room client. Merged commit
  `ffbc2357f23b1a728ed6794d2231356ff28f16ed`.

With both deployed, the parent task record now shows a completed live browser
probe (`2026-07-03T14:01:55Z`): `/agora/trading-room` nav `200`; `/bff/me`,
`/bff/agora/trading-room`, `/bff/agora/trading-room/decision-events`, SSE, and
shell-summary all `200`; no console errors; no `Failed to load Trading Room`;
screenshot shows the dark Agora layout. Parent status is `review_approved`
with reviewer note: "Owner should perform task-closeout-finalization and move
done if artifacts are complete."

This packet's handoff is narrower than prior follow-ups: the live recovery
chain this sidecar family has tracked since `AG-DYNUI-LIVE-DEFAULT-001` now
appears proven. This packet records that state and the remaining owner
closeout step; it does not itself finalize the parent task.

---

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar artifacts are L0 support records and must not override canonical L1/L2 truth. |
| `.orchestrator/task-briefs/ag_dynui_live_auth_003_sidecar_bff_handoff_followup_5.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff materials; ownership was auto-reassigned from Copilot to Claude2 after repeated Copilot quota failures. |
| `.orchestrator/skills/worker-anchor-commit.md` | Support packet work should be made durable with a task-scoped commit; no stash or broad staging. |
| `.orchestrator/skills/task-closeout-finalization.md` | This dispatch reason is `owned_ready_dispatch`, not `owned_finalize_dispatch`; the sidecar itself is `in_progress`, so full closeout is not the current action. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Sidecar is `in_progress`, owner `Claude2`, reviewer `Claude`, artifact path is this packet. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003` | Parent is `review_approved`; reviewer note records completed live browser proof at `2026-07-03T14:01:55Z` and asks the owner to run closeout. |
| Prior packets under `support/sidecars/AG-DYNUI-LIVE-AUTH-003/` | Earlier sidecars recorded Pantheon PR #2820, the BFF `502`/`200` transitions, execute-plans PR #148 (auth headers), and the remaining live-proof gap as of follow-up 4. |
| `git log --oneline -5` (this worktree) | Confirms PR #2834 (identity 500 fix) already merged to `dev` before this sidecar started; local branch is `task/AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5`, clean except this packet's generated task brief. |
| `git show 375ac2174 --stat` / `-s --format=%B` | Confirms the identity-500 root cause, fix scope (`_workspace_scope()` reuse across GET/decision/approve-modify routes), and regression test evidence recorded in the commit trailer. |
| `gh pr view 2834 --repo ajoe734/pantheon --json ...` | Confirmed PR #2834 merged `2026-07-03T13:44:54Z` as `2dd82311dcd95b9ebe4ed33a8d16666ecbb82791`. |
| `gh pr view 168 --repo ajoe734/execute-plans --json ...` | Confirmed PR #168 (title "AG-DYNUI-LIVE-AUTH-004: reuse shared BFF bearer fallback") merged `2026-07-03T13:40:34Z` as `ffbc2357f23b1a728ed6794d2231356ff28f16ed`. |
| `curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Dev FE now serves `execute-plans` commit `ffbc2357f23b1a728ed6794d2231356ff28f16ed`, source branch `dev`, live strict BFF mode, real writes disabled. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | Dev BFF returned HTTP `200`, service `operator-bff`, version `0.2.0`. |
| Unauthenticated `curl -sS -i` to `/bff/agora/trading-room` and `/bff/agora/trading-room/decision-events` | Both returned HTTP `401` with `AUTH_REQUIRED`; no-token/no-bearer access remains fail-closed even after the identity-500 fix. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## 3. Current State Summary

### Parent task state

Parent `AG-DYNUI-LIVE-AUTH-003` is `review_approved`. Unlike every prior
follow-up in this sidecar family, the parent record now shows the live
recovery evidence that was previously missing:

- execute-plans PR #168 merged commit `ffbc2357` and dev FE deploy/gate
  succeeded (deploy `28664312966`, integration gate `28664312972` success).
- Pantheon backend fix PR #2834 merged commit `2dd82311` and manual Pantheon
  Nonprod Deploy `28664660985` succeeded at `2026-07-03T14:02:05Z`.
- Live browser probe at `2026-07-03T14:01:55Z`: `/agora/trading-room` nav
  `200`; `/bff/me`, `/bff/agora/trading-room`,
  `/bff/agora/trading-room/decision-events`, SSE, and shell-summary all `200`;
  no console errors; no `Failed to load Trading Room`; screenshot
  `/tmp/agora-live-after-auth002.png` shows the dark Agora layout.

The reviewer note explicitly asks the owner to run
`task-closeout-finalization` and move the parent to `done` if artifacts are
complete. That is an action for the parent owner (`Claude`), not this sidecar.

### Live BFF state (reconfirmed by this sidecar)

| Probe | Result | Handoff meaning |
|---|---|---|
| `GET /health` | HTTP `200`, service `operator-bff`, version `0.2.0` | Public dev BFF readiness is probeable. |
| Unauthenticated `GET /bff/agora/trading-room` | HTTP `401`, code `AUTH_REQUIRED` | No-token aggregate read remains fail-closed even after the identity-500 fix. |
| Unauthenticated `GET /bff/agora/trading-room/decision-events` | HTTP `401`, code `AUTH_REQUIRED` | No-token event list remains fail-closed. |

### Live frontend state (reconfirmed by this sidecar)

```json
{
  "app": "execute-plans",
  "environment": "pantheon-dev-fe",
  "deployedAt": "20260703T134217Z",
  "commit": "ffbc2357f23b1a728ed6794d2231356ff28f16ed",
  "sourceRef": "ffbc2357f23b1a728ed6794d2231356ff28f16ed",
  "sourceBranch": "dev",
  "feHost": "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io",
  "bffHost": "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io",
  "buildMode": {
    "VITE_BFF_MODE": "live",
    "VITE_BFF_FALLBACK": "strict",
    "VITE_BFF_REAL_WRITES": "false"
  }
}
```

This matches the commit recorded in the parent's live-proof note (execute-plans
PR #168 merge commit `ffbc2357...`).

---

## 4. BFF Query Gap Matrix — Closed

Every gap tracked by the earlier follow-ups in this family is now resolved on
the deployed live path:

| Gap tracked by earlier follow-ups | Resolution |
|---|---|
| Trading Room client did not send bearer auth headers (`AUTH-003` scope) | Closed by Pantheon PR #2820 (mirror) and execute-plans PR #148 (broader live client, merged `63437558...`). |
| Live frontend deploy lagged the header fix (follow-ups 2–4) | Closed once dev FE deployed `63437558...` and later `ffbc2357...`. |
| BFF `500 INTERNAL_ERROR` on `GET /bff/agora/trading-room` once a real bearer identity reached the router | Closed by Pantheon PR #2834: attribute-only `OperatorIdentity` reads now go through `_workspace_scope()` instead of `identity.get(...)`. |
| No authenticated live browser proof | Closed: parent record shows a `2026-07-03T14:01:55Z` browser probe with `200` across nav, `/bff/me`, aggregate, decision-events, SSE, and shell-summary, no console errors, no `Failed to load Trading Room`. |
| Unauthenticated fail-closed behavior | Still intact: this sidecar reconfirmed `401 AUTH_REQUIRED` on both Trading Room routes with no token. |

No further BFF query gap is currently tracked for this sidecar family. The
one remaining action is procedural: parent owner closeout to `done`.

---

## 5. Operator Journey Handoff

### Journey A: Authenticated operator (now proven live)

1. Operator opens `/agora/trading-room` on the Pantheon dev frontend
   (`execute-plans` commit `ffbc2357...`, live strict BFF mode).
2. Browser has a cookie-backed session or bearer token in the shared BFF auth
   provider path; Trading Room client calls include both `credentials:
   "include"` and the shared/fallback bearer header.
3. BFF `extract_identity()` returns an `OperatorIdentity`; `trading_room/router.py`
   now reads identity fields through `_workspace_scope()`, so the aggregate GET
   and decision-events GET succeed instead of raising `AttributeError` → `500`.
4. UI renders the dark Agora Trading Room shell with live data; no
   `Failed to load Trading Room` message; no console errors.

### Journey B: Unauthenticated / no-token request

1. A request without a valid Bearer token hits
   `GET /bff/agora/trading-room` or `.../decision-events` directly.
2. BFF returns `401 AUTH_REQUIRED` before reaching any identity-shape-sensitive
   code path — fail-closed behavior is unaffected by the identity-500 fix.

### Journey C: Decision event write

1. Operator reviews a decision event and submits a choice through
   `decideOnEvent()`.
2. Bearer/tenant headers are attached (via `authHeaders()` /
   `getAuthProvider()` fallback); caller-provided `If-Match`,
   `Idempotency-Key`, and `X-Request-Id` are preserved.
3. The BFF write path also now reads `decided_by` and the idempotency scope
   through `_workspace_scope()`, avoiding the same attribute-vs-dict failure
   mode observed on the read path.
4. Existing write guards remain: `401` for missing/invalid auth, `403` for
   role failure, `409` for idempotency conflict, `412` for stale `If-Match`.

---

## 6. Reviewer Checklist

For this sidecar review:

1. Confirm this packet only adds support material and the generated task
   brief.
2. Confirm the PR #2834 root-cause and fix description matches the actual
   commit (`375ac2174`): attribute-only `OperatorIdentity` reads routed through
   `_workspace_scope()` instead of `identity.get(...)`.
3. Confirm PR #168's merge commit `ffbc2357...` matches the currently deployed
   dev FE `deployment.json` commit.
4. Confirm the live browser probe summary in this packet matches the parent
   task's recorded `next` field verbatim (timestamp, routes, screenshot path).
5. Confirm unauthenticated Trading Room routes still return `401
   AUTH_REQUIRED` after the identity-500 fix — i.e., the fix did not weaken
   fail-closed behavior.
6. Confirm no secret token, cookie, bearer value, session value, or browser
   credential is committed in this packet.

For parent AUTH-003 closeout (owner action, not this sidecar's scope):

1. Re-verify the recorded live browser evidence is still current if time has
   passed since `2026-07-03T14:01:55Z`.
2. Run `task-closeout-finalization` per `.orchestrator/skills/task-closeout-finalization.md`.
3. Record the final merge commits for PR #2834 and PR #168 in the parent
   closeout artifact/commit.
4. Move parent `AG-DYNUI-LIVE-AUTH-003` to `done` only after closeout checks
   pass.

---

## 7. Verification Performed For This Sidecar

| Command | Result |
|---|---|
| `git branch --show-current`, `git status --short` | Confirmed branch `task/AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5`; only dirty item was the generated task brief before this packet was added. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Confirmed sidecar is `in_progress`, owner `Claude2`, reviewer `Claude`. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-DYNUI-LIVE-AUTH-003` | Confirmed parent is `review_approved` with recorded live browser proof and a request for owner closeout. |
| `git show 375ac2174 --stat` and `-s --format=%B` | Confirmed the identity-500 fix root cause, scope, and verification trailer (`pytest agora/trading_room/test_trading_room.py agora/ -q`, 94 passed). |
| `gh pr view 2834 --repo ajoe734/pantheon --json number,title,state,mergedAt,mergeCommit,headRefName,url` | Confirmed merged `2026-07-03T13:44:54Z` as `2dd82311dcd95b9ebe4ed33a8d16666ecbb82791`. |
| `gh pr view 168 --repo ajoe734/execute-plans --json number,title,state,mergedAt,mergeCommit,headRefName,url` | Confirmed merged `2026-07-03T13:40:34Z` as `ffbc2357f23b1a728ed6794d2231356ff28f16ed`. |
| `curl -fsS https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` | Confirmed dev FE deployment commit `ffbc2357f23b1a728ed6794d2231356ff28f16ed`, live strict BFF mode, real writes false. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health` | Returned HTTP `200`, service `operator-bff`, version `0.2.0`. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room` | Returned HTTP `401` with `AUTH_REQUIRED`; no-token aggregate read is fail-closed. |
| `curl -sS -i https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/trading-room/decision-events` | Returned HTTP `401` with `AUTH_REQUIRED`; no-token decision-events read is fail-closed. |

Prepared by Claude2 for reviewer `Claude`.
