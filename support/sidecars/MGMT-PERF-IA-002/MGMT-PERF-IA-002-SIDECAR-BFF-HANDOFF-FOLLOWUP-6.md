# MGMT-PERF-IA-002 BFF And Frontend Handoff Follow-up 6

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-002` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar task | `MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-11` |
| Mutates canonical truth | `false` |

This packet is support material for parent-owner absorption. It changes no
canonical contract, BFF runtime or test, ranking formula, governance behavior,
registry state, or frontend source.

## 1. Current Evidence Boundary

At inspection time `origin/task/MGMT-PERF-IA-002` still points to candidate
commit `d0d4d0497`. No newer parent commit demonstrates recovery from current
`dev`, and no merged evidence closes effective-filter, historical-snapshot,
stable-pagination, or governed receipt-loopback gaps. The candidate remains
unmerged evidence and must not be used as the frontend contract source.

Follow-up 5 already contains the required response-example matrix. Repeating
or expanding speculative field names would not advance the parent. The next
useful checkpoint is therefore a clean parent PR with executable evidence.

## 2. Parent Recovery Gate

The BFF owner should publish one compact recovery record with all of the
following before requesting frontend wiring:

| Gate | Required evidence |
|---|---|
| Clean ancestry | Task branch starts from current `origin/dev`; branch diff contains only declared performance/ranking paths. |
| Collectable contract | Focused performance/ranking contract test collects and passes without restoring or deleting unrelated modules. |
| Effective query | Every claimed filter has matching, non-matching, and unsupported proof; the used context is recoverable from the response. |
| Snapshot truth | Latest, known historical, unknown historical, and malformed `asOf` outcomes are distinct. |
| Cohort stability | Ordering, pagination, totals, rank, evidence, and drilldown remain bound to one cohort and snapshot. |
| Honest evidence | Tied, excluded, null/non-finite, partial, degraded, stale, and unavailable rows preserve backend-authored state. |
| Governance boundary | Recommendation, Human Review, apply availability, operation, and receipt remain separate; absent capability fails closed. |

If a row cannot be proven, mark it deferred with a named owner. Do not satisfy
the gate with parameter acceptance, top-level field presence, or examples
synthesized from this sidecar.

Suggested parent-owned evidence commands:

```bash
git fetch origin dev task/MGMT-PERF-IA-002
git diff --name-status origin/dev...HEAD
git diff --check -- services/control-plane/bff
pytest -q services/control-plane/bff/test_bff_performance_ranking_read_model_contract.py
```

## 3. Frontend Release Gate

Until the clean BFF PR merges, the `execute-plans` owner should consume current
merged `dev` behavior only and render unsupported context/history/action paths
as unavailable. After merge, BFF ownership should provide sanitized captured
responses for filtered success, empty cohort, validation error, unavailable
history, tied/excluded/degraded rows, stable pagination, recommendation submit,
and any implemented operation-to-receipt loop.

Frontend acceptance should then prove:

1. persona/runtime/strategy/pool/sleeve/artifact/broker/stage/period/quarter and
   `asOf` context remains visible through Performance, Rankings, drilldown, and
   Human Review navigation;
2. unknown history never substitutes latest data;
3. browser sorting never renumbers official backend ranks;
4. null, exclusion, source-confidence, stale, and degraded states survive
   navigation without client-side joins or confidence upgrades;
5. recommendation submission is not displayed as approval or applied effect;
6. no direct service, registry, allocation, runtime, or broker mutation is
   attempted when a governed BFF action/receipt link is absent.

Frontend delivery belongs in the separate `ajoe734/execute-plans` repository,
using strict live BFF mode on Pantheon-owned dev hosting. This sidecar neither
materializes nor authorizes frontend changes.

## 4. Stop Condition And Handoff

Additional support-only follow-ups should stop until one of these material
deltas exists: a clean parent recovery commit, a focused test result, merged
response examples, or an explicit parent deferral decision. Without such a
delta, another packet would duplicate guidance rather than reduce delivery
risk.

Parent owner `Antigravity` may selectively absorb this recovery and release
gate. Reviewer `Antigravity` should verify the remote checkpoint, the
support-only boundary, and the absence of implementation claims. Approval of
this packet does not approve the parent runtime implementation and does not
authorize live-capital operations.

## 5. Sidecar Verification

```bash
git diff --check -- \
  support/sidecars/MGMT-PERF-IA-002/MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md
```
