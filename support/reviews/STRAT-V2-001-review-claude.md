# STRAT-V2-001 Claude Review

Date: 2026-05-18
Reviewer: Claude
Task ID: STRAT-V2-001
Owner: Codex

## Scope Reviewed

- `services/research/strategy_spec/production_distillation.py`
- `services/research/strategy_spec/test_production_distillation.py`
- `support/evidence/STRAT-V2-001/sample_run.json`
- `support/reviews/STRAT-V2-001-review-codex2.md`

## Acceptance Criteria Verification

1. **distill(source_record_id) returns StrategySpec dict**: Confirmed. Module-level `distill()` and `ProductionStrategySpecDistiller.distill()` both delegate to `distill_result()` and call `validate_strategy_spec_payload(payload)` before returning.

2. **Extracts hypothesis, universe, frequency, risk_caps from markdown sections**: Confirmed. `_parse_research_note()` calls `_section_value()` for hypothesis, `_extract_universe()` for symbols/venues/asset_classes, `_extract_frequency()` for frequency, and `_extract_risk_caps()` for caps — each raising `ValidationError` if the section is absent or empty.

3. **evidence_refs and code_refs binding via STRAT-004 helper**: Confirmed. `distill_result()` constructs `EvidenceBundle`, `EvidenceItem`, and `SourceRecord` from the parsed note then delegates to `StrategySpecConversionService.convert_source_material()`, which owns the binding into the returned `StrategySpecConversionResult`.

4. **Two fixture notes produce valid StrategySpec**: `test_distills_two_fixture_research_notes_to_valid_strategy_specs` creates both notes, calls `distill()` / `ProductionStrategySpecDistiller.distill()`, and asserts schema validation plus spot-checks on risk_caps, symbols, frequency, evidence_refs, and code_refs.

5. **Malformed source raises ValidationError**: `test_malformed_research_note_rejects_with_validation_error` tests a note missing the `risk_caps` section and asserts `ValidationError` with "risk_caps" in the message.

6. **pytest -q exit 0**: Verified independently.
   - `pytest -q test_production_distillation.py test_conversion.py test_lineage.py test_models.py`: **21 passed**
   - `pytest -q services/research/strategy_spec`: **25 passed**

## Code Quality Observations

- The parsing helpers (`_markdown_sections`, `_parse_key_values`, `_section_list`, `_extract_universe`, etc.) are correctly scoped and do not mutate shared state.
- Registry writes are explicitly excluded from the distillation surface; the distiller only produces payloads for the registry service to admit.
- `_extract_code_refs` validates that every `code_refs` entry carries `repo_ref` and `path` before returning, which matches the STRAT-004 binding contract.
- No live broker access, no live capital side effects.
- `sample_run.json` regenerates deterministically (diff exit 0) and is valid JSON.

## Lifecycle Note

The Codex2 scoped code review (support/reviews/STRAT-V2-001-review-codex2.md) found no code issues. The review was blocked from formal approval by a stale-status lifecycle issue. This Claude review confirms the same findings and provides the explicit formal approval needed to close the lifecycle gap.

## Decision

**Approved.** All acceptance criteria met, all scoped tests passing, sample artifact reproducible.
