# AG-FE-DYNUI-002 Sidecar Acceptance Follow-up 2

| Field | Value |
|---|---|
| Sidecar task | `AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` |
| Helper parent | `AG-FE-DYNUI-002` |
| Helper kind | `acceptance_packet` |
| Parent title | V11 Trading Room proposal preview and workspace shell |
| Parent owner / reviewer | `Codex2` / `Claude2` as of status readback |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-29` |
| Mutates canonical truth | `false` |
| Status | Ready for `Codex2` support review |

This is a support-only follow-up packet. It does not replace the already
archived `AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE` packet, and it does not approve
or implement the parent frontend runtime. Its purpose is to give the parent
owner and reviewer a current dependency/evidence gate after the original
acceptance sidecar closed.

## 1. Relationship To Existing Packets

| Packet | Current state | How this follow-up uses it |
|---|---|---|
| `AG-FE-DYNUI-002-SIDECAR-BFF-HANDOFF` | Merged support artifact, PR `#2570`. | Provides the earlier BFF/client/UI gap map around V11 proposal generation, proposal read, accept, and workspace read. |
| `AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE` | Archived `done`; packet PR `#2595` and closeout PR `#2596` merged. | Remains the main parent acceptance checklist. This follow-up only adds current readiness/evidence gates and reviewer routing. |
| `AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` | Active support sidecar. | Confirms that dependencies are now done, but parent implementation evidence is not yet visible from GitHub PR/branch checks. |

Do not treat this packet as canonical contract truth. If it appears to conflict
with L1/L2 docs, the canonical docs win and this support packet should be
corrected or reopened.

## 2. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates task work; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_dynui_002_sidecar_acceptance_followup_2.md` | Scope is acceptance checklist, dependency map, and support packet only; do not edit canonical truth. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support/docs changes should be made durable through narrow commits. |
| `.orchestrator/skills/task-closeout-finalization.md` | Closeout/done is reserved for owner finalization after review approval and merged PR. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` | Active `in_progress`, owner `Codex`, reviewer `Codex2`, artifact path is this file, helper parent is `AG-FE-DYNUI-002`, and `mutates_canonical` is `false`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-002` | Parent is active `in_progress`, owner `Codex2`, reviewer `Claude2`, and depends on XR, generator, V10 readiness, and FE Trading Room baseline. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE` | Prior acceptance sidecar is archived `done` with reviewer approval and merged closeout record. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-DYNUI-001`, `AG-BE-DYNUI-003`, `AG-FE-DYNUI-001`, `AG-FE-TR-001` | All parent dependencies are archived `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-003`, `AG-FE-DYNUI-004`, `AG-FE-DYNUI-005`, `AG-E2E-DYNUI-001` | Downstream edit/revision/visual/E2E scopes remain active future work and should not be absorbed into `AG-FE-DYNUI-002`. |
| `docs/04/agora_design_pack_dynui_2026-06-28/README.md` | Task graph routes V11 proposal preview and generated workspace shell to `AG-FE-DYNUI-002`. |
| `docs/04/agora_design_pack_dynui_2026-06-28/source-map-and-gap-map.md` | V11 requires generation progress, full workspace proposal preview, all view thumbnails/counts, accept-to-workspace shell, and no static dashboard substitution. |
| `support/sidecars/AG-FE-DYNUI-002/AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE.md` | Main acceptance checklist already covers V11 generated types, BFF proposal/workspace routes, no fixtures, no `DashboardRecipeV2` substitution, and downstream boundaries. |
| `support/sidecars/AG-FE-DYNUI-002/AG-FE-DYNUI-002-SIDECAR-BFF-HANDOFF.md` | Earlier handoff documented the frontend client/UI gap before XR and backend dependencies finished. |
| `gh pr list --repo ajoe734/execute-plans --state all --search "AG-FE-DYNUI-002"` | No visible execute-plans PR for the parent implementation at the time of this follow-up. |
| `git ls-remote --heads https://github.com/ajoe734/execute-plans.git 'task/AG-FE-DYNUI-002*'` | No visible execute-plans remote parent branch at the time of this follow-up. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## 3. Current Readiness Snapshot

