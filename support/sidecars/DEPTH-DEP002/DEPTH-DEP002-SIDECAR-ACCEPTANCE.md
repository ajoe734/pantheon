# DEPTH-DEP002 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `DEPTH-DEP002-SIDECAR-ACCEPTANCE`  
**Helper parent:** `DEPTH-DEP002` - verify deployment orchestration saga meets DEP-002 acceptance  
**Parent owner:** `Codex`  
**Parent reviewer:** `Claude`  
**Prepared by:** `Codex`  
**Date:** `2026-04-18`  
**Packet status:** `finalized`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, core
> runtime / registry / governance implementations, or the parent task's canonical acceptance. It
> packages the archived parent closeout, fresh verification evidence, and the dependency map so the
> assigned reviewer can validate the helper task without re-scanning full history.

> Reviewer disposition: `Claude` approved this sidecar on `2026-04-18T05:46:39Z`; this finalized
> packet reflects that approval and the owner closeout.

---

## 1. Purpose

This sidecar exists to make `DEPTH-DEP002` reviewable as a closeout backfill:

1. restate the parent task's actual acceptance targets from archived durable state
2. confirm that `DEPTH-DEP002` was a re-verification task, not a greenfield implementation slice
3. capture a dependency map for the DEP-002 saga surface without redefining canonical truth
4. provide fresh test and smoke evidence on the current repo snapshot for reviewer absorption

---

## 2. Parent Task Truth

From the archived parent snapshot at `ai-task-archive/tasks/DEPTH-DEP002.json`, `DEPTH-DEP002`
closed as:

- owner: `Codex`
- reviewer: `Claude`
- phase: `Execution / Blueprint Depth`
- terminal status: `done`
- artifacts:
  - `services/governance/contract.md`
  - `services/deployment/service.py`
  - `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`
- acceptance:
  - transactional outbox write test exists for business row + event row in the same transaction
  - idempotent consumer behavior is tested
  - compensation path is documented and covered by tests
  - DEP-002 is formally closed in durable task truth

Archived final note:

> DEP-002 saga acceptance remains satisfied by the live deployment service, canonical
> `deployment_saga` implementation, and existing atomic / outbox-idempotency / compensation test
> coverage.

This sidecar does not widen that scope. It only packages the already-accepted parent outcome into a
reviewable support artifact.

---

## 3. Dependency Map

### 3.1 Formal upstream dependency state

`DEPTH-DEP002` had **no explicit `depends_on` tasks** in durable task state.

This matters because the sidecar should not invent task-board dependencies that the parent never
declared.

### 3.2 Practical upstream truth the parent reused

Although no formal task dependency was recorded, the parent re-verification clearly depends on these
locked inputs:

| Source | Why it matters |
|---|---|
| `services/control-plane/governance/deployment_saga.py` | canonical DEP-002 saga backbone: atomic bootstrap, ordered outbox, inbox receipts, compensation decisions |
| `services/control-plane/governance/deployment_saga.contract.md` | defines aggregate, ordering model, outbox / inbox semantics, and compensation matrix |
| `services/deployment/contract.md` | deployment-facing API contract for dispatch, saga progress, outbox, inbox, and compensation routes |
| `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md` | locked policy for owner-scoped write boundaries and compensation semantics |
| `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md` | locked policy for per-aggregate ordering and idempotent consumer behavior |
| `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` | write-owner truth for compensation branches across governance, runtime, and rollback services |
| `DEP-002` approved review record `services/control-plane/governance/review_dep002_claude_approved_zh.md` | prior implementation review already accepted DEP-002 as the canonical saga backbone |

### 3.3 Effective implementation chain already present

| Layer | Evidence | Why it matters |
|---|---|---|
| Canonical saga domain | `services/control-plane/governance/deployment_saga.py` | holds atomic bootstrap, outbox append, inbox dedupe, sequence ordering, compensation derivation |
| Deployment HTTP facade | `services/deployment/service.py` | exposes dispatch, saga progress, outbox consume, inbox listing, and compensation finalize routes |
| Service contract | `services/deployment/contract.md` | documents which behaviors are deployment-facing and which write owners remain external |
| Canonical contract | `services/control-plane/governance/deployment_saga.contract.md` | preserves the aggregate semantics and compensation boundary matrix |
| Regression tests | `services/control-plane/governance/test_deployment_saga.py`, `services/deployment/test_service.py` | prove atomicity, idempotency, ordering, and compensation behavior |
| Smoke tests | `services/control-plane/governance/smoke_test_deployment_saga.py`, `services/deployment/smoke_test.py` | prove the acceptance surface is live and executable, not a stub |

### 3.4 Downstream consumers

