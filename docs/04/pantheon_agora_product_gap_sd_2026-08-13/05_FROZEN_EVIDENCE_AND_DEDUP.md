# Frozen Evidence and Deduplication Record

## 1. Why this record exists

This file separates observed evidence from design conclusions. The snapshot was
taken once during the 2026-08-13 audit so that continuously running workers do
not cause repeated plan churn. Before future execution-task generation, one new
bounded deduplication snapshot is required; the design should not otherwise be
rewritten to follow every worker transition.

## 2. Source baselines

### Pantheon

- repository: `ajoe734/pantheon`
- branch: `origin/dev`
- frozen SHA: `3307552b55af75850dab1d50e58cef9f86e10b53`
- audited in a clean isolated worktree; the shared `/home/lupin/pantheon`
  checkout was intentionally not used for edits because it contained active
  supervisor/worker changes.

Large Agora routers at the frozen baseline:

| File | Lines |
|---|---:|
| `services/control-plane/bff/agora/strategy_workshop/router.py` | 4,021 |
| `services/control-plane/bff/agora/research/router.py` | 2,628 |
| `services/control-plane/bff/agora/trading_room/router.py` | 3,561 |

Line count alone is not a defect. It corroborates that HTTP routing, domain
policy, projection, persistence orchestration, and downstream integration have
accumulated in the same modules and should be separated incrementally.

### Frontend

- repository: `ajoe734/execute-plans`
- branch: `origin/dev`
- frozen SHA: `3ee9f962a36626f085e2ca1c088b3ce4b4d08e6f`
- `dev` is the delivery base; `main` and Lovable are not delivery truth.

Runtime import evidence:

- `src/agora/AgoraApp.tsx` has no non-test consumer;
- `WorkshopCardRenderer` has no non-test consumer;
- the page-local CandidateReviewDrawer is the runtime implementation, while
  the BFF-wired `src/agora/components/CandidateReviewDrawer.tsx` has no
  non-test consumer;
- the old `WidgetRenderer` is consumed by `src/agora/dashboard/*` and the old
  `WidgetRevisionDrawer`; that island is not referenced by active Agora routes;
- the live path uses `src/agora/trading-room/*` and `ChartSpecRenderer`.

These observations justify a consolidation/removal task, not deleting files
without first migrating unique tests and behavior.

## 3. Direct code observations

The GAP report derives its high-severity findings from these concrete source
facts:

| Observation | Frozen source evidence |
|---|---|
| Workshop completeness is caller-provided | `state_map_json` request model and completeness POST persist caller blockers/NBQ |
| Workshop readiness has synthetic identity | readiness falls back to `unbound-{workshop_id}` |
| Trading Room caller supplies gate truth | proposal parses `tradingRoomReady`, plus client evidence/freshness fields |
| Trading Room lacks real position/risk projection | aggregate is built with empty positions and `RiskSummary(state="normal")` |
| Research/Trading mutations use read authorization | mutation handlers repeatedly call `require_read_role` and their router constructors do not receive a write-role dependency |
| Candidate production is hard-coded | candidate creation iterates `_default_registry_candidates` |
| Workspace/Widget authoring is client-faked | frontend keyword/regex helpers construct intent/spec and mock actions update client state |
| Performance suggestions have no producer | non-test search found no `PerformanceSuggestionStore.upsert_suggestion` caller |
| Decision events have no producer | non-test search found no production `upsert_decision_event` caller outside store ownership |
| Workshop canonical calls lack explicit service context | urllib requests attach JSON headers but no explicit service credential/delegated tenant envelope |
| Policy candidate intake can process inline | handoff honors `process_immediately` and claims/processes in the request |
| Consultation intake publishes its own result | policy-candidate intake defaults auto-decision to approved, creates published memo at confidence 0.95, and advances sponsor bridge |

“No producer found” means no non-test caller was present in the audited source
tree. It is intentionally narrower than claiming no external process could ever
write the same database.

## 4. Hosted-state snapshot

Read-only probes of the current Pantheon-owned dev host observed:

| Probe | Result |
|---|---|
| `GET https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io/deployment.json` | frontend `6a8d2d9b4f725056735eefd7165ef47b52cda53d`; declared BFF `be956c07aca889043ef301389412b6744452f20b`; live/strict; real/stub writes false |
| `GET https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io/bff/version` | running BFF `6367cea609e9d19053130ab8f9b1946d5d35dfc6`, HTTP 200 |
| `GET https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io/readyz` | HTTP 503/degraded; lifecycle projector controller/worker `repair_only`; `accepted_live=false`; cursor inconsistency reported |

