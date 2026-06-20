# AG-FE-000 Sidecar Review Packet

**Sidecar kind:** `review_packet`
**Sidecar task:** `AG-FE-000-SIDECAR-REVIEW`
**Helper parent:** `AG-FE-000` - Separate Agora/Management entry, build, auth audience
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Prepared by:** `Codex2`
**Sidecar reviewer:** `Claude`
**Date:** `2026-06-20`
**Sidecar status at packet time:** `in_progress`
**Parent status at packet time:** `review_approved`

> Scope constraint: support artifact only. This packet does not modify L1
> canonical truth, frontend implementation, BFF contracts, runtime registry,
> routing, or governance behavior. It organizes review evidence and handoff
> questions for the parent owner/reviewer.

## 1. Purpose

This packet gives `Claude` a compact handoff for the `AG-FE-000` review/support
surface. It records:

1. the parent acceptance surface and current lifecycle state
2. the PR/CI evidence available from GitHub
3. the implementation claims recorded in commit metadata
4. a scope discrepancy that should be reconciled before parent closeout is
   treated as fully narrated

This sidecar does not approve or reopen `AG-FE-000`; it is supporting evidence
for the parent owner and assigned reviewer.

## 2. Parent Task Summary

| Field | Value |
|---|---|
| Parent task | `AG-FE-000` |
| Title | Separate Agora/Management entry, build, auth audience |
| Owner / reviewer | `Claude` / `Codex` |
| Status from `ai-status.sh show` | `review_approved` |
| Parent PR | `https://github.com/ajoe734/pantheon/pull/1771` |
| PR state | `MERGED` |
| PR base / head | `dev` / `task/AG-FE-000` |
| Merge commit | `39e0e9f80234329dee8fae097aaa2ccbdf761547` |
| Merged at | `2026-06-20T09:52:00Z` |

Parent acceptance summary:

- `build:agora` and `build:management` each produce bundles.
- Agora bundle must not contain Management route/code strings.
- Agora and Management auth audiences are separated.
- Existing Management behavior remains intact.
- Implementation must remain aligned with referenced specs/schemas and must not
  invent fields, routes, enums, scoring, widgets, or order/funding authority.

## 3. Evidence Map

### 3.1 Lifecycle and PR evidence

| Evidence | Result | Source |
|---|---|---|
| Sidecar task is active and support-only | `AG-FE-000-SIDECAR-REVIEW`, owner `Codex2`, reviewer `Claude`, artifact path under `support/sidecars` | `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-000-SIDECAR-REVIEW` |
| Parent is ready for finalization | Parent status `review_approved`; review note says builds passed and Agora bundle had no Management route/code strings | `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-000` |
| Parent PR merged | PR #1771 state `MERGED`; merge commit `39e0e9f80234329dee8fae097aaa2ccbdf761547` | `gh pr view 1771 --repo ajoe734/pantheon` |
| Required visible checks passed | Commit trailers, Runtime mirror guard, Smoke acceptance, Forward to orchestrator all `SUCCESS` | `gh pr checks 1771 --repo ajoe734/pantheon` |

### 3.2 Verification claims recorded in parent commits

These are parent-commit claims captured from PR #1771 metadata. This sidecar did
not rerun the frontend build/test commands.

| Parent commit | Recorded verification claim |
|---|---|
| `aeb0ab163ee7980858fe7ae1b05076c3ec135ee3` | `npm run build:agora` passed, `npm run build:management` passed, dist/agora had 0 `management` refs, focused `managementAssistant` vitest passed, `npx tsc --noEmit` exited 0 |
| `0e8f22f9aaa6103e89d58b6be6f555659838e345` | `npm run build:agora` passed with 0 Management matches, `npm run build:management` passed, focused vitest 5/5 passed, `npx tsc --noEmit` exited 0 |
| `8792e85535566febe9c4eb02d2953f6067bb3220` | Parent brief updated to say PR scope issue was fixed and handed back to `Codex` |

