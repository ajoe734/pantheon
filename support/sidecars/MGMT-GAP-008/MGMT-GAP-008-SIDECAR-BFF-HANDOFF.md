# MGMT-GAP-008 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `MGMT-GAP-008-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `MGMT-GAP-008` - Management detail DTO and render honesty |
| Parent owner / reviewer | `Claude` / `Codex2` |
| Prepared by | `Codex2` |
| Reviewer | `Claude` |
| Date | 2026-07-01 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, BFF runtime code, frontend source, registry implementation, or
governance policy. It summarizes the BFF query gap, operator journey, and
frontend handoff boundaries for `MGMT-GAP-008`; the parent owner decides whether
and how to absorb it into the main implementation branch.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates task ownership; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/mgmt_gap_008_sidecar_bff_handoff.md` | Sidecar scope is support-only: BFF query gap, operator journey, and frontend handoff materials; no canonical truth changes. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-GAP-008-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Codex2`, reviewer `Claude`, artifact path is this file. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-GAP-008` | Parent task is `in_progress`, owner `Claude`; scope is live-id detail honesty, alias canonicalization, empty capability registry truth, and evidence-source degradation clarity. |
| `docs/04/pantheon_management_console_gap_2026-06-30/README.md` | The old missing-route framing is superseded; remaining issue is production honesty across detail pages, aliases, empty registries, and acceptance proof. |
| `docs/04/pantheon_management_console_gap_2026-06-30/archive/full-reaudit-addendum-2026-07-01.md` | Live-id probes found `status.undefined`, `risk.undefined`, blank headings/owners/updates, `NaN%`, empty capability seed-id leakage, and incomplete evidence-source resolution. |
| `docs/04/pantheon_management_console_gap_2026-06-30/archive/route-control-reaudit-2026-07-01.md` | Direct-render detail aliases remain for capital pools, ranking formulas, rebalances, and research; production behavior must redirect or share one canonical mapper. |
| `docs/bff/execution-tasks/2026-06-30-management-console-production-gap/MGMT-GAP-008-detail-render-honesty.md` | Parent acceptance requires no raw undefined/blank/NaN, honest empty registry states, and route evidence for known aliases. |
| `services/control-plane/bff/main.py` and `services/control-plane/bff/read_store.py` | Relevant BFF read/detail families exist. The gap is heterogeneous DTO shape plus unavailable/not-found handling, not a wholesale missing endpoint gap. |
| `services/control-plane/bff/tests/test_bff_consol_skills_mcp_registry_contract.py` | Tools/MCP/Skills registries are expected to return typed list envelopes when wired, with detail 404 for unknown ids. Empty live registries are valid truth. |
| `services/control-plane/bff/tests/test_bff_b3_management_evidence.py` | `/bff/management/evidence` composes evidence overview envelopes with source-surface metadata and resolved-link availability. |
| `/home/lupin/code/pantheon/.fe-ep` | Active audited frontend checkout is on `task/mgmt-gap-008-detail-honesty` with uncommitted parent work; observations below are not merged truth. |
| `/home/lupin/code/execute-plans` | Secondary execute-plans checkout is on `dev` and behind `origin/dev`; for this gap packet, the spec explicitly names `.fe-ep` as the audited frontend source. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current Frontend State Observed

Observed in `/home/lupin/code/pantheon/.fe-ep` at HEAD
`821ad41bbcf1d3bc6352744a6310a80f088b696a`, branch
`task/mgmt-gap-008-detail-honesty`, with uncommitted parent-owner changes.
Re-check before treating these as delivered.

| Surface | Observed state | Handoff meaning for parent |
|---|---|---|
| Detail alias routes | `src/App.tsx` has a new `DetailAliasRedirect` and routes `capital-pools/:id`, `ranking-formulas/:id`, `rebalances/:id`, and `research/:id` through canonical bases. | This matches the route/control audit direction. Consider preserving query/hash if any detail tabs or filters depend on URL state. |
| Base detail mapper | `src/lib/bff-v1/seed.ts` now normalizes common aliases into `id`, `name`, `state`, `risk`, `owner`, and `updatedAt`. | Good central place for FE-only BaseObject normalization. Keep this as display mapping, not canonical contract truth. |
| Header/badges | `EntityHeader`, `StatusBadge`, `RiskBadge`, and `Field` render fallbacks for missing label/owner/status/risk/field values. | This should eliminate `status.undefined`, `risk.undefined`, blank h1, and blank owner/update in the shared header if records pass through the normalizer. |
| Capital detail | `CapitalPoolDetail` guards one utilization calculation, but still has several domain metrics and related-row percentage cells that depend on live related data shape. | Parent should run DOM probes for every stat/card/table cell, not just the header. |
| Research detail | `ResearchDetail` still uses old fields such as `metric`, `metricValue`, `artifactId`, `hypothesis`, and random fold data; BFF RW-04 detail exposes `experiment_id`, `experiment_name`, `status`, `progress`, `framework`, `artifact_ids`, `failure`, etc. | Header may be fixed by the normalizer, but page-specific body mapping still needs live DTO adaptation or honest `N/A` states. |
| Artifact detail | `ArtifactDetail` still expects `kind`, `sizeMb`, `hash`, `sourceExperimentId`; BFF RW-05 detail exposes `artifact_type`, `metrics`, `parameters`, `produced_by_experiment_id`, `version_chain`, and allowed actions. | Body sections should not invent format/hash/license defaults when live fields are absent. |
| Deployment detail | `DeploymentDetail` uses `target`, `version`, `artifactId`, `owner`, runtime fields, and command-like dialogs. BFF deployment plan records use deployment-plan fields such as `plan_id`, `artifact_id`, `deployment_stage`, `status`, timestamps, and governance refs. | Detail body needs a deployment DTO adapter. Command truth remains `MGMT-GAP-004` scope; do not solve it here except by preserving existing disabled/receipt behavior. |
| Channel detail | `ChannelDetail` can now get header fallbacks, but still creates synthetic recent messages and a toast-only send-test action. | DTO honesty should label or remove synthetic history; command truth for send-test belongs to `MGMT-GAP-004`. |
| Tools/MCP/Skills detail | `ToolDetail`, `McpDetail`, and `SkillDetail` catch missing detail responses and render `DetailNotFound` with live-registry-empty copy. | This is the correct pattern for empty live registries; ensure it also appears after strict-live BFF 404, not only after generic promise rejection. |
| Evidence detail | Evidence detail reads BFF evidence models and renders `resolvedLink.availability`, redaction, source context, and degraded linked-decisions copy. | Parent should keep evidence-source resolution as available/degraded/unavailable, never as a silent source preview. |

## BFF Query Gap Matrix

| Frontend need | Current BFF surface | Current gap / required behavior |
|---|---|---|
| Canonical capital detail | `GET /bff/capital-pools/{id}` | Route exists. Live records may expose `pool_id`, `status`, `owner_id`, `risk_policy_ref`, `capital_allocation`, etc., not a full FE `BaseObject`. FE should normalize display aliases and guard missing utilization values; BFF must not fabricate risk/owner/update. |
| Canonical research detail | `GET /bff/research-experiments/{id}` | Route exists. RW-04 detail uses experiment-native fields, not old FE fields such as `metricValue` and `hypothesis`. Parent should adapt the page body to RW-04 fields or render `N/A`; do not fill random folds as live evidence. |
| Canonical artifact detail | `GET /bff/artifacts/{id}` | Route exists through read-model detail. RW-05 detail can omit old fields such as `hash` or `sizeMb`; FE should render artifact type, lineage/version chain, metrics, and allowed actions when present, otherwise explicit unavailable copy. |
| Canonical deployment detail | `GET /bff/deployments/{id}` | Route exists. Deployment-plan DTOs may use `plan_id`, `deployment_stage`, `target_stage`, `artifact_id`, `status`, and timestamps. FE detail should map these aliases before rendering header and domain cards. |
| Channel detail | `GET /bff/channels/{id}` | Route exists against local registry. Missing `kind`, `destination`, subscribers, filters, or owner must render `N/A`/unassigned; synthetic recent-message rows should not be presented as live history. |
| Ranking formula detail | `GET /bff/ranking-formulas/{id}` | Route exists. Alias route `/management/ranking-formulas/:id` should redirect to `/management/ranking/formulas/:id` or prove shared mapper coverage. |
| Rebalance detail | `GET /bff/rebalances/{id}` | Route exists. Alias route `/management/rebalances/:id` should redirect to `/management/rebalance/:id` or prove shared mapper coverage. |
| Empty tool registry | `GET /bff/tools`, `GET /bff/tools/{id}` | List can be 200 empty; detail returns 404 when id is not registered. FE must show live-empty/not-found copy and disable create/import/publish/retire actions unless command receipts exist. |
| Empty MCP registry | `GET /bff/mcp-servers`, `GET /bff/mcp-servers/{id}`, `GET /bff/mcp-tools`, `GET /bff/mcp-tools/{id}` | Same empty-list plus 404 detail pattern. FE must not leak seed ids such as `mcp_alpha` or leave a permanent loading shell. |
| Empty skills registry | `GET /bff/skills`, `GET /bff/skills/{id}` | Same empty-list plus 404 detail pattern. FE should distinguish live-empty from broken transport and avoid seed-backed skill details in strict live mode. |
| Evidence source resolution | `GET /bff/management/evidence`, detail adapter currently uses `/api/v1/knowledge/evidence/{refId}` | Evidence detail must surface `resolvedLink.availability`, redaction, source context, and surface metadata. `unavailable` is valid truth, not a render failure. |
| Alias final path proof | Frontend router, not BFF | BFF should not need duplicate alias endpoints. FE should redirect old detail aliases and the hosted probe should assert final canonical paths. |

## Parent Scope Boundary

`MGMT-GAP-008` owns:

- FE detail DTO normalization for the in-scope detail families.
- Shared display fallbacks for missing status, risk, owner, update time, and
  title/label.
- Page-specific mapping from BFF domain DTOs into honest detail cards and
  tables.
- Alias redirect or canonical shared mapper for `capital-pools/:id`,
  `ranking-formulas/:id`, `rebalances/:id`, and `research/:id`.
- Empty registry and not-found states for Tools/MCP/Skills.
- Evidence-source resolution display as live/degraded/unavailable.
- Tests and hosted strict-live evidence that no in-scope route renders raw
  `undefined`, `NaN`, blank critical fields, or seed-id leakage.

`MGMT-GAP-008` does not own:

- Adding new canonical L1 contracts or OpenAPI schemas.
- Replacing existing BFF endpoint ownership from `MGMT-GAP-003`.
- Command receipt implementation for write-like CTAs (`MGMT-GAP-004`).
- Studios/capability runner implementation or nav demotion (`MGMT-GAP-005`).
- Hosted all-route acceptance harness ownership (`MGMT-GAP-006`), except for
  the detectors this task hands to that harness.
- Session/RBAC `/bff/me` contract (`MGMT-GAP-009`).
- Load/bundle release gates (`MGMT-GAP-010`).

## Operator Journey To Implement

### Journey A: Canonical Live Detail Opens Honestly

1. Operator opens a canonical live-id detail page, for example
   `/management/capital/pool-rescue-0260513-06627c91`.
2. FE calls the corresponding BFF detail route exactly once for that entity
   family.
3. The detail adapter normalizes only display aliases needed by the FE
   `BaseObject` header.
4. Header shows a non-empty title/id, status badge, risk badge, owner, and
   update timestamp fallback.
5. Domain cards render BFF-native data when present and `N/A`/explicit missing
   copy when absent. No card, table, progress bar, or badge renders raw
   `undefined`, `NaN`, or blank critical text.

### Journey B: Old Detail Alias Does Not Create A Second Surface

1. Operator opens an old bookmarked alias such as
   `/management/research/exp-mgmt-qlib-006`.
2. FE redirects to `/management/experiments/exp-mgmt-qlib-006` or uses the exact
   same canonical mapper/render path.
3. Browser final URL and network trace prove there is one canonical detail
   surface.
4. The alias page does not direct-render a second copy of the component.

### Journey C: Empty Capability Registry Fails Honestly

1. Operator opens `/management/tools`, `/management/mcp`, or
   `/management/skills` while BFF returns 200 with an empty list.
2. List page shows "live registry empty" copy and disables production actions.
3. Operator opens a stale/seed detail id such as `/management/tools/tl_market_data`.
4. FE receives BFF 404 or empty detail and renders a not-found/live-empty detail
   state. It must not stay on a loading shell or render a seed record.

### Journey D: Evidence Source Resolution Is Truthful

1. Operator opens `/management/evidence/:id` or
   `/management/evidence?ref_id=...`.
2. FE reads the evidence detail model and renders source document, credibility,
   linked object, and resolved link availability.
3. If the source preview or resolved link is unavailable, the page says so with
   the reason/source id. It must not invent a preview or hide degraded status.

## Suggested Frontend Handoff

| Area | Suggested parent action |
|---|---|
| Normalizer | Keep the common `normalizeBaseObjectFields` layer, but cover all in-scope id/name/time aliases: `pool_id`, `experiment_id`, `artifact_id`, `plan_id`, `channel_id`, `formula_id`, `rebalance_id`, `tool_id`, `server_id`, `skill_id`; names from `experiment_name`, `artifact_type`, `plan_name`, and `title`; owner from `owner_id`, `created_by`, `updated_by`, or `run_config.requested_by`. |
| Header fallback | Ensure `EntityHeader` never prints an empty badge when id is missing. If no id can be inferred, show the route id as the display id. |
| Numeric guards | Add helpers for percentage/money/stat formatting. Use them in capital, runtime, deployment, rebalance, and research tables so every non-finite value becomes `N/A`, not `NaN%` or `Infinity`. |
| Research detail | Replace old `metricValue`/random fold rendering with RW-04 fields: status/stage/framework/progress, queued/started/completed timestamps, artifact ids, validation warnings, failure reason, and allowed actions. |
| Artifact detail | Replace hardcoded format/license/hash assumptions with RW-05 fields: `artifact_type`, `version`, `status`, metrics, parameters, provenance, experiment refs, version chain, and `allowedActions`. |
| Deployment detail | Map deployment-plan fields before render: `plan_id` to id, `status` to state, `deployment_stage`/`target_stage` to target, `artifact_id` to artifact link, timestamps to update/promoted fields. |
| Channel detail | Remove or label synthetic recent-message rows; show BFF registry fields and explicit unavailable copy for missing destination/subscribers/filters. |
| Capabilities detail | Keep `DetailNotFound`; make tests assert strict-live 404 leads there for Tools, MCP servers/tools, and Skills. |
| Alias routes | Route-level tests should assert final path for all four known detail aliases. Preserve search/hash if the detail route uses query-backed tabs or evidence refs. |
| Evidence detail | Keep resolved-link and surface banners visible; tests should cover `availability: unavailable` and redacted evidence without leaking source refs or preview tokens. |

## Suggested Acceptance Checks

| Check | Expected result |
|---|---|
| Live-id detail text scan | Hosted strict-live DOM text for all in-scope live ids contains no `status.undefined`, `risk.undefined`, standalone `undefined`, blank h1, blank owner/update, `NaN%`, or `Infinity`. |
| Alias final path | `/management/capital-pools/:id`, `/management/ranking-formulas/:id`, `/management/rebalances/:id`, and `/management/research/:id` land on canonical final URLs or share one tested mapper. |
| Empty registry list | `/management/tools`, `/management/mcp`, and `/management/skills` show live-empty copy with create/import/publish/retire disabled when BFF lists are empty. |
| Empty registry detail | Stale/seed ids for Tools/MCP/Skills show live-empty/not-found detail state, not seed detail and not permanent loading. |
| Page-specific DTO mapping | Research, artifact, deployment, capital, channel, ranking formula, and rebalance body cards/tables render BFF-native fields or `N/A`, never fabricated defaults. |
| Evidence source resolution | Evidence detail shows `available`, `degraded`, or `unavailable` source state with reason/source id and does not leak opaque storage refs. |
| Command-truth separation | Existing write-like gaps remain visible to `MGMT-GAP-004`; this task does not claim toast-only or local-state actions are production-safe. |

## Suggested Verification Commands For Parent

Sidecar-only verification:

```bash
git diff --check -- support/sidecars/MGMT-GAP-008/MGMT-GAP-008-SIDECAR-BFF-HANDOFF.md
```

Frontend parent-branch verification suggestions:

```bash
npm test -- src/management/pages/oversight/EvidenceExplorerPage.test.tsx
npm test -- src/management/pages/capabilitiesProductionTruth.test.ts
npm test -- src/lib/bff-v1/__tests__/management.test.ts
```

Hosted proof should be produced by the parent or `MGMT-GAP-006` harness, not by
this sidecar. It should archive route ids, final URLs, BFF endpoint calls, DOM
negative text checks, screenshots or text snapshots, FE commit SHA, BFF commit
SHA, and PR links.

## Handoff

This packet is ready for `Claude` review. Use it as support material for the
parent `MGMT-GAP-008` implementation and closeout. The most important handoff
point is that current BFF read/detail route families mostly exist; the parent
should focus on FE detail DTO adaptation, route alias canonicalization, honest
empty/not-found states, and hosted proof rather than adding new canonical truth.
