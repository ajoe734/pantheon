# APP-003-ROUTE-LIVE-FRONTEND-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-003-ROUTE-LIVE-FRONTEND-001` - publish route-live frontend activation packets for `CW-02`, `KW-04`, and `KW-05`
**Parent Owner**: `Codex`
**Parent Reviewer**: `Codex2`
**Parent Status**: `review`
**Sidecar Task**: `APP-003-ROUTE-LIVE-FRONTEND-001-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Codex2`
**Helper Kind**: `bff_handoff_packet`
**Refreshed**: `2026-04-23`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 truth, canonical BFF
> contracts, runtime behavior, registry or governance implementations, or the
> parent execution record. It packages the already-published route-live
> frontend packets, the truthful workbench-overview boundary, and the exact
> reviewer framing needed to keep this slice from being reopened as fake
> backend residue.

## 1. Executive Summary

`APP-003-ROUTE-LIVE-FRONTEND-001` was opened because three modules were already
route-live in the BFF but were still missing explicit frontend-activation
visibility on the execution board:

- `CW-02` Debate Transcript
- `KW-04` Insight Cards
- `KW-05` Strategy Spec

Current repo truth is now aligned:

- `CW-02` is route-live at
  `GET /api/v1/consultations/{session_id}/transcript`, and its module-local
  frontend activation packet is published at
  `docs/pantheon-handoffs/CW-02-debate-transcript/FRONTEND_CHANGE_SPEC.md`.
- `KW-04` is route-live at `GET /api/v1/knowledge/insights` plus
  `GET /api/v1/knowledge/insights/{insight_id}`, and its frontend handoff
  bundle is published at
  `docs/pantheon-handoffs/KW-04-insight-cards/FRONTEND_CHANGE_SPEC.md`.
- `KW-05` is route-live at the strategy-spec list, detail, version-history,
  and compare routes, and its frontend activation packet is published at
  `docs/pantheon-handoffs/KW-05-strategy-spec/FRONTEND_CHANGE_SPEC.md`.
- the Consultation and Knowledge workbench overview contracts now describe
  these modules truthfully as live-route surfaces with front-owned activation
  follow-up, not as pending-BFF work.
- the targeted route and overview contract tests pass in the current
  workspace.

Reviewer-safe conclusion:

- do not reopen `CW-02`, `KW-04`, or `KW-05` as Pantheon-side BFF gap work
- do point frontend consumers to the existing module-local handoff bundles
- do keep any separate `CW-04` follow-up isolated to its own front-publication
  lane; it is no longer a missing handoff or Pantheon-side route gap

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | durable lifecycle truth: parent task is in `review`; this sidecar is support-only and assigned to `Codex` with reviewer `Codex2` |
| `.orchestrator/task-briefs/app_003_route_live_frontend_001_sidecar_bff_handoff.md` | scoped execution brief and artifact target |
| `docs/reviews/2026-04-22-route-live-frontend-and-residual-truth-execution-packet.md` | original execution packet that materialized this route-live frontend activation slice |
| `docs/bff/PKT-consultation-workbench.md` | overview route truth that `CW-02` and `CW-04` both remain on the live-route side with published module-local frontend packets |
| `docs/bff/PKT-knowledge-workbench.md` | overview route truth that all Knowledge modules are route-live and `KW-02` through `KW-05` now have published frontend packets |
| `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` | Consultation module ordering plus the published `CW-02` transcript and `CW-04` memo handoff boundaries |
| `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md` | Knowledge module ordering plus `KW-04` and `KW-05` readiness gates |
| `docs/bff/CW-02-debate-transcript.md` | canonical transcript route, ordering, actor identity, and degradation semantics |
| `docs/bff/KW-04-insight-cards.md` | canonical list/detail insight-card contract and route-live status |
| `docs/bff/KW-05-strategy-spec.md` | canonical list/detail/version-history/compare contract and route-live status |
| `docs/pantheon-handoffs/CW-02-debate-transcript/FRONTEND_CHANGE_SPEC.md` | canonical frontend activation packet for the transcript timeline |
| `docs/pantheon-handoffs/KW-04-insight-cards/FRONTEND_CHANGE_SPEC.md` | canonical frontend handoff packet for card grid and detail surfaces |
| `docs/pantheon-handoffs/KW-05-strategy-spec/FRONTEND_CHANGE_SPEC.md` | canonical frontend activation packet for spec browse/detail/compare surfaces |
| `docs/pantheon-handoffs/LOVABLE_MASTER_SA.md` | app-shell and section-level frontend guidance that keeps these modules on the live-route side of the boundary |
| `.coordination/reviews/CW-04-redteam-memo-review.md` | sibling-loop truth that any remaining `CW-04` work is replay-clean front publication, not a missing handoff or BFF gap |
| `services/control-plane/bff/test_pkt015_consultation_workbench_contract.py` | executable proof for the Consultation overview truth |
| `services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py` | executable proof for the Knowledge overview truth |
| `services/control-plane/bff/test_cw02_debate_transcript_contract.py` | executable proof for transcript route semantics |
| `services/control-plane/bff/test_kw04_insight_cards_contract.py` | executable proof for insight-card list/detail behavior |
| `services/control-plane/bff/test_kw05_strategy_spec_contract.py` | executable proof for strategy-spec browse/detail/version/compare behavior |

