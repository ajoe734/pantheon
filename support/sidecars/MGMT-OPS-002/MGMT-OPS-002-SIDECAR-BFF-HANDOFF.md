# MGMT-OPS-002 BFF And Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `MGMT-OPS-002` |
| Parent title | Normalize management frontend adapters and data-confidence UI rules |
| Parent owner / reviewer | Parent owner decides absorption; this sidecar does not approve parent scope |
| Sidecar task | `MGMT-OPS-002-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex2` / `Codex` |
| Helper kind | `bff_handoff_packet` |
| Updated | `2026-07-08T06:51:51Z` |
| Mutates canonical | `false` |

This is a support artifact only. It packages the MGMT-OPS-001 BFF read-model
surface into a Wave 1 frontend handoff for MGMT-OPS-002. It does not edit L1
canonical truth, BFF/runtime implementation, frontend code, registries,
governance logic, or action policy.

---

## 1. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 status coordinates work; support packets do not override L1/L2 product truth. |
| `.orchestrator/task-briefs/mgmt_ops_002_sidecar_bff_handoff.md` | Sidecar scope is BFF query gap, operator journey, and frontend handoff material only. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-OPS-002-SIDECAR-BFF-HANDOFF` | Task is `in_progress`, owner `Codex2`, reviewer `Codex`, artifact path is this file. |
| `.orchestrator/skills/worker-anchor-commit.md` | Meaningful support/document changes must become durable through scoped worker commits. |
| `.orchestrator/skills/task-closeout-finalization.md` | Final `done` is owner-only after review approval and merged task PR; this packet is not a done transition. |
| `support/sidecars/MGMT-OPS-001/MGMT-OPS-001-SIDECAR-BFF-HANDOFF.md` | Prior sidecar established the source-confidence handoff framing and frontend gap matrix. |
| `services/control-plane/bff/operations_read_model.py` | Defines `DataConfidence`, `SourceState`, identity, performance, source, diagnostic, and finite metric helpers. |
| `services/control-plane/bff/main.py` | Publishes `GET /bff/management/operations-read-model/{persona_id}` with `OperationsReadModelEnvelope`. |
| `services/control-plane/bff/test_bff_mgmt_ops_001_operations_read_model_contract.py` | Covers OpenAPI envelope, finite metrics, formal/partial/fallback/degraded/unavailable confidence, 404, and focus persona fallback. |
| `services/control-plane/bff/BFF_API_CONTRACT.md` | Documents the per-persona operations read-model route and confidence semantics. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` | Current frontend path registry still lacks an `operations-read-model` helper. |
| `/home/lupin/code/execute-plans/src/management/pages/oversight/PerformanceAttribution.tsx` | Current page derives fallback locally from Persona Fleet and uses `NaN` sentinel rows for missing holdings. |
| `/home/lupin/code/execute-plans/src/management/pages/oversight/personaFleetLinks.ts` | Current fleet performance links preserve `dimension=persona` and `persona`, but not runtime, period, or source-confidence context. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.

---

## 2. Current BFF Surface For MGMT-OPS-002

MGMT-OPS-001 supplies the read-only route that MGMT-OPS-002 should consume:

```text
GET /bff/management/operations-read-model/{persona_id}?period=latest
```

The `200` response is an `OperationsReadModelEnvelope`:

- `data.identity`: persona, stage, runtime, paper ledger, capital pool,
  sleeve, strategy, artifact, broker, period, and `as_of` context;
- `data.data_confidence`: `formal`, `partial`, `fallback`, `degraded`, or
  `unavailable`;
- `data.performance`: finite numbers or `null`, never `nan`/`inf`;
- `data.sources[]`: source name, status, row count, freshness/error/coverage;
- `data.diagnostics[]`: explicit missing joins and degraded source reasons;
- `meta`: BFF read-surface metadata.

Unknown personas return a typed `404` with `RESOURCE_NOT_FOUND`; the UI should
render a stale-link/not-found state instead of fabricating an empty row.

---

## 3. Focus Persona Gap

Focus persona: `persona-20260528-04688755` (`Crypto-Alt-Hunter`).

Observed contract behavior from the BFF tests:

| Field | Expected value |
|---|---|
| `data.identity.persona_id` | `persona-20260528-04688755` |
| `data.identity.persona_label` | `Crypto-Alt-Hunter` |
| `data.identity.runtime_ids` | non-empty; runtime identity survives without formal attribution |
| `data.identity.paper_ledger_ids` | `paper-ledger-persona-20260528-04688755` |
| `data.data_confidence` | `fallback` |
| `performance_attribution.source_status` | `unavailable` |
| `portfolio_holdings.source_status` | `unavailable` |
| `persona_fleet_summary.source_status` | `ok` |
| `data.performance.pnl` | `48000.0` |
| `data.performance.sharpe` | `1.76` |
| `data.performance.drawdown_pct` | `0.064` |

