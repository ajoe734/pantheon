# INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED-SIDECAR-BFF-HANDOFF` |
| Helper parent | `INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED` |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude2` / `Claude` |
| Sidecar owner / reviewer | `Claude` / `Claude2` |
| Date | `2026-06-21` |
| Status | `review_approved / closeout finalized` |
| Current Pantheon dev base | `03742a5f710dc4784b066ea89e92c3884829e282` |
| Previous packet closeout | Followup-33 archived `done` at `2026-06-21T19:38:51Z`; packet PR merged at `03742a5f` (PR `#2133`) |
| Parent implementation PR | execute-plans PR `#66`, `OPEN` / `MERGEABLE`, head `de7834b8c33d39942e37f0fb8d4511726d828ad8`, updated `2026-06-21T11:34:55Z`; `integration-gate` still failed |
| execute-plans dev base | `574cc541bf326e031a2f6bf9081e428a708b929a` |
| Legacy compatibility PR | execute-plans PR `#63`, `OPEN`, head `e1cb9125c87d9ace0adf3dd9f17f24ff0542d9c5`, updated `2026-06-20T16:53:49Z` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI/source-of-truth contract semantics, BFF runtime code,
route registries, governance policy, database migrations, OpenClaw adapter
code, compatibility manifest source, or execute-plans source files.

## 1. Purpose

This sidecar packet supports
`INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED`, the
auto-integrator unblock task created when the followup-33 branch could not be
safely integrated due to a CI-red condition.

The purpose of this packet is to document:

1. The resolution of the CI-red: how the followup-33 branch was unblocked and
   merged.
2. The current BFF/frontend state after followup-33 merged into `origin/dev`.
3. Any remaining blockers on the Agora frontend path.

This sidecar does not approve, reopen, or implement parent `AG-FE-ID-001`.

## 2. CI-Red Resolution Summary

The auto-integrator created task
`INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED`
because branch `task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33` could not
be safely merged. The resolution sequence was:

| Step | Commit / Event | Result |
|---|---|---|
| followup-33 anchor commits produced | `29fe886b`, `38ba90e0`, `7802ca96`, `c3cade0c`, `05807f33` | Packet, review, and task brief files committed on the task branch. |
| Dev absorbed (PR `#2134` for AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 merged) | `1ab3fd93` | Dev advanced; followup-33 branch needed a rebase. |
| Rebase merge of dev into followup-33 branch | `3434c25a`, `5db62f7c` | Branch absorbed dev advances and became conflict-free. |
| PR `#2133` merged into `origin/dev` | `03742a5f` | All CI gates passed: commit trailers ✓, runtime mirror guard ✓, smoke acceptance ✓ (×2 checks). |
| followup-33 archived `done` | `2026-06-21T19:38:51Z` | Task closed with PR merge confirmed. |

The CI-red that triggered the parent task was resolved by the branch rebasing
to absorb the dev advances introduced by PR `#2134`. No BFF source, canonical
truth, or contract files were modified to clear the gate.

## 3. Current Task State Snapshot

| Task | Status | Handoff implication |
|---|---|---|
| `INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED` | Active `in_progress`; owner `Claude2`, reviewer `Claude` | Parent task documenting the CI-red resolution. This packet is the support artifact. |
| `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33` | Archived `done` at `2026-06-21T19:38:51Z` | PR `#2133` merged. Packet and review files are now on `origin/dev`. |
| `AG-FE-ID-001` | Active `blocked`; owner `Claude`, reviewer `Codex`, waiting for `Gemini` | Parent PR `#66` remains blocked by the execute-plans aggregate release gate, not by the Agora-specific shell/client review. |
| `AG-BE-ID-003` | Archived `done` | Servant-session backend facade remains available. |

## 4. Sources Rechecked

