# MGMT-OPS-003-GAP-002 BFF And Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `MGMT-OPS-003-GAP-002` |
| Parent scope | Runtime binding and telemetry truth repair |
| Parent owner / reviewer | `Copilot` / `Codex2` |
| Sidecar task | `MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Date | `2026-07-11` |
| Mutates canonical truth | `false` |

This support-only packet translates the hosted Portfolio Book findings into a
BFF query-gap map, an honest operator journey, frontend handoff rules, and
parent verification targets. It does not repair data, add routes, change BFF
or runtime behavior, edit registries, or alter canonical architecture and
governance truth. The parent owner decides which recommendations to absorb.

## 1. Evidence Boundary

The packet is based on:

- `docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/MGMT_OPS_003_HOSTED_GAP.md`;
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-002-runtime-data-quality.md`;
- the original `MGMT-OPS-003` Portfolio Book task packet;
- the current Portfolio Book handlers and projections in
  `services/control-plane/bff/main.py`;
- `services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`;
- runtime binding reads in `services/runtime-manager/main.py` and telemetry
  binding projection in `services/telemetry/runtime_summary.py`.

The hosted baseline is 19 capital pools, 6 runtimes, 2 telemetry runtimes, 14
holdings, 14 degraded holdings, 10 missing-binding holdings, 14 incidents, and
0 live runtimes. These are observations from the source gap record, not fresh
probes performed by this sidecar. No row may be removed merely to improve the
counters.

## 2. Current BFF Read Surface

The frontend already has a read-only contract family to observe repair results:

```text
GET /bff/management/portfolio-book
GET /bff/management/portfolio-book/pools
GET /bff/management/portfolio-book/exposure
GET /bff/management/portfolio-book/holdings
GET /bff/management/portfolio-book/positions
```

The holdings and positions surfaces expose normalized identity, deployment
stage, `capital_scope`, source status/issues, stale-telemetry state, risk state,
row-level incidents, links, and coverage counters. The summary surface reports
runtime, telemetry-runtime, stale-row, missing-binding, and degraded-source
counts. Performance attribution derives `data_confidence`; incomplete runtime
or telemetry joins must therefore remain partial, degraded, or unavailable.

This sidecar identifies no justified mutation endpoint for data repair. Repair
and quarantine remain parent-owned service/reconciliation work, not a browser
write assembled from Portfolio Book reads.

## 3. BFF Query Gap Matrix

| Operator need | Current evidence | Gap / parent decision |
|---|---|---|
| Account for every bad row | Portfolio Book returns row issues, incidents, and aggregate counters. | Parent reconciliation report needs a stable per-row before/after identity so repaired, quarantined, and unchanged rows can be matched without disappearing. |
| Explain the join failure | `source_issues` can identify missing persona binding and stale/degraded telemetry. | Ensure each unresolved row states which authoritative join is absent: runtime binding, persona, deployment plan, broker, paper ledger/canary sleeve/live pool, or telemetry. Do not collapse these into one generic degraded reason. |
| Distinguish repaired from quarantined | Current read surface shows present degradation, not necessarily reconciliation disposition. | Parent should decide whether existing issue/incident metadata can carry `repaired`/`quarantined` disposition and audit reference; if not, propose a bounded read-model addition. Do not invent the field in frontend code. |
| Prove reconciliation idempotency | Portfolio endpoints are snapshots. | A task-scoped report or governed reconciliation read is required to compare run id, input identity, action, reason, and no-op replay. Portfolio counters alone are insufficient. |
| Preserve capital identity | `capital_scope` distinguishes paper ledger, canary sleeve, live pool, and unknown/unbound projections. | Parent must propagate the authoritative scope id end-to-end. Unknown/unbound must remain explicit and must never inherit a paper or live identity. |
| Prove telemetry restoration | Summary exposes runtime and telemetry-runtime counts; rows expose stale/source issues. | Record runtime-level coverage and freshness, not only `2/6` style totals. An active uncovered runtime requires an explicit source status and incident. |
| Prevent false attribution | Attribution exposes `data_confidence`. | Required joins remaining degraded/unavailable must keep formal attribution impossible. Frontend must not recompute or upgrade confidence. |
| Navigate remediation | Row links can target Persona Fleet, Performance Attribution, and Human Review. | Links must preserve persona, runtime, binding, pool/ledger/sleeve, holding, period, source status, and incident context when available. Missing identifiers must disable the corresponding drilldown rather than fabricate one. |

## 4. Reconciliation Disposition Contract For Parent Use

The parent report should account for each hosted problem with at least:

| Field | Purpose |
|---|---|
| runtime and runtime-binding ids | Stable execution identity; both retained when they differ. |
| persona and deployment-plan ids | Establish the intended owner and activation lineage. |
| artifact and strategy ids | Trace the deployed workload without inferring ownership. |
| broker id | Explicit broker identity or an explicit missing reason. |
| capital scope type and id | Paper ledger, canary sleeve, live pool/sleeve, or unbound. |
| telemetry source and observed time | Coverage and freshness evidence for the runtime. |
| before issue codes | Exact diagnostic state before reconciliation. |
| disposition | `repaired`, `quarantined`, or `unchanged`; never silent deletion. |
| reason and evidence refs | Auditable authority for repair or quarantine. |
| after issue codes | Remaining degradation visible to BFF and UI. |
| reconciliation run/idempotency key | Proves replay is a no-op or produces the same result. |

