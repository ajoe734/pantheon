# MGMT-PERF-IA-002 BFF And Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-002` |
| Parent title | Performance and ranking read model |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar task | `MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex2` / `Codex` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-11` |
| Mutates canonical truth | `false` |

This packet is support material for parent-owner absorption. It does not edit
L1/L2 truth, BFF runtime code, ranking formulas, governance rules, registry
state, or the `execute-plans` frontend. Parent owner `Antigravity` decides
whether its findings compose with the canonical implementation.

## 1. Evidence Boundary

- `dev` contains the common-filter normalization from merged change
  `b178a2e38` / PR `#3093`.
- At inspection time, `origin/task/MGMT-PERF-IA-002` points to `760252320` and
  contains anchor commit `aadda1db9` for BFF contract tests. This is candidate
  task-branch evidence, not merged runtime truth.
- Existing BFF route families include performance attribution, Persona League,
  quarterly ranking, recommendation reads/submission, portfolio exposure, and
  generic ranking/rebalance surfaces.
- `MGMT-OPS-001` already established the single-persona identity,
  source-confidence, finite-or-null metric, source, and diagnostic vocabulary.
  This sidecar treats that vocabulary as an integration boundary and does not
  redefine it.

## 2. Common Read Context To Preserve

All performance, exposure, ranking, recommendation, and governed-apply links
should preserve one backend-authored read context:

```text
personaId, runtimeId, strategyId, capitalPoolId, sleeveId,
artifactId, brokerId, stage, period, asOf
```

Quarterly surfaces additionally preserve `quarter`. Filters should be echoed
or otherwise recoverable in response metadata so frontend drilldowns do not
silently change cohort or snapshot. `asOf` is a snapshot boundary, not merely
a display timestamp.

Frontend code must not locally join rows across different `snapshot_at` / `asOf`
values. If a requested historical snapshot cannot be served, the BFF should
return an explicit unsupported/unavailable result rather than silently serving
latest data under the requested timestamp.

## 3. BFF Query-Gap Matrix

| Operator need | Current/candidate surface | Gap or parent decision |
|---|---|---|
| Filtered performance attribution | `GET /bff/management/performance-attribution` plus by-persona/by-strategy/by-pool aliases | Common filters are available. Preserve one canonical response vocabulary across aliases; avoid alias-specific rank/confidence semantics. |
| Short-cycle persona ranking | `GET /bff/management/persona-league/rankings` | Return backend-authored rank, eligibility/exclusion reason, formula/version, metric window, confidence, source coverage, and snapshot identity. Browser must not sort raw metrics into an authoritative rank. |
| Formal quarterly ranking | `GET /bff/management/quarterly-ranking` and `/drilldown` | Preserve `quarter`, cohort/filter identity, evidence reference, formula/version, governance state, and stable row id between list and drilldown. |
| Recommendation review | `GET /bff/management/quarterly-ranking/recommendations` | Candidate tests require immutable `ranking_evidence_ref`, `human_review_state`, and `requires_human_gate_decision`. Recommendation is not approval or apply. |
| Recommendation submission | `POST /bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit` | Response/receipt must bind recommendation id, ranking evidence ref, selected snapshot, actor/idempotency context, and resulting Human Review state. It must not claim live-capital mutation. |
| Exposure context | `GET /bff/management/portfolio-book/exposure` and related portfolio-book reads | Ranking/performance rows need stable pool/sleeve/runtime links into exposure. Do not make the frontend aggregate holdings into authoritative exposure. |
| Governed apply | Existing ranking/rebalance/action surfaces are separate from the recommendation read | Parent should expose an explicit action-state/link contract (`observe`, `request_review`, `submitted`, `approved`, `apply_available`, `applying`, `applied`, `failed`) only when backed by governance truth. No client inference from rank or recommendation status. |
| Apply receipt loopback | No single read path is established by the sidecar evidence | Parent/follow-up should identify the canonical receipt lookup and return its link/id from the governed command. Ranking, Human Review, Portfolio Book, and Persona Fleet must converge on the same receipt. |
| Table scale | Existing list surfaces are bounded independently | Preserve server-side filtering, deterministic ordering, page size/token, and total/coverage metadata. Do not replace them with per-row operations-read-model calls or client-side full-dataset ranking. |
| Empty/degraded evidence | Candidate tests cover empty rebalance/formula collections and explicit source metadata | Empty is not zero performance. Missing/non-finite metrics remain `null`; excluded/unavailable rows carry reasons and must not be ranked as zero. |

The principal open integration gap is therefore not another unconstrained
performance endpoint. It is a stable cross-surface context and action/receipt
link contract that lets the frontend move from evidence to governed review
without rebuilding joins or state transitions in the browser.

## 4. Operator Journey

### A. Compare performance without changing the cohort

1. Operator opens Performance Attribution with a selected period, stage, pool,
   sleeve, strategy, or persona.