| Source | Why it matters |
|---|---|
| `git log --oneline --decorate 4e745eb0..03742a5f` | Shows the full commit chain from followup-33 baseline to current dev HEAD. |
| `AI_NAME=Claude python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED` | Confirms parent in `in_progress`; owner `Claude2`, reviewer `Claude`; next field shows investigation of PR `#2133`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33` | Confirms archived `done` at `2026-06-21T19:38:51Z`; PR `#2133` merge confirmed. |
| `git diff --name-status 4e745eb0..03742a5f -- services/control-plane/bff/agora ...` | Only followup-33 packet and review files added in AG-FE-ID-001 support path; no Agora BFF or contract delta. |
| `gh pr view 66 --repo ajoe734/execute-plans --json ...` | PR `#66` still `OPEN` / `MERGEABLE`; head `de7834b8`; integration-gate still failed; updated `2026-06-21T11:34:55Z`. |
| `gh pr view 63 --repo ajoe734/execute-plans --json ...` | PR `#63` still `OPEN`; head `e1cb9125`; updated `2026-06-20T16:53:49Z`. |
| `python3 -m pytest ... -q` (Agora BFF focused tests) | `39 passed in 20.24s`. |
| `python3 scripts/agora_compat_manifest.py deployment-gate ...` | Still fail-closed: not compatible, frontend runtime commit placeholder, blocking reasons non-empty. |

`current-work.md` and the full `ai-activity-log.jsonl` were not read.

## 5. Delta Since Followup-33 Baseline

Baseline: followup-33 dev base `4e745eb0`.

| Change | What changed | Parent implication |
|---|---|---|
| Pantheon dev advanced | `origin/dev` moved from `4e745eb0` to `03742a5f`. | Use `03742a5f` as the current dev base. |
| AG-BE-TR-001 sidecar | PR `#2134` merged AG-BE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2. | No AG-FE-ID-001 identity/servant implication. Separate BFF surface. |
| AG-FE-ID-001 followup-33 | PR `#2133` merged followup-33 packet, review, and task brief files. | Followup-33 files are now on `origin/dev`. CI gates passed. |
| Checked Agora paths | `git diff --name-status 4e745eb0..03742a5f -- services/control-plane/bff/agora services/control-plane/bff/main.py services/control-plane/specs/agora services/control-plane/openapi docs/contracts/agora` | No changes to Agora BFF runtime, contracts, or specs. |
| execute-plans PR `#66` | Unchanged: still open; head `de7834b8`; integration-gate still failing. | Not a merge-readiness improvement for AG-FE-ID-001. |
| execute-plans PR `#63` | Unchanged: still open; head `e1cb9125`; timestamp `2026-06-20`. | Continue to treat as unresolved legacy compatibility risk. |

## 6. BFF Query Ledger For Parent

The BFF route ledger is unchanged from followup-33. No Agora BFF routes were
added, removed, or modified between `4e745eb0` and `03742a5f`.

| Route or surface | Runtime BFF status | Frontend handoff rule |
|---|---|---|
| `GET /bff/agora/me` | Implemented; focused tests pass. | Parent may use for identity readiness through strict BFF transport. |
| `GET /bff/agora/capabilities` | Implemented; focused tests pass. | Parent may use for readiness/capability display. |
| `POST /bff/agora/servant/ensure` | Implemented; requires `Idempotency-Key` and `X-Request-Id`; maps OpenClaw sync failures to 503. | `servant.ts` must send required headers and map 401/403/422/503 without fabricating success. |
| `POST /bff/agora/servant/sessions` | Implemented; tests cover interactive/trainer/research_task, audit fields, 201 create. | Frontend may target only with strict servant-session clients and UI tests. |
| `GET /bff/agora/servant/sessions/{session_id}` | Implemented; tests pass. | Safe as servant-session detail route after create. |
| `POST /bff/agora/servant/sessions/{session_id}/messages` | Implemented; maps OpenClaw provider degradation. | Client must show degraded/error state without fabricating assistant success. |
| `POST /bff/agora/servant/sessions/{session_id}/terminate` | Implemented; tests pass. | Use only for sessions created through the servant facade. |
| `GET /bff/agora/servant/sessions/{session_id}/stream` | Implemented; SSE stream tests pass. | Can be used for session-scoped stream when frontend session UI is in scope. |
| `GET /bff/agora/servant` | Not implemented. | Do not depend on this route. PR `#66` correctly removed the unsupported preflight. |
| `POST /bff/agora/servant/reconcile` | Not implemented. | Keep out of parent UI unless backend support lands. |
| Legacy `GET/POST /bff/agora/sessions*` | Live but not servant-session. | Do not treat as proof of servant-session readiness. |
| Workshop SSE stream | Separate Phase 2 surface. | Not AG-FE-ID-001 Phase 1 acceptance scope. |
| Research plan/run routes | Separate Phase 3 scope. | Not parent shell acceptance scope. |