### 3.3 Merged PR file list

GitHub currently reports PR #1771 as 18 files, `1138` additions, and `205`
deletions.

| File cluster | Paths |
|---|---|
| Parent brief | `.orchestrator/task-briefs/ag_fe_000.md` |
| Execute-plans frontend/BFF files | `execute-plans/src/agora/pages/AskPersonas.tsx`; `execute-plans/src/lib/bff/agora.ts`; `execute-plans/src/lib/bff/assistantCatalog.ts`; `execute-plans/src/lib/bff/managementAssistant.ts` |
| Execute-plans generated Agora contract files | `execute-plans/src/lib/bff-v1/agora/contract-snapshot.json`; `execute-plans/src/lib/bff-v1/agora/types.ts` |
| Control-plane Agora scope files | `services/control-plane/bff/agora/identity/__init__.py`; `services/control-plane/bff/agora/identity/scope.py`; `services/control-plane/bff/agora/models.py`; `services/control-plane/bff/agora/router.py`; `services/control-plane/bff/tests/test_agora_identity_scope.py`; `services/control-plane/bff/tests/test_agora_router.py`; `services/control-plane/specs/agora/agora_user_scope.schema.json`; `services/control-plane/specs/agora/bundle_index.json`; `services/control-plane/specs/agora/servant_profile.schema.json` |
| Other non-frontend files | `services/persona/agent_usability_validation.py`; `tests/e2e/test_agent_trading_reflection_evolution_3000.py` |

## 4. Reviewer Attention Item

**Scope narrative mismatch.** The parent task brief says:

> PR #1771 scope fixed (2026-06-20): corrupted merge commit f13e66da excluded
> files dev added; b1741bbb + 4202c48a restore them. PR now shows 5 files /
> 207 insertions / 134 deletions within AG-FE-000 scope only.

GitHub PR metadata observed during this sidecar instead reports 18 files /
1138 additions / 205 deletions, including control-plane Agora spec/BFF files,
persona validation, and an e2e test.

This packet does not conclude that the merged PR is invalid. It does flag that
the parent owner/reviewer should reconcile one of these before final narrative
closeout:

- the parent brief's "5 files" statement is stale or inaccurate; or
- GitHub's merged PR file list includes expected `dev`/generated scope that
  should be explicitly explained; or
- follow-up review is needed for the extra non-frontend file clusters.

## 5. Support-Only Boundary Confirmation

- No L1 canonical policy or architecture document was edited by this sidecar.
- No runtime, registry, BFF router, schema, generated contract, or frontend
  implementation file was changed by this sidecar.
- No parent task lifecycle state was changed by this sidecar packet.
- The intended sidecar artifact is this file:
  `support/sidecars/AG-FE-000/AG-FE-000-SIDECAR-REVIEW.md`.

## 6. Handoff Recommendation

Reviewer: `Claude`

Recommended review checks:

1. Confirm this packet accurately captures PR #1771's merged state, merge
   commit, and visible green checks.
2. Confirm whether the PR file-list mismatch in section 4 is acceptable as
   documented, or whether it needs a parent follow-up note.
3. Confirm this packet stays support-only and does not attempt to modify the
   parent implementation or canonical contract truth.
4. If accepted, approve `AG-FE-000-SIDECAR-REVIEW` and hand any parent-scope
   reconciliation back to `AG-FE-000` owner/reviewer flow.

Suggested reviewer command after approval:

```bash
AI_NAME=Claude python3 scripts/ai_status.py approve AG-FE-000-SIDECAR-REVIEW "Review packet approved; PR #1771 merge/check evidence captured and scope-narrative mismatch documented for parent closeout reconciliation."
```

Suggested reviewer command if changes are required:

```bash
AI_NAME=Claude python3 scripts/ai_status.py reopen AG-FE-000-SIDECAR-REVIEW "Describe the specific correction needed in the sidecar packet."
```

*Prepared by Codex2 for the `AG-FE-000-SIDECAR-REVIEW` support slice.*
