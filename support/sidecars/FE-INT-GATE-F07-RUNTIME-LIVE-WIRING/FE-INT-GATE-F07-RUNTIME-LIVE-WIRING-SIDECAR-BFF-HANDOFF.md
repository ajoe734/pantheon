# FE-INT-GATE-F07-RUNTIME-LIVE-WIRING Sidecar: BFF and Frontend Handoff Packet

Task ID: FE-INT-GATE-F07-RUNTIME-LIVE-WIRING-SIDECAR-BFF-HANDOFF
Parent Task: FE-INT-GATE-F07-RUNTIME-LIVE-WIRING
Helper Kind: bff_handoff_packet
Prepared by: Codex
Reviewer: Codex2
Date: 2026-05-14
Mutates canonical truth: false

## Purpose

This packet supports the F07 runtime live-wiring follow-up by documenting the
BFF query surface, operator journey, and frontend handoff notes for
`/management/runtimes`.

It is a support artifact only. It does not change L1 canonical truth, core
contracts, runtime implementation, registry implementation, governance logic, or
the sibling `execute-plans` frontend source.

## Current Parent State

`FE-INT-GATE-F07-RUNTIME-LIVE-WIRING` was already `review_approved` in
`ai-status.json` at packet creation time and has since been closed.

Reviewer notes recorded for the parent:

| Item | Snapshot |
|---|---|
| Frontend wiring | `Runtimes.tsx` moved from `bff.runtimes.list()` plus `useEffect` to `useLiveListV1<Runtime>(lists.runtimes, ["Runtime"])`. |
| Route target | `/management/runtimes` now reads `/bff/runtimes` through the BFF v1 list facade. |
| Fixture update | The runtime fixture includes the fields rendered by the page: `kind`, `env`, `cpu`, `memory`, `latencyP95Ms`, `uptimePct`, and `region`. |
| Parent closeout gap | Historical only after parent closeout. At packet creation time, the parent owner still needed a task-scoped commit for sibling frontend changes: `src/management/pages/Runtimes.tsx` and `e2e/06-entity-registry.spec.ts`. |

At packet creation time, the sibling `execute-plans` worktree also had
unrelated dirty files and audit outputs. Parent closeout was expected to stage
only the parent-owned F07 files.

## BFF Query Surface

The backend BFF runtime compatibility surface already exists in
`services/control-plane/bff/main.py` and is covered by focused tests.

| Query/action | Current support | Handoff note |
|---|---|---|
| `GET /bff/runtimes` | Present. Requires read role, reads `read_store.list_runtime_bindings()`, supports `status`, `deployment_stage`, `page_token`, and `page_size`. | This is the list route the F07 page must call. It returns `items`, `page_info`, and `meta.surfaces.runtimes`. |
| `GET /bff/runtimes/{runtime_id}` | Present. Looks up by `runtime_id`, then falls back to `binding_id`. | The page does not need detail reads for F07, but the F07 probe uses missing-detail behavior as a registry contract guard. |
| `POST /bff/runtimes/{runtime_id}/actions/{action_id}` | Present as existing runtime action compatibility route. | F07 is a read-list wiring task. Do not use this sidecar to roll command adapters or canonical command envelope migration forward. |
| `/api/v1/runtime-bindings` and `/api/v1/runtimes/{runtime_id}/status` | Present as older APP/BFF read surfaces. | These are not the hosted frontend target for F07. The hosted route must use `/bff/runtimes`. |

### Query Gaps and Absorption Notes

1. No new BFF route is required for the parent F07 acceptance path. The gap was
   frontend consumption: hosted `/management/runtimes` previously rendered the
   seed label `executor-us-east-1` and did not read `/bff/runtimes` under route
   interception.
2. The frontend list facade normalizes BFF list payloads through
   `normalizeLiveListResponse(..., "entityRegistry")`, so a backend response with
   `items`/`data` plus `meta.total` can still become an exact-counted
   `ListEnvelope` for UI rendering.
3. If `FE_INT_GATE_LIVE_BFF=1` is later made mandatory for the live BFF probe,
   review two response-shape details before treating failures as frontend
   regressions:
   - current backend `/bff/runtimes` returns `items`, `page_info`, and `meta`;
     the Playwright fixture envelope also includes top-level `totalCountExact`.
   - current backend typed 404s use `OBJECT_NOT_FOUND`; the F07 Playwright
     fixture names the synthetic missing-detail error `RESOURCE_NOT_FOUND`.
4. Those live-probe deltas do not block the reviewed hosted DOM acceptance if the
   parent continues to validate runtime routing through fixture-backed
   interception and the UI list facade.

## Frontend Handoff

The parent frontend patch in `/home/lupin/code/execute-plans` is intentionally
small:

| File | Handoff summary |
|---|---|
| `src/management/pages/Runtimes.tsx` | Imports `lists` and `useLiveListV1` from `@/lib/bff-v1`; uses `const { items: rows, refresh } = useLiveListV1<Runtime>(lists.runtimes, ["Runtime"])`; calls `refresh()` after runtime actions. |
| `src/lib/bff-v1/paths.ts` | `paths.runtimes()` builds `/bff/runtimes`. No change needed for F07. |
| `src/lib/bff-v1/lists.ts` | `lists.runtimes` already maps to `liveOrMockList(paths.runtimes(), async () => seed.runtimes, "entityRegistry")`. No change needed for F07 beyond consuming it from `Runtimes.tsx`. |
| `e2e/06-entity-registry.spec.ts` | Runtime fixture now represents `B06 Runtime Binding` with the fields rendered by the runtime table; the all-12 registry test asserts every list route is read. |