Required diagnostic codes:

- `MISSING_ATTRIBUTION_MATCH`
- `MISSING_HOLDINGS_MATCH`
- `FORMAL_ATTRIBUTION_MISSING_USING_FLEET_FALLBACK`

MGMT-OPS-002 should remove page-local ambiguity here: fallback metrics are
valid runtime summary evidence, but they are not formal attribution or holdings
evidence.

---

## 4. BFF Query Gap Matrix

| Need | Current state | MGMT-OPS-002 handoff |
|---|---|---|
| Single-persona confidence | BFF has one route by `persona_id`. | Add a frontend path/helper and consume the BFF verdict at the adapter boundary. |
| Table-wide confidence | BFF route is per persona. | Avoid frontend N+1 calls for fleet/ranking tables; if table-wide badges are required, request a bounded list/batch BFF follow-up. |
| Focus persona fallback | BFF reports `fallback` plus diagnostics and finite metrics. | Performance Attribution must display fallback as fallback, not formal attribution, empty success, or zero-filled holdings. |
| Source coverage | BFF returns `sources[]`. | Render source status and coverage consistently across Persona Fleet, Portfolio Book, Attribution, League, Ranking, and Human Review. |
| Diagnostics | BFF returns `diagnostics[]`. | Surface diagnostics near the affected metrics before high-impact controls. |
| Missing/non-finite metrics | BFF normalizes non-finite values to `null`; frontend still has local `NaN` sentinel paths. | Shared formatters should render explicit empty/diagnostic states, never `NaN`, `undefined`, or fake `0`. |
| Drilldown context | Current fleet link keeps `dimension=persona` and `persona` only. | Carry persona, runtime, period, and source-confidence hint into attribution routes. |
| Governed actions | Operations read model is read-only. | Do not infer promote/pause/rebalance permission from confidence alone; action availability still comes from governed action/read-review surfaces. |

---

## 5. Frontend Adapter Rules

Add the route helper in `execute-plans/src/lib/bff-v1/paths.ts`:

```ts
mgmtOperationsReadModel: (personaId: string, period?: string) =>
  `${BASE}/management/operations-read-model/${enc(personaId)}${period ? `?period=${enc(period)}` : ""}`,
```

Add a typed adapter/read helper near existing management BFF adapters. It should
accept backend snake_case and expose frontend-friendly aliases without dropping
the raw fields:

| BFF field | Frontend alias | Rule |
|---|---|---|
| `identity.persona_id` | `identity.personaId` | Required row identity. |
| `identity.persona_label` | `identity.personaLabel` | Nullable display label. |
| `identity.runtime_ids` | `identity.runtimeIds` | Preserve array order and all ids. |
| `identity.paper_ledger_ids` | `identity.paperLedgerIds` | Do not infer live capital from paper ledger ids. |
| `identity.capital_pool_ids` | `identity.capitalPoolIds` | Nullable/empty means missing evidence, not zero exposure. |
| `identity.strategy_ids` | `identity.strategyIds` | Preserve raw ids for drilldown. |
| `identity.broker_ids` | `identity.brokerIds` | Evidence only; no broker mutation path. |
| `identity.period` | `identity.period` | Echo selected period. |
| `identity.as_of` | `identity.asOf` | Use as freshness timestamp. |
| `data_confidence` | `dataConfidence` | Primary visual authority; never recompute in browser. |
| `performance.drawdown_pct` | `performance.drawdownPct` | Fraction value; format as percent in UI. |
| `performance.performance_delta` | `performance.performanceDelta` | Nullable; no fallback zero. |
| `performance.source_contribution` | `performance.sourceContribution` | Nullable; no fallback zero. |
| `sources[].source_name` | `sources[].sourceName` | Stable badge/detail key. |
| `sources[].source_status` | `sources[].sourceStatus` | Map from backend `SourceState`. |
| `sources[].source_row_count` | `sources[].sourceRowCount` | Show count semantics explicitly. |
| `diagnostics[].code` | `diagnostics[].code` | Render as operator-visible evidence. |

Metric formatting rules:

- Accept numbers only when `Number.isFinite(value)` is true.
- Render `null`, `undefined`, `NaN`, `Infinity`, and absent fields as a
  placeholder plus diagnostic context when available.
- Format backend percentage fractions by multiplying by 100 only at display
  time.
- Treat missing `data_confidence` as unknown/degraded, not formal.

---

## 6. UI State Mapping

| `data_confidence` | Label | UI treatment | Action treatment |
|---|---|---|---|
| `formal` | Formal attribution | Positive badge; normal evidence panel. | Read-only confidence does not itself grant actions. |
| `partial` | Partial evidence | Warning badge; show missing optional evidence. | Require review context before high-impact actions. |
| `fallback` | Runtime fallback | Prominent warning/banner; show fallback source and diagnostics. | Lock or route high-impact actions to Human Review/data-quality triage. |
| `degraded` | Degraded telemetry | Error/warning banner; show degraded source. | Block safety-critical actions unless governed review explicitly permits. |
| `unavailable` | Data unavailable | Muted/error empty state with diagnostics. | Disable dependent actions; offer retry/review path. |

