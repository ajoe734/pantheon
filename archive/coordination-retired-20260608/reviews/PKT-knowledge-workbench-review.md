# PKT-knowledge-workbench Review Packet

## Date

2026-04-18

## Reviewer

Codex

## Findings

### 1. High: the returned request pair is not replayable from the advertised front commit

- The dispatched `ui-done` payload still points `source_commit` at
  `37a622bca69a95e2aae46aa8c6b0432ad72082a8` in
  `../front-ai-trading-system/.coordination/requests/PKT-knowledge-workbench-ui-done.yaml:1-5`.
- The paired `frontend-feedback` payload points to that same commit in
  `../front-ai-trading-system/.coordination/requests/PKT-knowledge-workbench-frontend-feedback.yaml:1-6`.
- Commit `37a622bca69a95e2aae46aa8c6b0432ad72082a8` does contain
  `src/pages/workbench/KnowledgeWorkbench.tsx`, but it does not contain:
  - `.coordination/requests/PKT-knowledge-workbench-ui-done.yaml`
  - `.coordination/requests/PKT-knowledge-workbench-frontend-feedback.yaml`
  - `docs/pantheon-feedback/PKT-knowledge-workbench/LOVABLE_CHANGE_FEEDBACK.md`
- Impact: Pantheon can review the sibling checkout, but the returned handoff is
  not transport-replayable from the commit tuple published in the request pair,
  so the loop cannot close through GitHub-visible coordination artifacts yet.

### 2. Medium: the shared navigation still says Knowledge is "Soon" even though `/knowledge` is live

- The sibling app now registers `/knowledge` as a live route in
  `../front-ai-trading-system/src/App.tsx:133-135`.
- The screen itself is implemented as the published overview-only landing page
  and renders backend-owned overview data from the single BFF route in
  `../front-ai-trading-system/src/pages/workbench/KnowledgeWorkbench.tsx:312-350`
  and `../front-ai-trading-system/src/pages/workbench/KnowledgeWorkbench.tsx:393-542`.
- The shared sidebar still marks the same Knowledge entry with
  `comingSoon: true` in
  `../front-ai-trading-system/src/components/AppSidebar.tsx:61-63`.
- The canonical packet says this surface is a truthful landing page, not a
  blocked placeholder:
  `docs/bff/PKT-knowledge-workbench.md:5-10`,
  `docs/screens/PKT-knowledge-workbench.md:3-19`.
- Impact: the overview route itself is truthful, but the shared navigation now
  advertises stale availability and undermines the packet's "overview is live"
  message.

## Reviewed Artifacts

- Canonical contract and packet docs:
  - `docs/bff/PKT-knowledge-workbench.md`
  - `docs/examples/PKT-knowledge-workbench.json`
  - `docs/screens/PKT-knowledge-workbench.md`
  - `docs/pantheon-handoffs/PKT-knowledge-workbench/FRONTEND_CHANGE_SPEC.md`
  - `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md`
- Pantheon coordination state:
  - `.coordination/responses/PKT-knowledge-workbench-contract-ready.yaml`
  - `.coordination/responses/PKT-knowledge-workbench-lovable-ui-task.yaml`
- Returned front-owned request pair:
  - `../front-ai-trading-system/.coordination/requests/PKT-knowledge-workbench-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-knowledge-workbench-frontend-feedback.yaml`
- Front feedback bundle:
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-knowledge-workbench/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-knowledge-workbench/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-knowledge-workbench/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-knowledge-workbench/QA_STATUS.md`
- Front implementation:
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/components/AppSidebar.tsx`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/pages/workbench/KnowledgeWorkbench.tsx`
  - `../front-ai-trading-system/src/pages/workbench/types.ts`
- Pantheon BFF implementation and tests:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py`

## Verified Positives

- The screen is wired through the shared `workbenchApi.getKnowledgeOverview()`
  helper; no component-level raw `fetch` path was introduced.
- The overview validates the required top-level fields plus `packet_family`,
  `module_counts`, every `modules[]` entry, every `support_refs[]` entry,
  `next_steps[]`, and `meta.surfaces` before rendering.
- The page preserves backend-owned `wave_order` sequencing and renders the
  packet-family note, missing contracts, support refs, and next steps verbatim.
- The missing-field branch points operators at
  `.coordination/requests/PKT-knowledge-workbench-bff-gap.yaml` instead of
  synthesizing registry, evidence, or strategy-spec browse state locally.