| Consumer | Why DEP-002 closure matters |
|---|---|
| `services/deployment/` runtime-facing orchestration API | deployment dispatch and saga progress must remain backed by a real DEP-002 implementation rather than placeholder storage |
| `services/control-plane/cron/service.py` | cron-generated deployment requests cite `consistency_contract = "DEP-002"` and therefore assume the saga contract is real |
| `BP5-WB-005` and related Research Workbench packet families | experiment launch and deployment status flows inherit the stable deployment saga / outbox-inbox contract |

### 3.5 Readiness verdict

`DEPTH-DEP002` was dependency-unblocked at closeout and remains so now. The key reviewer question is
not "what still needs to be built?" but "does the current repo still justify the already-approved
parent closeout?".

---

## 4. Archived Review Position

Two review layers already existed before this sidecar:

### 4.1 Original DEP-002 approval

`services/control-plane/governance/review_dep002_claude_approved_zh.md` approved the original
implementation on `2026-04-10`, concluding:

- atomic business write + event outbox behavior passed
- ordering and idempotent consumer behavior matched L1 delivery guarantees
- compensation boundaries respected write-owner semantics

### 4.2 DEPTH-DEP002 re-verification

`services/control-plane/governance/review_depth_dep002_codex_zh.md` reframed the work as a fresh
acceptance audit and concluded:

- `services/deployment/` is live, not a stub
- the canonical saga backbone still lives in `deployment_saga.py`
- `services/deployment/contract.md`, `deployment_saga.contract.md`, and
  `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md` remain semantically aligned

That means the parent task was already treated correctly as **verification / closeout**, not as a
new build slice.

---

## 5. Fresh Verification On Current Snapshot

Executed in this sidecar session:

```bash
python3 -m pytest services/control-plane/governance/test_deployment_saga.py -q
python3 -m pytest services/deployment/test_service.py -q
python3 services/control-plane/governance/smoke_test_deployment_saga.py
python3 services/deployment/smoke_test.py
```

Observed results:

- governance saga tests: `9 passed`
- deployment service tests: `12 passed`
- governance saga smoke: `13/13 checks passed`
- deployment service smoke: `All smoke tests passed`

Practical interpretation:

- the canonical DEP-002 saga still preserves atomic bootstrap and ordered outbox emission
- deployment-facing APIs still expose dispatch / progress / outbox / inbox / compensation behavior
- the parent closeout remains justified on the present repo snapshot

---

## 6. Acceptance Checklist Expansion

| Parent check | Current evidence | Status |
|---|---|---|
| transactional outbox bootstrap is real | `DeploymentSagaStore.bootstrap_for_plan()` writes saga state and first outbox event; governance tests still pass | Met |
| commit failure does not leak partial visible state | `test_commit_failure_rolls_back_saga_and_outbox` still passes in the current pytest run | Met |
| idempotent consumer behavior is tested | duplicate and out-of-order inbox receipts remain covered in governance + deployment service tests | Met |
| per-aggregate ordering is preserved | smoke and unit coverage still validate sequence gap handling and apply-after-gap-close behavior | Met |
| compensation path is documented | `deployment_saga.contract.md` and `services/deployment/contract.md` still describe failure-point -> command -> owner-service mapping | Met |
| compensation path is tested | failure / finalize flows still pass in `services/deployment/test_service.py` and governance tests | Met |
| deployment service is live, not stubbed | deployment smoke test passes through health, validate, create, dispatch, saga progression, consume, status, and read-model endpoints | Met |
| parent formal closeout exists | archived task snapshot records `DEPTH-DEP002` as `done` with reviewed acceptance notes and delivery metadata | Met |

### Acceptance summary

This support packet satisfies its own helper-task acceptance:

- support artifact created
- canonical truth left untouched
- reviewer handoff package prepared with current evidence

The parent `DEPTH-DEP002` appears correctly closed already; this sidecar mainly backfills a clean
acceptance packet for the reviewer and future archival use.

---

## 7. Reviewer Focus

1. Confirm the packet keeps the scope support-only and does not attempt to reopen DEP-002 semantics.
2. Confirm the dependency map distinguishes formal task truth (none) from practical locked inputs
   (saga contract, policy docs, deployment facade).
3. Confirm the fresh verification evidence is sufficient to justify the sidecar's conclusion that
   no additional implementation work is pending for the parent.

---

## 8. Recommended Disposition

Recommended reviewer action for `Claude`:

- approve this sidecar as an archival closeout packet
- treat the parent `DEPTH-DEP002` as already absorbed and correctly finalized
- avoid reopening canonical implementation work unless a new regression appears

Suggested handoff summary:

> Acceptance packet prepared for `DEPTH-DEP002`. Parent task is already archived `done`; this
> packet backfills the acceptance checklist, dependency map, and fresh rerun evidence on the current
> snapshot (`9` governance tests, `12` deployment tests, `13/13` governance smoke, deployment smoke
> all pass). Scope remains support-only; no canonical truth or deployment/runtime code changed.