## 7. Execute-Plans PR #66 Gate State

Unchanged from followup-33 observation:

| Check | State | Evidence |
|---|---|---|
| `integration-gate` | `fail` | Run `27902747928`, job `82565909429`; completed `2026-06-21T11:33:22Z` |

PR `#66` is `OPEN` / `MERGEABLE`. The PR is unblocked at the code level (Codex
re-review approved the AG-FE-ID-001 slice at `de7834b8`), but the aggregate
release gate remains failed. Gate breakdown (from the release summary comment):

| Gate | Result | Owner |
|---|---|---|
| Gate 0 Preconditions | `PASS` | - |
| Gate 1 Static / Build / Unit | `FAIL` | Gemini |
| Gate 2 Contract Drift | `FAIL` | Codex |
| Gate 3 BFF Route Probes | `WARN` | Codex |
| Gate 4 Browser Frontend E2E | `PASS` | - |
| Gate 5 Playwright User Flows | `FAIL` | Codex |
| Gate 6 A11y / Perf | `FAIL` | Codex2 |
| Gate 7 Release Decision | `FAIL` | Codex |

Parent should not mark itself done until the gate reruns cleanly or the
repository records a formal exception disposition.

## 8. Minimal Current Operator Journey

The honest status-shell journey is unchanged from followup-33. Assuming PR
`#66` or equivalent frontend code is used:

```text
Operator opens the approved Agora entry
  -> frontend verifies Agora-scoped auth/audience
  -> frontend calls GET /bff/agora/me through the strict identity client
  -> BFF returns tenant/user predicate, capabilities, and servant policy
  -> frontend calls GET /bff/agora/capabilities
  -> frontend calls POST /bff/agora/servant/ensure with required headers
  -> BFF returns a user-private agora_servant ServantProfile envelope
  -> shell renders servant status and no-authority policy facts
  -> session/command UI remains skeleton or disabled unless strict
     /bff/agora/servant/sessions* clients and UI acceptance are in scope
```

Backend session journey available for future frontend follow-through (backend
routes remain available at current dev):

```text
frontend creates a servant session with POST /bff/agora/servant/sessions
  -> session_type is interactive by default, or trainer/research_task when supplied
  -> BFF sends approved context_bundle to OpenClaw with audit fields
  -> frontend sends messages through POST /sessions/{id}/messages
  -> stream reads use GET /sessions/{id}/stream
  -> terminate uses POST /sessions/{id}/terminate
  -> OpenClaw degradation maps to typed dependency-unavailable behavior
```

## 9. Parent Absorption Checklist

The parent task (`INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED`)
may be closed once the following are confirmed:

| Check | Required evidence |
|---|---|
| CI-red root cause documented | This packet documents the rebase-to-dev-advance mechanism that cleared the gate. |
| PR `#2133` merged confirmation | Confirmed: `03742a5f` is the merge commit; `done` archived at `2026-06-21T19:38:51Z`. |
| No canonical truth mutations | Confirmed: only support artifact files changed in the sidecar path. |
| BFF state unchanged | Confirmed: 39 BFF tests passed; no Agora route or contract changes. |
| AG-FE-ID-001 blocker still valid | Parent `AG-FE-ID-001` remains blocked pending execute-plans PR `#66` merge or aggregate gate disposition. This unblock task does not clear that blocker. |
| Compat manifest fail-closed | Confirmed: compatibility status not compatible, frontend runtime commit placeholder, blocking reasons non-empty. |
| Gate ownership preserved | Aggregate gate failures remain with recorded owners (Gate 1: Gemini, Gate 2/5/7: Codex, Gate 6: Codex2). Not absorbed into AG-FE-ID-001 or this unblock task. |

