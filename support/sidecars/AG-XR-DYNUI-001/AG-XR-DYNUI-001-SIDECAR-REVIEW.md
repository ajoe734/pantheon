# AG-XR-DYNUI-001 Sidecar Review Packet

| Field | Value |
|---|---|
| Task ID | `AG-XR-DYNUI-001-SIDECAR-REVIEW` |
| Helper kind | `review_packet` |
| Parent task | `AG-XR-DYNUI-001` - Dynamic Trading Room OpenAPI and generated frontend types |
| Parent owner / reviewer | `Codex` / `Claude` |
| Sidecar owner / reviewer | `Codex2` / `Codex` |
| Date | `2026-06-29` |
| Mutates canonical truth | `false` |
| Status | Reviewer approved; owner closeout pending |

## Purpose

This packet supports review of `AG-XR-DYNUI-001` by consolidating the visible
repo, GitHub, generated-type, and compatibility-gate evidence for the dynamic
Trading Room cross-repo contract slice.

It is support-only. It does not change L1 canonical truth, OpenAPI, JSON
Schema, BFF runtime behavior, frontend generated types, compatibility manifests,
drift scripts, widget registry/governance implementation, broker authority,
RuntimeBinding, or any capital-affecting surface.

## Review Verdict

`AG-XR-DYNUI-001` is not reviewable from the evidence visible to this sidecar.

The L0 task state says the parent is in `review` and ready for review because a
"v1.5 dynamic Trading Room OpenAPI/schema bundle" and regenerated execute-plans
Agora types were added. The repo and GitHub evidence visible here do not show a
matching parent branch, parent PR, parent implementation commit, v1.5 bundle,
v1.5 OpenAPI file, regenerated v1.5 frontend snapshot, or upgraded
compatibility manifest.

This is a parent-review blocker, not an approval. The parent owner should either
publish the missing parent implementation branch/PR and evidence, or reopen the
parent task with a concrete blocker.

## Sources Used

| Source | Relevant finding |
|---|---|
| `.orchestrator/task-briefs/ag_xr_dynui_001_sidecar_review.md` | Sidecar scope is review packet/evidence summary only; no canonical/runtime changes. |
| `AI_COLLABORATION_GUIDE.md` | Support artifacts cannot override canonical architecture or product truth. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DYNUI-001-SIDECAR-REVIEW` | Sidecar is active `review_approved`, owner `Codex2`, reviewer `Codex`, artifact path is this packet, with Codex approval notes recorded. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DYNUI-001` | Parent is active `review`, owner `Codex`, reviewer `Claude`, and claims v1.5 dynamic Trading Room OpenAPI/schema bundle plus regenerated types are ready. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DYNUI-001-SIDECAR-ACCEPTANCE` | Acceptance sidecar is archived `done`; PR `#2584` merged at `c7bd20fee399c34b5cf56ca1b147533a8cfbe3af`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-DYNUI-001` | Upstream workspace proposal/workspace backend task is archived `done`; closeout merge `eac485c90360a93545b5bf023e9324ca50c1b342`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-DYNUI-002` | Upstream widget revision/version/rollback backend task is archived `done`; closeout merge `b3c8e654be5502be7c97e69d69f8aabee3a2ab53`. |
| `support/sidecars/AG-XR-DYNUI-001/AG-XR-DYNUI-001-SIDECAR-ACCEPTANCE.md` | Parent acceptance expects additive dynamic bundle/OpenAPI, generated FE types, compatibility manifest upgrade, and fail-closed drift checks. |
| `gh pr list --repo ajoe734/pantheon --state all --search "AG-XR-DYNUI-001"` | Only the sidecar acceptance PR was found; no parent implementation PR was found. |
| `gh pr list --repo ajoe734/pantheon --state open --json ...` | No open PR for `AG-XR-DYNUI-001` or a dynamic Trading Room XR contract implementation was visible. |
| `git show-ref \| rg 'AG-XR-DYNUI-001'` | Local parent branch `task/AG-XR-DYNUI-001` points to `b3c8e654...`, the `AG-BE-DYNUI-002` merge commit; there is no remote parent branch. |
| `services/control-plane/specs/agora/*` and `services/control-plane/openapi/*` | Current repo has bundle indexes through v1.4 and OpenAPI files through v1.4 only. No v1.5 bundle/OpenAPI file exists. |
| `execute-plans/src/lib/bff-v1/agora/contract-snapshot.json` | Generated frontend snapshot still reports `contract_version: "1.1"` and `source_bundle: "services/control-plane/specs/agora/bundle_index.v1_1.json"`. |
| `scripts/agora_compat_manifest.py` and `docs/contracts/agora/dev-compatibility-manifest.json` | Compatibility manifest logic and dev manifest remain v1.1-oriented. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Blocking Findings

