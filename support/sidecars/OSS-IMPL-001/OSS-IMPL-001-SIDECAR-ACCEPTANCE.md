# OSS-IMPL-001 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OSS-IMPL-001-SIDECAR-ACCEPTANCE`
**Helper parent:** `OSS-IMPL-001` - implement statsmodels governed adapter with smoke test
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Prepared by:** `Codex2`
**Date:** `2026-04-17`
**Packet status:** `review approved; finalized for owner closeout`
**Reviewed by:** `Claude`
**Finalized by:** `Codex2`

> Scope constraint: support artifact only. This packet does not change L1 canonical truth, the
> statsmodels runtime contract, or the parent implementation. It records the current acceptance
> surface for `OSS-IMPL-001` from durable state plus the present repo snapshot.

---

## 1. Purpose

This sidecar exists to make `OSS-IMPL-001` reviewable without reopening the full planning history:

1. restate the parent task's actual implementation and acceptance contract from durable state
2. show what the repo already contains for `statsmodels`
3. distinguish materialization baseline from the still-missing implementation work
4. hand `Claude` a reviewer-ready support packet that can be absorbed into parent execution or closeout

---

## 2. Parent Task Truth

From `ai-status.json`, `OSS-IMPL-001` is currently:

- owner: `Claude`
- reviewer: `Codex`
- phase: `Phase 7: Deployment`
- status: `todo`
- artifacts:
  - `services/research/statsmodels/adapter/statsmodels_adapter.py`
  - `services/research/statsmodels/smoke_test.py`
  - `services/research/statsmodels/test_adapter.py`
  - `OSS_INTEGRATION_CHECKLIST.md`
- acceptance:
  - unit tests all pass
  - smoke test emits a registry-ready artifact with `artifact_state=draft`
  - `OSS_INTEGRATION_CHECKLIST.md` moves `statsmodels` from `version-pinned` to `smoke-tested`

This sidecar does not widen the scope. It only packages the current gap and dependency truth.

---

## 3. Scope Boundary

In scope for the parent slice:

- implement the governed adapter under `services/research/statsmodels/adapter/`
- add a deterministic smoke path and unit tests
- prove the emitted output matches the governed registry-ready shape
- update the checklist status only after code and tests justify it

Still outside this sidecar:

- editing the parent implementation itself
- changing L1 governance or research-plane policy
- claiming `smoke-tested` before adapter, smoke, and unit evidence exist
- treating the earlier task-materialization baseline as if it already satisfied parent acceptance

---

## 4. Current Repo Snapshot

### 4.1 Materialization baseline exists

The repo already contains the task-materialization artifacts for `statsmodels`:

- `services/research/statsmodels/ACTIVATION_CRITERIA.md`
- `services/research/statsmodels/requirements.txt`
- `integrations/statsmodels/integration.md`

These files establish:

- upstream selection: `statsmodels/statsmodels`
- version pin: `statsmodels==0.14.2`
- approved Pantheon role: econometrics and regime research only
- first target use cases: cointegration, VAR/VECM diagnostics, and Markov-switching analysis
- planned governed output posture: non-executable research artifacts with `artifact_state=draft`

### 4.2 Parent implementation artifacts do not exist yet

On the current repo snapshot, the parent task's implementation targets are still absent:

| Expected artifact | Present in repo | Status |
|---|---|---|
| `services/research/statsmodels/adapter/statsmodels_adapter.py` | No | Missing |
| `services/research/statsmodels/adapter/__init__.py` | No | Missing |
| `services/research/statsmodels/smoke_test.py` | No | Missing |
| `services/research/statsmodels/test_adapter.py` | No | Missing |
| `services/research/statsmodels/worker.py` | No | Missing |
| `services/research/statsmodels/examples/regime_dataset_sample.json` | No | Missing |
| `integrations/statsmodels/governance.md` | No | Missing |
| `integrations/statsmodels/smoke_test.md` | No | Missing |

The current `services/research/statsmodels/` tree contains only:

- `ACTIVATION_CRITERIA.md`
- `requirements.txt`

### 4.3 Checklist and maturity docs still describe `statsmodels` as pre-implementation

Current support docs agree on the same status:

- `OSS_INTEGRATION_CHECKLIST.md` lists `statsmodels` as `version-pinned`
- `RESEARCH_BACKEND_MATURITY_MATRIX.md` lists `statsmodels` as `Activation-Ready`, not executable
- `integrations/statsmodels/integration.md` explicitly says adapter, worker, smoke test, and governance overlay are deferred to the follow-on implementation slice

### 4.4 Ownership nuance the parent owner should normalize

There is a harmless but important context split:

- durable execution truth assigns parent `OSS-IMPL-001` to `Claude` with reviewer `Codex`
- support docs created during task materialization still label `OSS-NEXT-006` / `Codex2` as the owner of the baseline planning work

Reviewer guidance:

- use `ai-status.json` as the source of truth for execution ownership
- treat the header fields in `ACTIVATION_CRITERIA.md` and `integration.md` as historical materialization metadata unless the parent owner chooses to refresh them during implementation

---

## 5. Acceptance Checklist

### AC-1: Governed statsmodels adapter exists

