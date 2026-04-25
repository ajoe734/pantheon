# APP-003-CW04-FRONTEND-HANDOFF-001 BFF and Frontend Support Packet

**Sidecar kind:** `bff_handoff_packet`  
**Parent task:** `APP-003-CW04-FRONTEND-HANDOFF-001` - publish CW-04 module-local frontend handoff bundle  
**Parent owner:** `Codex`  
**Parent reviewer:** `Codex2`  
**Sidecar owner:** `Codex`  
**Sidecar reviewer:** `Codex2`  
**Date:** `2026-04-23`  
**Last refresh:** `2026-04-23T08:10Z`
**Mutates canonical:** `no`

> Support artifact only. This packet does not change canonical truth, runtime
> behavior, or the main task's acceptance criteria. It exists to reconcile the
> helper brief with current repo truth and give the reviewer one compact place
> to verify the CW-04 BFF/frontend handoff state.

## 1. Executive Summary

The helper brief was claimed while the CW-04 handoff publication work was still
described as pending. Current repo truth has already moved past that point:

- the CW-04 memo list/detail BFF routes are live
- the module-local frontend handoff bundle now exists at
  `docs/pantheon-handoffs/CW-04-redteam-memo/FRONTEND_CHANGE_SPEC.md`
- the screen spec and family packet both reflect the published handoff state, including the live list-row and degradation semantics
- the coordination bundle (`contract-ready`, `lovable-ui-task`, gap template,
  ui-done template) is present
- Pantheon's degraded-detail contract wording and example payload were tightened
  during review, so the live BFF truth now explicitly documents that degraded
  detail keeps the full memo envelope while forcing governance CTA authority off
- this refresh revalidated that the task is now queued for review by `Codex2`
  after orchestrator reassignment from `Codex3`, and that the sibling front
  repo still advertises
  `source_commit: eee2bc2765073f333895611edad80a5d053c864d` even though that
  commit is a PKT-001 replay fix, not a CW-04 memo UI transport snapshot
  containing the reviewed pages and feedback bundle; both returned CW-04
  request files still explicitly say the UI / feedback exist only in the
  current workspace and that no new git commit was created

The remaining issue in the current CW-04 loop is not a Pantheon BFF gap. The
open follow-up recorded in `.coordination/reviews/CW-04-redteam-memo-review.md`
is front publication replay truth: the reviewed UI work exists, but the
advertised `source_commit` does not yet point at one immutable Git-visible
transport commit containing the memo pages plus the request/feedback bundle.

Practical conclusion for this sidecar:

- treat the BFF and module-local frontend handoff as published and aligned
- do not reopen canonical BFF contract work from this helper lane
- use this packet to review the current truth boundary and the remaining
  replay-clean front publication risk

## 2. Current Repo Truth Snapshot

| Area | Current truth | Notes |
|---|---|---|
| Canonical BFF contract | `docs/bff/CW-04-redteam-memo.md` | Route-live contract for `GET /api/v1/consult/memos` and `GET /api/v1/consult/memos/{memo_id}`. |
| Example payload | `docs/examples/CW-04-redteam-memo.json` | Includes list, degraded detail, and unavailable semantics aligned to current BFF behavior. |
| Screen spec | `docs/screens/CW-04-redteam-memo.md` | Now synced to the published handoff and no longer treats `stale` as a surface-state enum. |
| Frontend change spec | `docs/pantheon-handoffs/CW-04-redteam-memo/FRONTEND_CHANGE_SPEC.md` | Published module-local handoff bundle for production UI activation. |
| Family readiness | `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` | Marks CW-04 as route-live with a published module-local frontend activation packet. |
| Master frontend summary | `docs/lovable/PANTHEON_FRONTEND_SA.md` | Lists Consultation CW-04 as route-live with a published handoff bundle. |
| Contract-ready packet | `.coordination/responses/CW-04-redteam-memo-contract-ready.yaml` | Published with `status: live` and frontend-ready action list. |
| Lovable dispatch packet | `.coordination/responses/CW-04-redteam-memo-lovable-ui-task.yaml` | Published with allowed endpoints, constraints, acceptance, and handoff paths. |
| Gap / completion templates | `.coordination/requests/CW-04-redteam-memo-bff-gap.example.yaml`, `.coordination/requests/CW-04-redteam-memo-ui-done.example.yaml` | Front loop templates are present and ready. |
| Front review packet | `.coordination/reviews/CW-04-redteam-memo-review.md` | Records that the remaining blocker is replay-clean front publication, not a Pantheon route or contract defect. |

