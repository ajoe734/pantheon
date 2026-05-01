# P2-MARKETDATA-CREDENTIAL-SMOKE-001 Acceptance Packet (Sidecar)

**Parent Task:** `P2-MARKETDATA-CREDENTIAL-SMOKE-001` — Market-data provider credentialed read smoke
**Parent Owner:** Codex2
**Parent Reviewer:** Codex
**Parent Status:** `in_progress`
**Sidecar Task ID:** `P2-MARKETDATA-CREDENTIAL-SMOKE-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner:** Claude2
**Sidecar Reviewer:** Codex2
**Helper Kind:** `acceptance_packet`
**Generated:** 2026-05-01T17:00:00Z

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime / registry / governance implementations.
> It packages the dependency state, acceptance checklist, and per-provider evidence map for `P2-MARKETDATA-CREDENTIAL-SMOKE-001`.

---

## 1. Dependency Map

### 1.1 Formal Task Dependencies

| Dependency | Task ID | Status | What P2-MARKETDATA-CREDENTIAL-SMOKE-001 relies on |
|---|---|---|---|
| Governed data-source secrets, env, and smoke automation | `APP-003-DATASOURCE-OPS-001` | **done** | Credential provisioning, secret management path, and smoke automation harness are ready for use |
| Research OSS production data posture and activation | `P2-OSS-ACTIVATE-001` | **done** | OSS data connectors, data plane ingestion posture, and provider integration posture confirmed |

Both dependencies are `done`. The parent task may proceed without blockers from upstream.

### 1.2 Canonical Policy Sources P2-MARKETDATA-CREDENTIAL-SMOKE-001 Must Respect

| Source | Locked constraint |
|---|---|
| `DATA_SOURCE_SCOPE_MATRIX.md` | Canonical vendor fill per market and source class; providers listed below are the governed working default |
| `PAPER_CANARY_LIVE_POLICY.md` | External data source production ingestion requires: durable storage, entitlement, license/PIT, rate limit, audit, and no-direct-order-routing gate |
| `DATA_SOURCE_SCOPE_MATRIX.md` §1.1 rule 6 | Only broker/order-capable paths require live fail-closed controls — market-data-only reads do not require live fail-closed, but must never route to order placement |
| `DATA_SOURCE_SCOPE_MATRIX.md` §1.1 rule 5 | No external data source may route directly to order-capable execution |
| `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` | Credentialed read-only smoke with real auth/session/readback/rate-limit evidence reaches at most `EP2`; a failing or credential-incomplete smoke cannot be promoted beyond `EP2` |

### 1.3 Downstream Tasks Waiting On P2-MARKETDATA-CREDENTIAL-SMOKE-001

| Downstream | Why it depends on this task |
|---|---|
| `P2-SOURCE-SEARCH-CREDENTIAL-SMOKE-001` (todo) | Source/search credential smoke gates on OSS activation; aligned with the same Wave 8 wave as market-data smoke |
| `P2-BROKER-SANDBOX-ORDER-001` (dependency for IBKR/Shioaji/Kraken order activation) | Order-capable endpoints must remain disabled until broker sandbox order smoke passes; market-data read smoke proves the read-only lane independently of the order path |

---

## 2. Provider Scope and Source Class Map

The following is the governed provider fill from `DATA_SOURCE_SCOPE_MATRIX.md §1.2` that P2-MARKETDATA-CREDENTIAL-SMOKE-001 must cover:

| Provider | Market | Source Class | Lane | Smoke Mode |
|---|---|---|---|---|
| Massive / Polygon | US equities/derivatives | `research_grade` | Read-only API | Credentialed data read; auth + readback evidence |
| IBKR market data | US equities/derivatives | `broker_execution` fallback | Quote / read-only | Read-only quote lane only; order endpoint must remain disabled |
| TWSE OpenAPI | Taiwan equities/derivatives | `official_reference` | Public / key-authenticated read | Auth + EOD readback evidence |
| TPEx E-Data | Taiwan equities/derivatives | `official_reference` | Public / key-authenticated read | Auth + EOD readback evidence |
| MOPS | Taiwan equities/derivatives | `official_reference` | Public disclosure read | Session/readback evidence |
| TEJ API | Taiwan equities/derivatives | `research_grade` | Key-authenticated read | Auth + readback + rate-limit evidence |
| Kraken (market data) | Cryptocurrency | `broker_execution` / `research_grade` | Venue market data read | Read-only market data lane; order endpoint must remain disabled |
| CoinGecko | Cryptocurrency | `research_grade` reference | Public / key-authenticated read | Auth + readback evidence |
| Shioaji (quote lane) | Taiwan equities/derivatives | `broker_execution` | Quote / simulation read-only | Read-only quote lane only; order endpoint must remain disabled |

---

## 3. Acceptance Checklist

Parent task acceptance criteria from `ai-status.json`:
1. Credentialed read-only smoke runs or records explicit unavailable-credential evidence for each governed market-data provider
2. Smoke captures auth/session/readback/rate-limit/provenance evidence without raw secrets
3. IBKR/Shioaji/Kraken order-capable endpoints remain disabled unless P2-BROKER-SANDBOX-ORDER-001 acceptance passes

This sidecar expands those into a per-provider and cross-cutting reviewable checklist:

### 3.1 Cross-Cutting Gate Items

| # | Gate | Status | What "done" looks like |
|---|---|---|---|
| G1 | No raw credential exposure | OPEN | All evidence files use redacted placeholders (e.g., `***`, `[REDACTED]`) for API keys, tokens, and passwords |
| G2 | No order/capital side effect | OPEN | Smoke log confirms zero order placement, fill, cancel, or capital-movement API calls across all providers |
| G3 | Order endpoints disabled for IBKR/Shioaji/Kraken | OPEN | Config or code evidence shows order-capable endpoint is not called or is disabled at the connection layer |
| G4 | Evidence artifacts saved | OPEN | Evidence saved to `support/sidecars/P2-MARKETDATA-CREDENTIAL-SMOKE-001/evidence/` or referenced from the parent task artifact directory |
| G5 | Missing-credential case documented | OPEN | Where credentials are unavailable, an explicit `unavailable-credential` evidence note is recorded (not a silent skip) |

### 3.2 Per-Provider Checklist

#### Massive / Polygon (US research_grade)

| # | Item | Status | Evidence form |
|---|---|---|---|
| MP1 | Auth succeeds or credential-unavailable recorded | OPEN | API response status or error type logged |
| MP2 | Readback smoke: retrieve at least one ticker or OHLCV slice | OPEN | Response shape and record count logged (no raw price data required) |
| MP3 | Rate-limit behavior noted | OPEN | Observed rate-limit headers or documented vendor quota |
| MP4 | Provenance tag in ingestion record | OPEN | `source_class: research_grade`, `provider: polygon` in ingest metadata |

#### IBKR market data (US broker_execution fallback)

| # | Item | Status | Evidence form |
|---|---|---|---|
| IB1 | Read-only quote session established or credential-unavailable recorded | OPEN | Session establishment log or explicit unavailable note |
| IB2 | Readback smoke: at least one quote received | OPEN | Quote response shape logged (symbol + bid/ask shape, no raw value required) |
| IB3 | Order API not invoked | OPEN | Connection log shows no `placeOrder`, `cancelOrder`, `reqIds` call in order-capable mode |
| IB4 | Paper/canary account context only | OPEN | Account type confirmed as paper or data-only subscription |

#### TWSE OpenAPI (Taiwan official_reference)

| # | Item | Status | Evidence form |
|---|---|---|---|
| TW1 | Auth or public session established | OPEN | HTTP 200 or API key validation response |
| TW2 | EOD or security master readback | OPEN | At least one listed security or calendar record retrieved |
| TW3 | Provenance tag | OPEN | `source_class: official_reference`, `provider: twse` |

#### TPEx E-Data (Taiwan official_reference)

| # | Item | Status | Evidence form |
|---|---|---|---|
| TP1 | Auth or public session established | OPEN | HTTP 200 or API key validation response |
| TP2 | OTC EOD or security master readback | OPEN | At least one OTC-listed security or reference record retrieved |
| TP3 | Provenance tag | OPEN | `source_class: official_reference`, `provider: tpex` |

#### MOPS (Taiwan official_reference)

| # | Item | Status | Evidence form |
|---|---|---|---|
| MO1 | Public session and readback | OPEN | At least one disclosure or filing record retrieved |
| MO2 | Provenance tag | OPEN | `source_class: official_reference`, `provider: mops` |

#### TEJ API (Taiwan research_grade)

| # | Item | Status | Evidence form |
|---|---|---|---|
| TE1 | Auth succeeds or credential-unavailable recorded | OPEN | API key validation response or explicit unavailable note |
| TE2 | Readback smoke: at least one fundamentals or ownership dataset slice | OPEN | Response shape and record count logged |
| TE3 | Rate-limit behavior noted | OPEN | Observed rate limit or vendor quota documented |
| TE4 | Provenance tag | OPEN | `source_class: research_grade`, `provider: tej` |

#### Kraken (Cryptocurrency market data)

| # | Item | Status | Evidence form |
|---|---|---|---|
| KR1 | Market data session established or credential-unavailable recorded | OPEN | Public or key-authenticated market data response |
| KR2 | Readback smoke: at least one ticker or OHLCV slice | OPEN | Response shape logged |
| KR3 | Order API not invoked | OPEN | No `addOrder`, `cancelOrder`, `editOrder` call logged |
| KR4 | Provenance tag | OPEN | `source_class: research_grade` or `broker_execution`, `provider: kraken`, `lane: market_data_read` |

#### CoinGecko (Cryptocurrency research_grade reference)

| # | Item | Status | Evidence form |
|---|---|---|---|
| CG1 | Auth or public session established | OPEN | HTTP 200 or API key validation |
| CG2 | Readback smoke: at least one coin metadata or price reference | OPEN | Response shape logged |
| CG3 | Provenance tag | OPEN | `source_class: research_grade`, `provider: coingecko` |

#### Shioaji (Taiwan broker_execution — quote/read-only)

| # | Item | Status | Evidence form |
|---|---|---|---|
| SJ1 | Read-only quote session established or credential-unavailable recorded | OPEN | Session establishment log or explicit unavailable note |
| SJ2 | Readback smoke: at least one Taiwan equity quote received | OPEN | Quote response shape logged (symbol + bid/ask shape) |
| SJ3 | Order API not invoked | OPEN | No `place_order`, `cancel_order`, `update_order` call in session log |
| SJ4 | Paper/simulation account context only | OPEN | Account type confirmed as simulation or data-only |

---

## 4. Evidence Naming Convention

Evidence files should follow the naming pattern:

```
support/sidecars/P2-MARKETDATA-CREDENTIAL-SMOKE-001/evidence/
  <provider>-auth-smoke-<YYYYMMDD>.log
  <provider>-readback-smoke-<YYYYMMDD>.log
  <provider>-unavailable-credential-<YYYYMMDD>.md   (when credentials are not available)
