# Review: STRAT-V2-001 — Strategy spec distillation production smoke

Reviewer: Claude
Reviewed at: 2026-05-19
Status: **APPROVED**

## Scope

Reviewed three task artifacts:
- `services/research/strategy_spec/production_distillation.py`
- `services/research/strategy_spec/test_production_distillation.py`
- `support/evidence/STRAT-V2-001/sample_run.json`

## Acceptance Criteria Check

| Criterion | Result |
|---|---|
| `distill(source_record_id)` returns schema-valid StrategySpec dict | ✅ |
| Extracts hypothesis, universe, frequency, risk_caps from markdown sections | ✅ |
| evidence_refs + code_refs binding via STRAT-004 helper | ✅ |
| 2 fixture research notes produce valid StrategySpec | ✅ |
| Malformed source (missing risk_caps) raises ValidationError | ✅ |
| No changes to STRAT-001..004 public API | ✅ |
| registry_write_performed: false | ✅ |
| sample_run.json with lineage edges present | ✅ |

## Implementation Notes

**`production_distillation.py`**:
- Public API (`distill`, `distill_registry_payload`, `ProductionStrategySpecDistiller`) is minimal and correctly scoped.
- Markdown parsing is robust: handles key/value extraction from structured sections, bullet lists, and inline mappings.
- All required fields (`hypothesis`, `universe`, `frequency`, `risk_caps`, `code_refs`) raise `ValidationError` when absent.
- Evidence binding correctly produces both `EvidenceBundle` and `EvidenceItem` objects, satisfying STRAT-004 lineage requirements.
- Governance invariants enforced: `governance.direct_execution_allowed: False`, `registry_write_authority: registry_service_only`.
- `_iso_timestamp` and `_confidence` include proper range validation.
- No live broker side effects; no registry writes from distiller itself.

**`test_production_distillation.py`**:
- Two fixture notes (TW momentum + US ETF reversal) both produce schema-valid StrategySpec and are asserted at key field level.
- Registry payload admissibility tested end-to-end via TestClient.
- Malformed note (missing risk_caps section) correctly raises `ValidationError`.

**`sample_run.json`**:
- Complete pipeline output with `strategy_spec`, `registry_payload`, `seed_promotion_lineage_edge`, `evidence_code_lineage_edge`, `candidate_advance_request`.
- All lineage edge IDs are deterministic and properly formatted.
- `artifact_state: draft`, `lifecycle_state: draft`, `registry_write_performed: false` — correct.

## Verdict

All acceptance criteria met. Implementation is independent of STRAT-001..004 public APIs. No live broker or capital side effects. Approve for owner finalization.
