# MGMT-PERF-IA-005 BFF And Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-005` |
| Parent owner / reviewer | `Claude` / `Antigravity` |
| Sidecar task | `MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex2` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-11` |
| Mutates canonical | `false` |

This packet is support material only. It does not define a new BFF contract,
change L1 truth, implement runtime or registry behavior, edit the frontend, or
approve the parent task. The parent owner decides whether and how to compose it.

## 1. Parent Boundary

`MGMT-PERF-IA-005` replaces duplicated Promotion Allocation ranking tables with
one Governance Decisions workspace:

- `/management/governance-decisions?tab=recommendations`
- `/management/governance-decisions?tab=capital`
- `/management/governance-decisions?tab=policy`

The workspace consumes ranking evidence but is not a ranking authority.
Recommendation, Human Review decision, and applied action are separate states.
Any capital, access, rebalance, or deployment-stage mutation must remain behind
Human Review, governed apply, precondition evidence, and an apply receipt.

## 2. Current BFF Surface And Query Gaps

| UI need | Existing surface observed | Handoff conclusion |
|---|---|---|
| Recommendations queue | `GET /bff/management/quarterly-ranking/recommendations` provides recommendation evidence and governance state. | Reuse this read surface first. Preserve immutable ranking snapshot/evidence references; do not reconstruct rankings in the browser. Confirm it can represent recommendation, review, approval, rejection, expiry, blocked, applied, and superseded without client inference. |
| Human Review context | Governance review queue and governance-ledger read surfaces exist. | The frontend needs stable identifiers linking recommendation -> ranking snapshot -> review item/decision. If identifiers cannot be joined, request a BFF composition change rather than correlating by labels or timestamps. |
| Capital allocation | Capital-pool and rebalance routes exist, including `GET /bff/rebalances`. | No evidence inspected establishes one Governance Decisions capital projection containing proposal impact, limits, preconditions, reviewer, timestamps, and apply receipt. Parent should either compose this in BFF or render an honest unavailable state. Avoid frontend fan-out that invents lifecycle joins. |
| Ranking policy | Ranking endpoints expose ranking evidence; no inspected route establishes a policy/formula collection for this workspace. | Empty or absent policy/formula data must render `unavailable`, not a fabricated default formula. A new read contract, if required, belongs to the BFF owner and parent coordination. |
| Source confidence | `MGMT-PERF-IA-002` defines formal, partial, fallback, degraded, and unavailable plus freshness, coverage, missing bindings, and observed time. | Carry these backend-authored fields through every tab. Missing confidence fails closed to unavailable/degraded; the browser must not promote fallback evidence to formal. |
| Common filters | Parent dependency normalizes persona, runtime, strategy, capital pool, sleeve, artifact, broker, stage, period, and as-of. | URLs use canonical IA names (`capital_pool`, `as_of`); adapters may translate wire names. Preserve only relevant context when switching tabs or linking to Rankings Center. |
| Governed apply | BFF action catalog and rebalance command surfaces exist. | A visible button is not proof of authorization. Enable apply only from backend action/precondition state and route it through the existing governed command/Human Review machinery with idempotency and receipt evidence. |

The main query gap is therefore not another ranking endpoint. It is a bounded,
backend-owned governance projection (or explicit compatible joins) that keeps
recommendation, review, proposal, precondition, application, and receipt
identities distinct. The sidecar does not prescribe its route name or schema.

## 3. Operator Journey

1. Operator opens Governance Decisions at `tab=recommendations` with relevant
   persona/runtime/period context preserved.
2. The queue shows backend confidence, freshness, evidence coverage, immutable
   ranking snapshot reference, recommendation state, and Human Review state.
3. Operator follows the snapshot link to `/management/rankings`, then returns
   without losing the originating filter context. No full ranking table is
   embedded in Governance Decisions.
4. Operator opens a recommendation. The UI distinguishes proposed impact from
   current allocation and shows limits, missing bindings, preconditions,
   reviewer, and timestamps.
5. If evidence is partial/fallback/degraded/unavailable, or preconditions are
   incomplete, the UI offers evidence inspection/data-quality escalation or a
   review request; it does not offer direct live mutation.
6. Human Review records approve, reject, request-changes, or expiry separately
   from recommendation state.
7. Only an approved and still-valid proposal can enter governed apply. The UI
   displays command progress separately from approval.
8. Completion requires an apply receipt linked back to recommendation,
   snapshot, review decision, and precondition results. Failure or supersession
   remains visible and never appears as applied.

## 4. Frontend Handoff

- Use one tab shell and shared query parser for `recommendations`, `capital`,
  and `policy`; unknown tabs fall back safely without discarding filters.
- Remove `real-ranking` and `paper-candidates` tables. Replace them with compact
  immutable snapshot references and links to Rankings Center.
- Model lifecycle values as backend-owned discriminated states. Do not infer
  `approved`, `applied`, or `expired` from timestamps, ranks, or button history.
- Render empty rebalance and policy/formula collections as explicit empty or
  unavailable panels according to source metadata.
- Show proposal impact as a comparison, not as current truth. Label current,
  proposed, approved, applying, applied, failed, and superseded separately.
- Keep action controls outside ranking rows. High-impact controls require an
  eligible backend action, role authorization, satisfied preconditions, Human
  Review identity, idempotency protection, and a receipt destination.
- Preserve desktop/mobile information order: confidence and status first,
  evidence and impact second, governed action last.
- Legacy Promotion Allocation redirects should select the relevant tab and
  preserve only canonical filters; add loop-free redirect tests.

## 5. Fail-Closed Rendering Rules

- Missing or unknown source confidence is not formal.
- Missing ranking snapshot identity blocks recommendation-to-apply progression.
- Missing review identity or approval receipt means “not approved”.
- Approval without an apply receipt means “approved”, never “applied”.
- Empty policy/formula or rebalance data is not permission to synthesize one.
- `null`, non-finite, or missing impact values render as unavailable, never zero.
- A stale or superseded recommendation cannot mutate live state.

## 6. Parent And Reviewer Checklist

- Confirm Governance Decisions contains no competing full ranking table.
- Confirm each recommendation links to immutable ranking evidence and exposes
  backend source confidence/freshness/coverage.
- Confirm every lifecycle state required by the parent has an explicit backend
  representation or an honest unavailable state.
- Confirm capital proposals show current versus proposed impact, limits,
  preconditions, reviewer/timestamps, and receipt linkage without client joins.
- Confirm zero rebalance and zero policy/formula rows render truthfully.
- Confirm tests prove ranking rows cannot directly mutate live state and that
  approval is not rendered as application.
- Confirm legacy redirects are loop-free and preserve relevant filters.
- Confirm this packet remains support-only; any BFF/frontend implementation is
  owned and reviewed under the parent or a separately assigned task.

## 7. Verification Notes

Source inspection only; no runtime or frontend code was changed. Reviewed:

- parent task packet `MGMT-PERF-IA-005-governance-decisions.md`;
- dependency packet `MGMT-PERF-IA-002-performance-ranking-read-model.md`;
- target IA route, filter, confidence, and governed-action rules;
- BFF tests/action catalog references for quarterly recommendations,
  governance review/ledger, capital pools, rebalances, and governed actions.

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.
