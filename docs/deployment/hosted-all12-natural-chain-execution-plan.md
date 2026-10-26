# Hosted All-12 Natural Chain: Executable Completion Plan (addendum)

- Task: `H12-NATURAL-CHAIN-PLAN-20261026` (docs-only; owner Antigravity, reviewer Codex)
- Dated: 2026-10-26. Source/cutoff: protected `dev` at `7adfefbbc0b17e756229e751a298f9570a6c7e10`
  (PR #6819 merge). Facts below are as of 2026-10-26T07:21Z, with the same-task rework facts of 07:24–07:41Z
  (completed-marker receipt, PR #6822 task) labelled where used.
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
   consumer still have to be proven. Merged-not-hosted source fixes: late-discovery window (#6819) and Source
   recipe metadata (#6821). A third necessary source fix, completed-fence TTL (#6822, in review), addresses a now
   reproduced current defect (§4); source fixed is not runtime safe.
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
| Source / Registry / Research | `services/source_ingestion/` (`distillation_worker.py`), Registry | Source record, draft StrategySpec | Registry, later H1 | be written by repository/file/queue hand edits |
| Runtime / Telemetry | runtime-manager, `services/telemetry/` | canonical events (`created_at`), loop receipts | Reconciliation, Incident, Evolution | be edited retroactively |
| Reconciliation | `services/reconciliation-drift/main.py` | evaluations, DriftReport, Incident via `_classify_drift_report_incident` → `POST /api/incidents/consume-drift-report` | Incident | be replaced by an operator POST |
| Incident | `services/incidents/main.py`, `services/incident/reference_validation.py` | owner-stored IncidentCase | Evolution/H4 | back-date or reactivate bindings |
| Evolution / H4 | `services/evolution/`, [EVOLUTION_REVIEW_AND_THRESHOLDS.md](../../EVOLUTION_REVIEW_AND_THRESHOLDS.md) §6.1, §12 | proposal, decision | downstream plane only if executed | execute without downstream acceptance |

The original sole runtime/source sequencer and the original report/TOTAL roles are unchanged. Plane boundary:
the product BFF may perform **authorized business-owner writes** through existing owner routes (Research, Source,
Registry, Incident, Evolution); that remains legitimate. What product BFF/OpenClaw must not have is
repository/source-code write, canonical development-task/worktree authority, or deployment authority.
`kernel_debug` (`POST /bff/management/nl/ask`) is read-only diagnostics. No new BFF/tooling bridge endpoint is
added by this plan.

## 2. Verified / merged-not-hosted / required / unknown matrix (cutoff above)

| Fact | Status | Ref |
|---|---|---|
| A/B/C completed; 9 receipts + independent typed health = 10 actual loop receipts | verified | private sanitized audit root (location only; hashes recorded in the sanitized audit index, no raw payload copied) |
| D40243931212 | **business PAUSED** (workflow success ≠ pass); 6 actual-state checks succeeded; `baseline_metric_count=0`/`drift_check_count=0` = **unmeasured, not zero drift** | [HOSTED-ALL12-D-OWNER-AUTH-20261025 evidence](evidence/HOSTED-ALL12-D-OWNER-AUTH-20261025/evidence.json) |
| Evolution `.0325` input coverage | verified eligible/complete; no forecast forced. Coverage read ≠ recorded producer tick (§9) | healthy-outcome review 20261025 (sanitized audit ref) |
| E40263493681 and rollback/ack coverage E40274396263 | **completed; do not rerun.** Verify exact official/cache scope when citing | [HOSTED-ALL12-E-EXECUTOR-ROLE-20261026 evidence](evidence/HOSTED-ALL12-E-EXECUTOR-ROLE-20261026/evidence.json); retirement/cleanup and rollback/ack-data legs are separate evidence |
| Original binding retired | 2026-10-26T03:20:29Z; baseline immutable | binding record |
| Holding-currency UI correction | hosted and verified on f9abe36e / FE24534489 (one attempt, 13 bounded checks); no rerun | official workflow run/artifact for FE24534489 and Root sanitized audit ref (no repo manifest located at this cutoff) |
| Duplicate execution anomaly | **real, historical**; current vulnerability reproduced (completed-fence TTL), per-execution historical version attribution still separate | §4 |
| Incident POST at 2026-10-26T06:28:42Z | **HTTP 422** solely because discovery was after retirement; owner GET afterwards 404; no Incident exists | [INCIDENT-LATE-DISCOVERY evidence](evidence/INCIDENT-LATE-DISCOVERY-WINDOW-20261026/evidence.json) |
| PR #6819 (late discovery window, merged 2026-10-26T07:05:24Z, `7adfefbbc0b17e756229e751a298f9570a6c7e10`) | merged **not hosted**; not proof of a hosted Incident | GitHub metadata read |
| PR #6821 (Source recipe metadata, merged 2026-10-26T07:00:25Z, `a65520039d67f5fbde3ffd8ffb79c9d6d0f37fde`) | merged **not hosted** | [SOURCE-DISTILLATION evidence](evidence/SOURCE-DISTILLATION-RECIPE-METADATA-20261026/evidence.json) |
| Served runtime | f9abe36e / execute-plans24534489 / run40253695959; public `acceptedAt` 2026-10-26T02:22:36Z, Root terminal 02:23:23Z (07:07Z was only a later observation); has none of #6819/#6821/#6822 | coordination tail |
| Parked source-only draft | workflow40278697937 failed before H1; Source `hosted-all12-h12-01a11-20261026-d834-src`, existing failed job `distill-f1d4ced550481c2ebf8c3120`, DLQ 3/3 | Source diagnosis |
| Completed-fence TTL defect | **reproduced current defect** (isolated real Redis, exact f9 code, three cold processes, production TTL 86400 shortened to 1 s in fixture: enqueue 1/0/1, side effects 1/1/2). Not a third real hosted order; not attribution of each historical execution/version | `/tmp/pantheon-continue-20261023/duplicate-signal-ttl-repro/accepted-f9-real-Redis-three-cold-process-TTL-reproduction-20261026.json` sha256 `c207aa18f45b781a07c39330e98189919eee731e708d4f8a5a18310e527feee1`; task `PAPER-COMPLETED-FENCE-TTL-20261026` |
| PR #6822 (completed-fence TTL source fix) | in review at this rework; **not merged, not hosted** | task `PAPER-COMPLETED-FENCE-TTL-20261026`; re-read GitHub metadata at S2 |
| Existing completed markers (two owner-verified PAPER bindings) | preservation **applied** by one temporary live owner action 2026-10-26T07:24:57Z: `PERSIST` returned 1, PTTL -1, value sha unchanged, no new key/marker/order; expired markers not recovered. Does not replace #6822 for future completions | receipt sha256 `8a506348f6a9d5fc21fe9f40e113cdb9efa9b01ae4ffe35210343186ed2a4b29`; preflight sha256 `e0d144c449a24643a6fef6d2ee705415c007c9dc580cd097524d0a88ed309ecf`; first-attempt guard failure (short container ID, no mutation) sha256 `295695b38998bb2ef25e83b77ec7db8226c1d783981e209ab942c7576e44d423` |
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
A breach is asserted only after the negative cases in §3.3 are excluded from canonical records.

Two different things are kept apart:

- **Reproduced current vulnerability**: the completed execution fence and processed cache expire together
  (Redis Lua `SETEX`), so after expiry a cold producer re-enqueues the same immutable decision and a cold worker
  re-acquires execution authority. Proven by the isolated real-Redis three-cold-process reproduction (§2 row, sha
  `c207aa18…feee1`); current dev `d834` modules match f9. This is a source-level reproduction, not a third real
  order and not proof of the historical server expiry event itself.
- **Attribution of each historical execution/version**: unchanged evidence question; recorded per execution from
  canonical records, never inferred from the reproduction.

Delivery: #6822 (source review, CI, integration) joins **one** grouped necessary dev delivery with #6819/#6821
(S3). Source fixed ≠ runtime safe: only an accepted served runtime containing it counts.

**Existing completed markers (bounded, already applied; not repeated).** Owner-supported action: conditional
`PERSIST` of only the two already-existing, owner-verified PAPER `completed:` keys, exact value/SHA unchanged,
PTTL>0 → `PERSIST` 1 / PTTL -1. Missing/changed/started/already-persistent keys are skipped and reported; no
`SET`/`DEL`, no recovery of expired markers, no global scan, no completion manufactured from a processed cache;
raw tokens/credentials stay private. Actual sequence: read-only preflight 07:15Z → first attempt aborted by the
short-container-ID guard **before any mutation** (07:21Z readback, mutations 0) → corrected single action
2026-10-26T07:24:57Z, labelled temporary live safety exception. It is never repeated and never undone, and it does
not replace #6822 durability for future completion. The source worker executes none of it.

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
  satisfies original L10 is decided **now** by the assigned doc review against the original contract; S6 only
  applies the recorded criteria through the existing sequencer/verifier. No runtime reviewer-decides-scope gate
  is added.
- If a human-discovered Incident alone does not establish Reconciliation detection/consumption, the exact
  missing owner output (a Reconciliation duplicate-execution result consumable by `consume-drift-report`) is
  stated and the smallest follow-up named; it is never relabelled as automatic detection.
- If a duplicate-execution check/delivery contract is absent, the smallest follow-up is an existing-owner
  source task (Reconciliation owner) through the normal supervisor flow. No fabricated DriftReport, report
  refs or API.
- Branch closed only with: real canonical event links, owner-stored Incident, and delivery/readback refs.

### 6.2 L11/H4
Verified existing interfaces (`services/evolution/main.py`): `POST /api/evolution/proposals/from-incident`
(derives canonical Incident/Postmortem lineage; **stops at proposed**), `GET /api/evolution/proposals/{decision_id}`,
`POST .../review`, `.../approve`, `.../reject`, `.../cancel`, `.../execute`, `GET .../boundary`. Use proper existing
owner authentication/tenant context (no raw credentials in docs, no generic fake bodies). A real proposed
`EvolutionDecision` is a proposed decision record, not H4 approval or execution: outcome "proposal recorded"
proves L11 only. Executor-fence faults must not be silently mapped to an unrelated model action and retired
resources are not reactivated; historical read/proposal is kept separate from any new active execution.
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
other metadata and guard stay.

After an accepted fixed runtime, **one** administrative retry (reset + redispatch is one action, not two):

1. Preflight: `DistillationJobQueue.get(source_id)` for Source ID `hosted-all12-h12-01a11-20261026-d834-src`
   (not the job ID) must resolve to the existing failed job `distill-f1d4ced550481c2ebf8c3120`. If unresolved,
   stop: `reset_to_pending` would create a **new** job when none exists, so it is never called on a guessed ID.
2. Action: `DistillationWorker.redispatch(source_id, source)` (`distillation_worker.py` ~L1793; validates the
   Source is non-rejected, delegates to `DistillationJobQueue.reset_to_pending(source_id, requested_by=...)`,
   ~L1320; idempotent while pending). Failed/DLQ history is preserved through this owner method, never SQL/file/
   queue JSON edits.
3. Processing: the existing worker/controller route for the existing processing method
   (`DistillationWorker.run_pending`); no HTTP route is invented and no broad drain of other jobs. `limit=1`
   is not assumed to target this Source; confirm the processed job's `source_id` in readback.
4. Readback: job + draft materialized; the new case stays parked (no H1/Alpha/Teaching/model/new case).

If the existing invocation path cannot be named at S5, that is a named blocker, not an invented route.

## 8. Ordered execution checklist (after this doc is reviewed and merged)

| # | Step | Owner / entry | Read-only preflight | Evidence | Success / pause / refusal | Failure / retry / cleanup |
|---|---|---|---|---|---|---|
| S0 | This doc review + merge | supervisor, Codex review, required checks | PR diff scope = 3 files | PR/head/merge | merged; else exact blocker | fix in same PR |
| S1 | Per-execution historical version attribution (current defect already reproduced, §4) | designated single VM observer | canonical order/event records only | attribution or "undetermined" | recorded either way; reproduction not substituted | no third fill |
| S2 | Confirm #6819/#6821/#6822 review/CI/integration; recheck L10 gap | GitHub metadata read | merge SHAs, dev head | refs | merged ⇒ proceed; else pause | none |
| S3 | One grouped exact-pair dev delivery (#6819+#6821+#6822) | existing publisher/lease only, docs-first publisher ownership (§11) | hosted manifest identities | manifest + workflow | pair accepted; failed gate ⇒ no switch | existing rollback; no second dispatcher |
| S4 | One late-discovery Incident retry after accepted runtime | Incident owner | served commit includes #6819; event `created_at` in window | owner POST response + GET readback | 201 + stored Incident; 422 ⇒ record exact reason | no backdating; no repeat loop |
| S5 | Parked Source job one administrative retry, draft-only | §7 (`redispatch` → `run_pending`) | job still failed; runtime includes #6821 | job + draft readback | draft materialized; failure ⇒ keep DLQ history | no second redrive |
| S6 | L10 conformance readback against criteria already recorded by the doc review | existing sequencer/verifier | Incident links to canonical events | owner refs | closed per §6.1 or named gap | smallest existing-owner task |
| S7 | L11/H4 proposal/decision readback (`from-incident`, GET; review/approve only if the branch claims it) | Evolution owner | Incident ref, standing consent | decision record | per §6.2 | residual if unsupported |
| S8 | Completed E/AC5 | – | cite completed E40263493681 / E40274396263 | refs | **no replay** | – |
| S9 | Original report writer updates original three files; assigned independent TOTAL | existing writer/reviewer | all refs | reports | per §10 | – |

Source changes needed later stay in the supervisor/task/PR flow; queues and canonical JSON are not hand-edited.

## 9. Outcome / coverage matrix

| Outcome | Meaning | Proves | Does **not** prove |
|---|---|---|---|
| unmeasured | no baseline/drift computed (D today) | pipeline ran | zero drift |
| incomplete input | required input missing | – | healthy |
| measured healthy/zero | comparator ran on valid inputs | that comparator only | other loops |
| eligible no-op/zero candidates | actually recorded producer tick/result for the same target shows zero candidates (reuse the real tick, no manual sweep) | evaluable no-op | action; an eligible coverage read alone is not this |
| real anomaly | §3.3 invariant violated on canonical records | L10 input | Incident stored |
| proposal recorded | owner stored proposal | L11 | decision |
| decision accepted | `approved` | H4 | `executed` |
| actually executed | downstream ack + immutable ref (+cooldown origin) | execution branch | other branches |
| cleanup complete | E retirement done | retirement only | dated rollback: rollback/ack-data legs are separate evidence |
| unobserved | branch not exercised | – | must stay partial |

No "12/12" from CI, UI, source merge, normal heartbeat, generic Incident POST, or unrelated infra events.

## 10. Definition of done

- Original six AC, 21 dependencies, 49-checkpoint closure, three formal reports and H1–H4 scope are preserved;
  true unaffected auth/tenant/idempotency/persistence/dated-rollback evidence is reused.
- All12 is claimed only with the originally required real outputs and consumers; otherwise the result is
  explicitly **partial** and the parent stays open. D10 receipts paused/unmeasured is not a 12-pass.
- Staging/prod do not advance before this goal. No source or business action before this plan is
  reviewed; running CI is not cancelled.

## 11. Docs-first sequencing and publisher ownership

- The existing `Hourly Publish Cut` workflow (id 278206785, `nightly-publish-cut.yml`) was temporarily disabled
  by this support at 2026-10-26T07:19Z (prior state active; no run cancelled; normal deployment entry left
  enabled). Receipt: `/tmp/pantheon-natural-chain-plan-20261026/publisher-pause-receipt.json`. This plan preserves
  that operator sequencing; it is not a new platform, lock or approval gate.
- After this docs task's genuine review/checks/integration and the required source fixes (#6819/#6821/#6822)
  are ready, the original sole sequencer and the pause owner coordinate restore/adopt of **one** actual delivery
  using existing workflow controls. Another operator's hold is never blindly enabled. Already-running CI/source
  delivery may finish.
- No new product/domain action runs before the docs-first barrier except a separately evidenced minimal safety
  rescue (e.g. the 07:24Z marker preservation, §4); no retrospective cancellation, no new operator/MFA gate.

## 12. Validation record

`git diff --check`, JSON parse of the evidence file, and Markdown link/reference resolution were run locally;
exit statuses are recorded in the evidence file. No functional/hosted/model suites were rerun. This doc rework
is DocSourceReview feedback only; it grants no product authority. The original assigned review must bind the new
exact head and required CI; the earlier head `2e26ac269` is not deliverable.
