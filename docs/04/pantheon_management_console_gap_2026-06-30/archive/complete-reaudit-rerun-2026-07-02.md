# Management Console Complete Re-Audit Rerun - 2026-07-02

| Field | Value |
|---|---|
| Status | Superseding rerun after the user challenged that the prior audit was still partial |
| Base | `origin/dev` at `56879cd152ddef67349239c39c2991fc7bdff1a5` |
| Worktree | `/tmp/pantheon-mgmt-full-reaudit-rerun-20260702` |
| Branch | `task/mgmt-full-reaudit-rerun-20260702` |
| Scope | Current repo management entrypoint, Vite runtime behavior, isolated `apps/management` screens, FastAPI route table, OpenAPI route table, frontend path/fetcher coverage, BFF read smoke, list-contract audit, focused validation |
| Correction | The previous re-audit still understated backend route count and did not clearly separate mounted UI, typed adapters, historical E2E path names, and BFF/API routes. This rerun treats those as separate layers. |

## Executive Finding

The current repo does **not** contain a large mounted Management Console with
many repetitive pages.

The current repo contains a very thin Management entrypoint plus a large BFF/API
surface:

- `execute-plans/src/entries/management-main.tsx` mounts only:
  - `LiveEvidenceManifestPanel`;
  - `LoopTruthPanel`;
  - `OodaPacketDrawer`.
- The Vite management app serves `/management.html`; direct visits to
  `/management/*` paths return blank 404 pages in local dev.
- `OodaPacketDrawer` is mounted, but there is no visible trigger in
  `management-main.tsx`, so it is not an operator-accessible workflow.
- `apps/management/src/screens/**` contains three isolated read-only widgets,
  but they are not imported by the active `execute-plans` Management app.
- FastAPI currently mounts 61 `/bff/management*` routes across 60 unique paths.
  That is the real large surface today.

Therefore the core problem is not "too many finished UI pages." The core
problem is a layer mismatch:

1. many BFF management capabilities exist;
2. many frontend path builders and typed DTOs exist;
3. some historical E2E files still name many `/management/*` product routes;
4. almost none of that is mounted as a current, routable, operator workflow.

## Evidence Run

### Frontend Runtime Probe

Commands:

```sh
cd execute-plans
npm ci
npm run dev:management -- --host 127.0.0.1
```

Vite selected `http://127.0.0.1:5175/` because `5174` was already in use.

Playwright route probe:

| Path group | Result |
|---|---|
| `/management.html` | HTTP 200, title `Pantheon Management Console`, visible text length 417 |
| `/management/control-room` | Blank 404, no headings, no buttons, no BFF requests |
| `/management/strategies` | Blank 404, no headings, no buttons, no BFF requests |
| `/management/sentinel` | Blank 404, no headings, no buttons, no BFF requests |
| `/management/interventions` | Blank 404, no headings, no buttons, no BFF requests |
| `/management/evidence` | Blank 404, no headings, no buttons, no BFF requests |
| `/management/persona-league` | Blank 404, no headings, no buttons, no BFF requests |
| `/management/portfolio-book` | Blank 404, no headings, no buttons, no BFF requests |
| `/management/readiness/broker-live` | Blank 404, no headings, no buttons, no BFF requests |
| `/management/nl/ask` | Blank 404, no headings, no buttons, no BFF requests |
| `/management/ai/conversations` | Blank 404, no headings, no buttons, no BFF requests |

Visible `/management.html` text sample:

```text
BFF Live Evidence unavailable Source: mock Manifests: 0 Evidence: 0/0
Loop Truth unavailable Source: mock_seed Truth: seed fixture Loops: 0 Live proof: 0 Degraded: 0
```

Visible headings on `/management.html`:

- `BFF Live Evidence`
- `No live evidence manifests`
- `Loop Truth`
- `No loop health records`

Visible controls:

- four `Refresh` buttons;
- no navigation links;
- no forms;
- no Management AI prompt box;
- no visible OODA drawer trigger.

### Current Mounted UI

