# OSS-NEXT-008 Governed Regression Refresh

Last updated: 2026-04-17
Owner: Codex2
Reviewer: Codex
Status: ready for review

## Scope

Refresh smoke and no-regression evidence for the already-governed OSS paths:

- `OpenClaw`
- `DSPy`
- `imitation`
- `MLflow`

## Commands Run

### OpenClaw

- `bash scripts/openclaw-smoke-test.sh`
- `bash scripts/openclaw-gateway-adapter-smoke.sh`

### DSPy

- `python3 services/learning/dspy/smoke_test.py`
- `python3 -m unittest discover -s services/learning/dspy -p 'test_*.py'`

### imitation

- `python3 services/learning/imitation/smoke_test.py`
- `python3 -m unittest discover -s services/learning/imitation -p 'test_*.py'`

### MLflow

- `python3 services/registry/experiments/smoke_test.py`
- `python3 -m unittest discover -s services/registry/experiments -p 'test_*.py'`

## Results

### OpenClaw

- Baseline smoke passed `6/6`
- Live gateway smoke passed all four governed workflows: `pantheon.ingest`, `pantheon.review`, `pantheon.retrain`, `pantheon.deploy`
- Baseline artifacts: `/tmp/openclaw-bp5-oss-001.gMVtn9`
- Live artifacts: `/tmp/openclaw-bp5-oss-002.OMDUTb`

### DSPy

- Smoke passed with backend `stub_bootstrap_fewshot`
- Registry id: `reg-persona-router-prompt-bundle-0.1.0`
- Storage path: `learning/dspy/persona-router/0.1.0/prompt_bundle.json`
- Checksum refreshed to `sha256:9fb5ff92b4050787015afa93119abe1b5b6be04257fbe57a9c08980c41244201`
- Unit tests: `Ran 3 tests` / `OK`

### imitation

- Smoke passed with backend `stub_bc`
- Registry id: `reg-alpha-mean-reversion-imitation-0.1.0`
- Storage path: `learning/imitation/alpha-mean-reversion/0.1.0/artifact_bundle.json`
- Checksum refreshed to `sha256:02d757f6a00ef711a34ce21dbc9b90dbb0f6b4e32ee80d8600cff2578cb4ced9`
- Unit tests: `Ran 3 tests` / `OK`

### MLflow

- Smoke passed with backend `memory`
- Result: `LP-003 smoke test passed with backend=memory: registry metadata mapped into experiment metadata.`
- Unit tests: `Ran 4 tests` / `OK`

## Canonical Docs Updated

- `integrations/openclaw/smoke_test.md`
- `integrations/openclaw/evidence_pack.md`
- `integrations/dspy/smoke_test.md`
- `integrations/imitation/smoke_test.md`
- `integrations/mlflow/smoke_test.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- `docs/reviews/2026-04-16-oss-ecosystem-gap-analysis.md`

## Conclusion

All four governed OSS paths remain executable after recent repo changes. The prior gap review can continue to classify them as governed, but the stale 2026-04-15/2026-04-16 evidence has now been refreshed to 2026-04-17.
