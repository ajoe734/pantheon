# MGMT-PERF-IA-008 BFF And Frontend Handoff Packet

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-008-SIDECAR-BFF-HANDOFF` |
| Parent task | `MGMT-PERF-IA-008` — Hosted acceptance and closeout |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This packet is support material for the parent owner. It does not change L1
canonical truth, BFF routes or schemas, runtime/registry/governance behavior,
deployment configuration, or `execute-plans` source. It is not hosted proof
and does not approve or finalize the parent task.

## 1. Closeout Boundary

The parent must prove one deployed operator loop:

```text
Fleet -> Performance Center -> Rankings Center
      -> Governance Decisions -> Human Review -> Apply Receipt
```

The existing BFF route families can provide the individual reads used by this
loop: Portfolio Book and performance attribution; Persona League and quarterly
ranking; quarterly recommendation reads/submission; and governed review
surfaces. The closeout must not infer that these reads form one atomic snapshot
or that a recommendation/review decision is an applied action.

The support conclusion is therefore: do not create a closeout-only aggregate
endpoint. Capture each response with its backend-authored identifiers,
fulfilled filters, snapshot/as-of evidence, source state, and links, then prove
that the deployed frontend preserves those values between surfaces. If a
required identity or receipt link is absent, record a precise contract blocker
instead of joining records by labels, ranks, timestamps, or display text.

## 2. BFF Query And Evidence Gap Ledger

| Closeout question | Evidence required | Fail-closed disposition |
|---|---|---|
| Which build was exercised? | Pantheon and `execute-plans` merge SHAs, deployed revisions, host/origin, capture time, and ancestry from merged commits. | A local or merged-only test is not hosted evidence. A hosted bundle with unknown revision cannot close the task. |
| Did context survive the journey? | Requested and fulfilled persona, runtime, strategy, pool/sleeve, broker, stage, period/quarter, snapshot/as-of, recommendation, and review identifiers at every applicable hop. | Do not claim preservation from matching labels or default filters. Record the first hop that loses identity. |
| Is performance formal? | Response-owned source/confidence state, freshness/observed time, coverage, missing bindings, diagnostics, and finite-or-null metrics. | Fallback, partial, stale, degraded, unavailable, and healthy empty remain distinct; none becomes formal or numeric zero. |
| Is ranking reproducible? | Backend-authored rank, eligibility/exclusion, formula/version where supplied, cohort/window, snapshot identity, and evidence reference. | Client sorting may change display order but must not renumber official rank or manufacture a snapshot. |
| Is a recommendation governed? | Immutable ranking evidence reference, recommendation id/state, Human Review id/state/link, actor/time, and any precondition/idempotency evidence returned by the BFF. | `recommended`, `submitted`, and `approved` are not `applied`. No ranking row directly mutates capital, access, promotion, freeze, or rebalance state. |
| Was an action applied? | Canonical operation/apply receipt id, truthful terminal or pending state, target identity, timestamps, and a read-back link/effect when available. | If no receipt is returned or retrievable, close the hosted scenario as safely non-applied and log the missing receipt contract. Never infer application from a success toast. |
| Are legacy routes equivalent? | Initial URL, final canonical URL, redirect count, preserved allow-listed query context, final tab, response status, console/network result, and back/forward behavior. | A redirect loop, silent context reset, mock/fixture response, or dead end is a blocker. |
| Are composed panels equally healthy? | Per-request/per-section source state and snapshot time, including request failures and unavailable collections. | A healthy page shell cannot hide a degraded or failed attribution, exposure, ranking, or governance section. |

The parent should assign a bounded Pantheon BFF follow-up if hosted evidence
shows that stable backend identifiers cannot connect ranking evidence to Human
Review or Human Review to an apply receipt. This sidecar deliberately does not
invent route or field names for that gap.

## 3. Operator Journey To Capture

1. Open Persona Fleet on hosted dev with a stable persona/runtime and explicit
   period or snapshot context. Record the deployed revisions and initial BFF
   response/source state.
2. Follow the formal Performance Center link. Verify the final URL and returned
   identity/time context. Exercise Overview, Attribution, and Exposure &
   Holdings without losing shared filters; keep each panel's source state and
   timestamp visible.
3. Capture at least one formal path and one honest non-formal path (partial,
   fallback, stale, degraded, or unavailable). Confirm null/non-finite values
   render as unavailable and no visible `nan`, `NaN`, `undefined`, or false
   zero appears.
4. Follow a stable link into Rankings Center. Distinguish Rolling from
   Quarterly, retain applicable context, and capture backend rank, cohort,
   eligibility, snapshot, and evidence references. Confirm the only full
   ranking tables are on this center.
5. Open or create a recommendation from immutable ranking evidence. Verify
   Governance Decisions shows recommendation, review, decision, operation, and
   receipt as distinct lifecycle records and contains no competing full
   ranking table.
6. Enter Human Review through a BFF/frontend-owned link. Approve, reject, or
   leave safely non-applied according to the authorized hosted fixture. Record
   the review identity and prove return navigation restores the originating
   recommendation/ranking/performance context.
7. If an authorized governed apply is available, capture its operation/receipt
   and read-back evidence. Otherwise record the precise safe non-application
   reason; absence of authorization is a valid safety result, not an excuse to
   fabricate an applied receipt.
8. Repeat the essential path on a mobile viewport, then crawl every documented
   legacy performance/ranking/allocation entry. Verify one bounded redirect to
   the correct canonical tab, query preservation, no console/chunk/request
   failures, and usable back/forward navigation.

## 4. Frontend Hosted-Acceptance Handoff

- Run the Pantheon-owned hosted frontend in strict live BFF mode. Evidence must
  identify the actual BFF host and must not come from mock, fixture, fallback,
  direct service, or direct broker calls.
- Use one typed URL/context serializer. Capture both requested URL state and
  backend-fulfilled identity/snapshot metadata; a requested `asOf` value alone
  does not prove the backend served that cut.
- Reset pagination whenever filters, tab/read family, period, quarter, or
  snapshot changes. Do not infer collection completeness from visible rows.
- Keep loading, healthy empty, partial, fallback, stale, degraded, unavailable,
  malformed/unknown-enum, unauthorized, and transport-failure states explicit.
- Preserve unknown backend enum values as unknown/unavailable states, never a
  healthy default. Format only finite numbers.
- Treat BFF links and stable identifiers as authority. Do not correlate records
  using a persona name, rank position, recommendation copy, actor, or time.
- Mobile evidence must retain identity, scope, period/snapshot, source state,
  primary metric, and governed next action before secondary detail.
- A screenshot is supporting evidence, not the contract proof by itself. Pair
  it with URL, viewport, response/capture reference, deployed revision, and
  expected assertion.

## 5. Parent Evidence Matrix

| Evidence bundle | Minimum contents |
|---|---|
| Child delivery ledger | Tasks `001`–`007`; repository; PR; merge SHA; reviewer verdict; deployed status; explicit supersession evidence where applicable. |
| Deployment manifest | Pantheon and `execute-plans` merged/deployed SHAs, target branch, host, deploy time, and ancestry verification. |
| Desktop/mobile route crawl | Canonical plus legacy URLs, viewport, final URL/tab, redirect count, status, preserved context, console/network failures, and artifact reference. |
| BFF capture index | Route/method, redacted request context, response status, snapshot/source state, stable ids, capture time, and linked screenshot/test assertion. |
| Data-state proof | Formal plus non-formal/degraded/unavailable/healthy-empty cases; null/non-finite rendering; unmatched bindings; no fixture authority. |
| Governance proof | Ranking evidence -> recommendation -> Human Review -> apply receipt or precise safely non-applied result, with separate identifiers/states. |
| IA uniqueness proof | Sidebar, command palette, breadcrumbs, Cockpit/entity links agree; Rankings is the only full ranking-table owner; Governance consumes references. |
| Residual-risk ledger | Gap, observed behavior, severity, owner, follow-up task, redirect expiry/telemetry owner, and release impact. |

Store parent evidence under the task's declared archive. Redact credentials,
cookies, authorization headers, and personal data; retain only the identity and
trace fields needed to reproduce the acceptance claim.

## 6. Parent Absorption Checklist

Parent owner `Antigravity` should absorb this packet only after:

- every child is merged or explicitly superseded with evidence;
- deployed SHAs are known descendants of the recorded merges;
- desktop and mobile prove the complete loop and canonical/legacy navigation;
- requested versus fulfilled filter/snapshot context is recorded per hop;
- formal and non-formal data states remain visibly distinct with no false
  zeroes or non-finite display values;
- Rankings owns full ranking tables and Governance references immutable ranking
  evidence without direct apply authority;
- Human Review ends in a traceable receipt or a precise safely non-applied
  result;
- residual BFF/identity/receipt gaps have named owners and do not get concealed
  by frontend joins;
- parent reviewer `Claude` evaluates the composed delivery independently.

This packet cannot serve as child merge evidence, deployment evidence, hosted
proof, Human/Ops approval, or parent closeout on its own.

## 7. Reviewer Handoff

Reviewer `Antigravity` should verify that this packet:

- changes only support material and does not redefine canonical/runtime truth;
- maps the hosted loop to concrete BFF and frontend evidence without inventing
  a closeout aggregate endpoint;
- keeps source-confidence and lifecycle states fail-closed;
- requires stable identifiers and deployed-revision ancestry rather than
  display-level correlation;
- gives the parent a usable evidence matrix and residual-gap disposition.

Recommended review command:

```bash
AI_NAME=Antigravity \
  REVIEW_FILE=support/sidecars/MGMT-PERF-IA-008/MGMT-PERF-IA-008-SIDECAR-BFF-HANDOFF.md \
  ./scripts/ai-status.sh approve MGMT-PERF-IA-008-SIDECAR-BFF-HANDOFF \
  "Support-only hosted BFF/frontend evidence handoff approved for parent absorption."
```

## 8. Preparation Evidence

- Prepared on `task/MGMT-PERF-IA-008-SIDECAR-BFF-HANDOFF` with `origin`
  pointing to `ajoe734/pantheon`.
- Read the task-scoped brief, collaboration guide, anchor/closeout rules,
  parent execution packet, and related sidecar handoffs.
- Inspected the relevant registered BFF route families and existing task
  delivery records; no runtime or frontend file was changed.
- Did not scan `current-work.md` or the full `ai-activity-log.jsonl`.
