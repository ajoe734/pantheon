# STRAT-004 Evidence: Evidence / Code Refs Lineage

Task: STRAT-004 - evidence / code refs lineage
Owner: Codex
Reviewer: Claude
Date: 2026-05-16

## Scope

Added the StrategySpec evidence/code reference link needed by the research
pipeline:

- Reused the existing `StrategySpec` schema/model `evidence_refs[]` and
  `code_refs[]` surface from the STRAT-001 baseline.
- Promoted the existing `services/research/strategy_spec/lineage.py`
  dependency helper into the STRAT-004 public package surface.
- Regression-tested refs built from a `StrategySpecSeed` plus governed
  `SourceRecord` / `EvidenceItem` inputs.
- Regression-tested the normalized `strategy_spec_evidence_code_linked`
  lineage edge helper.
- Added helper support for attaching refs to StrategySpec payloads while
  preserving `provenance.source_refs`.
- Documented the new lineage surface in the StrategySpec README and contract.
- Exported lineage helpers from `services.research.strategy_spec`.

## Acceptance Mapping

- Evidence link: `evidence_refs[]` carries `EvidenceBundle` and `EvidenceItem`
  refs from the seed lineage.
- Code refs link: `code_refs[]` carries allowlisted repo/path/commit/symbol/line
  refs from repo `SourceRecord` fallback metadata, explicit SourceRecord
  `code_refs`, or EvidenceItem metadata.
- Lineage safety: helper rejects SourceRecord/EvidenceItem inputs outside the
  seed lineage and emits replayable edge fields (`edge_id`, `from_type`,
  `from_id`, `to_type`, `to_id`, `edge_type`, `created_at`, `actor_ref`,
  `trace_id`, `evidence_refs`).
- Non-execution boundary: no registry write, experiment launch, broker route, or
  execution authority is added.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/research/strategy_spec/models.py services/research/strategy_spec/lineage.py services/research/strategy_spec/__init__.py services/research/strategy_spec/test_models.py services/research/strategy_spec/test_lineage.py
python3 -m json.tool services/control-plane/specs/strategy_spec.schema.json >/tmp/strategy_spec_schema_check.json
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/strategy_spec/test_models.py services/research/strategy_spec/test_lineage.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/source_ingestion/tests/test_strategy_seed_builder.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/strategy_spec -q
git diff --check -- services/control-plane/specs/contract.md services/research/strategy_spec/README.md services/research/strategy_spec/__init__.py services/research/strategy_spec/test_lineage.py support/evidence/STRAT-004/README.md
```

Results:

- `py_compile`: passed
- `json.tool`: passed
- targeted StrategySpec lineage/model tests: 14 passed
- seed builder: 5 passed
- `services/research/strategy_spec`: 22 passed
- `git diff --check`: passed

## Worktree Boundary

The repository already had unrelated dirty files from other active tasks and
generated orchestrator state before this implementation. STRAT-004-owned
changes are limited to:

- `services/control-plane/specs/contract.md`
- `services/research/strategy_spec/README.md`
- `services/research/strategy_spec/__init__.py`
- `services/research/strategy_spec/test_lineage.py`
- `support/evidence/STRAT-004/README.md`

The schema/model `EvidenceRef` and `CodeRef` objects are part of the existing
STRAT-001 baseline, and `lineage.py` was introduced by the STRAT-003 conversion
dependency. STRAT-004 regression-validates and publishes that lineage surface
without changing the lineage helper implementation.
