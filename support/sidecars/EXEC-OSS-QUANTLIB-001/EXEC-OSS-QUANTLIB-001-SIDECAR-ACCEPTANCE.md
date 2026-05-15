# EXEC-OSS-QUANTLIB-001 Acceptance Packet and Dependency Map (Sidecar)

**Parent Task**: `EXEC-OSS-QUANTLIB-001` - Advance QuantLib next-wave execution readiness
**Parent Owner**: `Copilot`
**Parent Reviewer**: `Codex`
**Parent Status**: `todo` (parent has not yet been reworked after later QuantLib implementation landed)
**Sidecar Task**: `EXEC-OSS-QUANTLIB-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `acceptance_packet`
**Generated**: `2026-04-21`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance implementations. It
> packages the current QuantLib readiness state, dependency chain, and review
> posture for parent-owner absorption.

---

## 1. Executive Summary

`EXEC-OSS-QUANTLIB-001` was cut as a next-wave readiness slice: make the first
QuantLib execution lane concrete by locking source selection, defining the
governed adapter boundary, and naming the smoke-test next step.

The current repo state is now ahead of that original parent scope:

- the original readiness/materialization outputs exist:
  `services/research/quantlib/ACTIVATION_CRITERIA.md`,
  `services/research/quantlib/requirements.txt`, and
  `integrations/quantlib/integration.md`
- the governed adapter and smoke path were later implemented under the follow-on
  QuantLib lane:
  `services/research/quantlib/adapter/`, `smoke_test.py`, `test_adapter.py`,
  `integrations/quantlib/governance.md`, and `integrations/quantlib/smoke_test.md`
- local verification on `2026-04-21` still passes:
  `python3 services/research/quantlib/smoke_test.py` => `assertions: OK`
  and
  `python3 -m pytest services/research/quantlib/test_adapter.py -q` =>
  `17 passed, 1 skipped`
- `OSS_INTEGRATION_CHECKLIST.md` now records QuantLib as `governed`

That means this sidecar should be read as a **readiness and dependency packet**,
not as a request to reopen the canonical OSS status. For the parent owner, the
useful action is to reconcile the older execution slice with the later landed
QuantLib work and then decide whether the parent should be closed, superseded,
or converted into a narrow follow-up note.

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Canonical owner / reviewer / lifecycle truth for the parent and this sidecar |
| `services/research/quantlib/ACTIVATION_CRITERIA.md` | Original readiness contract: use-case binding, source selection, adapter boundary, smoke-test gate |
| `services/research/quantlib/requirements.txt` | Confirms the pinned package version `QuantLib-Python==1.18` |
| `integrations/quantlib/integration.md` | Shows QuantLib moved beyond planning debt into a governed integration evidence pack |
| `integrations/quantlib/governance.md` | Documents the research-only boundary and draft-only output semantics |
| `integrations/quantlib/smoke_test.md` | Records the smoke procedure and last-known-good results |
| `services/research/quantlib/adapter/quantlib_adapter.py` | Actual governed adapter surface for validated inputs, stub/real backends, and workflow entrypoint |
| `services/research/quantlib/smoke_test.py` | Executable smoke proof for the governed artifact envelope |
| `services/research/quantlib/test_adapter.py` | Unit coverage for schema rejection and output behavior |
| `OSS_INTEGRATION_CHECKLIST.md` | Current canonical maturity row for QuantLib (`governed`) |
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` | Historical matrix showing QuantLib as activation-ready / version-pinned before the later implementation wave |
| `docs/reviews/2026-04-20-development-progress-and-next-work-inventory.md` | Confirms `EXEC-OSS-QUANTLIB-001` belongs to lower-priority OSS next-wave work, not the main critical path |

---

## 3. Acceptance Scope Verification

The parent task acceptance says it must:

1. make the QuantLib next-wave gap and first executable slice explicit
2. make the adapter / smoke-test / governed I/O boundary explicit
3. leave an execution-ready plan or patch set that is reviewable

This sidecar verifies that those conditions are already satisfied in repo state,
with later implementation going further than the parent originally required:

| Scope Item | Verification | Status |
|---|---|---|
| QuantLib use-case and next-wave gap are explicit | `ACTIVATION_CRITERIA.md` scopes QuantLib to derivatives pricing and fixed-income analytics in the Research Plane only | PASS |
| Source selection is locked | `ACTIVATION_CRITERIA.md` and `requirements.txt` pin `QuantLib-Python==1.18` from `lballabio/QuantLib` | PASS |
| Governed adapter boundary is explicit | `ACTIVATION_CRITERIA.md` defines governed inputs, research-only outputs, and rejected roles; `governance.md` reiterates draft-only, non-executable semantics | PASS |
| Smoke-test next step is explicit | `ACTIVATION_CRITERIA.md` names the smoke commands and `integrations/quantlib/smoke_test.md` records the runnable procedure | PASS |
| Execution-ready surface exists, not just a plan | `adapter/quantlib_adapter.py`, `smoke_test.py`, and `test_adapter.py` are present and runnable | PASS |
| Default local verification still works | On `2026-04-21`, smoke test passed and pytest returned `17 passed, 1 skipped` | PASS |
| Canonical OSS tracking reflects the advanced state | `OSS_INTEGRATION_CHECKLIST.md` lists QuantLib as `governed` with evidence references | PASS |

---

## 4. Dependency Map

### 4.1 Historical Readiness Chain

```text
Phase 6 OSS ecosystem closure
  -> identified QuantLib as missing backend materialization
  -> cut OSS-NEXT-007 / EXEC-OSS-QUANTLIB-001 readiness lane
  -> materialized:
       services/research/quantlib/ACTIVATION_CRITERIA.md
       services/research/quantlib/requirements.txt
       integrations/quantlib/integration.md
```

