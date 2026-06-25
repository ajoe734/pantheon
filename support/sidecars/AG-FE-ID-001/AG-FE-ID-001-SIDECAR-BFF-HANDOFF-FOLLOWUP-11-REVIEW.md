# Review: AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11

| Field | Value |
|---|---|
| Reviewer | `Claude` |
| Owner | `Codex2` |
| Review date | `2026-06-20` |
| Outcome | **Approved** |
| Review notes (zh) | 審查通過｜無需修改｜請 Codex2 完成 closeout 收尾 |

## Scope Compliance

The packet correctly declares `Mutates canonical truth: false`. The artifact is
a `support/sidecars/` document only. No changes were made to L1 canonical docs,
OpenAPI, capability manifests, BFF runtime code, registry code, governance
implementation, OpenClaw adapter code, or execute-plans source. Scope is
strictly limited to the `bff_handoff_packet` helper kind as required by the
sidecar constraint rules.

The working tree shows only the task-brief file
(`.orchestrator/task-briefs/ag_fe_id_001_sidecar_bff_handoff_followup_11.md`)
as modified, which is a worker-managed artifact and does not affect canonical
truth.

## Content Review

**§1 Purpose** correctly identifies the eleventh followup purpose: updating the
handoff after `origin/dev` advanced to merge commit `18f5bceb` via PR #1874.
The key delta vs FOLLOWUP-10 is accurately stated: `AG-BE-ID-003-SIDECAR-BFF-
HANDOFF-FOLLOWUP-2` is archived `done` and merged, confirming three new
session-routing findings — the parallel `/bff/agora/ask/sessions` surface, the
non-canonical `quick_ask` default in `POST /bff/agora/sessions`, and stale
ownership wording in `agora/identity/router.py`.

**§2 Current Task State Snapshot** is accurate as of 2026-06-20. Task statuses
are consistent with the live `ai_status.py show` output:

- `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11`: `review` (correct for
  this packet).
- `AG-FE-ID-001`: still `todo`; `AG-BE-ID-003` dependency correctly maintained.
- `AG-BE-ID-002`: archived `done`; servant ensure/provision/reconcile path
  established in FOLLOWUP-10 and carried forward correctly.
- `AG-BE-ID-003`: `blocked`, `waiting_for: Claude`; `session_type` gap is the
  primary contract blocker.
- `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`: archived `done`, PR #1874
  merged — the new reference for session-facade FE gating.
- `AG-XR-003`: `blocked`, `waiting_for: Claude2` — kept separate and not
  conflated with servant-session readiness.
- `AG-FE-DB-004`: archived `done`; correctly isolated from
  servant/session shell readiness.

Dependency honesty rule is correctly applied throughout: parent `AG-FE-ID-001`
cannot claim session readiness while `AG-BE-ID-003` is blocked.

**§3 Sources Rechecked** is complete and appropriately scoped. The packet
correctly avoids reading `current-work.md` and the full
`ai-activity-log.jsonl`, consistent with the task brief's reading instruction.

**§4 Delta Since Followup-10** is the core addition. Each row has a concrete
"Parent implication" column. Notable accuracy points:

- PR #1874 merge commit reference (`18f5bcebe06c0cd4ef0121a4b77de142b5909553`)
  is specific and verifiable.
- `ask/sessions` quick-ask split is a genuine new finding from the merged
  followup-2 packet and is correctly surfaced here.
- `quick_ask` non-canonical default is called out with the right consequence:
  parent session client must not omit `session_type` or treat legacy behavior as
  canonical servant-session readiness.
- Stale ownership wording in `agora/identity/router.py` is flagged correctly as
  an ambiguity the parent should not act on until BFF ownership is resolved.
- Servant ensure implementation is noted as unchanged — no regression from
  FOLLOWUP-10's 501→200 transition.
- Test docstring staleness (`test_agora_router.py` header says 501, tests assert
  200) is flagged as an explicit caveat, telling the parent to trust executable
  assertions over file comments.
- Frontend target files remain missing — confirmed by §12 source scan.

**§5 BFF Query Ledger** is accurate and well-differentiated. `/servant/ensure`
correctly reflects the implemented+tested status carried forward from FOLLOWUP-10.
Routes without runtime handlers (`/servant`, `/servant/reconcile`,
`/servant/sessions*`) are kept in the "no frontend use until runtime lands"
category. The new `ask/sessions*` row is a correct addition, accurately
constraining its use to `quick_ask` mode only and noting the ASK ownership
disposition. The legacy `sessions*` row in `main.py` is also correctly
distinguished from a canonical servant-session facade.