The expected hosted regression signature is clear:

| Symptom | Interpretation |
|---|---|
| `/management/runtimes` renders `B06 Runtime Binding` and the intercepted calls include `/bff/runtimes` | F07 runtime live wiring is behaving as expected. |
| `/management/runtimes` renders `executor-us-east-1` | The page is still using seed runtime data or old `bff.runtimes.list()` behavior. |
| The all-12 registry test lacks a `/bff/runtimes` call | The runtime route has regressed out of list-route coverage. |
| The page renders a seed/mock/fallback banner under strict hosted verification | Treat as a frontend live-mode regression, not as an acceptable F07 fallback. |

## Operator Journey

```text
Operator opens /management/runtimes
  -> RuntimesPage mounts
  -> useLiveListV1 calls lists.runtimes
  -> lists.runtimes requests GET /bff/runtimes when live/strict BFF mode is active
  -> normalizeLiveListResponse converts the BFF list payload into ListEnvelope<Runtime>
  -> table renders the BFF runtime label, kind, env, status, CPU, memory, p95 latency, uptime, and region
  -> operator sees B06 Runtime Binding in F07 fixture-backed verification
  -> operator does not see legacy seed-only executor-us-east-1 as the sole runtime evidence
```

Runtime actions remain outside the F07 read-list acceptance:

```text
Operator invokes a runtime table action
  -> existing mutations.runtimeAction path handles the command
  -> RuntimesPage calls refresh() after success
  -> refresh re-reads lists.runtimes
  -> table stays aligned with /bff/runtimes
```

Do not use this sidecar to change command-envelope semantics, action endpoint
deprecation behavior, or write gates.

## Review and Closeout Handoff

Historical parent handoff for the parent owner (`Codex2`):

1. Keep the parent F07 closeout scoped to sibling `execute-plans` files already
   reviewed by Claude.
2. Stage only `src/management/pages/Runtimes.tsx` and
   `e2e/06-entity-registry.spec.ts` for the parent task-scoped commit.
3. Do not stage unrelated dirty sibling files such as `PersonaLab.tsx`,
   `SkillPromptEditor.tsx`, `CapabilitiesLists.tsx`, `.lovable/audits/*`,
   `.tmp-bff-route-manifest/`, `playwright-report/`, or `test-results/`.
4. Use the parent reviewer notes in `ai-status.json` as the closeout acceptance
   basis; this sidecar is supporting context, not replacement review approval.

Parent F07 has since closed, so the parent closeout gap above is retained only
as handoff history.

For the sidecar reviewer (`Codex2`):

1. Confirm this packet is support-only and does not modify canonical truth.
2. Confirm the BFF route notes match the existing backend compatibility surface.
3. Confirm the frontend handoff describes the reviewed parent patch accurately.
4. If satisfied, approve this sidecar with:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh approve FE-INT-GATE-F07-RUNTIME-LIVE-WIRING-SIDECAR-BFF-HANDOFF "Support-only BFF/frontend handoff packet is complete and accurate."
```

Reviewer approval was recorded on 2026-05-14T13:20:34Z. Codex2 reverified the
`/bff/runtimes` list/detail/action surface, sibling frontend handoff facts,
`git diff --check`, and the focused BFF pytest set.

## Verification

Commands run from `/home/lupin/code/pantheon`:

```bash
python3 -m pytest \
  services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py::test_bff_deployment_runtime_and_risk_action_routes_return_final_envelopes \
  services/control-plane/bff/test_bff_consol_016_detail_smoke_a.py::test_detail_smoke_a_pack_a_routes_resolve_acceptance_links \
  services/control-plane/bff/test_bff_consol_016_detail_smoke_a.py::test_detail_smoke_a_phantom_family_ids_return_typed_404 \
  -q
# 3 passed in 17.28s
```

Read-only checks performed:

| Source | Result |
|---|---|
| `ai-status.json` | Parent has closed; this sidecar is `review_approved`; reviewer is `Codex2`. |
| `/home/lupin/code/execute-plans/src/management/pages/Runtimes.tsx` | Confirms `useLiveListV1<Runtime>(lists.runtimes, ["Runtime"])` wiring and `refresh()` after actions. |
| `/home/lupin/code/execute-plans/e2e/06-entity-registry.spec.ts` | Confirms runtime fixture label `B06 Runtime Binding`, list path `/bff/runtimes`, and all-12 list-route assertion. |
| `services/control-plane/bff/main.py` | Confirms backend `/bff/runtimes` list/detail/action compatibility routes exist. |
| `services/control-plane/bff/data/fixtures_pack_a.json` and `fixtures_pack_b.json` | Confirms runtime binding fixtures are present and fail-closed paper/canary metadata is not live-capital authority. |

No hosted Playwright run was executed by this sidecar. Hosted DOM verification
belongs to the parent implementation evidence and reviewer approval.

## Closeout Notes

Closeout started by Codex on 2026-05-14T13:25:03Z under
`owned_finalize_dispatch`.

Closeout remains support-only:

- no L1 canonical truth was modified;
- no core contract, runtime, registry, governance, or frontend implementation
  was modified;
- the only task-owned repo artifact is this handoff packet.

Closeout verification:

```bash
python3 -m pytest services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py::test_bff_deployment_runtime_and_risk_action_routes_return_final_envelopes services/control-plane/bff/test_bff_consol_016_detail_smoke_a.py::test_detail_smoke_a_pack_a_routes_resolve_acceptance_links services/control-plane/bff/test_bff_consol_016_detail_smoke_a.py::test_detail_smoke_a_phantom_family_ids_return_typed_404 -q
# 3 passed in 7.90s
```
