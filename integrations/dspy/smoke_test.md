# DSPy Integration — Smoke Test

Last updated: 2026-04-17
Owner: BP5-OSS-003 (Codex)
Reviewer: Claude
Status: executable smoke path verified
Primary entrypoint: `python3 services/learning/dspy/smoke_test.py`

## 1. Objective

Prove that the DSPy integration row is backed by a runnable local adapter path rather than a
README-only claim.

## 2. Prerequisites

Minimum local smoke prerequisites:

- Python 3.10+
- repo checkout with `services/learning/dspy/`

Optional upstream smoke prerequisites:

- `pip install -r services/learning/dspy/requirements.txt`
- a model endpoint exposed through `PANTHEON_DSPY_MODEL`

## 3. Canonical Commands

Deterministic local smoke:

```bash
python3 services/learning/dspy/smoke_test.py
```

Optional upstream DSPy smoke:

```bash
PANTHEON_DSPY_MODEL=<provider/model> python3 services/learning/dspy/smoke_test.py --backend dspy
```

Unit coverage:

```bash
python3 -m unittest discover -s services/learning/dspy -p 'test_*.py'
```

## 4. What the Smoke Path Verifies

The smoke script loads `examples/preference_dataset_sample.json` and proves that:

1. the governed dataset can be parsed successfully
2. the adapter builds training and evaluation slices from governed examples
3. `run_dspy_workflow()` emits a registry-ready `prompt_bundle`
4. the bundle writes a stable storage path under `learning/dspy/{strategy_id}/{version}/`
5. evaluation metrics include deny-first regression checks

## 5. Verified Result

Verified on 2026-04-17 with the default stub backend:

- backend: `stub_bootstrap_fewshot`
- training examples: `4`
- evaluation examples: `4`
- registry id: `reg-persona-router-prompt-bundle-0.1.0`
- storage path: `learning/dspy/persona-router/0.1.0/prompt_bundle.json`
- checksum: `sha256:9fb5ff92b4050787015afa93119abe1b5b6be04257fbe57a9c08980c41244201`
- `intent_accuracy = 1.0`
- `tool_selection_precision = 1.0`
- `deny_coverage_delta = 0.0`
- `mandatory_deny_violation_count = 0`

Unit coverage result on 2026-04-17:

- `python3 -m unittest discover -s services/learning/dspy -p 'test_*.py'`
- `Ran 3 tests`
- `OK`

## 6. Acceptance

Treat the DSPy row as smoke-proven when:

- the smoke command exits `0`
- the workflow emits a registry id and governed storage path
- deny-first metrics remain non-regressive
- unit coverage still passes