The current review packet also records the Pantheon-side follow-up already
performed during review: the degraded-detail example and wording were refreshed
to match the live BFF projection, and the contract test now asserts that
degraded detail preserves mapping, metadata, and evidence refs while keeping
governance CTA authority disabled.

Revalidation at `2026-04-23T08:10Z` confirmed the same state still holds:

- `docs/screens/CW-04-redteam-memo.md`,
  `docs/pantheon-handoffs/CW-04-redteam-memo/FRONTEND_CHANGE_SPEC.md`,
  `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md`, and
  `docs/lovable/PANTHEON_FRONTEND_SA.md` still describe CW-04 as route-live
  with a published handoff bundle and backend-owned degraded/governance
  semantics
- `git -C ../front-ai-trading-system show --stat --oneline --no-patch
  eee2bc2765073f333895611edad80a5d053c864d` still resolves to
  `eee2bc2 Repoint PKT-001 coordination packets to replay-clean transport
  commit`, not a CW-04 UI publication snapshot
- `../front-ai-trading-system/.coordination/requests/CW-04-redteam-memo-ui-done.yaml`
  and `...-frontend-feedback.yaml` still pin that same SHA and still state the
  UI / feedback live only in the current workspace with no new git commit
  created for this task
- `git -C ../front-ai-trading-system status --short -- ...` still shows the
  reviewed CW-04 memo pages, request pair, and feedback bundle as untracked in
  the sibling workspace
- `python3 -m pytest services/control-plane/bff/test_cw04_redteam_memo_contract.py -q`
  still passes with `7 passed`

## 3. BFF Query Boundary the Frontend Must Keep

The frontend remains constrained to the live memo route family only.

### 3.1 Allowed reads

- `GET /api/v1/consult/memos`
- `GET /api/v1/consult/memos/{memo_id}`

### 3.2 Allowed list query params

- `status`
- `page_token`
- `page_size`

### 3.3 Backend-owned fields that must not be synthesized client-side

| Topic | Backend-owned field |
|---|---|
| Row navigation | `items[].route_href` |
| Memo-to-session mapping | `session_to_memo_mapping` |
| Evidence navigation | `evidence_refs[].link` |
| Governance CTA authority | `allowedActions.canInitiateGovernanceReview` |
| Surface health | `meta.surfaces.redteam_memo.state` |
| Freshness | `meta.staleness.status`, `meta.staleness.as_of` |
| Recommendation shape | plain `recommendations[]` string list |

### 3.4 Frontend prohibitions that still matter

- do not derive memo state from request, transcript, committee, or other raw
  Consultation surfaces
- do not build memo detail URLs from `memo_id` when `route_href` is provided
- do not infer governance CTA visibility from `status = "published"` alone
- do not construct evidence links from `id` or `artifact_ref`
- do not turn `meta.staleness.status = "stale"` into a
  `meta.surfaces.redteam_memo.state` value
- do not add per-recommendation severity, workflow state, or approval metadata
  in v1

## 4. Truthful Operator Journey

This is the current operator path implied by the published CW-04 bundle.

1. Enter `/consultation/memos`.
2. Fetch the memo list from `GET /api/v1/consult/memos`.
3. Render each row from backend-shaped summary fields and navigate only through
   `route_href`.
4. Open one memo detail and fetch `GET /api/v1/consult/memos/{memo_id}`.
5. Render summary, recommendations, evidence drawer, and
   `session_to_memo_mapping` directly from the detail payload.
6. Show the governance handoff CTA only when
   `allowedActions.canInitiateGovernanceReview` is `true`.
7. If `meta.surfaces.redteam_memo.state = "degraded"`, show last-known content
   with a degraded banner and keep the governance CTA off.