| Surface | Current state | Consequence for parent review |
|---|---|---|
| Parent task | `AG-FE-DYNUI-002` is active `in_progress`. | Parent review is not ready until a parent implementation branch/PR and evidence are visible. |
| Parent implementation PR/branch | No execute-plans PR or remote branch matching `AG-FE-DYNUI-002` was visible from the checks above. | Reviewer should not infer runtime readiness from support packets alone. |
| Prior acceptance sidecar | `AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE` is archived `done`. | Use that packet as the main checklist; do not ask this follow-up to restate every criterion. |
| XR/generated types | `AG-XR-DYNUI-001` archived `done`; Pantheon PR `#2593` and execute-plans PR `#80` are recorded in status as merged. | Parent FE should build on the v1.5 generated contract surface, not local durable type guesses. |
| Servant generator | `AG-BE-DYNUI-003` archived `done`; status records validator-backed generated proposal evidence. | Parent can expect generated proposal payloads, but must still show BFF-derived state in its own implementation evidence. |
| V10 readiness handoff | `AG-FE-DYNUI-001` archived `done`; status records V10 Strategy Workshop runtime closeout. | Parent can wire join-to-generation from readiness without reopening V10 card/rail scope. |
| Trading Room baseline | `AG-FE-TR-001` archived `done`; existing baseline is `DashboardRecipeV2`/aggregate oriented. | Parent must keep V11 `TradingRoomWorkspaceProposal` separate from legacy recipe fallback. |
| Downstream edit/revision/visual/E2E | `AG-FE-DYNUI-003`, `AG-FE-DYNUI-004`, `AG-FE-DYNUI-005`, and `AG-E2E-DYNUI-001` remain future work. | Parent should stop at proposal preview and initial non-empty workspace shell. |

## 4. Parent Evidence Gate

`AG-FE-DYNUI-002` is reviewable only when the parent owner can point to a
frontend implementation PR/branch with evidence for all of these items:

1. A ready Strategy Workshop handoff or Trading Room strategy action calls the
   V11 proposal generation route through a typed BFF client helper.
2. Proposal generation/read state comes from BFF response/readback; no timer
   only progress, local generated fixture, or static dashboard appears in live
   strict mode.
3. A `TradingRoomWorkspaceProposal` preview renders all generated views before
   accept, including the seven V11 Winner Branch views when present.
4. Each preview view shows title, purpose, order, widget count, data
   availability/completeness, warnings, and personalization applied from
   proposal data.
5. Accept calls the v1.5 accept route and lands in or loads a non-empty
   `TradingRoomWorkspace` shell with `activeViewId`, views, and widget
   placeholders/renderers.
6. The V11 path uses generated v1.5 Trading Room proposal/workspace types or a
   documented temporary adapter tied to the v1.5 checksum. It does not invent
   durable app-only fields.
7. `DashboardRecipeV2`, `dashboard_recipe_id`, `getDashboardRecipeById`, and
   older recipe preview/grid components are not used as the V11 proposal or
   workspace source of truth unless an explicit compatibility adapter proves
   type compatibility.
8. Typed error handling clears or preserves state as appropriate for `403`,
   `404`, `409`, `412`, `422`, and `501`/capability-not-ready.
9. Tests cover generation, proposal preview completeness, seven-view Winner
   Branch ordering, accept-to-non-empty shell, strict no-fixture fallback, no
   recipe substitution, and no forbidden operator terms/actions.
10. Screenshot or Playwright evidence shows the proposal preview and accepted
    workspace shell against a real BFF contract payload or approved contract
    fixture, not hand-authored static cards.

If any item requires adding backend fields/routes, generated types, widget
revision UI, grid mutation persistence, visual parity, or E2E scope, parent
owner should open a blocker or handoff instead of widening `AG-FE-DYNUI-002`.

## 5. Dependency Map

```mermaid
graph TD
    SRC["AG-DYNUI-SRC-001<br/>done<br/>source/gap/invariant map"] --> FE001
    FE001["AG-FE-DYNUI-001<br/>done<br/>V10 readiness handoff"] --> FE002
    FETR["AG-FE-TR-001<br/>done<br/>Trading Room baseline"] --> FE002

    BE001["AG-BE-DYNUI-001<br/>done<br/>workspace proposal/routes"] --> XR001
    BE002["AG-BE-DYNUI-002<br/>done<br/>revision/version contracts"] --> XR001
    XR001["AG-XR-DYNUI-001<br/>done<br/>v1.5 OpenAPI + generated FE types"] --> FE002
    BE003["AG-BE-DYNUI-003<br/>done<br/>servant generator + validator"] --> FE002

    FE002["AG-FE-DYNUI-002<br/>in_progress<br/>proposal preview + workspace shell"]
    FE002 --> FE003["AG-FE-DYNUI-003<br/>todo<br/>grid editor + personalization"]
    FE003 --> FE004["AG-FE-DYNUI-004<br/>todo<br/>widget revision drawer"]
    FE004 --> FE005["AG-FE-DYNUI-005<br/>todo<br/>visual parity"]
    FE005 --> E2E["AG-E2E-DYNUI-001<br/>todo<br/>full dynamic UI E2E"]
```