## 10. Verification Performed For This Sidecar

| Command | Result |
|---|---|
| `git branch --show-current` | `task/INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED-SIDECAR-BFF-HANDOFF` |
| `git status --short` | Only `.orchestrator/task-briefs/integration_unblock_ag_fe_id_001_...md` untracked. |
| `git log --oneline 4e745eb0..03742a5f --decorate` | Shows PR `#2133` and PR `#2134` merges since followup-33 baseline. |
| `git diff --name-status 4e745eb0..03742a5f -- services/control-plane/bff/agora ...` | Only followup-33 packet and review files added; no Agora BFF or contract changes. |
| `AI_NAME=Claude python3 scripts/ai_status.py show INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED` | Active `in_progress`; owner `Claude2`, reviewer `Claude`. |
| `AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33` | Archived `done` at `2026-06-21T19:38:51Z`; PR `#2133` merge confirmed. |
| `gh pr view 66 --repo ajoe734/execute-plans --json ...` | `OPEN` / `MERGEABLE`; head `de7834b8`; integration-gate failing. |
| `gh pr view 63 --repo ajoe734/execute-plans --json ...` | `OPEN`; head `e1cb9125`; updated `2026-06-20`. |
| `python3 -m pytest ... -q` (Agora BFF focused tests) | `39 passed in 20.24s`. |
| `python3 scripts/agora_compat_manifest.py deployment-gate ...` | Fail-closed: not compatible, placeholder frontend runtime commit, blocking reasons non-empty. |

## 11. Handoff To Reviewer

Reviewer `Claude2`: please review this support-only packet for factual accuracy
and scope discipline. The packet documents the CI-red resolution for PR `#2133`
and confirms that no BFF/contract state changed since followup-33.

Suggested approval command:

```bash
AI_NAME=Claude2 python3 scripts/ai_status.py approve INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED-SIDECAR-BFF-HANDOFF "CI-red resolution handoff packet approved: PR #2133 merged, BFF state verified, no canonical truth mutations. Parent unblock task may close on these facts."
```

Suggested reopen command if changes are required:

```bash
AI_NAME=Claude2 python3 scripts/ai_status.py reopen INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED-SIDECAR-BFF-HANDOFF "Describe the exact packet correction needed."
```

*Prepared by Claude for the `INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED-SIDECAR-BFF-HANDOFF` support slice.*

## 12. Closeout Record

| Field | Value |
|---|---|
| Review approval | Approved by `Claude2` per task brief next field and `ai_status.py show` `review_approved` confirmation. |
| Approval message | "CI-red resolution handoff packet approved: PR #2133 merged at 03742a5f, BFF state verified (39 tests pass), no Agora BFF or contract changes, no canonical truth mutations. Gate ownership for PR #66 aggregate gate preserved. Compat manifest fail-closed confirmed. Parent unblock task may close on these facts." |
| Closeout date | `2026-06-21` |
| Finalized by | `Claude` |
| No canonical mutations | Confirmed. Only support artifact files committed in this task slice. |
| Task brief file | `.orchestrator/task-briefs/integration_unblock_ag_fe_id_001_sidecar_bff_handoff_followup_33_ci_red_sidecar_bff_handoff.md` included in closeout commit as worker-workspace artifact. |
| Closeout commit scope | `support/sidecars/.../INTEGRATION-UNBLOCK-AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-33-CI-RED-SIDECAR-BFF-HANDOFF.md`, `.orchestrator/task-briefs/integration_unblock_ag_fe_id_001_sidecar_bff_handoff_followup_33_ci_red_sidecar_bff_handoff.md` |