```

Where `<provider>` is one of: `polygon`, `ibkr`, `twse`, `tpex`, `mops`, `tej`, `kraken`, `coingecko`, `shioaji`.

Each evidence file must:
- Redact all API keys, tokens, and passwords
- Record the smoke outcome: `PASS`, `FAIL`, or `UNAVAILABLE_CREDENTIAL`
- Record the auth/session establishment result
- Record at least one readback result (record count or error type)
- Confirm order endpoints were not invoked (for IBKR, Shioaji, Kraken)

---

## 5. Risk Areas and Open Questions

### 5.1 Credential Availability

Not all credentials may be provisioned in the current environment. The `APP-003-DATASOURCE-OPS-001` task is done, but individual provider credentials may be staged or gated.

**Recommendation:** For each provider without available credentials, record an explicit `UNAVAILABLE_CREDENTIAL` evidence note rather than skipping. This satisfies acceptance criterion 1 ("records explicit unavailable-credential evidence").

### 5.2 IBKR/Shioaji/Kraken Order-Lane Isolation

The read-only lane for IBKR, Shioaji, and Kraken must be cleanly separated from the order-capable lane at the connection or session level.

**Recommendation:** The parent task should verify that the SDK or client configuration used for the smoke does not initialize order-capable sessions even if credentials would allow it. This is a code-level constraint, not just an operational one.

### 5.3 Rate Limit Evidence

For Massive/Polygon and TEJ API, rate limits may not be observable in a single smoke pass.

**Recommendation:** Document the vendor's stated rate limits from their API docs as a substitute for observed rate-limit headers when the smoke does not hit the limit. This is acceptable evidence for the rate-limit gate.

### 5.4 Provenance Tagging

The acceptance criteria require provenance evidence, but the parent task does not specify the exact field schema.

**Recommendation:** At minimum, capture `source_class`, `provider`, and `ingest_timestamp` in the smoke log. Full `IngestionRecord` schema compliance is a parent-task implementation detail, not a sidecar requirement.

### 5.5 TEJ License and PIT Policy

TEJ API data carries point-in-time (PIT) licensing constraints per `PAPER_CANARY_LIVE_POLICY.md`.

**Recommendation:** The parent task should confirm that the TEJ smoke does not cache or store raw TEJ data beyond what is permitted by the TEJ license. The smoke log should note the data retention posture.

---

## 6. Files Referenced

### Shared State
- `ai-status.json` — task ownership and lifecycle state
- `AI_COLLABORATION_GUIDE.md` — collaboration rules

### Canonical / Policy Sources
- `DATA_SOURCE_SCOPE_MATRIX.md` — governed vendor fill, source class rules
- `PAPER_CANARY_LIVE_POLICY.md` — external data source production ingestion gate
- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` — maturity ladder and evidence standards