| # | Finding | Evidence | Review impact |
|---|---|---|---|
| 1 | No reviewable parent PR or remote branch is visible. | `gh pr list --search "AG-XR-DYNUI-001"` returned only sidecar PR `#2584`; `git show-ref` has no `refs/remotes/origin/task/AG-XR-DYNUI-001`. | Claude cannot review a parent implementation from GitHub evidence. |
| 2 | The local parent branch contains no XR implementation delta. | `task/AG-XR-DYNUI-001` resolves to `b3c8e654be5502be7c97e69d69f8aabee3a2ab53`, the `AG-BE-DYNUI-002` merge. `git diff --name-status origin/dev...task/AG-XR-DYNUI-001` is empty. | The parent branch does not carry the claimed OpenAPI/type-generation work. |
| 3 | No dynamic v1.5 bundle/OpenAPI exists in the visible repo. | `find` shows bundle indexes through `bundle_index.v1_4.json` and OpenAPI files through `agora_v1_4.openapi.yaml`; no `bundle_index.v1_5.json` or `agora_v1_5.openapi.yaml` exists. | Acceptance criteria for explicit additive dynamic bundle and route-family publication are unmet. |
| 4 | Dynamic Trading Room schemas are not exposed through OpenAPI/generated types. | Scoped `rg` found `TradingRoomWorkspaceProposal`, `TradingRoomWidgetSpec`, and `WidgetRevisionProposal` in `trading_room_workspace.schema.json` only. Generated `types.ts` only exposes dashboard-recipe rollback for the `rollback` token. | Frontend tasks still lack generated V11 Trading Room route/type truth. |
| 5 | Type generation and compatibility gates still target the old contract surface. | `generate-agora-types --check --bundle-index bundle_index.v1_1.json` passes with 17 schemas / 96 operations. `contract-snapshot.json` and `agora_compat_manifest.py` remain v1.1-oriented. | Drift closure for dynamic Trading Room cannot be claimed. |
| 6 | The latest visible extension chain is not healthy for v1.4 generation either. | `generate-agora-types --check --bundle-index bundle_index.v1_4.json` fails with a v1.3 base bundle digest mismatch. | Even before adding dynamic v1.5, the extension-chain check needs repair or an explicit bundling strategy. |

## Evidence Matrix

| Acceptance area from sidecar packet | Visible state | Sidecar assessment |
|---|---|---|
| Additive dynamic bundle version | No v1.5 bundle or OpenAPI file visible. | Not satisfied. |
| Dynamic schema bundled | `trading_room_workspace.schema.json` exists but is not in the latest visible bundle chain. | Not satisfied. |
| OpenAPI route family complete | No `trading-room/proposals`, `trading-room/workspaces`, `revision-proposals`, or workspace rollback routes found in OpenAPI files. | Not satisfied. |
| Generated primary schemas | Generated `types.ts` does not expose V11 Trading Room workspace/revision types. | Not satisfied. |
| Generated route map | Generated operation map remains 96 operations from v1.1 check; dynamic Trading Room operations absent. | Not satisfied. |
| Frontend snapshot points at dynamic bundle | Snapshot points at `bundle_index.v1_1.json`. | Not satisfied. |
| Compatibility manifest upgraded | Manifest and script still use `agora.v1.1`, `bundle_index.v1_1.json`, and `agora_v1_1.openapi.yaml`. | Not satisfied. |
| Backend dependencies current | `AG-BE-DYNUI-001` and `AG-BE-DYNUI-002` are archived `done`. Focused backend test now passes 40 tests. | Satisfied for upstream BE precondition only. |
| No order/capital/runtime authority leak | No new dynamic XR surface is visible. Existing hits are safety-prohibition text and `prohibited_authority` enums. | No new XR leak observed, but this does not compensate for missing contract publication. |

## Verification Performed

Commands run from this task worktree:

```bash
git status -sb
git branch --show-current
git remote -v
./scripts/git/task_start.sh "AG-XR-DYNUI-001-SIDECAR-REVIEW"
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DYNUI-001-SIDECAR-REVIEW
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DYNUI-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-XR-DYNUI-001-SIDECAR-ACCEPTANCE
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-DYNUI-001
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-DYNUI-002
gh pr list --repo ajoe734/pantheon --state all --search "AG-XR-DYNUI-001" --json number,state,mergedAt,mergeCommit,url,title,headRefName,baseRefName,statusCheckRollup,updatedAt --limit 30
gh pr list --repo ajoe734/pantheon --state open --json number,state,url,title,headRefName,baseRefName,statusCheckRollup,updatedAt --limit 50
git show-ref | rg 'AG-XR-DYNUI-001'
git diff --name-status origin/dev...task/AG-XR-DYNUI-001
find services/control-plane/specs/agora -maxdepth 1 -type f -name 'bundle_index*.json' -o -name '*trading_room*schema*.json'
find services/control-plane/openapi -maxdepth 1 -type f
rg -n "TradingRoomWorkspaceProposal|TradingRoomWidgetSpec|WidgetRevisionProposal|trading-room/proposals|trading-room/workspaces|revision-proposals|rollback" services/control-plane/openapi services/control-plane/specs/agora execute-plans/src/lib/bff-v1/agora scripts/agora_compat_manifest.py scripts/test_agora_compat_manifest.py
node execute-plans/scripts/generate-agora-types.mjs --check --pantheon-root . --bundle-index services/control-plane/specs/agora/bundle_index.v1_1.json
node execute-plans/scripts/generate-agora-types.mjs --check --pantheon-root . --bundle-index services/control-plane/specs/agora/bundle_index.v1_4.json
python3 -m pytest scripts/test_agora_compat_manifest.py -q
python3 scripts/agora_compat_manifest.py verify --allow-pending --manifest docs/contracts/agora/dev-compatibility-manifest.json
python3 -m pytest services/control-plane/bff/agora/trading_room/test_trading_room.py -q
rg -n "RuntimeBinding|place_order|enable_live|capital_binding|broker_order|dangerouslySetInnerHTML|eval\(|new Function|<script" services/control-plane/openapi services/control-plane/specs/agora execute-plans/src/lib/bff-v1/agora
git diff --check -- .orchestrator/task-briefs/ag_xr_dynui_001_sidecar_review.md support/sidecars/AG-XR-DYNUI-001/AG-XR-DYNUI-001-SIDECAR-REVIEW.md
```

Observed results:

- Current sidecar branch was reset to `origin/dev` tip `ea9053bb` before
  editing via `./scripts/git/task_start.sh`.
- Parent L0 state is `review`, but no visible parent PR or remote branch backs
  the review claim.
- Local `task/AG-XR-DYNUI-001` is `b3c8e654...`, with no diff from its merge
  base and no current XR implementation delta.
- Only bundle indexes v1.0, v1.1, v1.2, v1.3, and v1.4 exist; only OpenAPI
  v1, v1.1, v1.2, v1.3, and v1.4 exist.
- The dynamic Trading Room schema exists in backend specs, but current OpenAPI
  and generated frontend type output do not expose the dynamic route/type
  family required by parent acceptance.
- `node ... bundle_index.v1_1.json --check` passed: `Agora generated types are
  current: 17 schemas, 96 operations.`
- `node ... bundle_index.v1_4.json --check` failed:
  `Extended Agora base bundle digest mismatch` for
  `services/control-plane/specs/agora/bundle_index.v1_3.json`.
- `python3 -m pytest scripts/test_agora_compat_manifest.py -q` passed:
  `4 passed in 1.56s`.
- `python3 scripts/agora_compat_manifest.py verify --allow-pending ...`
  passed for the existing v1.1 pending manifest.
- `python3 -m pytest services/control-plane/bff/agora/trading_room/test_trading_room.py -q`
  passed: `40 passed in 13.42s`.
- Safety grep returned existing prohibition text and `prohibited_authority`
  enum hits; no new XR contract output was visible to assess.
- `git diff --check` for this sidecar's intended files passed.

## Reviewer Approval And Closeout Handoff

`Codex` approved this support-only packet after reviewing PR `#2589`.

Approval notes recorded in L0 status:

- PR `#2589` only contains the task brief and this support packet; it does not
  change canonical truth, OpenAPI/schema, runtime, frontend generated types, or
  compatibility manifests.
- Latest refs still do not show a parent `AG-XR-DYNUI-001` PR, remote branch,
  implementation delta, or v1.5 dynamic OpenAPI/generated-type/compat-manifest
  evidence.
- Focused verification confirmed the current v1.1 generated-type check passes,
  the v1.4 extension-chain check fails on the known v1.3 base digest mismatch,
  compatibility manifest checks pass, and Trading Room backend focused tests
  pass.

Owner closeout is limited to making the approved sidecar artifact durable and
then moving `AG-XR-DYNUI-001-SIDECAR-REVIEW` from `review_approved` to `done`.
Parent `AG-XR-DYNUI-001` remains owned by `Codex` with reviewer `Claude`; this
sidecar does not replace the parent review decision.

## Support-Only Boundary Confirmation

- No L1/L2 canonical policy or architecture document was edited.
- No schema, OpenAPI, BFF route, persistence layer, widget registry,
  governance logic, frontend runtime, generated type file, compatibility
  manifest, or drift script was changed.
- This packet does not approve or implement `AG-XR-DYNUI-001`.
- The only intended deliverables are this review packet and the generated
  task-scoped brief used to route the sidecar.

Prepared by `Codex2` for the `AG-XR-DYNUI-001-SIDECAR-REVIEW` support slice.