These are evidence requirements, not a new canonical schema. The parent owner
must map them to existing authoritative models or explicitly request a bounded
contract change through normal review.

## 5. Operator Journey

### Detect And Triage

1. Operator opens Portfolio Book and sees coverage counters before any success
   language: runtimes, telemetry runtimes, stale rows, missing bindings, and
   degraded sources.
2. Operator filters by stage, runtime, broker, source status, stale telemetry,
   or risk state. URL state survives refresh and can be shared with a reviewer.
3. Each affected holding remains visible with incident severity, issue codes,
   runtime/persona identity when known, and an explicit unknown/unbound value
   when not known.
4. Operator opens Human Review with the row and incident context preserved.
   Portfolio Book itself does not silently repair identity data.

### Repair Or Quarantine

1. An authorized parent-owned reconciliation process traces the row across
   runtime binding, deployment plan, persona, capital identity, artifact, and
   telemetry sources.
2. Where authoritative identifiers agree, the process repairs the projection
   and records before/after evidence.
3. Where authority is absent or conflicting, the row is quarantined with a
   reason and remains visible as degraded/unavailable.
4. Replaying the same reconciliation key produces a no-op or the same audited
   disposition; it does not duplicate bindings or incidents.

### Verify Downstream Honesty

1. Operator refreshes Portfolio Book and matches every prior row to repaired,
   quarantined, or unchanged disposition.
2. Counters improve only for repaired joins; quarantined rows remain counted.
3. Performance Attribution stays partial/degraded/unavailable wherever
   required identity or telemetry evidence is still missing.
4. Reviewer samples raw runtime-binding and telemetry records rather than
   approving from summary counters alone.

## 6. Frontend Handoff

- Consume BFF `source_status`, `source_issues`, incidents, `capital_scope`,
  `risk_state`, `telemetry_stale`, and coverage counters as authoritative read
  evidence. Do not infer repaired state from a lower aggregate count.
- Render paper ledger, canary sleeve, live pool/sleeve, and unknown/unbound as
  distinct text labels; do not rely on color and do not default unknown to
  paper or live.
- Keep degraded and quarantined rows visible under filters and pagination.
  Never use client-side deduplication to hide an unresolved identity conflict.
- Treat missing ids as missing evidence. Disable or omit an invalid drilldown
  and show the issue reason instead of constructing a guessed URL.
- Preserve available persona, runtime, binding, capital-scope, holding, period,
  source-status, and incident parameters across Persona Fleet, Performance
  Attribution, and Human Review links.
- Display `formal attribution` only when the BFF verdict is formal. Partial,
  degraded, unavailable, stale, or unknown evidence must not be upgraded in
  browser code.
- Do not add a frontend repair mutation unless a separately reviewed governed
  BFF action contract supplies authorization, validation, idempotency, audit,
  and receipt semantics.

## 7. Parent Acceptance And Test Handoff

The parent owner should provide:

- a reconciliation report covering all 10 hosted missing-binding holdings and
  all 4 runtimes without telemetry coverage at the recorded baseline;
- normal, missing, stale, quarantined, repaired, conflict, and idempotent replay
  tests across runtime binding, telemetry projection, and Portfolio Book;
- proof that broker and capital-scope identity propagates from its authoritative
  source rather than being guessed by the BFF;
- before/after authenticated BFF evidence for runtime count, telemetry-runtime
  count, degraded rows, missing bindings, broker identity, and capital scope;
- a negative test showing unresolved rows cannot receive formal attribution;
- raw-source reviewer samples plus the completed task
  `REVIEWER_CHECKLIST.md`;
- Pantheon PR/check/merge SHA, BFF deploy run, and authenticated hosted probes.

Recommended frontend contract cases for the composing task:

| Fixture | Required assertion |
|---|---|
| Baseline 14 degraded / 10 missing | All 14 incidents remain visible; no formal/covered claim appears. |
| Repaired binding | Stable row identity remains, issue clears with evidence, and scope id is explicit. |
| Quarantined conflict | Row remains visible with reason; formal attribution stays blocked. |
| Active runtime without telemetry | Explicit unavailable/degraded source and incident render. |
| Mixed capital scopes | Paper, canary, live, and unknown/unbound are textually distinct. |
| Context round trip | Filters and drilldown identifiers survive refresh/back navigation. |

## 8. Sidecar Review Checklist

- Confirm every route and field claim is supported by the checked-in BFF
  implementation/tests or clearly labeled as a parent recommendation.
- Confirm the packet does not claim fresh hosted probing or completed repair.
- Confirm no canonical truth, runtime/BFF code, registry, governance, or
  frontend source was changed.
- Confirm unresolved/quarantined rows stay visible and block formal attribution.
- Confirm the parent owner, not this sidecar, decides contract changes and
  absorption into the canonical implementation.

## 9. Focused Verification

```bash
git diff --check -- support/sidecars/MGMT-OPS-003-GAP-002/MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF.md
rg -n "portfolio-book|missing_binding|capital_scope|telemetry_runtime|data_confidence" services/control-plane/bff/main.py services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py
rg -n "runtime_binding_id|binding_id" services/telemetry/runtime_summary.py services/runtime-manager/main.py
```

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned because the task brief did not require global history.