## 3. Route-Live Query Gap And Current Repo State

### 3.1 What the parent task actually needed to fix

The execution packet did not identify a new architecture gap for these
modules. It identified a visibility and packetization gap:

- the BFF routes were already live or had just become live
- the frontend activation packets for `CW-02`, `KW-04`, and `KW-05` needed to
  exist and be represented truthfully in supervisor-visible surfaces
- workbench-overview materials needed to stop making these modules look like
  hidden backend residue

### 3.2 Current route and handoff matrix

| Module | Current Pantheon-side BFF state | Frontend handoff packet | Reviewer interpretation |
|---|---|---|---|
| `CW-02` | `GET /api/v1/consultations/{session_id}/transcript` is live; `sequence_no` ordering, actor identity, `meta.staleness`, and `meta.surfaces.transcript.state` are backend-owned | `docs/pantheon-handoffs/CW-02-debate-transcript/FRONTEND_CHANGE_SPEC.md` | closed Pantheon-side route gap; remaining work is front implementation against the live transcript route |
| `KW-04` | `GET /api/v1/knowledge/insights` and `GET /api/v1/knowledge/insights/{insight_id}` are live; filter metadata, resolved links, and linked-source drilldown are backend-owned | `docs/pantheon-handoffs/KW-04-insight-cards/FRONTEND_CHANGE_SPEC.md` | closed Pantheon-side route gap; remaining work is front activation without client-side card or filter synthesis |
| `KW-05` | list, detail, version-history, and compare routes are live under `/api/v1/knowledge/strategy-specs`; version identity, ancestry, compare output, and citation bundle remain backend-owned | `docs/pantheon-handoffs/KW-05-strategy-spec/FRONTEND_CHANGE_SPEC.md` | closed Pantheon-side route gap; remaining work is front activation against backend-owned version and compare semantics |

### 3.3 Overview-route truth that must remain stable

Two overview routes are the reviewer-safe entry points that keep the route-live
activation state honest:

- `GET /api/v1/workbench/consultation`
  - must keep `CW-02` on the live-route side
  - must keep `CW-04` on the live-route side with its published module-local
    frontend handoff bundle
  - must keep any remaining `CW-04` follow-up framed as front publication
    replay / closeout, not missing Pantheon BFF routes or handoff publication
- `GET /api/v1/workbench/knowledge`
  - must keep all five Knowledge modules on the live-route side
  - must keep the remaining follow-up framed as `KW-01` hardening plus
    front-owned UI activation, not missing BFF routes

### 3.4 Current gap classification

| Item | State | Notes |
|---|---|---|
| `CW-02` Pantheon-side BFF query gap | none | route and handoff packet are both published |
| `KW-04` Pantheon-side BFF query gap | none | list/detail routes and handoff bundle are both published |
| `KW-05` Pantheon-side BFF query gap | none | browse/detail/version/compare routes and handoff bundle are both published |
| Frontend activation work | open but front-owned | UI implementation should consume the already-published packets, not ask Pantheon to re-open route design |
| Consultation sibling follow-up outside this task | open but not a handoff/BFF gap | `CW-04` handoff publication is already complete; the separate loop is narrowed to replay-clean front publication |

## 4. Verification Replayed For This Sidecar

On `2026-04-23`, this sidecar re-verified the most relevant evidence:

- `pytest -q services/control-plane/bff/test_pkt015_consultation_workbench_contract.py services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py services/control-plane/bff/test_cw02_debate_transcript_contract.py services/control-plane/bff/test_kw04_insight_cards_contract.py services/control-plane/bff/test_kw05_strategy_spec_contract.py`
- result: `25 passed in 7.77s`
- `python3 -m json.tool docs/examples/CW-02-debate-transcript.json`
- `python3 -m json.tool docs/examples/KW-04-insight-cards.json`
- `python3 -m json.tool docs/examples/KW-05-strategy-spec.json`
- result: all three example payloads parse cleanly

