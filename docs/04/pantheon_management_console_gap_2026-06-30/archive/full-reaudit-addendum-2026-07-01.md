# Management Console Full Re-Audit Addendum - 2026-07-01

| Field | Value |
|---|---|
| Status | Re-audit addendum for MGMT-GAP production closure |
| Re-audit date | 2026-07-01 |
| FE host | `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io` |
| BFF host | `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` |
| FE deployment under test | `41551e32432c7a7963716f9f197ee31f5fdd48a8` |
| FE mode | `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, `VITE_BFF_REAL_WRITES=false` |
| Related docs | `README.md`, `MGMT-GAP-002-closeout-2026-07-01.md`, `docs/04/pantheon_management_console_load_gap_2026-07-01/MANAGEMENT_CONSOLE_LOAD_GAP_SPEC.md` |

## 1. Why This Re-Audit Exists

The earlier management-console audit correctly found that the visible route set
mostly renders and that several canonical read routes are now wired. It was
still partial for production judgment because it over-weighted route existence
and clean render status.

This addendum re-checks the console as an operator system:

- route and nav inventory;
- hosted FE deployment proof;
- CORS/origin behavior;
- authenticated BFF list availability;
- detail-route behavior with live ids;
- mock, unavailable, and high-risk control flags;
- DTO/render honesty problems such as `status.undefined`, blank owners, and
  `NaN%`;
- write-like controls that still need command receipt truth.

## 2. Route Inventory

Static route extraction from `execute-plans` `src/App.tsx` and
`src/management/ManagementLayout.tsx` found:

| Class | Count | Meaning |
|---|---:|---|
| Total management route entries | 106 | Including parent, redirects, details, aliases, and hidden tools |
| Visible nav entries | 53 | First-level operator surfaces |
| Detail routes | 27 | `:id` or equivalent deep-link surfaces |
| Redirect routes | 17 | Compatibility aliases or retired pages |
| Hidden non-nav routes | 8 | Hidden direct routes, including studios and aliases |

The repetition is real. It comes from three sources:

1. many registry pages share the same list/detail scaffold;
2. aliases render the same component instead of redirecting;
3. non-production or empty capability/studio surfaces are still routable.

## 3. Hosted Browser Re-Audit

The hosted route audit covered 86 route samples:

- 53 visible nav routes;
- 26 detail/deep-link samples;
- hidden studio and compatibility aliases;
- safe interactions only: tabs, filters, selects, and search inputs. High-risk
  write CTAs were recorded, not executed.

Observed summary:

| Signal | Routes |
|---|---|
| Mock flag | `/management/readiness/strict-publish`, `/management/loops/execution`, `/management/alpha-factory`, `/management/studios/skill-sandbox` |
| Unavailable flag | `/management/trading-pulse`, `/management/tools`, `/management/skills` |
| High-risk/destructive surface flag | `/management/readiness/ep5`, `/management/readiness/broker-live`, `/management/interventions`, `/management/governance/policies`, `/management/governance/permissions`, `/management/settings`, `/management/skills`, `/management/channels` |
| Redirects observed | `/management/lineage -> /management/lineage?root=audit`, `/management/deployment -> /management/deployments`, `/management/deployment/:id -> /management/deployments/:id` |

Important caveat: local `127.0.0.1` strict-live preview is not a valid live
proof because the dev BFF CORS policy only allowed the hosted FE origin. Local
browser probes produced false `Failed to fetch` noise. Hosted FE origin is the
correct proof surface.

## 4. Authenticated BFF Availability

Using a dev operator token with roles `operator,reviewer,approver` and tenant
`tenant-dev`, BFF list reads showed:

| Endpoint family | Live status | Notes |
|---|---|---|
| `/bff/strategies` | 200 non-empty | Example id `tw-momentum-vslice` |
| `/bff/personas` | 200 non-empty | Example id `persona-20260620-69d3ac96` |
| `/bff/capital-pools` | 200 non-empty | Example id `pool-rescue-0260513-06627c91` |
| `/bff/research-experiments` | 200 non-empty | Example id `exp-mgmt-qlib-006` via `experiment_id` |
| `/bff/artifacts` | 200 non-empty | Example id `rart-20260615-002` via `artifact_id` |
| `/bff/incidents` | 200 non-empty | Example id `inc-87c655c3e3c9` via `incident_id` |
| `/bff/deployments` | 200 non-empty | Example id `plan-rescue-0260513-06627c91` |
| `/bff/channels` | 200 non-empty | Example id `approval` |
| `/bff/management/evidence` | 200 non-empty | Example id `evref-rart-20260615-002` |
| `/bff/management/human-inbox` | 200 non-empty | Example id `readiness_blocker:persona:persona-us-equity` |
| `/bff/management/persona-intent` | 200 non-empty | Example id `persona_trace:sess-rescue-0260513-d611ddc2` |
| `/bff/rebalances` | 200 empty | Empty truth must be explicit, not silently mock-filled |
| `/bff/evolution-programs` | 200 empty | Empty truth must be explicit |
| `/bff/tools` | 200 empty | Capability pages should not imply production tools exist |
| `/bff/mcp-servers` | 200 empty | Same |
| `/bff/mcp-tools` | 200 empty | Same |
| `/bff/skills` | 200 empty | Same |

The same token received `403 /bff/me` during page loads. That is a systemic
RBAC/session contract mismatch: data reads can succeed while the session surface
is degraded. It should be fixed or the integration harness should use the exact
role set required by `/bff/me`.

## 5. Detail Route Findings

Live-id detail probes prove the old "route renders" check is insufficient.

| Route | Result |
|---|---|
| `/management/strategies/tw-momentum-vslice` | Opens live detail, but many write CTAs remain command-truth scope |
| `/management/personas/persona-20260620-69d3ac96` | Opens live detail; shows degraded warning and many write CTAs |
| `/management/personas/:id/onboarding` | Opens; claims five governed write steps, must be proven by command receipts |
| `/management/capital/pool-rescue-0260513-06627c91` | Opens but renders `status.undefined`, `risk.undefined`, blank owner/update fields, and `NaN%` |
| `/management/capital-pools/:id` | Same component as `/management/capital/:id`; should redirect rather than duplicate render |
| `/management/experiments/exp-mgmt-qlib-006` | Opens but h1 is blank and body shows `status.undefined`, `risk.undefined`, and mock/seed-like fields |
| `/management/research/exp-mgmt-qlib-006` | Same as experiments alias; should redirect or be canonicalized |
| `/management/artifacts/rart-20260615-002` | Opens but shows `status.undefined`, `risk.undefined`, blank owner/update fields |
| `/management/incidents/inc-87c655c3e3c9` | Opens and is materially useful, but mitigation/postmortem/training buttons are write-truth scope |
| `/management/deployments/plan-rescue-0260513-06627c91` | Opens but h1 is blank and shows `status.undefined`, `risk.undefined`, blank owner/update fields |
| `/management/deployment/:id` | Redirects to plural deployment detail; this is correct compatibility behavior |
| `/management/channels/approval` | Opens but shows `status.undefined`, `risk.undefined`, blank fields |
| `/management/tools/:id`, `/management/mcp/:id`, `/management/skills/:id` | Live registries are empty; mock seed ids 404 and pages remain in "loading" style. These should not be treated as production capability surfaces |
| `/management/evidence/evref-rart-20260615-002` | Opens, but resolved source reports `unavailable`; evidence exists but source preview/resolution is incomplete |
| `/management/human-inbox/readiness_blocker:persona:persona-us-equity` | Opens, correctly says action cannot proceed for missing `research-owner` role; this is a good fail-closed pattern |

## 6. What Needs Adjustment

These should remain in the console but need UX/contract adjustment:

| Area | Adjustment |
|---|---|
| Registry list pages | Keep shared scaffold, but reduce repetition by adding domain-specific primary columns/actions and explicit empty/degraded source badges |
| Detail aliases | Convert old detail aliases such as `capital-pools/:id`, `ranking-formulas/:id`, `rebalances/:id`, and `research/:id` into redirects to canonical detail routes |
| Decision workbench | Consolidate `human-inbox`, `sentinel`, `interventions`, `approvals`, and `governance` into a clearer decision cluster; do not delete the separate capabilities until command ownership is clear |
| Performance cluster | Keep `portfolio-book`, `persona-league`, `quarterly-ranking`, and `performance-attribution`, but make the navigation read as one performance suite rather than four unrelated dashboards |
| Empty live registries | Tools/MCP/Skills pages must show "live registry empty" and disable production actions, not appear as broken or seed-backed |
| Auth/session | Align `/bff/me` role requirements with the dev operator/integration-gate token, or change the gate token to include the required viewer/session role |
| DTO normalization | Normalize detail DTOs so pages never render `status.undefined`, `risk.undefined`, blank owner/update fields, or `NaN%` |
| Load performance | Use the separate load-gap plan: code split management routes, defer shell fanout, remove duplicate jobs reads, avoid network-idle as readiness proof |

## 7. What Should Be Deleted, Hidden, Or Redirected

Do not delete canonical operator viewpoints just because they share layout.
Deletion should target duplicate render surfaces and non-production tools.

| Surface | Recommendation |
|---|---|
| `/management/studios/skill-sandbox` | Hide or guard behind non-production/runtime-runner flag until a real skill-runner trace exists |
| `/management/studios/formula` | Hide or guard until real backtest job/readback exists; keep only if MGMT-GAP-005 wires a runner |
| `/management/settings` Break-Glass tab | Hide/disable until it is a governed command; current toast-only behavior is dangerous |
| `PostmortemLibraryPage` seed rows | Remove seed rows from production nav; replace with real `/bff/postmortems` or an explicit unavailable state |
| `AlphaFactoryBoard` | Demote while it still reports mock/configured-mock state and has no production command path |
| Empty Tools/MCP/Skills detail paths | Do not surface seed ids. Keep list pages only as empty live registries or hide until capability service is populated |
| Old detail aliases | Keep bookmark compatibility as redirects only; stop rendering duplicate components behind alias paths |
| Retired NL console | If no route uses it, delete the component in a cleanup PR after import audit |

## 8. What Needs Deep Production Development

| Task | Deep development required |
|---|---|
| `MGMT-GAP-004` | Enumerate every write-like CTA and replace toast/local success with governed command receipt, audit id, dry-run proof, or disabled state |
| `MGMT-GAP-005` | Decide runtime-backed runner vs demotion for Formula Studio, Skill Sandbox, Tools, MCP, and Skills |
| `MGMT-GAP-008` | Fix live-id detail DTO/render honesty for `status.undefined`, `risk.undefined`, blank h1/owner/update, `NaN%`, detail aliases, and empty capability seed-id leakage |
| `MGMT-GAP-009` | Align `/bff/me`, tenant, roles, and management data reads so the session/RBAC contract is coherent and fail-closed |
| `MGMT-GAP-010` | Land the load-gap follow-up as a bundle, shell-fanout, route-ready, and release-gate performance task |
| `MGMT-GAP-006` | Build a hosted, authenticated management harness that uses live ids, checks endpoint calls, detects mock/unavailable/`undefined`/`NaN`, session/RBAC mismatch, load regressions, and records JSON/Markdown evidence |
| `MGMT-GAP-007` | Track the full gap set to merged/deployed proof; do not close on local render success |
| Load gap follow-up | Split bundle, defer shell reads, aggregate shell counts, remove duplicate jobs request, and harden BFF read concurrency |

## 9. Updated Classification

| Page family | Keep | Adjust | Hide/delete | Deep dev |
|---|---:|---:|---:|---:|
| Cockpit, persona fleet, human inbox, evidence, persona intent | Yes | Yes | No | Some, especially evidence resolution and role-specific gates |
| Readiness pages | Yes | Yes | No | Yes, for strict publish mock and broker/EP5 command proof |
| Performance pages | Yes | Yes | No | Medium |
| Registry list/detail | Yes | Yes | No | High for DTO normalization and command receipts |
| Operations pages | Yes | Yes | No | High for ack/escalate/resolve command receipts |
| Governance pages | Yes | Yes | No | High for memory/consult/permissions write truth |
| Workflows/hooks/knowledge/lineage | Yes | Yes | No | Medium to high |
| Settings | Partial | Yes | Break-glass hidden until real | High |
| Studios/capabilities | Conditional | Yes | Hide if not wired | High |
| Legacy aliases | No separate surface | Redirect only | Delete duplicate render | Low |

## 10. Bottom Line

The console is not "broken" in the simple route-render sense. It is also not
production-level.

The real production gap is this:

- too many surfaces are exposed as first-class management pages before they have
  durable live truth;
- several live detail DTOs render undefined/blank/NaN fields;
- empty live registries are indistinguishable from broken capability pages;
- write-like controls still over-promise without command receipt proof;
- the acceptance harness must be authenticated, hosted, live-id based, and able
  to detect mock/unavailable/undefined/NaN/control-truth failures.

This addendum expands the existing `MGMT-GAP-*` task split where the evidence
needs a named owner. `MGMT-GAP-004`, `MGMT-GAP-005`, `MGMT-GAP-008`,
`MGMT-GAP-009`, `MGMT-GAP-010`, and then `MGMT-GAP-006` are now the main
blockers to production-level closure.
