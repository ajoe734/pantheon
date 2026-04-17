# OSS-IMPL-003 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `OSS-IMPL-003-SIDECAR-ACCEPTANCE`  
**Helper parent:** `OSS-IMPL-003` - implement vectorbt governed adapter with smoke test  
**Parent owner:** `Claude`  
**Parent reviewer:** `Codex`  
**Prepared by:** `Codex2`  
**Date:** `2026-04-17`  
**Packet status:** `prepared from current repo snapshot; ready for Claude review`

> Scope constraint: support artifact only. This packet does not edit canonical truth, runtime
> contracts, registry/governance semantics, or the parent implementation. It records the current
> acceptance surface for `OSS-IMPL-003` from the task brief plus the live repo snapshot.

---

## 1. Purpose

This sidecar exists to give the reviewer a compact acceptance snapshot for the vectorbt slice:

1. restate the parent task truth and explicit acceptance checks
2. map the current repo evidence to those checks
3. separate passing evidence from blocking gaps
4. hand `Claude` a support packet that can be forwarded into the parent review/fix loop

---

## 2. Parent Task Truth

From `ai-status.json` and the task brief, the parent task is currently:

- owner: `Claude`
- reviewer: `Codex`
- phase: `Phase 7: Deployment`
- status: `todo`
- artifact surface: `services/research/vectorbt/adapter/vectorbt_adapter.py`,
  `services/research/vectorbt/smoke_test.py`, `services/research/vectorbt/test_adapter.py`,
  `OSS_INTEGRATION_CHECKLIST.md`

Recorded parent acceptance is:

1. `unit tests 全部通過`
2. `smoke test 產生 registry-ready artifact（artifact_state=draft）`
3. `OSS checklist vectorbt 狀態更新為 smoke-tested`

This sidecar does not widen the parent scope into L1 contract work or broader OSS governance edits.

---

## 3. Scope Boundary

In scope for this sidecar:

- inspect the current vectorbt implementation snapshot
- run the directly relevant smoke/unit verification commands
- record acceptance mapping and dependency notes
- hand off a support packet for reviewer consumption

Out of scope:

- editing `services/research/vectorbt/*`
- updating `OSS_INTEGRATION_CHECKLIST.md`
- modifying canonical architecture/policy files
- finalizing the parent task lifecycle on behalf of `Claude`

---

## 4. Current Repo Snapshot

### 4.1 Implemented files present

Current repo snapshot includes:

- `services/research/vectorbt/adapter/vectorbt_adapter.py`
- `services/research/vectorbt/adapter/__init__.py`
- `services/research/vectorbt/smoke_test.py`
- `services/research/vectorbt/test_adapter.py`
- `services/research/vectorbt/requirements.txt`
- `services/research/vectorbt/ACTIVATION_CRITERIA.md`
- `integrations/vectorbt/integration.md`

That is enough surface to evaluate the parent acceptance directly.

### 4.2 Adapter/governance shape aligns with the planned vectorbt contract

The implementation in `vectorbt_adapter.py` currently matches the planned governed shape:

- `GovernedVectorbtInputAdapter` validates dataset ids, lineage refs, OHLCV fields, instrument
  count, and minimum bar count
- `StubVectorbtBackend` exists for CI-safe execution
- `VectorbtBackend` is available behind the normal runtime split
- `run_vectorbt_workflow()` emits an artifact bundle plus registry-ready entry
- output keeps `artifact_state = "draft"` and `deployment_summary.current_stage = "none"`

This is materially aligned with the design captured in `integrations/vectorbt/integration.md`.

### 4.3 Smoke path passes on the current snapshot

Command run:

```bash
python3 services/research/vectorbt/smoke_test.py
```

Observed result:

- command exited `0`
- backend used: `stub_backtest`
- dataset shape satisfied the minimum path: `2` instruments and `70` total bars
- emitted registry entry reports:
  - `artifact_type = backtest_result`
  - `artifact_state = draft`
  - `deployment_stage = none`
- smoke assertions completed with `assertions: OK`

This means the second parent acceptance item is currently met.

### 4.4 Unit-test path is currently blocked by a syntax error

Command run:

```bash
python3 -m unittest services/research/vectorbt/test_adapter.py
```

Observed result:

- command exited non-zero before tests executed
- Python raised a `SyntaxError` in
  `services/research/vectorbt/test_adapter.py:98`
- offending statement:
  `del alt["source_dataset_refs"] if "source_dataset_refs" in alt else None`

Impact:

- the unit-test suite does not currently run
- the first parent acceptance item is not yet met
- this is a parent implementation issue, not a sidecar issue

### 4.5 Checklist status is not yet updated to `smoke-tested`

Current `OSS_INTEGRATION_CHECKLIST.md` still shows vectorbt as `version-pinned`, with wording that
the next step is to implement the governed adapter, smoke test, and evidence pack before claiming
`smoke-tested`.

Impact:

- the third parent acceptance item is not yet met
- the repo snapshot still reflects pre-closeout maturity wording for vectorbt

### 4.6 Broader activation-criteria note

`services/research/vectorbt/ACTIVATION_CRITERIA.md` still lists two Gate 1 files that are not
present in the current snapshot:

- `services/research/vectorbt/worker.py`
- `services/research/vectorbt/examples/strategy_dataset_sample.json`

These are not part of the parent task's explicit `ai-status.json` acceptance list, so they are not
the reason the parent currently fails. They are still worth keeping visible if the parent owner
plans to align the implementation fully with the broader activation document.

---

## 5. Acceptance Checklist

### AC-1: Unit tests all pass

| Check | Evidence | Status |
|---|---|---|
| unit-test command runs successfully | `python3 -m unittest services/research/vectorbt/test_adapter.py` | Not met |
| unit tests execute instead of failing at import/parse time | `SyntaxError` at `services/research/vectorbt/test_adapter.py:98` | Not met |

### AC-2: Smoke test emits registry-ready draft artifact

| Check | Evidence | Status |
|---|---|---|
| smoke command succeeds | `python3 services/research/vectorbt/smoke_test.py` exited `0` | Met |
| artifact type is `backtest_result` | smoke output | Met |
| artifact state is `draft` | smoke output | Met |
| deployment stage is `none` | smoke output | Met |
| governed output assertions pass | smoke output `assertions: OK` | Met |

### AC-3: OSS checklist row updated to `smoke-tested`

| Check | Evidence | Status |
|---|---|---|
| vectorbt row exists | `OSS_INTEGRATION_CHECKLIST.md` line with `vectorbt` entry | Met |
| status is updated to `smoke-tested` | row still reads `version-pinned` | Not met |
| wording reflects completed smoke evidence | row still describes follow-on implementation work | Not met |

### Acceptance summary

Current parent acceptance is **not yet satisfied**.

- one of three explicit acceptance items is met: the smoke test passes
- two explicit acceptance items remain open: unit tests and checklist promotion
- reviewer should treat this packet as a blocker-aware acceptance snapshot, not as closeout approval

---

## 6. Dependency Map

### 6.1 Durable task dependency truth

Per `ai-status.json`, `OSS-IMPL-003` has no explicit durable `depends_on` entries.

This sidecar should not invent task-level blockers that are not recorded there.

### 6.2 Direct implementation dependencies inside the parent slice

The parent acceptance currently depends on four concrete surfaces:

| Surface | Why it matters |
|---|---|
| `services/research/vectorbt/adapter/vectorbt_adapter.py` | defines governed adapter, stub/real backend split, and output envelope |
| `services/research/vectorbt/smoke_test.py` | proves the governed smoke path emits the expected draft artifact |
| `services/research/vectorbt/test_adapter.py` | is the recorded unit-test gate for parent acceptance |
| `OSS_INTEGRATION_CHECKLIST.md` | is the maturity-truth artifact that must reflect `smoke-tested` |

### 6.3 Practical sequencing observed in the current snapshot

The parent's remaining path is straightforward:

1. fix the syntax issue in `test_adapter.py` so the suite can execute
2. rerun the vectorbt unit tests to satisfy the first acceptance item
3. update the vectorbt checklist row from `version-pinned` to `smoke-tested`
4. hand the parent back for review with fresh evidence

The sidecar does not claim any broader runtime dependency beyond that narrow acceptance chain.

---

## 7. Reviewer Handoff Recommendation

For `Claude` as sidecar reviewer:

- verify this packet accurately reflects the current snapshot
- use it as a support artifact showing that the vectorbt smoke path is already viable
- keep the task in a fix/review loop because parent acceptance is still blocked by the unit-test
  syntax error and stale checklist state

For `Codex` as parent reviewer later:

- expect smoke evidence to be strong once the owner resubmits
- require a passing unit-test run and checklist-state update before approving the parent

---

## 8. Sidecar Scope Declaration

This file is the only artifact created by this sidecar acceptance pass.

- no canonical L1 or L2 document was modified
- no vectorbt implementation file was modified
- no global summary file was manually edited
- no parent status was force-closed from this sidecar