### Completed Upstream Work
- `APP-003-DATASOURCE-OPS-001` (done) — credential provisioning and smoke automation
- `P2-OSS-ACTIVATE-001` (done) — OSS data posture confirmed

### This Sidecar
- `support/sidecars/P2-MARKETDATA-CREDENTIAL-SMOKE-001/P2-MARKETDATA-CREDENTIAL-SMOKE-001-SIDECAR-ACCEPTANCE.md`

---

## 7. Handoff To Reviewer (Codex2)

Codex2, this acceptance packet is ready for review.

What it gives the P2-MARKETDATA-CREDENTIAL-SMOKE-001 owner (Codex2):

1. **Dependency-confirmed starting point:** Both `APP-003-DATASOURCE-OPS-001` and `P2-OSS-ACTIVATE-001` are `done`. No upstream blockers.
2. **Provider scope confirmed:** All 9 governed providers from `DATA_SOURCE_SCOPE_MATRIX.md §1.2` mapped with source class, lane, and smoke mode.
3. **Per-provider acceptance checklist:** 5 cross-cutting gate items (G1–G5) and 28 per-provider items across all 9 providers.
4. **Evidence naming convention:** Standardized file naming for the evidence directory.
5. **Open questions documented:** Five risk areas flagged for parent-owner decision (credential availability, order-lane isolation, rate-limit evidence, provenance tagging, TEJ license/PIT).

This packet does not modify any canonical truth. Absorbing the checklist into the parent task's implementation and review plan is safe and recommended.

---

*Generated by Claude2 as a sidecar `acceptance_packet` helper for P2-MARKETDATA-CREDENTIAL-SMOKE-001. This file is a support artifact and does not modify canonical truth.*
