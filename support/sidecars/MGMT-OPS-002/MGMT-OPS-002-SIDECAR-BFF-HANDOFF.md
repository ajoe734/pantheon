# MGMT-OPS-002 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `MGMT-OPS-002` — Normalize management frontend adapters and data-confidence UI rules
**Parent Owner**: Codex2
**Parent Reviewer**: Claude2
**Parent Status**: `todo`
**Sidecar Owner**: Antigravity
**Sidecar Reviewer**: Codex
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-07-08T04:28:20Z

> [!NOTE]
> This is a support artifact only. It does not modify L1 canonical truth, core contract schemas, or backend runtime governance implementations. It packages Wave 0 outputs (`MGMT-OPS-001`) into a Wave 1-ready frontend handoff packet.

---

## 1. What MGMT-OPS-002 Can Reuse Immediately

Wave 0 (`MGMT-OPS-001`) locked the BFF response models and read pathways. The frontend system (`execute-plans`) should consume these directly.

### 1.1 Autorun Read Model Endpoint
- **Route**: `GET /bff/management/operations-read-model/{persona_id}`
- **OpenAPI Type**: `OperationsReadModelEnvelope`
- **Behavior**: Retrieves a unified record containing identity, performance stats, data source states, and diagnostic codes for a specific persona. It handles missing database joins gracefully, returning diagnostics instead of failing or filtering out rows.

### 1.2 Data Confidence Hierarchy
`DataConfidence` is defined as a standard enum in the backend (`services/control-plane/bff/operations_read_model.py`):
- `formal`: Canonical data source has a matching row and all required joins are healthy.
- `partial`: Canonical source exists, but some optional joined evidence is missing.
- `fallback`: Core attribution/holdings are missing; summary was synthesized from fleet runtime metrics.
- `degraded`: Upstream database or service returned degraded/fixture coverage.
- `unavailable`: Data source did not respond or cannot produce the requested slice.

---

## 2. Focus Persona Identity Triage: `persona-20260528-04688755`

The focus persona `persona-20260528-04688755` (`Crypto-Alt-Hunter`) exposes a critical identity mismatch that the frontend adapter must handle gracefully.

### 2.1 The Mismatch
- **Persona Fleet View**: Resolves `persona-20260528-04688755` to runtime `runtime-crypto-paper` with summary stats (`pnl = $48,000`, `max_drawdown = 6.40%`).
- **Attribution View**: `runtime-crypto-paper` is linked to a seed default persona (`persona-crypto`). Querying formal attribution for `persona-20260528-04688755` returns nothing because of this mapping difference.

### 2.2 The Fallback Solution
Querying `GET /bff/management/operations-read-model/persona-20260528-04688755` yields:
- `data_confidence`: `"fallback"`
- `performance`: Stats populated from the Persona Fleet runtime summary (PnL: `48000.0`, drawdown: `0.064`).
- `diagnostics`: Surfaced explicit codes:
  - `MISSING_ATTRIBUTION_MATCH`
  - `MISSING_HOLDINGS_MATCH`
  - `FORMAL_ATTRIBUTION_MISSING_USING_FLEET_FALLBACK`

> [!IMPORTANT]
> The frontend adapter must inspect these diagnostics and render a prominent fallback banner rather than claiming the data represents formal attribution.

---

## 3. Frontend Data Adapter & Normalization Guidelines

The frontend code under `execute-plans:src/management` and `execute-plans:src/lib` must normalize property names, format missing values safely, and prevent `NaN`/`undefined` from leaking into operator screens.

### 3.1 Field Normalization Matrix
Translate `snake_case` BFF fields to frontend camelCase equivalents at the adapter boundary:

| BFF Property | Composed Frontend Property | Type | Description |
|---|---|---|---|
| `persona_id` | `personaId` | `string` | Unique identifier of the persona |
| `persona_label` | `personaLabel` | `string \| null` | Human-readable persona label |
| `runtime_ids` | `runtimeIds` | `string[]` | Linked runtime identifiers |
| `paper_ledger_ids` | `paperLedgerIds` | `string[]` | Linked paper ledger accounts |
| `capital_pool_ids` | `capitalPoolIds` | `string[]` | Linked live capital pool IDs |
| `sleeve_ids` | `sleeveIds` | `string[]` | Composed asset sleeve IDs |
| `strategy_ids` | `strategyIds` | `string[]` | Underlying strategy config IDs |
| `artifact_ids` | `artifactIds` | `string[]` | Registered artifact output IDs |
| `as_of` | `asOf` | `string` | ISO timestamp of last update |
| `pnl` | `pnl` | `number \| null` | Absolute profit and loss in USD |
| `pnl_pct` | `pnlPct` | `number \| null` | Percentage return relative to capital |
| `drawdown_pct` | `drawdownPct` | `number \| null` | Peak-to-trough drawdown fraction |
| `risk_pct` | `riskPct` | `number \| null` | Risk limit utilization fraction |
| `performance_delta` | `performanceDelta` | `number \| null` | Short-term performance difference |
| `source_contribution` | `sourceContribution` | `number \| null` | Weight of the specific source |
| `coverage_ratio` | `coverageRatio` | `number \| null` | Data ingestion coverage score |

