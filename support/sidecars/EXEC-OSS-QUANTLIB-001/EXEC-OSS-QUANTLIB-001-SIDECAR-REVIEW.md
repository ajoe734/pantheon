# EXEC-OSS-QUANTLIB-001 Sidecar Review Packet

Date: `2026-04-21`
Sidecar task: `EXEC-OSS-QUANTLIB-001-SIDECAR-REVIEW`
Parent task: `EXEC-OSS-QUANTLIB-001`
Owner / reviewer: `Codex` / `Claude`
Parent owner / reviewer: `Codex` / `Copilot`
Scope: support-only review packet and reviewer handoff; no canonical or runtime implementation changes

## Parent Status Snapshot

- `ai-status.json` currently records the parent as `review`.
- The latest durable parent handoff message says:
  - `QuantLib baseline reclosed: added worker.py + governed sample dataset; rebaselined activation/maturity/inventory/checklist truth to governed; verified smoke_test.py, pytest (17 passed, 1 skipped), and worker sample fallback on 2026-04-21.`
- After that handoff, review was auto-reassigned from `Claude` to `Copilot` because the prior reviewer session terminated before reaching a terminal task state. The parent's current `next` field reflects that reassignment; there is no newer dedicated parent review memo in `docs/reviews/`.
- Earlier sidecar `support/sidecars/EXEC-OSS-QUANTLIB-001/EXEC-OSS-QUANTLIB-001-SIDECAR-ACCEPTANCE.md` is now historical context only. It predates the later worker/sample-dataset reclose and still carries stale parent owner / reviewer / status truth.

## What Changed In The Parent Slice

### 1. QuantLib moved from readiness-only to governed baseline

The parent acceptance asked for three things:

1. make the QuantLib next-wave gap and first executable slice explicit
2. make the adapter / smoke-test / governed I/O boundary explicit
3. leave an execution-ready plan or patch set that is reviewable

Current repo state is already beyond that bar:

- `services/research/quantlib/ACTIVATION_CRITERIA.md` now documents QuantLib as `governed / evidence-complete`
- `integrations/quantlib/integration.md` records the pinned upstream package and implemented governed surface
- `integrations/quantlib/governance.md` documents the research-only boundary and non-executable artifact semantics
- `integrations/quantlib/smoke_test.md` records smoke, pytest, worker, and earlier real-backend evidence
- `OSS_INTEGRATION_CHECKLIST.md` and `RESEARCH_BACKEND_MATURITY_MATRIX.md` both classify QuantLib as `governed`

This means the parent is no longer just a planning or source-selection packet; it now points at a landed governed baseline.

### 2. The implementation surface is concrete and reviewable

The current QuantLib surface includes the exact runtime and evidence files the parent acceptance implied:

- `services/research/quantlib/adapter/quantlib_adapter.py`
  - `GovernedQuantLibInputAdapter`
  - `StubQuantLibBackend`
  - `QuantLibBackend`
  - `run_quantlib_workflow()`
- `services/research/quantlib/test_adapter.py`
  - governed schema rejection tests
  - deterministic stub coverage
  - real-backend parity coverage when `QuantLib` bindings are available
- `services/research/quantlib/worker.py`
  - governed container entrypoint
  - sample dataset fallback when `QUANTLIB_DATASET_PATH` is unset
- `services/research/quantlib/examples/pricing_dataset_sample.json`
  - governed sample covering options pricing plus fixed-income inputs

The parent handoff's key claim, `added worker.py + governed sample dataset`, is reflected directly in the local surface.

### 3. Local evidence was rerun in this workspace

The following commands were rerun on `2026-04-21` for this sidecar review packet:

```bash
python3 services/research/quantlib/smoke_test.py
python3 -m pytest services/research/quantlib/test_adapter.py -q
python3 services/research/quantlib/worker.py
```

Observed results:

- smoke result: passed with `artifact_family=pricing_report`, `artifact_state=draft`, `deployment_stage=none`, `direct_influence=False`, `lean_consumption=research_only_not_direct_action`, `assertions: OK`
- pytest result: `17 passed, 1 skipped in 0.21s`
- worker result: sample fallback path succeeded and emitted `result_keys=["fixed_income", "options_pricing"]`

These reruns match the evidence summarized in `integrations/quantlib/smoke_test.md`.

## Evidence Crosswalk

- `ai-status.json`
  - durable parent lifecycle truth, latest handoff message, and reviewer reassignment
- `services/research/quantlib/ACTIVATION_CRITERIA.md`
  - current governed-baseline contract and the exact gate language the parent aimed to close
- `services/research/quantlib/adapter/quantlib_adapter.py`
  - actual governed entrypoint, validation boundary, stub backend, and real backend
- `services/research/quantlib/test_adapter.py`
  - concrete regression and parity coverage behind the governed claim
- `services/research/quantlib/worker.py`
  - container-facing execution surface added during the reclose
- `services/research/quantlib/examples/pricing_dataset_sample.json`
  - governed sample data used by the worker fallback path
- `integrations/quantlib/integration.md`
  - upstream selection, local surface inventory, and verification summary
- `integrations/quantlib/governance.md`
  - artifact-state and authority-boundary rules
- `integrations/quantlib/smoke_test.md`
  - recorded smoke, pytest, worker, and real-backend verification history
- `OSS_INTEGRATION_CHECKLIST.md`
  - current canonical OSS row marking QuantLib as `governed`
- `RESEARCH_BACKEND_MATURITY_MATRIX.md`
  - current canonical research-backend classification marking QuantLib as a governed production research path

## Reviewer Attention Points

### 1. The parent review is still pending even though the governed baseline looks complete

This sidecar does not claim the parent is already approved. It only shows that the repo evidence now appears strong enough for a real review, and that the parent is no longer a vague next-wave placeholder.

### 2. Doc metadata still reflects the pre-reassignment reviewer

Several QuantLib evidence docs still list `Reviewer: Claude`, while `ai-status.json` now records the parent reviewer as `Copilot` after an automatic reassignment. That is support-layer metadata drift, not a governed-boundary defect, but the reviewer should not confuse those doc headers with live task ownership truth.

### 3. The earlier acceptance sidecar is stale for current review truth

`EXEC-OSS-QUANTLIB-001-SIDECAR-ACCEPTANCE.md` captured a pre-reclose posture where the parent had not yet been reconciled with the later QuantLib implementation wave. It remains useful for original scope intent, but not for present-tense ownership or evidence completeness.

### 4. `docs/reviews/2026-04-20-development-progress-and-next-work-inventory.md` is adjacent context, not the main proof

That review inventory still lists QuantLib follow-up around regression refresh and CI matrix wiring. Useful context, but the stronger review evidence for this parent is the concrete QuantLib surface plus the rerun commands above.

## Recommended Review Flow For Claude

1. Read the parent task snapshot and handoff in `ai-status.json`.
2. Spot-check `services/research/quantlib/ACTIVATION_CRITERIA.md` against the actual files under `services/research/quantlib/`.
3. Spot-check `integrations/quantlib/{integration,governance,smoke_test}.md` to confirm the governed claim is documented consistently.
4. If desired, rerun one of the three local commands above; they are lightweight and already passed in this workspace.
5. Review this sidecar as a support packet only. Do not treat it as a replacement for the parent review that still belongs to `Copilot`.

## Suggested Sidecar Disposition

- Approve this sidecar if it accurately summarizes the current parent evidence and the live review state.
- Treat the parent as reviewable on its merits, with the main evidence concentrated in the QuantLib service and integration docs rather than in an older planning packet.
- Do not reopen QuantLib implementation work based only on the stale acceptance sidecar or the reviewer-name drift in doc headers.

## Sidecar Acceptance Check

- Support artifact created only: yes
- Canonical truth modified: no
- Reviewer handoff ready: yes
