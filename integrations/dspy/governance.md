# DSPy Integration — Governance Overlay

Last updated: 2026-04-15
Owner: BP5-OSS-003 (Codex)
Reviewer: Claude
Status: governed runtime boundary documented
Related task: `LP-001`

## 1. Governance Principle

> DSPy may optimize governed persona-policy artifacts. It may not bypass Pantheon's registry, promotion, or execution authority.

The DSPy adapter is a learning-time optimizer only. It does not own deployment stage,
capital allocation, runtime binding, or LEAN execution.

## 2. Input Governance

The adapter enforces strict dataset gating before any optimization run starts.

Mandatory constraints:

- `actor_role` must be `operator` or `approver`
- `target.promotion_state` must be `candidate` or `paper`
- `event_type` must be one of the governed FB-001 event types accepted by the adapter
- datasets must include both governed training and evaluation examples

Mandatory deny semantics:

- deny intents are limited to the governed deny set
- deny tools are limited to the governed deny tool set
- mandatory deny examples must not regress during optimization

The adapter rejects malformed or out-of-scope examples instead of silently widening scope.

## 3. Output Governance

The DSPy workflow emits a governed `prompt_bundle` and a registry-ready entry.

Governed output rules:

- lifecycle starts at `draft`
- lineage must point back to source dataset refs
- the bundle is schema-validated before packaging
- `direct_live_influence` remains false in emitted governance metadata

DSPy output is therefore eligible for later registry review, but not for direct promotion or execution.

## 4. Regression Gate

The smoke and evaluation path tracks the explicit deny-first regression gate that the earlier spike defined:

- `deny_coverage_delta >= -0.02`
- `mandatory_deny_violation_count == 0`

The current governed smoke sample stays stronger than the minimum threshold:

- `deny_coverage_delta = 0.0`
- `mandatory_deny_violation_count = 0`

## 5. Authority Boundary

DSPy never receives authority over:

- registry truth
- deployment stage changes
- OpenClaw runtime sessions
- LEAN execution
- rollback semantics

Its responsibility ends at producing a governed optimization artifact plus supporting metadata.

## 6. Upgrade and Backend Rules

When changing the DSPy version pin or backend behavior:

1. update `services/learning/dspy/requirements.txt` and `DSPY_VERSION_PIN`
2. rerun `python3 services/learning/dspy/smoke_test.py`
3. rerun `python3 -m unittest discover -s services/learning/dspy -p 'test_*.py'`
4. update `integration.md`, this governance file, and `OSS_INTEGRATION_CHECKLIST.md`

Optional upstream backend runs must still preserve the same governed input filters,
deny-case evaluation, and draft-only output lifecycle.