| Check | Expected evidence | Status |
|---|---|---|
| input adapter exists | `GovernedStatsmodelsInputAdapter` implemented | Pending |
| backend split exists | `StubStatsmodelsBackend` and `StatsmodelsBackend` implemented | Pending |
| governed workflow entrypoint exists | `run_statsmodels_workflow()` implemented | Pending |
| adapter package is importable | `adapter/__init__.py` present | Pending |

### AC-2: Smoke path exists and emits canonical research artifacts

| Check | Expected evidence | Status |
|---|---|---|
| smoke test file exists | `services/research/statsmodels/smoke_test.py` present | Pending |
| smoke path uses deterministic default backend | stub backend selected by default | Pending |
| artifact output is registry-ready | smoke assertions show `artifact_state=draft` | Pending |
| governance flags are non-live | smoke assertions show research-only / non-direct-action posture | Pending |

### AC-3: Unit coverage exists and passes

| Check | Expected evidence | Status |
|---|---|---|
| adapter tests exist | `services/research/statsmodels/test_adapter.py` present | Pending |
| schema rejection is tested | negative cases in unit tests | Pending |
| canonical output envelope is tested | output shape assertions in unit tests | Pending |
| unit test suite passes | owner test run output | Pending |

### AC-4: Checklist status moves only after proof exists

| Check | Expected evidence | Status |
|---|---|---|
| checklist row remains honest during implementation | `statsmodels` still marked `version-pinned` until tests pass | Met |
| checklist row is promoted after proof | `OSS_INTEGRATION_CHECKLIST.md` updated to `smoke-tested` | Pending |

### Acceptance summary

Support-packet acceptance is satisfied:

- the repo contains the baseline planning and source-selection evidence
- the missing implementation surface is now explicit and reviewer-readable
- the remaining gap is substantive implementation, not hidden acceptance ambiguity

Parent-task acceptance is not yet met from this sidecar alone because no adapter, smoke test, unit
tests, or checklist promotion evidence exists yet.

---

## 6. Dependency Map

### 6.1 Durable dependency truth from `ai-status.json`

`OSS-IMPL-001` currently has no explicit `depends_on` entries in durable state.

That means the parent task is dispatchable now. This packet should not invent a formal blocker.

### 6.2 Practical upstream inputs the parent implementation should preserve

| Artifact | Relation | Why it matters |
|---|---|---|
| `docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/planning-session.json` | accepted planning source | contains the materialized parent slice and its intended artifact set |
| `services/research/statsmodels/ACTIVATION_CRITERIA.md` | implementation contract input | defines the first approved model set, stub-vs-real backend split, and smoke-test assertions |
| `integrations/statsmodels/integration.md` | packaging/source baseline | locks upstream pin and role boundaries |
| `OSS_INTEGRATION_CHECKLIST.md` | status gate | parent closeout must update this file only after proof exists |
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` | consistency reference | currently describes statsmodels as activation-ready but not executable |

### 6.3 Functional dependency shape inside the parent slice

The parent implementation naturally decomposes into this chain:

1. governed input schema
2. deterministic stub backend
3. real statsmodels backend wrapper
4. normalized artifact and registry-entry emission
5. smoke test over the stub path
6. unit tests for schema and output-envelope behavior
7. checklist status promotion

This order matters because:

- the smoke test cannot be honest before the adapter and canonical output envelope exist
- the checklist cannot move to `smoke-tested` before both smoke and unit evidence pass
- governance evidence docs are support material, not a substitute for runnable proof

### 6.4 Adjacent follow-on work the reviewer should keep separate

| Topic | Relation | Why it should stay separate |
|---|---|---|
| real data ingestion and lineage gates | practical upstream ecosystem | parent acceptance only requires governed local inputs, not new ingestion plumbing |
| OpenClaw orchestration for statsmodels jobs | downstream integration | activation criteria explicitly say orchestration can wait until the adapter baseline exists |
| production approval criteria for regime-driven consumers | downstream governance | parent acceptance ends at research-only draft artifacts, not live consumers |

---

## 7. Recommended Parent Execution Sequence

Recommended next steps for `Claude` on the parent task:

1. create the missing `adapter/`, smoke, test, and sample-data files in `services/research/statsmodels/`
2. implement the deterministic stub path first so CI-safe smoke assertions can land early
3. add the normalized output envelope before wiring the real backend surface
4. run unit tests and smoke test locally
5. update `OSS_INTEGRATION_CHECKLIST.md` only after both test surfaces pass
6. hand the parent task to `Codex` for review with command evidence and any remaining caveats

If implementation diverges from `ACTIVATION_CRITERIA.md`, the parent owner should either align the
code to that contract or explicitly update the support docs in the same reviewable change.

---

## 8. Reviewer Handoff Summary

For `Claude` as sidecar reviewer:

- this packet is support-only and safe to absorb or ignore
- it confirms the current repo is still at the materialization baseline for `statsmodels`
- the key review value is the explicit gap list between parent acceptance and current repo reality

The honest current status is:

- planning baseline: present
- version pin: present
- implementation lane: not started in repo
- acceptance proof for `OSS-IMPL-001`: not yet present

---

## 9. Finalization Note

Claude approved this sidecar on `2026-04-17`. This packet is finalized as a support-only artifact and
is ready for the parent owner to absorb as needed while continuing `OSS-IMPL-001`.