The UI should not hide source/diagnostic evidence inside a collapsed detail by
default when confidence is `fallback`, `degraded`, or `unavailable`.

---

## 7. Operator Journey Handoff

### Persona Fleet To Performance Attribution

Current fallback link shape:

```text
/management/performance-attribution?dimension=persona&persona={personaId}
```

Recommended MGMT-OPS-002 link shape:

```text
/management/performance-attribution?dimension=persona&persona={personaId}&runtime={runtimeId}&period={period}&source={dataConfidence}
```

Keep accepting the existing `persona` query parameter for compatibility. If the
frontend introduces `personaId`, map it to the same internal focus value and do
not break current route tests.

### Focus Persona Attribution

1. Operator selects `persona-20260528-04688755` from Persona Fleet.
2. Attribution route keeps persona, runtime, period, and confidence hint.
3. Page reads `/bff/management/operations-read-model/{persona_id}`.
4. Page renders `Runtime fallback` with the three required diagnostics.
5. Performance metrics render `$48,000`, `1.76`, and `6.40%` as fallback
   evidence.
6. Holdings section renders a missing-match diagnostic instead of `NaN`,
   `undefined`, blank success, or a zero-value holding row.

### Human Review

When confidence is not `formal`, Human Review packets should include:

- persona id, runtime ids, ledger/pool ids, period, and `as_of`;
- `data_confidence`;
- `sources[]` summarized by source name/status/count;
- `diagnostics[]` codes/messages;
- the proposed governed action and its separate authorization source.

---

## 8. Recommended Frontend Test Coverage

Add focused tests in `execute-plans` after the adapter is implemented:

| Test | Assertion |
|---|---|
| Path helper | `paths.mgmtOperationsReadModel("persona-x", "latest")` returns `/bff/management/operations-read-model/persona-x?period=latest`. |
| Adapter normalization | Snake_case identity/source fields produce camelCase aliases and preserve raw diagnostics. |
| Non-finite metrics | `null`, `NaN`, `Infinity`, and missing values render placeholders, not text `NaN`/`undefined` or fake zero. |
| Focus fallback | Mock focus persona BFF response with `fallback`; Attribution renders `Runtime fallback`, diagnostics, `$48,000`, `1.76`, and `6.40%`. |
| Drilldown context | Persona Fleet performance link carries persona, runtime, period, and source/confidence context while preserving current `persona` compatibility. |
| Action gating | Fallback/degraded/unavailable confidence does not expose high-impact action CTAs unless a governed review packet/action surface provides permission. |

---

## 9. Parent / Reviewer Checklist

For parent owner absorption:

- Use the MGMT-OPS-001 read model as the source of confidence truth instead of
  page-local heuristics.
- Keep the sidecar boundary: if table-wide confidence is needed, create a
  bounded BFF follow-up rather than front-end N+1 calls.
- Preserve existing frontend route compatibility while adding richer context.
- Do not route any new write/governance action through this read model.
- Carry diagnostics into Human Review and data-quality triage surfaces.

For sidecar reviewer `Codex`:

- Confirm this packet edits support material only.
- Confirm the BFF route, focus persona values, and diagnostic codes match the
  checked-in BFF tests.
- Confirm frontend gap claims match the current `execute-plans` files listed in
  Sources Read.
- Confirm the packet does not claim canonical, runtime, registry, or governance
  implementation changes.

---

## 10. Verification Notes

Source inspection performed:

- `git status -sb`, `git branch --show-current`, and `git remote -v`.
- `AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-OPS-002-SIDECAR-BFF-HANDOFF`.
- `services/control-plane/bff/operations_read_model.py`.
- `services/control-plane/bff/main.py` route implementation.
- `services/control-plane/bff/test_bff_mgmt_ops_001_operations_read_model_contract.py`.
- `services/control-plane/bff/BFF_API_CONTRACT.md`.
- `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts`.
- `/home/lupin/code/execute-plans/src/management/pages/oversight/PerformanceAttribution.tsx`.
- `/home/lupin/code/execute-plans/src/management/pages/oversight/personaFleetLinks.ts`.
- `/home/lupin/code/execute-plans/src/lib/bff-v1/managementConsoleReads.ts`.

Focused validation performed:

- `python3 -m pytest services/control-plane/bff/test_bff_mgmt_ops_001_operations_read_model_contract.py -q`
  -> `11 passed, 8 warnings`.

No runtime, registry, governance, BFF implementation, frontend implementation,
L1 canonical document, or live environment changes were made by this sidecar.
