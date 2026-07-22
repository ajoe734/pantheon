# MGMT-GAP-006 Management Hosted Production Acceptance

Generated: 2026-07-01T19:13:13.570Z
FE: https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
BFF: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
Commit: 2129b56cbf86
Overall: **warn** (pass=true)

## Route coverage

- baseline routes (reproduces 2026-07-01 route-control-reaudit 93-route set): 93
- live nav links discovered on hosted cockpit: 63
- live nav links not in the 2026-07-01 baseline: 7 (/management/personas/alpha-trader, /management/personas/risk-guard, /management/personas/fx-scout, /management/personas/earnings-sniper, /management/personas/macro-watcher, /management/personas/crypto-scout, /management/personas/capital-steward)
- 2026-07-01 baseline nav links no longer present in live nav: 0
- total routes crawled (baseline/live-nav + live-id detail): 103
- total buttons/enabled/disabled: 1303/1203/100
- total links: 5775, inputs: 9

## Gate checks

| Status | Check | Note |
|---|---|---|
| pass | No route crashes, is blank, or fails to navigate. | count:0 |
| pass | No known alias direct-renders instead of redirecting to its canonical path. | count:0 |
| pass | No detail route shows raw undefined/NaN/Invalid Date. | count:0 |
| pass | No route claims seed fallback armed in strict-live mode. | count:0 |
| pass | No route shows a mock/demo success claim as production truth. | count:0 |
| pass | No CORS console errors on the hosted origin. | count:0 |
| pass | No render-crash console errors. | count:0 |
| pass | Session/RBAC: invalid session cannot read privileged management data. | Authenticated /bff/me returns operator identity.:pass; Invalid/no-role token is rejected by /bff/me (401/403).:pass; Privileged management read is not served under an invalid session (fails closed).:pass |
| pass | MGMT-LOAD-006/007 release-load-gate manifest reports result.pass=true. | result.pass:true overall:pass failures:0 missing:0 |
| warn | Write-CTA source scan: toast.success() calls are backed by a governed/receipt signal (soft gate). | ungoverned:22/34 (strict:false) |

## Failing / crashed / blank routes

None

## Alias direct-render failures

None

## Detail-honesty violations (undefined/NaN/Invalid Date)

None

## Seed-fallback-armed / mock-success claims

None

## Console errors by class (routes with >=1 hit)

| Route | CORS | Network | Render-crash | Benign |
|---|---:|---:|---:|---:|
| `/management/cockpit` | 0 | 4 | 0 | 0 |
| `/management/persona-fleet` | 0 | 4 | 0 | 0 |
| `/management/human-inbox` | 0 | 1 | 0 | 0 |
| `/management/trading-pulse` | 0 | 1 | 0 | 0 |
| `/management/evolution-journal` | 0 | 2 | 0 | 0 |
| `/management/evidence` | 0 | 1 | 0 | 0 |
| `/management/persona-intent` | 0 | 1 | 0 | 0 |
| `/management/portfolio-book` | 0 | 1 | 0 | 0 |
| `/management/persona-league` | 0 | 3 | 0 | 0 |
| `/management/quarterly-ranking` | 0 | 1 | 0 | 0 |
| `/management/performance-attribution` | 0 | 2 | 0 | 0 |
| `/management/readiness/ep5` | 0 | 1 | 0 | 0 |
| `/management/readiness/broker-live` | 0 | 1 | 0 | 0 |
| `/management/readiness/capital-binding-live` | 0 | 1 | 0 | 0 |
| `/management/readiness/bff-ha` | 0 | 1 | 0 | 0 |
| `/management/readiness/strict-publish` | 0 | 1 | 0 | 0 |
| `/management/strategies` | 0 | 1 | 0 | 0 |
| `/management/loops` | 0 | 1 | 0 | 0 |
| `/management/interventions` | 0 | 1 | 0 | 0 |
| `/management/approvals` | 0 | 1 | 0 | 0 |
| `/management/postmortems` | 0 | 1 | 0 | 0 |
| `/management/hooks` | 0 | 1 | 0 | 0 |
| `/management/channels` | 0 | 1 | 0 | 0 |
| `/management/audit` | 0 | 1 | 0 | 0 |
| `/management/loops/optimization` | 0 | 2 | 0 | 0 |
| `/management/loops/research` | 0 | 1 | 0 | 0 |
| `/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` | 0 | 1 | 0 | 0 |
| `/management/evidence/evref-demo-readiness-001` | 0 | 1 | 0 | 0 |
| `/management/evidence?ref_id=evref-demo-readiness-001` | 0 | 1 | 0 | 0 |
| `/management/persona-intent/trace-001` | 0 | 1 | 0 | 0 |
| `/management/strategies/stg_001` | 0 | 1 | 0 | 0 |
| `/management/personas/per_quant/onboarding` | 0 | 0 | 0 | 1 |
| `/management/capital/cp_alpha` | 0 | 0 | 0 | 1 |
| `/management/rebalance/rb_q2_2026` | 0 | 0 | 0 | 1 |
| `/management/evolution/ev_001` | 0 | 0 | 0 | 1 |
| `/management/deployments/dp_001` | 0 | 0 | 0 | 1 |
| `/management/incidents/in_021` | 0 | 0 | 0 | 1 |
| `/management/governance/ap_301` | 0 | 0 | 0 | 1 |
| `/management/tools/tl_market_data` | 0 | 0 | 0 | 1 |
| `/management/mcp/mcp_alpha` | 0 | 0 | 0 | 1 |
| `/management/mcp-tools/mt_001` | 0 | 0 | 0 | 1 |
| `/management/skills/sk_macro_brief` | 0 | 0 | 0 | 1 |
| `/management/channels/ch_slack_alerts` | 0 | 0 | 0 | 1 |
| `/management/one-ring` | 0 | 4 | 0 | 0 |
| `/management/overview` | 0 | 4 | 0 | 0 |
| `/management/command-center` | 0 | 4 | 0 | 0 |
| `/management/risk-center` | 0 | 4 | 0 | 0 |