- Sibling front verification passed on the current working tree:
  - `npm run build`
  - `npx eslint src/lib/bffClient.ts src/pages/workbench/KnowledgeWorkbench.tsx src/pages/workbench/types.ts`
- Targeted Pantheon verification passed:
  - `python3 -m pytest services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py -q`
  - Result: `1 passed`
- Direct local BFF probing with FastAPI `TestClient` returned `200 OK` for
  `GET /api/v1/workbench/knowledge` in the current workspace and surfaced the
  expected overview payload:
  - `overall_status = overview_ready`
  - module order = `KW-01`, `KW-02`, `KW-03`, `KW-04`, `KW-05`
  - `packet_family.note` keeps all five modules blocked

## Decision

`PKT-knowledge-workbench` is **follow-up required**.

The Knowledge Workbench overview itself is statically aligned on the published
single-route packet and Pantheon's current BFF workspace serves the expected
read model. No new Pantheon endpoint or shadow-state work is required for the
current packet scope.

The remaining blockers are front-owned:

- the request pair is not replayable from the advertised `source_commit`
- the shared navigation still labels the live overview route as coming soon

## Required Follow-up

1. Front repo: republish the canonical `frontend-feedback` and `ui-done` pair
   from one truthful Git-visible commit that contains:
   - `src/lib/bffClient.ts`
   - `src/pages/workbench/KnowledgeWorkbench.tsx`
   - `src/pages/workbench/types.ts`
   - `.coordination/requests/PKT-knowledge-workbench-ui-done.yaml`
   - `.coordination/requests/PKT-knowledge-workbench-frontend-feedback.yaml`
   - `docs/pantheon-feedback/PKT-knowledge-workbench/`
2. Front repo: make both request payloads point `source_commit` at that same
   immutable publication commit SHA.
3. Front repo: remove the stale `comingSoon` badge from the Knowledge sidebar
   entry if `/knowledge` remains the published live overview route.
4. Front repo: keep rendering only backend-owned overview data from
   `GET /api/v1/workbench/knowledge`; do not add registry, note, evidence, or
   strategy-spec browse state while `KW-01` through `KW-05` remain blocked.

## Residual Risk

- This review verified the sibling front build, targeted lint slice, and the
  local Pantheon read route, but it did not run browser QA against a deployed
  Pantheon environment.
- The current open issues are publication-truth and UI availability messaging,
  not Pantheon route correctness.

## 2026-04-19 Closeout Addendum

The remaining front-owned blockers are resolved in the latest replay-clean
publication.

- The knowledge overview request pair is now Git-visible at
  `c9c1e20726bfc1d35f3ddcbb4f7552859f1d8f5d`.
- Both request bodies now point `source_commit` at
  `77ab876e05dbb206f4fd4abc39051df86f6127c2`, which contains the knowledge
  overview screen and feedback bundle.
- The stale `comingSoon` badge was removed from the live Knowledge sidebar
  entry in the same transport bundle.
- Pantheon's knowledge overview contract remains green:
  `python3 -m pytest services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py -q`
  passed in the current workspace.

## 2026-04-22 Closeout Sync

Pantheon rechecked the current remote front publish commit and the current
Knowledge overview runtime truth.

- `git -C ../front-ai-trading-system ls-remote --heads origin pkt-004-detail-fix`
  now resolves to `dbc4a16dc0e9f0b8d33e1576908341ea056c660d`.
- That remote publish commit still contains the canonical request pair, the
  full `docs/pantheon-feedback/PKT-knowledge-workbench/*` bundle, the reviewed
  Knowledge Workbench UI files, and the AppSidebar truth fix while truthfully
  pinning `source_commit` back to
  `77ab876e05dbb206f4fd4abc39051df86f6127c2`.
- `python3 -m pytest services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py -q`
  now returns `2 passed` in the current workspace.
- A direct local FastAPI `TestClient` read of
  `GET /api/v1/workbench/knowledge` now returns `200 OK` with
  `headline = KW-01 to KW-05 are route-live`,
  `module_counts.ready = 5`,
  backend-owned module order `KW-01` through `KW-05`,
  and backend-authored next steps focused on frontend activation.
- This current route-live contract truth supersedes the earlier 2026-04-18
  review-cycle wording that described `KW-01` through `KW-05` as blocked. The
  reviewed `/knowledge` UI remains valid because it renders backend-owned
  overview fields verbatim and does not synthesize module browse state locally.

## Final Decision

**APPROVED.**
