# LSP-005-V2 Strict Publish Audit

Status: FAIL
Task: `LSP-005-V2`
Checked: 2026-05-19T15:55:28Z
Deployment URL: `https://pantheon-dev.lovable.app/management`
Browser probe base URL: `https://pantheon-dev.lovable.app`

## Component Results

| Task | Component | Status | Evidence |
|---|---|---:|---|
| LSP-002-V2 | Browser /health and /bff/me probe | PASS | `/health` status `200`, `/bff/me` status `200`, mock-seed-free `True` |
| LSP-003-V2 | Hosted bundle hash capture | PASS | `2` JS bundle(s), `3` total asset(s) |
| LSP-004-V2 | Forbidden runtime path scan | FAIL | `84` forbidden signal(s), `3` scanned URL(s) |

## Bundle Hashes

| Asset URL | sha256 | bytes |
|---|---:|---:|
| `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | `4d2243c39e6f1aae730e0f102c8b36738f7d32c94d797bc9582e8c868d51ffab` | 2008993 |
| `https://pantheon-dev.lovable.app/~flock.js` | `a86e084b4f82709814be6c15fd6305daa783fda87ad95402da9a4d3a1dd6d748` | 21296 |

## Forbidden Runtime Signals

| Signal | Source URL | line | column | snippet |
|---|---|---:|---:|---|
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 716 | 51381 | `...apshotted to localStorage. Snapshot now to force-write, or reset to clear and reload from seed.",persistSnapshot:"Snapshot now",persistSnapshotDone:"Persistence snapshot writ...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 140434 | `...re="BFF-CONSOL-007",xre="2026-05-13T11:28:46Z",yre={brief_path:"execute-plans/src/lib/bff/seed.ts",actual_frontend_path:"../execute-plans/src/lib/bff-v1/seed.ts",seed_data_pa...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 140497 | `..."execute-plans/src/lib/bff/seed.ts",actual_frontend_path:"../execute-plans/src/lib/bff-v1/seed.ts",seed_data_path:"../execute-plans/src/mocks/seed.ts",notes:["The pantheon ch...` |
| /mocks/ | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 140542 | `...ontend_path:"../execute-plans/src/lib/bff-v1/seed.ts",seed_data_path:"../execute-plans/src/mocks/seed.ts",notes:["The pantheon checkout does not contain an execute-plans/ dir...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 140549 | `...path:"../execute-plans/src/lib/bff-v1/seed.ts",seed_data_path:"../execute-plans/src/mocks/seed.ts",notes:["The pantheon checkout does not contain an execute-plans/ directory....` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 140715 | `...rectory.","The sibling execute-plans checkout exposes the seed accessor at src/lib/bff-v1/seed.ts; src/lib/bff/client.ts and src/lib/bff-v1/legacy.ts bridge the current live/...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 141313 | `...ssor that should be replaced by the typed live surface or command client and removed from seed.ts after the migration task lands.",deferred:"No safe direct live replacement. ...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 145251 | `...e write facade/tests."},{name:"bff.strategies.list",category:"live_required",seed_source:"seed.strategies",live_routes:["GET /bff/strategies"],replacement:"managementClient.s...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 145543 | `...gement Console family."},{name:"bff.strategies.get",category:"live_required",seed_source:"seed.strategies",live_routes:["GET /bff/strategies/{id}"],replacement:"managementCli...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 145871 | `...age/audit related tabs."},{name:"bff.personas.list",category:"live_required",seed_source:"seed.personas",live_routes:["GET /bff/personas"],replacement:"managementClient.perso...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 146153 | `...nagement Console family."},{name:"bff.personas.get",category:"live_required",seed_source:"seed.personas",live_routes:["GET /bff/personas/{id}"],replacement:"managementClient....` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 146475 | `...mory from live BFF."},{name:"bff.capitalPools.list",category:"live_required",seed_source:"seed.capitalPools",live_routes:["GET /bff/capital-pools"],replacement:"managementCli...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 146769 | `...ck A fixture family."},{name:"bff.capitalPools.get",category:"live_required",seed_source:"seed.capitalPools",live_routes:["GET /bff/capital-pools/{id}"],replacement:"manageme...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 147090 | `... in strict live."},{name:"bff.rankingFormulas.list",category:"live_required",seed_source:"seed.rankingFormulas",live_routes:["GET /bff/ranking-formulas"],replacement:"managem...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 147420 | `...atibility routes."},{name:"bff.rankingFormulas.get",category:"live_required",seed_source:"seed.rankingFormulas",live_routes:["GET /bff/ranking-formulas/{id}"],replacement:"ma...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 147707 | `... live wiring surface."},{name:"bff.rebalances.list",category:"live_required",seed_source:"seed.rebalances",live_routes:["GET /bff/rebalances"],replacement:"managementClient.r...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 147997 | `...vernance queue family."},{name:"bff.rebalances.get",category:"live_required",seed_source:"seed.rebalances",live_routes:["GET /bff/rebalances/{id}"],replacement:"managementCli...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 148290 | `...nce live transcript."},{name:"bff.deployments.list",category:"live_required",seed_source:"seed.deployments",live_routes:["GET /bff/deployments"],replacement:"managementClient...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 148578 | `...ack A fixture family."},{name:"bff.deployments.get",category:"live_required",seed_source:"seed.deployments",live_routes:["GET /bff/deployments/{id}"],replacement:"managementC...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 148885 | `...and approval pointers."},{name:"bff.evolution.list",category:"live_required",seed_source:"seed.evolutionPrograms",live_routes:["GET /bff/evolution-programs"],replacement:"man...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 149180 | `..."Pack B fixture family."},{name:"bff.evolution.get",category:"live_required",seed_source:"seed.evolutionPrograms",live_routes:["GET /bff/evolution-programs/{id}"],replacement...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 149484 | `...B owns live transcript."},{name:"bff.research.list",category:"live_required",seed_source:"seed.researchExperiments",live_routes:["GET /bff/research-experiments"],replacement:...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 149834 | `...es research-experiments."},{name:"bff.research.get",category:"live_required",seed_source:"seed.researchExperiments",live_routes:["GET /bff/research-experiments/{id}"],replace...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 150142 | `...ink analysis evidence."},{name:"bff.artifacts.list",category:"live_required",seed_source:"seed.artifacts",live_routes:["GET /bff/artifacts"],replacement:"managementClient.art...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 150420 | `..."Pack B fixture family."},{name:"bff.artifacts.get",category:"live_required",seed_source:"seed.artifacts",live_routes:["GET /bff/artifacts/{id}"],replacement:"managementClien...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 150696 | `...ail should include lineage."},{name:"bff.jobs.list",category:"live_required",seed_source:"seed.jobs",live_routes:["GET /bff/jobs"],replacement:"managementClient.jobs.list or ...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 150877 | `...obs",follow_up_tasks:["BFF-CONSOL-018","BFF-CONSOL-025"],priority:"P0",notes:"There is no seed.ts jobs.get; managementClient has a placeholder detail gap owned by BFF-CONSOL-...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 151032 | `...wned by BFF-CONSOL-018."},{name:"bff.runtimes.list",category:"live_required",seed_source:"seed.runtimes",live_routes:["GET /bff/runtimes"],replacement:"managementClient.runti...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 151352 | `...sed for production live."},{name:"bff.runtimes.get",category:"live_required",seed_source:"seed.runtimes",live_routes:["GET /bff/runtimes/{id}"],replacement:"managementClient....` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 151632 | `...e A owns live transcript."},{name:"bff.alerts.list",category:"live_required",seed_source:"seed.alerts",live_routes:["GET /bff/alerts"],replacement:"managementClient.alerts.li...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 151895 | `...es:"Pack C fixture family."},{name:"bff.alerts.get",category:"live_required",seed_source:"seed.alerts",live_routes:["GET /bff/alerts/{id}"],replacement:"managementClient.aler...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 152177 | `.../bff/risk/alerts/{id}."},{name:"bff.incidents.list",category:"live_required",seed_source:"seed.incidents",live_routes:["GET /bff/incidents"],replacement:"managementClient.inc...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 152455 | `..."Pack C fixture family."},{name:"bff.incidents.get",category:"live_required",seed_source:"seed.incidents",live_routes:["GET /bff/incidents/{id}"],replacement:"managementClien...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 152770 | `...lback-option evidence."},{name:"bff.approvals.list",category:"live_required",seed_source:"seed.approvals",live_routes:["GET /bff/approvals"],replacement:"managementClient.app...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 153048 | `..."Pack C fixture family."},{name:"bff.approvals.get",category:"live_required",seed_source:"seed.approvals",live_routes:["GET /bff/approvals/{id}"],replacement:"managementClien...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 153340 | `... deployment-link evidence."},{name:"bff.audit.list",category:"live_required",seed_source:"seed.auditEvents",live_routes:["GET /bff/audit"],replacement:"managementClient.audit...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 153654 | `...drawer should be disabled."},{name:"bff.tools.list",category:"live_required",seed_source:"seed.tools",live_routes:["GET /bff/tools"],replacement:"managementClient.tools.list ...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 153912 | `...tes:"Pack C fixture family."},{name:"bff.tools.get",category:"live_required",seed_source:"seed.tools",live_routes:["GET /bff/tools/{id}"],replacement:"managementClient.tools....` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 154190 | `... seed in strict live."},{name:"bff.mcpServers.list",category:"live_required",seed_source:"seed.mcpServers",live_routes:["GET /bff/mcp-servers"],replacement:"managementClient....` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 154486 | `...mcp-final alias route."},{name:"bff.mcpServers.get",category:"live_required",seed_source:"seed.mcpServers",live_routes:["GET /bff/mcp-servers/{id}"],replacement:"managementCl...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 154780 | `...se seed in strict live."},{name:"bff.mcpTools.list",category:"live_required",seed_source:"seed.mcpTools",live_routes:["GET /bff/mcp-tools"],replacement:"managementClient.mcpT...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 155066 | `...s mcp-final alias route."},{name:"bff.mcpTools.get",category:"live_required",seed_source:"seed.mcpTools",live_routes:["GET /bff/mcp-tools/{id}"],replacement:"managementClient...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 155354 | `... use seed in strict live."},{name:"bff.skills.list",category:"live_required",seed_source:"seed.skills",live_routes:["GET /bff/skills"],replacement:"managementClient.skills.li...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 155617 | `...es:"Pack C fixture family."},{name:"bff.skills.get",category:"live_required",seed_source:"seed.skills",live_routes:["GET /bff/skills/{id}"],replacement:"managementClient.skil...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 155904 | `...se seed in strict live."},{name:"bff.channels.list",category:"live_required",seed_source:"seed.channels",live_routes:["GET /bff/channels"],replacement:"managementClient.chann...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 156207 | `...E-topic alignment input."},{name:"bff.channels.get",category:"live_required",seed_source:"seed.channels",live_routes:["GET /bff/channels/{id}"],replacement:"managementClient....` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 156500 | `...ed in strict live."},{name:"bff.routePolicies.list",category:"live_required",seed_source:"seed.routePolicies",live_routes:["GET /bff/personas","GET /bff/personas/{id}/route-p...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 156894 | `...-policy BFF routes."},{name:"bff.routePolicies.get",category:"live_required",seed_source:"seed.routePolicies",live_routes:["GET /bff/personas","GET /bff/personas/{id}/route-p...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 157308 | `...policy DTOs."},{name:"bff.routePolicies.forPersona",category:"live_required",seed_source:"seed.routePolicies",live_routes:["GET /bff/personas/{id}/route-policy"],replacement:...` |
| seed. | `https://pantheon-dev.lovable.app/assets/index-DdbnZ6Bd.js` | 760 | 157640 | `...t backend replacement."},{name:"bff.policyVersions.list",category:"deferred",seed_source:"seed.policyVersions",live_routes:[],replacement:"Add version history to route-policy...` |
| _truncated_ | _see JSON for remaining 34 signal(s)_ |  |  |  |

## Errors

- LSP-004-V2 failed: forbidden_signals=84 errors=0

## Packet Notes

- This packet preserves the raw LSP-002, LSP-003, and LSP-004 component payloads in the JSON artifact.
- The final verdict is fail-closed: every component must pass for `passed` to be true.