## Live entity id resolution

| Entity | Endpoint | Status | Live id |
|---|---|---:|---|
| strategies | /bff/strategies | 0 | (none) |
| personas | /bff/personas | 0 | (none) |
| capital | /bff/capital-pools | 0 | (none) |
| ranking-formulas | /bff/ranking-formulas | 200 | (none) |
| rebalance | /bff/rebalances | 200 | (none) |
| evolution | /bff/evolution-programs | 200 | (none) |
| experiments | /bff/research-experiments | 200 | (none) |
| artifacts | /bff/artifacts | 200 | (none) |
| deployments | /bff/deployments | 200 | plan-rescue-0260513-06627c91 |
| incidents | /bff/incidents | 200 | inc-87c655c3e3c9 |
| tools | /bff/tools | 200 | (none) |
| mcp | /bff/mcp-servers | 200 | (none) |
| mcp-tools | /bff/mcp-tools | 200 | (none) |
| skills | /bff/skills | 200 | (none) |
| channels | /bff/channels | 200 | approval |

## Session / RBAC

| Status | Check | Note |
|---|---|---|
| pass | Authenticated /bff/me returns operator identity. | status:200 |
| pass | Invalid/no-role token is rejected by /bff/me (401/403). | status:403 |
| pass | Privileged management read is not served under an invalid session (fails closed). | status:403 |

## Write-CTA source-scan (toast.success without a nearby governed/receipt signal)

Scanned 168 files under `src/management`; 34 `toast.success(` call sites, 22 without a governed/receipt signal within 25 lines.

- `src/management/components/detail/AllocationLimitsManager.tsx:35` — `toast.success(t("phase13.capital.limits.queued"), {`
- `src/management/components/detail/ArtifactRollbackPanel.tsx:72` — `toast.success(t("artifact.rollback.done"), {`
- `src/management/components/detail/EvolutionFreezePanel.tsx:56` — `toast.success(t("phase13.evolution.freeze.queued"), {`
- `src/management/components/detail/FreezeUnfreezePanel.tsx:75` — `toast.success(t("phase13.capital.freeze.queued"), {`
- `src/management/components/detail/FreezeUnfreezePanel.tsx:90` — `toast.success(t("phase13.capital.freeze.queued"), {`
- `src/management/components/detail/MetricFreezeManager.tsx:52` — `toast.success(t("toast.actionQueued"), {`
- `src/management/components/detail/OverrideManager.tsx:40` — `toast.success(t("phase13.rebalance.override.queued"), {`
- `src/management/components/detail/PromotionPanel.tsx:51` — `toast.success(t("phase13.evolution.promotion.queued"), {`
- `src/management/components/detail/RebalanceWorkflowTab.tsx:43` — `toast.success(t("phase21.rebalance.workflow.advanced"), {`
- `src/management/components/detail/RebalanceWorkflowTab.tsx:61` — `toast.success(t("phase21.rebalance.workflow.rerun"), {`
- `src/management/components/detail/StrategySpecTab.tsx:28` — `toast.success(locked ? t("phase13.strategy.spec.unlocked") : t("phase13.strategy.spec.locked"), {`
- `src/management/pages/IncidentDetail.tsx:120` — `toast.success(t("incident.closed"), {`
- `src/management/pages/IncidentDetail.tsx:276` — `toast.success(t("incident.mitigation.logged"), {`
- `src/management/pages/IncidentDetail.tsx:301` — `toast.success(t("incident.postmortem.appended"), {`
- `src/management/pages/IncidentDetail.tsx:320` — `toast.success(t("incident.feedbackQueued"), {`
- `src/management/pages/IncidentDetail.tsx:343` — `toast.success(t("incident.constraint.created"), {`
- `src/management/pages/PersonaDetail.tsx:243` — `toast.success(t("toast.saved"), {`
- `src/management/pages/PersonaDetail.tsx:260` — `toast.success(t("toast.saved"), {`
- `src/management/pages/PersonaOnboarding.tsx:89` — `toast.success(`Step ${n} ${t("persona.onboarding.stageStatus.done")}`, {`
- `src/management/pages/StrategyDetail.tsx:129` — `toast.success(t("strategy.sweep.queued"), {`
- `src/management/pages/StrategyDetail.tsx:366` — `toast.success(t("toast.incidentAdvanced", { id: r.id, status }), {`
- `src/management/pages/StrategyDetail.tsx:378` — `toast.success(t("incident.postmortem.appended"), {`

## Load / release gate (MGMT-LOAD-006/007)

- manifest: /tmp/pantheon-worker-worktrees/pantheon/mgmt-gap-006/docs/04/pantheon_management_console_load_gap_2026-07-01/archive/release-load-gate-2026-07-01.json
- status: pass
- note: result.pass:true overall:pass failures:0 missing:0

## Notes

- Real writes were not performed (`--click-write-ctas` not enabled: false); write-CTA mock-success risk is assessed via the source scan above plus the live `mock_write_success` text pattern, per the task's non-scope note.
- Fixture-id detail routes (e.g. `stg_001`, `cp_alpha`) intentionally reuse the 2026-07-01 crawl's ids; a 404/not-found state for those ids on a strict-live BFF is expected and desired (no seed-id leakage), not a failure by itself — only raw undefined/NaN/blank rendering fails this gate.