**§6 Session-Facade Blockers That Matter To The Frontend** is an accurate and
expanded blocker list. Each entry maps to a concrete frontend consequence. The
new entries for `ask/sessions` quick-ask filtering, the `quick_ask` silent
default, and the stale migration scope in `identity/router.py` are all
consistent with the delta described in §4.

**§7 Frontend Surface To Hand Off** is accurate. `AgoraApp.tsx`, `identity.ts`,
and `servant.ts` are still missing — confirmed by the §12 source scan. Required
parent decisions are clearly stated for each surface. The caution to reuse
generated `types.ts` types rather than hand-typing DTOs is a useful guard.

**§8 Updated Minimal Status-Shell Contract** correctly maintains the
servant-profile-ready framing from FOLLOWUP-10 (session facade still
unavailable) while adding a `session_stream_unavailable` callout consistent with
the shared-SSE finding. The authority prohibitions and state table are complete
and not regressed.

**§9 Operator Journey** provides two paths: the current honest journey (servant
ensure reachable, sessions unavailable) and the future session journey (still
blocked). Both are accurate. The Idempotency-Key requirement and 503 OpenClaw
handling are carried forward correctly.

**§10 Parent Absorption Checklist** carries forward the full 14-check set from
FOLLOWUP-10. The new `ask session split` check is a correct addition reflecting
the PR #1874 finding. No prior checks were silently removed or downgraded.

**§11 Suggested Parent Verification** is correct and actionable. The `rg`
commands targeting `quick_ask`, `ask/sessions`, `session_type`, and
`OPENCLAW_UPSTREAM_DEGRADED` are well-targeted to the new delta claims.

**§12 Sidecar Verification** records explicit commands and results:

- Branch confirmed correct (`task/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-11`).
- Already up to date with `origin/dev` at the merge commit.
- Task state verified from live status commands.
- `AgoraApp.tsx`, `identity.ts`, `servant.ts` all MISSING — confirmed.
- Source grep found `quick_ask`, `ask/sessions`, shared SSE routes in `main.py`
  as expected; `OPENCLAW_UPSTREAM_DEGRADED` not found in session routes — consistent
  with the blocker in §6.
- `35 passed in 19.49s` for the focused BFF/identity/servant/OpenClaw test set
  (consistent with FOLLOWUP-10's 35 count; no regression).
- Frozen v1 schema bundle verify passed for 15 files.
- v1.1 OpenAPI YAML parse OK.
- Agora generated types check passed: 17 schemas, 96 operations (no change from
  FOLLOWUP-10 — consistent with the packet's claim that servant ensure
  implementation is unchanged and no new operations were added).
- `git diff --check` clean for task-owned files.

All verification results are consistent with the packet's claims.

## Approval Notes

This packet is approved. It is a faithful incremental update over FOLLOWUP-10,
adding only the delta caused by: (a) PR #1874 merging
`AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` to `dev`, which surfaces three
new session-routing findings — the `ask/sessions` quick-ask split, the
non-canonical `quick_ask` default in `/bff/agora/sessions`, and the stale
ownership wording in `agora/identity/router.py`; and (b) the resulting need to
expand the session-facade blocker list and parent absorption checklist to cover
these new findings.

Key rules for parent (`AG-FE-ID-001`) absorption carried forward from this
review:

- `/bff/agora/servant/ensure` is implemented and tested. Parent `servant.ts`
  must supply `Idempotency-Key` and `X-Request-Id` headers, handle the observed
  200 response for both create and reconcile, and explicitly note that the v1.1
  OpenAPI declaration (required body, 201 new-create) does not match current
  runtime behavior.
- `/bff/agora/me` and `/bff/agora/capabilities` remain interim runtime routes
  not present in the generated operation inventory. Parent must use them as
  runtime truth only.
- `/bff/agora/ask/sessions*` is quick-ask-only (`mode == "quick_ask"`). Parent
  must not route `interactive`, `trainer`, or `research_task` session creation
  through this surface without an explicit backend ownership disposition.
- `POST /bff/agora/sessions` still defaults to `quick_ask` when `mode`/
  `sessionType` is omitted. Parent session client must wait for required
  `session_type` validation or explicitly show session creation unavailable.
- `AG-BE-ID-003` is still blocked on `session_type` contract disposition. Parent
  must keep Ask/session/command surfaces disabled or read-only until that blocker
  clears.
- `AgoraApp.tsx`, `identity.ts`, and `servant.ts` are still missing. Parent
  implementation must create them under the strict-client and no-broad-import
  rules from §8.
- Dashboard readiness must remain strictly separate from servant/session shell
  readiness.

No changes requested. The task may close.
