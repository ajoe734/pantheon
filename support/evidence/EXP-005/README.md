# EXP-005 Evidence

## Scope

Implemented controlled `ExperimentRun -> Artifact Registry` writeback in the
research contracts and research orchestrator.

The new write path only registers completed-run artifacts as `draft` or
`candidate` registry entries. It preserves `deployment_stage=none`, records
`producer_run_id`, and builds lineage back to the ExperimentRun plus available
StrategySpec and dataset refs.

Primary files:

- `services/research/experiments/registry_writeback.py`
- `services/research/experiments/test_registry_writeback.py`
- `services/research/main.py`
- `services/research/tests/test_research_orchestrator_http_service.py`
- `services/research/experiments/README.md`

## Verification

```bash
python3 -m py_compile services/research/experiments/registry_writeback.py services/research/main.py
python3 -m pytest services/research/experiments/test_registry_writeback.py -q
python3 -m pytest services/research/tests/test_research_orchestrator_http_service.py -q
python3 -m pytest services/research/tests
python3 -m pytest services/registry/test_service.py -q
```

Result: all commands passed locally.

## Closeout Verification

2026-05-16 owner closeout reran:

```bash
python3 -m py_compile services/research/experiments/registry_writeback.py services/research/main.py
git diff --check -- services/research/experiments/registry_writeback.py services/research/experiments/test_registry_writeback.py services/research/experiments/__init__.py services/research/experiments/README.md services/research/main.py services/research/tests/test_research_orchestrator_http_service.py support/evidence/EXP-005/README.md support/evidence/EXP-005/review-claude.md
python3 -m pytest services/research/experiments/test_registry_writeback.py services/research/tests/test_research_orchestrator_http_service.py services/registry/test_service.py -q
```

Result: `56 passed in 70.37s`; compile and diff-check passed.