| Component | Mounted? | Direct data dependency | Operational status |
|---|---:|---|---|
| `LiveEvidenceManifestPanel` | Yes | `managementClient.evidenceExplorer.list({ page_size: 25 })` -> `/bff/management/evidence` | Keep and deepen; current mock mode renders empty evidence. |
| `LoopTruthPanel` | Yes | `managementClient.loopHealth.list()` -> `/bff/v5/loop-health` | Keep and deepen; current mock mode renders no loop records. |
| `OodaPacketDrawer` | Technically mounted | `managementClient.oodaPackets.get(id)` -> `/bff/ooda/packets/{id}` | Adjust: add a real trigger or remove from shell until a workflow can open it. |

### Isolated `apps/management` Widgets

| Widget | Current evidence | Decision |
|---|---|---|
| `HumanGateStatus` | Props-only read component; tests import from `apps/management`; no `execute-plans` import found. | Migrate into Human Inbox / Governance workbench, or archive/delete as orphan. |
| `BrokerGoNoGoDashboard` | Props-only read component; no active shell import. | Migrate into Readiness suite, or archive/delete as orphan. |
| `CapitalBindingGoNoGoDashboard` | Props-only read component; no active shell import. | Migrate into Readiness suite, or archive/delete as orphan. |

These widgets are not bad code, but they are not a live app. Leaving them under a
second `apps/management` tree creates false confidence.

## BFF/API Inventory

Direct FastAPI route-table extraction found:

| Count | Meaning |
|---:|---|
| 61 | `/bff/management*` route entries |
| 60 | unique `/bff/management*` paths |
| 55 | GET route entries |
| 5 | POST route entries |
| 1 | OPTIONS route entry for performance attribution CORS |

OpenAPI exposes the same 60 unique management paths.

Important correction against the prior document: it said 55 unique routes. The
current route table has 61 route entries and 60 unique paths. The difference is
from included routers, AI/NL routes, strategy seed command routes, and the
explicit OPTIONS route.

### Authenticated BFF Smoke

Test mode:

```sh
PANTHEON_BFF_AUTH_STUB=true
PANTHEON_BFF_AUTH_MODE=permissive
Authorization: Bearer op-reaudit:operator,reviewer,approver:mfa
```

Read smoke result across 55 GET entries:

| Status | Count | Interpretation |
|---|---:|---|
| `200` | 51 | Aggregate/list/readiness routes returned payloads |
| `404` | 4 | Expected sample-detail misses for non-existent IDs |

Safe POST probes:

| Route | Result | Note |
|---|---|---|
| `POST /bff/management/nl/ask` | `202 Accepted` | Created an in-memory audit smoke conversation. |
| `POST /bff/management/nl/ask/stream` | `200 text/event-stream` | Stream emitted metadata, then `OPENCLAW_ADAPTER_URL_NOT_CONFIGURED`, then `[DONE]`. |

Write-command POST routes under `/bff/management/strategy-seeds/{seed_id}/...`
were not executed against a real store during this audit because they are
mutating command surfaces. They need command UI, idempotency, receipt, audit, and
readback coverage before being exposed to operators.

## Frontend Coverage Matrix

Route-to-frontend extraction found:

| Layer | Count / list |
|---|---|
| BFF management routes | 61 entries / 60 unique paths |
| `paths.ts` management path builders | 46 |
| `bff-v1/management.ts` fetch functions | 40 |
| `managementClient` management path refs | 14 |
| `managementAssistant` path refs | 2 |
| Actual mounted component client refs | `evidenceExplorer`, `loopHealth`, `oodaPackets` |

`managementClient` wraps these management BFF surfaces:

- `managementEvidence`
- `managementEvolutionJournal`
- `managementFleet`
- `managementHumanInbox`
- `managementHumanInboxItem`
- `managementPersonaFleet`
- `managementPersonaIntent`
- `managementReadinessBffHa`
- `managementReadinessBrokerLive`
- `managementReadinessCapitalBindingLive`
- `managementReadinessEp5`
- `managementReadinessStrictPublish`
- `managementTradingPulse`
- `managementTradingPulseRankings`

`managementAssistant` wraps:

- `managementNlAsk`
- `managementAiConversation`