8. If `meta.surfaces.redteam_memo.state = "unavailable"`, replace content with
   the canonical unavailable state.
9. If a required field is absent or diverges from the published contract, stop
   implementation and emit `.coordination/requests/CW-04-redteam-memo-bff-gap.yaml`.

## 5. Coordination Artifact Map

These are the files a frontend or reviewer should use in order.

1. `docs/bff/CW-04-redteam-memo.md`
2. `docs/examples/CW-04-redteam-memo.json`
3. `docs/screens/CW-04-redteam-memo.md`
4. `docs/pantheon-handoffs/CW-04-redteam-memo/FRONTEND_CHANGE_SPEC.md`
5. `.coordination/responses/CW-04-redteam-memo-contract-ready.yaml`
6. `.coordination/responses/CW-04-redteam-memo-lovable-ui-task.yaml`
7. `.coordination/requests/CW-04-redteam-memo-bff-gap.example.yaml`
8. `.coordination/requests/CW-04-redteam-memo-ui-done.example.yaml`
9. `.coordination/reviews/CW-04-redteam-memo-review.md`

The first eight files define the live handoff and the front-lane stop/go
templates. The review packet is the current loop-state truth for what still
needs follow-up.

## 6. Residual Risk and Non-Goals

### 6.1 Residual risk

The remaining CW-04 risk is publication replayability in the sibling front repo.
Per `.coordination/reviews/CW-04-redteam-memo-review.md`, the reviewed UI work
is not yet represented by one truthful Git-visible transport commit referenced
by the advertised `source_commit` fields.

Revalidation at `2026-04-23T08:10Z` still shows both front request files pinned
to `eee2bc2765073f333895611edad80a5d053c864d`, while
`src/pages/consultation/RedTeamMemoList.tsx`,
`src/pages/consultation/RedTeamMemoDetail.tsx`, the CW-04 request pair, and
`docs/pantheon-feedback/CW-04-redteam-memo/` remain outside that commit as
untracked sibling-workspace files. Both request files still describe that state
as "present in the current workspace" / "no new git commit was created", so
the replay gap remains explicit in the transported metadata as well as the git
tree.

This means:

- Pantheon does not need a new BFF route, projection, or contract revision for
  CW-04 in this loop
- the next required repair belongs to the front publication request pair and
  transport commit, not to canonical Pantheon truth

### 6.2 Non-goals for this sidecar

- no edits to L1 or L2 canonical documents
- no edits to runtime/BFF implementation
- no attempt to close the front replay issue from this support lane

## 7. Reviewer Focus

For `Codex2` reviewing this helper packet:

1. Confirm the packet is support-only and does not claim any new canonical BFF
   truth.
2. Confirm the helper brief's original "missing handoff" framing is now stale
   relative to current repo truth, and that this packet corrects that drift.
3. Confirm the listed artifact map matches the actually published CW-04 bundle.
4. Confirm the remaining open item is accurately narrowed to replay-clean front
   publication, not a Pantheon-side BFF or contract gap.
5. Confirm the operator journey and frontend prohibitions still preserve the
   backend-owned memo mapping, evidence links, freshness semantics, and
   governance CTA authority.

## 8. References

- `support/sidecars/APP-003-CW04-IMPL-001/APP-003-CW04-IMPL-001-SIDECAR-BFF-HANDOFF.md`
- `docs/bff/CW-04-redteam-memo.md`
- `docs/examples/CW-04-redteam-memo.json`
- `docs/screens/CW-04-redteam-memo.md`
- `docs/pantheon-handoffs/CW-04-redteam-memo/FRONTEND_CHANGE_SPEC.md`
- `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md`
- `docs/lovable/PANTHEON_FRONTEND_SA.md`
- `.coordination/responses/CW-04-redteam-memo-contract-ready.yaml`
- `.coordination/responses/CW-04-redteam-memo-lovable-ui-task.yaml`
- `.coordination/requests/CW-04-redteam-memo-bff-gap.example.yaml`
- `.coordination/requests/CW-04-redteam-memo-ui-done.example.yaml`
- `.coordination/reviews/CW-04-redteam-memo-review.md`
