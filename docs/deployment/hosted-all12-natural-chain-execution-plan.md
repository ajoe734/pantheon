# Hosted All-12 Natural Chain: Executable Completion Plan (addendum)

- Task: `H12-NATURAL-CHAIN-PLAN-20261026` (docs-only; owner Antigravity, reviewer Codex)
- Dated: 2026-10-26. Source/cutoff: protected `dev` at `7adfefbbc0b17e756229e751a298f9570a6c7e10`
  (PR #6819 merge). Facts below are as of 2026-10-26T07:21Z.
- Status: **planning/source addendum.** It is not hosted acceptance, not an all12 report, not an
  authorization artifact, and executes nothing. It does not amend the approved original design
  [hosted-all12-causal-chain-design.md](hosted-all12-causal-chain-design.md); where this file and the
  original disagree, the original governs.

## 0. Decision summary and non-goals

1. Continue the **original single natural chain** `h12-01a11-20261023-9aeb` (runtime `rt-eb295fe2`, binding
   `rb-eea1cd16cf3745479f42e8b4440b3c27`, frozen artifact checksum
   `fa3a3cdf56c61c8ada5cf29863a89204e13aafa89845b98fe60bff419c6aed75`). No isolated or fabricated positive
   case replaces it. IDs, raw records, timestamps, prior approvals, model and parameters are preserved.
2. Three different comparisons were being conflated. They are separated in §3: structural state checks,
   financial/strategy comparison, and the real execution-integrity invariant.
3. The real historical duplicate-fill anomaly is the candidate original L10 path. Its owner output and
   consumer still have to be proven; today only the late-discovery source limitation is fixed (merged, not hosted).
4. The ordered checklist (§8) is the continuation. Each step runs through its existing owner or entry point;
   Markdown does not execute anything.

Non-goals: new service/queue/dispatcher/parent/resolver/issuer/framework; edits to the three formal parent
reports or PR #6668; product/workflow/test/policy/threshold/schema changes; a 39-owner GET matrix;
per-release or every-version rollback; second human/MFA/grant/attestation; forcing a positive outcome;
re-running E/AC5, holding-UI, H2/Teaching/model/Research or earlier loop phases for freshness; any
Source/H1/Alpha/Teaching/model/new-case progression beyond §7.

## 1. SA: ownership and caller → output → consumer graph

| Plane | Owner / entry point | Produces | Consumed by | Never |
|---|---|---|---|---|
| Development tooling | V2 TaskStore, supervisor, auto-workers, `scripts/ai-status.sh` | task state, PR delivery | humans/workers | product truth (see [boundary](../02-architecture/development-tooling-product-boundary.md)) |
| Delivery | existing hosted publisher/lease workflows | exact-version FE/BFF pair, manifest | hosted runtime | become task authority or a second dispatcher |
| Source / Registry / Research | `services/source_ingestion/` (`distillation_worker.py`), Registry | Source record, draft StrategySpec | Registry, later H1 | write from BFF/OpenClaw |
| Runtime / Telemetry | runtime-manager, `services/telemetry/` | canonical events (`created_at`), loop receipts | Reconciliation, Incident, Evolution | be edited retroactively |
| Reconciliation | `services/reconciliation-drift/main.py` | evaluations, DriftReport, Incident via `_classify_drift_report_incident` → `POST /api/incidents/consume-drift-report` | Incident | be replaced by an operator POST |
| Incident | `services/incidents/main.py`, `services/incident/reference_validation.py` | owner-stored IncidentCase | Evolution/H4 | back-date or reactivate bindings |
| Evolution / H4 | `services/evolution/`, [EVOLUTION_REVIEW_AND_THRESHOLDS.md](../../EVOLUTION_REVIEW_AND_THRESHOLDS.md) §6.1, §12 | proposal, decision | downstream plane only if executed | execute without downstream acceptance |

The original sole runtime/source sequencer and the original report/TOTAL roles are unchanged. Product
BFF/OpenClaw (`POST /bff/management/nl/ask`, `kernel_debug`) is read-only diagnostics and never writes source
or canonical tasks.

## 2. Verified / merged-not-hosted / required / unknown matrix (cutoff above)

| Fact | Status | Ref |
|---|---|---|
| A/B/C completed; 9 receipts + independent typed health = 10 actual loop receipts | verified | private sanitized audit root (location only) |
| D40243931212 | **business PAUSED** (workflow success ≠ pass); 6 actual-state checks succeeded; `baseline_metric_count=0`/`drift_check_count=0` = **unmeasured, not zero drift** | D402439 auth-repair readback |
| Evolution `.0325` input coverage | verified eligible/complete; no forecast forced | healthy-outcome review 20261025 |
| E40263493681 and rollback/ack coverage E40274396263 | **completed; do not rerun.** Verify exact official/cache scope when citing | closeout evidence |
| Original binding retired | 2026-10-26T03:20:29Z; baseline immutable | binding record |
| Holding-currency UI correction | hosted and verified on f9abe36e / FE24534489 (one attempt, 13 bounded checks); no rerun | Root153 evidence |
| Duplicate execution anomaly | **real, historical**; cause (fence/version) **unknown**, under single designated observer | §4 |
| Incident POST at 2026-10-26T06:28:42Z | **HTTP 422** solely because discovery was after retirement; owner GET afterwards 404; no Incident exists | [INCIDENT-LATE-DISCOVERY evidence](evidence/INCIDENT-LATE-DISCOVERY-WINDOW-20261026/evidence.json) |
| PR #6819 (late discovery window, merged 2026-10-26T07:05:24Z, `7adfefbbc0b17e756229e751a298f9570a6c7e10`) | merged **not hosted**; not proof of a hosted Incident | GitHub metadata read |
| PR #6821 (Source recipe metadata, merged 2026-10-26T07:00:25Z, `a65520039d67f5fbde3ffd8ffb79c9d6d0f37fde`) | merged **not hosted** | [SOURCE-DISTILLATION evidence](evidence/SOURCE-DISTILLATION-RECIPE-METADATA-20261026/evidence.json) |
| Served runtime | f9abe36e / execute-plans24534489 / run40253695959 (accepted at 07:07Z); has neither fix | coordination tail |
| Parked source-only draft | workflow40278697937 failed before H1; job `distill-f1d4ced550481c2ebf8c3120` failed, DLQ 3/3 | Source diagnosis |
| Full financial comparator | **unavailable** (§3.2) | feasibility `NOT_PUBLISHED_NOT_AUTHORITY_ACCEPTED` |
| Hosted L10 Incident, L11/H4 same-chain decision | **not yet observed** | – |
| Full all12 and independent TOTAL | **false** | – |

Keep separate in any report: observer/harness commit, served commit, historical event-producer version, and
artifact checksum. No future success is written in past tense in this file.

## 3. Three comparison paths (never one baseline)

### 3.1 Structural state checks (exists)
Valid completed actual-state analysis of the original window. Proves the pipeline ran; it proves **no**
measured financial zero.

### 3.2 Financial / strategy comparison (unavailable until facts exist)
Needs, as independently produced inputs: exact model/version/checksum of the target; window; initial state;
cadence; cost model; units; provenance, as-of and freshness; immutable delivery refs.

| Field group | Producer → publisher → consumer | Today |
|---|---|---|
| Target model/version/checksum | Registry/Research → Registry → Reconciliation | artifact checksum exists; replay model identity not matched |
| Window, initial state, cadence, costs, units | independent baseline preparer (Research owner) → delivery → Reconciliation | **no published body** |
| Provenance/as-of | same | missing |

Rules: approved Evolution `.0325` is `expected_drawdown`, not a Reconciliation denominator; the 49-bar/48-fill
historical replay is not the current two-fill window and must not be renamed as its benchmark; a same-as-of
quote comparator is quote consistency, not strategy performance; no invented OHLCV/ref/expected values or
resolver; Research profile guard unchanged. If facts stay insufficient this path is recorded **unavailable**,
and §3.1/§3.3 results are not relabelled as it.

### 3.3 Real execution-integrity invariant (L10 candidate)
Invariant: one logical signal/order intent ⇒ at most the allowed quantity committed at the broker, within one
binding/runtime/artifact/source bar. It is not a performance baseline. Proof comes only from canonical records
(telemetry events by `GET /api/telemetry/events/{id}` and broker order IDs), scope key
(tenant, binding, runtime, plan, artifact checksum, signal, run, source bar) and allowed quantity read from
the owner record. Expected values are never copied from the observation.

Negative cases that must not be counted as a breach: partial fills of one order; duplicate transport receipts
of the same broker order; corrected/reversed events; fills in a different window/scope.

## 4. The real historical anomaly (stated without assuming cause)

First fill `4f1e0f1b-f167-4b7f-9673-7f7f9a71c3a8` and second fill `a4263fe2-9448-42bf-9cf2-30f28fd89fc9`
are two **distinct committed full SELL-1 orders** for the same signal/run/binding/runtime/artifact/source bar.
Whether current code is still vulnerable is **unknown**; no third fill is created. Disposition step S1 (§8)
records, from the designated single VM observer, the fence/version cause (or "undetermined") before claiming
more. A breach is asserted only after the negative cases in §3.3 are excluded from canonical records.

## 5. SD: data and temporal contracts

Conceptual unless noted; existing DTO fields only, no new mandatory field.

- Identity: tenant, binding, runtime, plan, pool, artifact id/version/checksum, signal, run, order, broker order,
  event ids (existing DTO).
- Time: **occurrence** = immutable Event DTO top-level `created_at` (existing; `event_time` exists only inside
  `correlation_envelope`). **Discovery/receipt** = Incident `created_at`, preserved unchanged. Discovery after
  retirement is not event occurrence.
- Window: [`effective_at`, `retired_at`] of the **original** binding. No reactivation, backdating or role change.
- `_binding_window_errors` (`services/incident/reference_validation.py`) after #6819: late-created Incident
  qualifies only if every referenced event resolves via `GET /api/telemetry/events/{id}` (id matches) with
  `created_at` valid RFC3339 UTC inside the window. Missing/unverifiable/outside window keeps rejection;
  identity/lineage checks unchanged. Active-binding requirements stay for **new** execution only.
- Source: as-of and producer code provenance recorded with the served commit, separate from the artifact checksum.

## 6. L10 and L11/H4

### 6.1 L10 owner route
- Preferred (original design: Reconciliation-derived): a Reconciliation DriftReport/evaluation →
  `POST /api/incidents/consume-drift-report`. This needs a duplicate-execution check; no such check was found
  among the Reconciliation evaluation/reconcile routes (`/evaluations`, `/paper-runs/reconcile`,
  `/incident-triggers/consume`, `/scheduled-reconcile`) as read at the cutoff. **Unverified gap**; to be
  re-confirmed in S2.
- Actually supported today: operator-discovered `POST /api/incidents` (`create_incident`). This is
  **operator discovery, not automatic Reconciliation detection**, and is never presented as such. Whether it
  satisfies the original L10 design is a reviewer decision recorded at S6, not assumed.
- If a duplicate-execution check/delivery contract is absent, the smallest follow-up is an existing-owner
  source task (Reconciliation owner) through the normal supervisor flow. No fabricated DriftReport, report
  refs or API.
- Branch closed only with: real canonical event links, owner-stored Incident, and delivery/readback refs.

### 6.2 L11/H4
- Real proposal/decision derived from the actual same-chain Incident; exact target/pool/tenant/lineage;
  standing genuine h12 paper decision authorization (H1–H4 consent remains valid; no new human/MFA/grant).
- `approved` ≠ `executed` ([§12.1](../../EVOLUTION_REVIEW_AND_THRESHOLDS.md)): `executed` requires the
  authoritative downstream plane to accept and return an immutable ref. Downstream ack and cooldown origin
  (§12.3) are required **only** if the chosen branch claims execution. Approval-only/no-action branches have
  no such prerequisite; no universal gate is added. No forced model change; successful models are not rerun.
- Unsupported historical action stays a named blocker/residual.

## 7. Source-only draft (existing failed job)

Cause: producer copied Source-authoritative `strategy_seed.execution_recipe` into EvidenceItem metadata and the
Source-only validator correctly rejected it. #6821 strips only that nested copy (deepcopy); Source recipe,
other metadata and guard stay. After an accepted fixed runtime: reset/redrive **only** the existing failed
job `distill-f1d4ced550481c2ebf8c3120` once, to materialize the parked draft; DLQ/failed history retained;
the new case stays parked (no H1/Alpha/Teaching/model/new case). Owner interface:
`DistillationWorker.reset_to_pending` / `redispatch` (`services/source_ingestion/distillation_worker.py`).
**No HTTP route for it was found at the cutoff**; S8 must name the verified existing invocation path
(otherwise it is a named blocker, not an invented route).

## 8. Ordered execution checklist (after this doc is reviewed and merged)

| # | Step | Owner / entry | Read-only preflight | Evidence | Success / pause / refusal | Failure / retry / cleanup |
|---|---|---|---|---|---|---|
| S0 | This doc review + merge | supervisor, Codex review, required checks | PR diff scope = 3 files | PR/head/merge | merged; else exact blocker | fix in same PR |
| S1 | Fence/version-cause disposition | designated single VM observer | canonical order/event records only | cause or "undetermined" | recorded either way; no code assumption | no third fill |
| S2 | Confirm #6819/#6821 review/CI/integration; recheck L10 gap | GitHub metadata read | merge SHAs, dev head | refs | merged ⇒ proceed; else pause | none |
| S3 | One grouped exact-pair dev delivery | existing publisher/lease only | hosted manifest identities | manifest + workflow | pair accepted; failed gate ⇒ no switch | existing rollback; no second dispatcher |
| S4 | One late-discovery Incident retry after accepted runtime | Incident owner | served commit includes #6819; event `created_at` in window | owner POST response + GET readback | 201 + stored Incident; 422 ⇒ record exact reason | no backdating; no repeat loop |
| S5 | Parked Source job reset/redrive once, draft-only | §7 interface | job still failed; runtime includes #6821 | job + draft readback | draft materialized; failure ⇒ keep DLQ history | no second redrive |
| S6 | L10 conformance readback | reviewer | Incident links to canonical events | owner refs | closed per §6.1 or named gap | smallest existing-owner task |
| S7 | L11/H4 proposal/decision readback | Evolution owner | Incident ref, standing consent | decision record | per §6.2 | residual if unsupported |
| S8 | E/AC5 | – | cite completed E40263493681 / E40274396263 | refs | **no replay** | – |
| S9 | Original report writer updates original three files; assigned independent TOTAL | existing writer/reviewer | all refs | reports | per §10 | – |

Source changes needed later stay in the supervisor/task/PR flow; queues and canonical JSON are not hand-edited.

## 9. Outcome / coverage matrix

| Outcome | Meaning | Proves | Does **not** prove |
|---|---|---|---|
| unmeasured | no baseline/drift computed (D today) | pipeline ran | zero drift |
| incomplete input | required input missing | – | healthy |
| measured healthy/zero | comparator ran on valid inputs | that comparator only | other loops |
| eligible no-op/zero candidates | evaluated, nothing to propose | Evolution input coverage | action |
| real anomaly | §3.3 invariant violated on canonical records | L10 input | Incident stored |
| proposal recorded | owner stored proposal | L11 | decision |
| decision accepted | `approved` | H4 | `executed` |
| actually executed | downstream ack + immutable ref (+cooldown origin) | execution branch | other branches |
| cleanup complete | retirement/rollback evidence | dated rollback proof | – |
| unobserved | branch not exercised | – | must stay partial |

No "12/12" from CI, UI, source merge, normal heartbeat, generic Incident POST, or unrelated infra events.

## 10. Definition of done

- Original six AC, 21 dependencies, 49-checkpoint closure, three formal reports and H1–H4 scope are preserved;
  true unaffected auth/tenant/idempotency/persistence/dated-rollback evidence is reused.
- All12 is claimed only with the originally required real outputs and consumers; otherwise the result is
  explicitly **partial** and the parent stays open. D10 receipts paused/unmeasured is not a 12-pass.
- Staging/prod do not advance before this goal. No source or business action before this plan is
  reviewed; running CI is not cancelled.