### 4.2 Follow-On Implementation Chain That Overtook the Parent

```text
QuantLib readiness baseline
  -> follow-on implementation lane (OSS-IMPL-002 / OSS-GATE2-001)
       -> services/research/quantlib/adapter/quantlib_adapter.py
       -> services/research/quantlib/adapter/__init__.py
       -> services/research/quantlib/smoke_test.py
       -> services/research/quantlib/test_adapter.py
       -> integrations/quantlib/governance.md
       -> integrations/quantlib/smoke_test.md
       -> OSS_INTEGRATION_CHECKLIST.md row advanced to governed
```

### 4.3 Current Review-Relevant Dependency Facts

| Item | Status | Why it matters to the parent |
|---|---|---|
| `services/research/quantlib/ACTIVATION_CRITERIA.md` | present | Satisfies the parent's original task-materialization objective |
| `services/research/quantlib/requirements.txt` | present | Locks the upstream package/version decision |
| `integrations/quantlib/integration.md` | present | Gives the parent its source-selection and packaging record |
| `services/research/quantlib/adapter/` | present | Proves the adapter boundary is no longer hypothetical |
| `services/research/quantlib/smoke_test.py` | present and passing locally | Proves the named next step became executable |
| `services/research/quantlib/test_adapter.py` | present and passing locally except one real-backend skip | Confirms CI-safe/default-workspace coverage still holds |
| `integrations/quantlib/governance.md` | present | Confirms governed I/O and research-only boundary were completed |
| `integrations/quantlib/smoke_test.md` | present | Captures archived smoke evidence and procedure |
| `OSS_INTEGRATION_CHECKLIST.md` | `governed` | Means the broader OSS lane has already absorbed work beyond this parent slice |

---

## 5. Parent-Owner Action Summary

For `Copilot` as parent owner, the support recommendation is:

1. do not treat `EXEC-OSS-QUANTLIB-001` as an unstarted greenfield implementation lane
2. treat it as an older readiness/materialization task whose intended outputs now
   exist and have been overtaken by later QuantLib integration work
3. review the parent against the readiness-level acceptance only:
   source selection, adapter boundary, smoke path, and execution-ready next step
4. avoid reopening canonical OSS docs from this sidecar; if any closure action is
   needed, keep it to status-layer reconciliation and handoff wording
5. if the parent is judged fully satisfied by existing artifacts, hand it to
   reviewer `Codex` with a note that the sidecar found the repo already beyond
   the original acceptance bar
6. if the parent still needs a follow-up, scope that follow-up narrowly around
   status reconciliation or residual doc drift, not around rebuilding the
   QuantLib adapter lane

Known residual caveat:

- `ACTIVATION_CRITERIA.md` still describes `worker.py` and
  `examples/pricing_dataset_sample.json` as Gate 1 expectations, but those files
  are not present in the current `services/research/quantlib/` surface. This is
  support-doc drift, not a blocker to this sidecar packet.

---

## 6. Local Verification Snapshot

Commands run in this workspace on `2026-04-21`:

```bash
python3 services/research/quantlib/smoke_test.py
python3 -m pytest services/research/quantlib/test_adapter.py -q
```

Observed results:

- smoke output reported:
  `artifact_family=pricing_report`,
  `framework=quantlib`,
  `artifact_state=draft`,
  `deployment_stage=none`,
  `direct_influence=False`,
  `lean_consumption=research_only_not_direct_action`,
  `assertions: OK`
- pytest output reported:
  `17 passed, 1 skipped in 0.14s`
- the skipped test remains the real-backend path when local QuantLib bindings are
  unavailable in the default workspace; archived evidence in
  `integrations/quantlib/smoke_test.md` still records the earlier real-backend rerun

---

## 7. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar work is limited to `support/sidecars/EXEC-OSS-QUANTLIB-001/EXEC-OSS-QUANTLIB-001-SIDECAR-ACCEPTANCE.md` |
| No canonical truth edited | PASS | No L0/L1 docs, runtime files, or checklist rows were modified by this sidecar |
| Parent acceptance is mapped against repo reality | PASS | Packet distinguishes original readiness scope from later QuantLib implementation that overtook it |
| Dependency chain is explicit | PASS | Packet maps readiness baseline -> later adapter/smoke/governance implementation -> current checklist state |
| Local evidence was refreshed, not assumed | PASS | Smoke and pytest were rerun in this workspace on `2026-04-21` |
| Reviewer can use this to disposition the parent | PASS | Packet states whether the parent should be treated as satisfied, superseded, or narrowed to reconciliation-only work |

---

## 8. Handoff to Reviewer (`Codex`)

This sidecar is ready for review as the acceptance packet for
`EXEC-OSS-QUANTLIB-001`.

What it gives you:

1. a clear statement that the parent was originally a readiness/materialization
   slice, not the full governed adapter build
2. the dependency chain showing that later QuantLib work already produced the
   adapter, smoke path, and governance artifacts
3. a fresh local verification snapshot so the packet is grounded in current repo
   reality rather than older planning records

Recommended reviewer stance:

1. approve this sidecar if it accurately reflects that the repo has already moved
   beyond the parent's original acceptance bar
2. use it to decide whether the parent should be closed as effectively satisfied
   by existing artifacts or reduced to a narrow status-reconciliation task
3. do not absorb this packet as a reason to rewrite QuantLib canonical truth;
   this slice is support-only

---
*Generated by Codex2 as a sidecar `acceptance_packet` helper for `EXEC-OSS-QUANTLIB-001`. This file is a support artifact and does not modify canonical truth.*