This sidecar did not rerun unrelated frontend build steps or browser QA because
the task scope is support-only packaging for a reviewer handoff.

## 5. Frontend Handoff Boundary

The parent lane does not need to invent new module-local specs. The existing
frontend packets are already the correct dispatch surfaces.

| Module | Frontend must use | Frontend must not do |
|---|---|---|
| `CW-02` | preserve backend event order by `sequence_no`; use backend-owned actor identity, `meta.staleness`, and `meta.surfaces.transcript.state`; treat `evidence_refs[]` as canonical ids only | do not re-sort by `event_time`; do not infer actor identity from roster position; do not construct evidence URLs from raw ids |
| `KW-04` | populate filters only from backend `filter_metadata`; navigate through `route_href` and `resolved_link`; treat confidence and supersession as backend-owned | do not synthesize insight cards from `KW-01` to `KW-03` data; do not invent tags or recency buckets; do not build URLs from raw refs |
| `KW-05` | use backend route params and returned `route_href`; treat `strategy_id + spec_version_id` as canonical identity; use backend compare output and citation links verbatim | do not diff arbitrary JSON client-side; do not reconstruct ancestry from timestamps or labels; do not infer compare authority without `allowedActions.canCompare` |

Cross-cut rules that still apply from the published frontend architecture:

- BFF-first only: do not aggregate multiple Pantheon routes in the browser
  unless the packet explicitly allows it
- degradation is first-class: use backend `meta.surfaces` and `meta.staleness`
  instead of flattening degraded state into empty UI
- live updates are overlay only: snapshot responses remain the source of truth

## 6. Truthful Operator And Frontend Journey

### 6.1 Consultation path

1. Operator enters the Consultation workbench through
   `GET /api/v1/workbench/consultation`.
2. The overview truthfully keeps both `CW-02` and `CW-04` on the live-route
   side with published activation packets; any remaining `CW-04` follow-up
   stays isolated to its own front-publication lane.
3. When the operator opens one transcript, the frontend calls
   `GET /api/v1/consultations/{session_id}/transcript`.
4. The transcript screen renders rows exactly in backend-owned `sequence_no`
   order, uses backend actor identity, and treats `partial | degraded |
   unavailable` exactly as the contract defines.

### 6.2 Knowledge path

1. Operator enters the Knowledge workbench through
   `GET /api/v1/workbench/knowledge`.
2. The overview truthfully shows all five Knowledge modules on the live-route
   side, with `KW-04` and `KW-05` already carrying published handoff bundles.
3. For insight browsing, the frontend calls
   `GET /api/v1/knowledge/insights` and
   `GET /api/v1/knowledge/insights/{insight_id}`.
4. The card grid and detail surfaces render backend-owned filters, confidence,
   supersession, evidence resolution, and linked-source drilldown without
   recreating aggregation logic in the browser.
5. For strategy browsing, the frontend calls the strategy-spec list, detail,
   version-history, and compare routes under `/api/v1/knowledge/strategy-specs`.
6. The spec viewer and compare surface use backend-owned version identity,
   ancestry, citation links, and compare output exactly as returned.

## 7. Reviewer Checklist

For `Codex2` reviewing this sidecar:

- confirm the packet stays support-only and does not redefine canonical truth
- confirm it classifies `CW-02`, `KW-04`, and `KW-05` as closed Pantheon-side
  route gaps
- confirm it points consumers back to the existing module-local frontend
  packets instead of inventing replacement handoff bundles
- confirm it preserves the workbench-overview boundary without reintroducing a
  fake `CW-04` handoff gap, and keeps Knowledge on the live-route side
- confirm it treats remaining work as front-owned UI activation, with sibling
  `CW-04` follow-up narrowed to front publication replay rather than hidden BFF
  incompleteness

## 8. Suggested Parent-Task Interpretation

If the parent review accepts the current evidence, the safe closeout framing is:

- route-live frontend activation packet publication for `CW-02`, `KW-04`, and
  `KW-05` is complete in the current workspace
- the remaining implementation work for those three modules is frontend-owned
  consumption of already-published packets
- any sibling `CW-04` follow-up remains outside this parent task and is now
  narrowed to front publication replay / closeout, not missing handoff
  publication or Pantheon route design