Therefore the served pair is not the manifest exact pair. The write posture is
safely read-only, but this snapshot cannot reaccept old hosted write evidence.

## 5. Bounded data-state observation

A read-only database/runtime snapshot collected during the same audit showed:

| Domain | Observed state |
|---|---|
| Workshop | 119 sessions: 114 open, 4 in review, 1 concluded |
| Workshop cards | 14 cards, all `consult_result`; no broad typed strategy-card journey |
| Workshop events/version links | 74 events and 10 version links |
| Completeness/readiness | no materialized authoritative completeness or readiness rows in the observed set |
| Persona | 14 requests, 14 syntheses, 25 revisions, 267 proposals |
| Candidate/Research | effectively no real candidate universe |
| Trading Room | one persisted workspace/store payload, effectively empty |
| Performance | zero suggestions, receipts, and action-audit rows |
| Dataset extraction | 81 observe records, 81 handoffs pending, no complete learn chain |
| Policy learning | no current Agora policy candidate chain in the observed set |

These counts are point-in-time evidence, not an evergreen monitoring claim.
They support the code finding that Persona output is materialized while the
core Strategy reconstruction/candidate/Trading/Performance/Learning journey is
not.

## 6. Task and PR deduplication snapshot

### Archived task rows found

The following Agora-related rows were archived/done in canonical task history:

- `AGORA-CURRENT-CLOSE-20260808`
- `AGORA-CURRENT-GAP-EXECUTION-20260808`
- `AGORA-CURRENT-HOSTED-REACCEPT-20260808`
- `AGORA-DEV-DEPLOY-RECOVERY-20260808`
- `AGORA-IMIT-HANDOFF-CONSUME-20260808`
- `AGORA-L12-CROSS-LOOP-INTEGRATE-20260808`
- `AGORA-L12-REAL-VERIFIER-20260808`
- `AGORA-LEARNING-CROSS-LOOP-BIND-20260808`
- `AGORA-UI-LIFECYCLE-RECONCILE-20260808`
- `PRODUCT-V2-AGORA-DATASET-R3-20260813`
- `PRODUCT-V2-AGORA-LEARNING-CLOSURE-20260813` (superseded in its own board
  history)
- `PRODUCT-V2-AGORA-LEARNING-CLOSURE-R2-20260813`

Interpretation:

- archive status proves the bounded task was processed according to its task
  definition;
- it does not prove a current UI caller, production producer, owner scope,
  independent reviewer stage, current deployment, or the complete product
  journey;
- future task generation must not blindly reopen the same evidence/test scope;
  it must target the source gaps and replacement design in this packet.

### Recent merged code/evidence considered

- PR #4821: Agora dataset R3 tenant-safe extraction/handoff tests/evidence;
- PR #4824: policy-learning R3 candidate/handoff/worker code;
- PR #4823: Consultation R3 policy-candidate intake code;
- PR #4813: Learning closure R2 evidence.

The current audit includes these changes. In particular, it does not repeat the
old claim that policy-learning or Consultation endpoints are absent; it finds
that delivery and independence semantics remain incorrect/incomplete.

### Open PR and branch snapshot

- no open PR with Agora in the title was returned for Pantheon;
- no open Agora PR was returned for `execute-plans`;
- `origin/task/L12-CODE-GAP-SD-20260813` is a twelve-loop product audit branch,
  not an Agora product task, and is excluded;
- the earlier local Agora current-gap packet/worktree is historical planning
  state and is not used as proof of current product completeness.

No new task packet was sent during this audit.

## 7. Evidence interpretation rules for future closeout

The future implementation closeout must preserve these distinctions:

| Evidence | Proves | Does not prove |
|---|---|---|
| Unit test | local behavior under its fixture | runtime caller or hosted delivery |
| Endpoint/OpenAPI | contract is addressable | producer, owner scope, or UI reachability |
| Direct store seed | projector/view behavior | production input path |
| Archived task | task lifecycle completed | current source/environment/product journey |
| Merged PR | source is in target branch | deployed symlink/manifest serves it |
| Hosted manifest | declared accepted candidate | running service identity unless read back |
| HTTP 2xx | transport request succeeded | durable downstream result without receipt/readback |
| Historical exact-pair proof | that pair passed at that time | current drifted pair passes now |

The full product is accepted only by correlated source, receipt, event,
artifact, identity, deployment, and browser evidence for the same current pair.