### 3.2 Metric Sanitization and Display Formatting
Do not permit raw string coercion of missing numeric fields. If a value is non-finite (`NaN`, `Infinity`) or `null`, render an explicit state rather than `0` or `nan`.

#### Recommended Typescript Helper:
```typescript
interface FormatOptions {
  type: 'currency' | 'percent' | 'decimal';
  emptyPlaceholder?: string;
  decimals?: number;
}

export function formatSafeMetric(value: any, options: FormatOptions): string {
  const placeholder = options.emptyPlaceholder ?? '--';
  if (value === null || value === undefined) {
    return placeholder;
  }
  
  const num = Number(value);
  if (Number.isNaN(num) || !Number.isFinite(num)) {
    return placeholder;
  }

  const decimals = options.decimals ?? 2;

  switch (options.type) {
    case 'currency':
      return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }).format(num);
    case 'percent':
      // Backend percentages are stored as fractions (e.g. 0.182 for 18.2%)
      return `${(num * 100).toFixed(decimals)}%`;
    case 'decimal':
      return num.toFixed(decimals);
    default:
      return String(value);
  }
}
```

### 3.3 Visual State Tokens
Target UI pages must map the `data_confidence` enum to consistent CSS classes and labels:

| Confidence Level | Badge Style | Label | Action Permission |
|---|---|---|---|
| `formal` | Green Solid | `Formal Attribution` | Full operator actions allowed |
| `partial` | Yellow Outline | `Partial Evidence` | Review recommended before actions |
| `fallback` | Orange Border | `Runtime Fallback` | Operational actions locked; Triage first |
| `degraded` | Red Outline | `Degraded Telemetry` | Safety-critical actions block; Redirect to CLI |
| `unavailable` | Grey Solid | `Data Unavailable` | Disable actions; Display diagnostic info |

---

## 4. Operator Journey & Navigation Wiring

### 4.1 Preserving Context in Drilldowns
When navigating from the **Persona Fleet** list to **Performance Attribution**, the link must carry context parameters to avoid default selections resetting state.

- **Source Route**: `/management/persona-fleet`
- **Target Route**: `/management/performance-attribution`
- **Query Params**:
  - `dimension`: MUST equal `persona`
  - `personaId`: Current row's `persona_id` (e.g. `persona-20260528-04688755`)
  - `runtimeId`: First runtime in row's `runtime_ids` (e.g. `runtime-crypto-paper`)
  - `period`: Active duration filter (e.g. `latest`, `30d`)
  - `source`: Rows confidence level (`fallback` or `formal`)

### 4.2 Diagnostic Overlay for Fallbacks
If the target `Performance Attribution` page loads with `data_confidence === "fallback"`, the page should:
1. Render a **Data Confidence Alert Banner** at the top of the details panel.
2. Display the diagnostic codes (e.g. `MISSING_HOLDINGS_MATCH`) inside a collapsible diagnostic drawer.
3. Replace empty holding tables with a clear message: `"Holdings database has no record matching selected persona 'persona-20260528-04688755'. Rendering fallback metrics from runtime summary."`

---

## 5. Recommended E2E Test Scenarios

Verify the frontend adapter rules in `execute-plans:e2e`:

### 5.1 Diagnostic Redirection Test
1. Mock `GET /bff/management/operations-read-model/persona-20260528-04688755` to return a `fallback` payload with diagnostic codes.
2. Navigate to `/management/persona-fleet` and click the performance attribution link on the target persona row.
3. Assert that the URL contains the preserved query parameters.
4. Assert that the target page renders a `Runtime Fallback` alert and displays `$48,000` rather than `NaN` or an empty view.
5. Assert that no table row or metric displays `nan` or `undefined`.

---

## 6. Verification & Self-Audit Checklist

| Check | Status | Verification Command / Target |
|---|---|---|
| Support artifact only | ✅ PASS | Only `support/sidecars/MGMT-OPS-002/MGMT-OPS-002-SIDECAR-BFF-HANDOFF.md` created |
| No canonical changes | ✅ PASS | Referenced `OperationsReadModelEnvelope` and L1 architecture without edits |
| Matches actual implementation | ✅ PASS | Validated against `main.py` route and `operations_read_model.py` schema |
| Ready for review | ✅ PASS | Handed over to Codex for Downstream execution |

---

*Generated by Antigravity as a sidecar `bff_handoff_packet` helper for MGMT-OPS-002. This file is a support artifact and does not modify canonical truth.*