These BFF paths have no current frontend path builder/client wrapper and should
not be treated as implemented UI:

- `/bff/management/ai/attachments/{attachment_id}`
- `/bff/management/ai/audit`
- `/bff/management/ai/conversations`
- `/bff/management/consult-rules`
- `/bff/management/data-sources`
- `/bff/management/memory-governance`
- `/bff/management/nl/ask/stream`
- `/bff/management/permissions`
- `/bff/management/persona-league/{persona_id}`
- `/bff/management/shell-summary`
- `/bff/management/strategy-seeds`
- `/bff/management/strategy-seeds/{seed_id}`
- `/bff/management/strategy-seeds/{seed_id}/merge`
- `/bff/management/strategy-seeds/{seed_id}/review`
- `/bff/management/strategy-seeds/{seed_id}/submit-replication`

These BFF paths have low-level typed fetchers but no current mounted workflow:

- cockpit / board pack / governance ledger;
- strategy allocation / capital flow / risk radar / incident timeline;
- HIQ backlog / intervention stream;
- loop throughput;
- cost attribution;
- portfolio book family;
- persona league family;
- quarterly ranking family;
- performance attribution family;
- sentinel pulse.

## List Contract Audit

Command:

```sh
python3 scripts/audit_management_list_contract.py \
  --baseline docs/architecture/management-list-contract-baseline.json \
  --format summary
```

Result:

```text
issues=65 new=0 retired=0
```

Breakdown:

| Category | Count |
|---|---:|
| `camel-snake-duplicate` | 61 |
| `project-before-page` | 4 |

All 65 are P1. The remaining debt clusters are:

- Management AI/NL casing mirrors;
- strategy allocation rows;
- capital flow rows;
- risk radar rows;
- incident timeline rows;
- loop throughput rows;
- cost attribution rows;
- four project-before-page helpers.

This contract debt should be fixed before expanding UI screens, because mounted
pages would otherwise cement duplicated DTO shapes.

## What To Adjust

These are valid surfaces, but their current shape should be adjusted before more
UI is added:

| Surface | Adjustment |
|---|---|
| Management entrypoint | Replace the two-panel demo shell with a real cockpit shell, route map, and honest empty/degraded states. |
| Direct `/management/*` paths | Add real routing or redirect strategy. A management app that only works at `/management.html` is not acceptable for operator URLs. |
| OODA drawer | Add visible trigger and packet selection source, or remove it from the mounted shell until it is reachable. |
| `managementClient` | Keep, but expose canonical snake_case payloads only; stop normalizing camel/snake mirrors into UI assumptions. |
| Historical E2E route names | Reconcile or retire tests that refer to `/management/control-room`, `/management/strategies`, etc. if the current app no longer serves those routes. |
| `apps/management` widgets | Either migrate into `execute-plans` or archive/delete. Do not leave them as a silent second management app. |
| Readiness surfaces | Merge EP5, broker live, capital binding live, BFF HA, and strict publish into one Readiness suite instead of five unrelated pages. |
| Decision/ops queues | Merge Human Inbox, Intervention Stream, HIQ Backlog, Sentinel Pulse, Governance Ledger, Approvals, Alerts, and Incidents into one operator decision workbench with filters and receipts. |

## What To Delete / Hide

Delete or hide only things that create false product surface area:

| Candidate | Reason |
|---|---|
| Unmounted duplicate `apps/management` tree | Delete/archive if not migrated into the active app. It currently creates false confidence. |
| One-page-per-endpoint navigation | Do not build first-level pages for every BFF endpoint. It would recreate the repetition the user is worried about. |
| Historical direct-render aliases | Any legacy route alias should redirect to a canonical route, not render a second copy of a page. |
| Mock studio routes / seed-only labs | Hide until backed by real runner/receipt/readback. |
| Unreachable OODA drawer wiring | If no trigger is added, remove it from `management-main.tsx` to avoid claiming a mounted workflow. |
| Empty create/config CTA surfaces | Hide or downgrade until command APIs and audit receipts exist. |

## What Needs Deep Development

These should become real operator workflows, not thin endpoint pages:

| Workflow | BFF/API ingredients | Needed product depth |
|---|---|---|
| Evidence and loop truth | `/bff/management/evidence`, `/bff/v5/loop-health`, OODA packets | Timeline, filters, evidence drilldowns, live/source truth labels, replay links, degraded-state triage. |
| Management AI Ops | `/bff/management/nl/ask`, `/nl/ask/stream`, `/ai/audit`, `/ai/conversations`, `/ai/attachments` | Chat UI, provider status, audit log, streaming error handling, conversation list/detail, attachment viewer, trace filter, RBAC. |
| Decision workbench | human inbox, intervention stream, HIQ backlog, sentinel pulse, governance ledger, approvals/alerts/incidents | Unified queue, severity/status/persona/runtime filters, detail panel, command submission, idempotency, receipt, audit readback. |
| Performance review suite | portfolio book, persona league, quarterly ranking, performance attribution, cost attribution | One performance workspace with tabs/drilldowns, not separate first-level pages. |
| Readiness/go-live suite | readiness EP5/broker/capital/BFF HA/strict publish plus isolated widgets | Gate matrix, blocking reasons, evidence refs, human-gate signatures, no-side-effect guarantees. |
| Strategy/capital/risk analytics | strategy allocation, capital flow, risk radar, incident timeline, loop throughput | Cross-filtered analytics with route-owned page projections after list-contract cleanup. |
| Strategy seed review | strategy-seeds list/detail/review/merge/submit-replication | Governed command UI with explicit mutation boundaries, idempotency, approvals, and readback. |

## Recommended Build Order

1. Fix the remaining 65 P1 list-contract issues.
2. Add a real Management router and decide canonical URLs.
3. Promote the current evidence/loop truth shell into the first real workflow.
4. Add Management AI Ops as a separate workflow because backend is already
   substantial but UI is missing.
5. Migrate or delete the three `apps/management` widgets.
6. Build Decision Workbench around the existing queue/governance routes.
7. Build Performance Review as one suite, not one page per BFF endpoint.
8. Add a hosted route acceptance harness proving every visible nav route:
   - loads a nonblank page;
   - calls expected BFF endpoint(s);
   - has no silent mock fallback;
   - has explicit empty/degraded states;
   - exposes no write control without command receipt/readback.

## Validation

Commands run during this rerun:

```sh
npm ci
npm run build:management
npm run test -- --run src/management/components/live-evidence src/management/components/loop-truth src/management/components/ooda
python3 scripts/audit_management_list_contract.py --baseline docs/architecture/management-list-contract-baseline.json --format summary
python3 -m pytest services/control-plane/bff/test_management_list_contract_guardrail.py services/control-plane/bff/test_bff_management_delta_routes.py services/control-plane/bff/tests/test_bff_b6_management_nl_ask.py services/control-plane/bff/tests/test_management_nl_assistant_provider.py -q
```

Results:

| Check | Result |
|---|---|
| `npm ci` | Passed; npm audit reports 5 vulnerabilities. |
| `npm run build:management` | Passed; bundle `237.27 kB`, gzip `70.35 kB`. |
| Live Evidence / Loop Truth tests | Passed: 3 tests. |
| OODA drawer test suite | Failed before tests collect: `@/i18n` import cannot resolve. Existing drift; not caused by this doc audit. |
| List-contract audit | `issues=65 new=0 retired=0`. |
| BFF focused pytest | Passed: 91 tests, 12 FastAPI deprecation warnings. |

## Bottom Line

The prior audit was still too blended. This rerun separates the facts:

- Current mounted UI: only two visible panels and an unreachable drawer.
- Runtime route behavior: `/management/*` direct paths are blank 404 in the
  current Vite management dev app.
- Backend/API: large and mostly responsive, 61 route entries / 60 unique paths.
- Frontend adapters: partial and uneven.
- Isolated widgets: useful but orphaned.

The right next move is not to polish dozens of duplicate UI pages. The right next
move is to choose a smaller set of operator workflows, delete/archive orphaned
surfaces, fix contract debt, and then mount real routable pages with acceptance
proof.