## 6. Reviewer Handoff

Reviewer: `Codex2`

Please verify this follow-up on support-packet terms only:

1. It stays support-only and does not edit or redefine canonical truth,
   schemas, OpenAPI, BFF runtime, frontend runtime, registry, governance, or
   broker authority.
2. It correctly points to the already archived main acceptance sidecar instead
   of duplicating or superseding it.
3. It records that upstream dependencies are done while parent implementation
   evidence is still not visible from the checked execute-plans PR/branch
   surfaces.
4. It keeps `AG-FE-DYNUI-002` separated from downstream grid editor, widget
   revision drawer, visual parity, and E2E tasks.
5. The parent evidence gate is concrete enough for `Codex2`/`Claude2` to use
   when the parent implementation becomes reviewable.

Suggested reviewer approval command:

```bash
AI_NAME=Codex2 REVIEW_FILE=support/sidecars/AG-FE-DYNUI-002/AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md \
  REVIEW_NOTES_ZH="審核通過：AG-FE-DYNUI-002 follow-up acceptance packet 保持 support-only，承接已歸檔的主 acceptance packet，更新依賴狀態為 XR/generator/V10 readiness/Trading Room baseline 已 done，同時明確記錄 parent implementation PR/branch evidence 尚未 visible；parent review gate 聚焦 v1.5 generated types、BFF-derived proposal generation/read/accept/workspace shell、no fixture/no DashboardRecipeV2 substitution、typed error states、screenshot/Playwright evidence，且不擴張到 AG-FE-DYNUI-003/004/005/E2E。" \
  ./scripts/ai-status.sh approve AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2 \
  "Follow-up acceptance packet approved; support artifact updates AG-FE-DYNUI-002 dependency/evidence gate without changing canonical truth or runtime."
```

Suggested reopen command:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh reopen AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2 \
  "Describe the factual correction, missing evidence gate, or scope-boundary issue needed before approval."
```

## 7. Validation Run

Commands run from this sidecar worktree:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-DYNUI-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-DYNUI-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-003
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-004
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-DYNUI-005
AI_NAME=Codex ./scripts/ai-status.sh show AG-E2E-DYNUI-001
gh pr list --repo ajoe734/execute-plans --state all --search "AG-FE-DYNUI-002" --json number,state,mergedAt,mergeCommit,url,title,headRefName,baseRefName,statusCheckRollup,updatedAt --limit 20
git ls-remote --heads https://github.com/ajoe734/execute-plans.git 'task/AG-FE-DYNUI-002*'
git diff --check -- .orchestrator/task-briefs/ag_fe_dynui_002_sidecar_acceptance_followup_2.md support/sidecars/AG-FE-DYNUI-002/AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md
```

Observed results:

- Branch is `task/AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2`.
- Sidecar is active `in_progress`, owner `Codex`, reviewer `Codex2`.
- Parent `AG-FE-DYNUI-002` is active `in_progress`, owner `Codex2`,
  reviewer `Claude2`.
- Prior acceptance sidecar is archived `done`.
- Upstream dependencies `AG-XR-DYNUI-001`, `AG-BE-DYNUI-003`,
  `AG-FE-DYNUI-001`, and `AG-FE-TR-001` are archived `done`.
- Downstream `AG-FE-DYNUI-003`, `AG-FE-DYNUI-004`, `AG-FE-DYNUI-005`, and
  `AG-E2E-DYNUI-001` remain future work.
- No execute-plans `AG-FE-DYNUI-002` PR or matching remote branch was visible
  from the GitHub/remote checks.
- No parent runtime tests were run by this sidecar because it changes support
  artifacts only.

Prepared by `Codex` for the
`AG-FE-DYNUI-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` support slice.