2. BFF returns backend-ranked attribution rows plus snapshot/source confidence.
3. Operator drills into a persona or strategy; the frontend carries the same
   filters and `asOf` boundary.
4. Missing formal telemetry is shown as partial/degraded/unavailable with
   diagnostics. It is never coerced to zero or promoted to formal evidence.
5. Operator follows stable identity links to exposure, Persona League, or
   quarterly drilldown without the frontend recomputing rank.

### B. Move from ranking to governed recommendation

1. Operator opens Persona League for short-cycle monitoring, then Quarterly
   Ranking for the formal governance cohort.
2. Each row shows rank, eligibility, formula/version, metric window, confidence,
   evidence coverage, and snapshot identity.
3. Operator opens quarterly drilldown and verifies the immutable ranking
   evidence and exposure context.
4. Operator creates or submits a recommendation. The UI labels it
   `recommendation` / `submitted`, never `approved` or `applied`.
5. The BFF returns the Human Review state/link and preserves recommendation,
   ranking-evidence, quarter, persona, and snapshot identity.

### C. Apply only after Human Review

1. Authorized reviewer decides through the governed Human Review surface.
2. Only an approved, still-current recommendation may expose an apply action;
   stale snapshot, changed binding, degraded evidence, or missing approval must
   fail closed.
3. Apply uses the BFF-owned command/idempotency/precondition path, not a direct
   frontend mutation of runtime, allocation, ranking, or broker state.
4. The command returns an operation/receipt id and truthful asynchronous state.
5. Portfolio Book, Persona Fleet, ranking detail, and Human Review link to the
   same receipt. The UI shows `applied` only after receipt/read-model evidence
   confirms the effect.

## 5. Frontend Handoff

Recommended `execute-plans` integration order:

1. Define one typed `ManagementReadContext` and serializer for the common
   filters plus `quarter` where relevant.
2. Preserve returned snapshot/filter identity in route state and drilldown
   links; do not default back to `latest` during navigation.
3. Reuse shared `data_confidence`, source-status, freshness, diagnostics, and
   finite-or-null rendering from the operations read-model work.
4. Treat BFF rank, eligibility, exclusion reason, formula/version, and evidence
   refs as authoritative. Client sorting may change presentation only and must
   not renumber the official rank.
5. Render recommendation, review decision, apply availability, operation, and
   receipt as distinct states.
6. Disable high-impact controls when evidence is partial/degraded/unavailable,
   the snapshot is stale, identity changed, or no governed action link exists.
7. Use strict BFF mode on the Pantheon-owned dev frontend/BFF hosts; do not add
   mock fallback or direct service/broker calls.

Minimum row fields the frontend should be able to rely on after parent
composition:

```json
{
  "id": "stable-row-id",
  "identity": { "persona_id": "persona-1", "runtime_id": "runtime-1" },
  "rank": 3,
  "eligible": true,
  "exclusion_reason": null,
  "formula": { "id": "formula-id", "version": "v1" },
  "period": "latest",
  "snapshot_at": "2026-07-11T00:00:00Z",
  "data_confidence": "formal",
  "source_refs": {},
  "evidence_refs": [],
  "links": { "drilldown": "/bff/management/quarterly-ranking/drilldown?..." }
}
```

This is an interoperability sketch, not a new canonical schema.

## 6. Parent And Reviewer Checklist

Parent owner should decide and record:

- whether all four read families echo/recover the normalized filter and
  snapshot context;
- which endpoint owns official rank and which fields make eligibility,
  exclusions, formula version, and evidence coverage explicit;
- the canonical Human Review link/state returned after recommendation submit;
- the governed apply endpoint and canonical receipt lookup, if apply is in
  parent scope, or a named follow-up owner if it is not;
- deterministic pagination/order semantics and stale-snapshot failure behavior;
- focused tests proving null/non-finite, empty, degraded, excluded, stale, and
  unauthorized paths fail honestly.

Reviewer `Codex` should verify this sidecar:

- changes only this support packet;
- labels branch-only observations as candidate evidence;
- does not redefine ranking formulas, confidence vocabulary, governance, or
  runtime truth;
- prevents frontend-derived official ranking, cross-snapshot joins, and direct
  apply mutations;
- gives the parent owner a concrete query-gap and operator/frontend handoff.

## 7. Suggested Verification

```bash
git diff --check -- support/sidecars/MGMT-PERF-IA-002/MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF.md
rg -n "performance-attribution|persona-league|quarterly-ranking" services/control-plane/bff/main.py
pytest -q services/control-plane/bff/test_bff_performance_ranking_read_model_contract.py
```

The pytest command validates parent implementation surfaces and remains the
parent owner's responsibility if the branch is still evolving. Sidecar-local
verification requires `git diff --check` and review of the claims above.

## 8. Handoff

Please review this as a support-only packet and return it to parent owner
`Antigravity` for selective absorption. Approval of this packet does not approve
the parent runtime implementation and does not authorize live-capital changes.
