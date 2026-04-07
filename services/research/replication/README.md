# Replication Gate

**Task:** RS-003 — Run first-pass replication gate before registry admission  
**Status:** ✅ COMPLETE  
**Tests:** 24 unit tests + 5 smoke tests (all passing)

---

## Overview

The Replication Gate (`services/research/replication/`) validates research candidates discovered through RS-001/RS-002 before admission to the registry (REG-001).

**Gate Location in Pipeline:**
```
RS-001 (Research Ingestion)
    ↓
RS-002 (Research Normalization)
    ↓
  [ REPLICATION GATE ] ← you are here
    ↓
REG-001 (Registry Entry)
    ↓
REG-002 (Promotion Gate)
    ↓
EX-001 (Execution)
```

---

## Quick Start

### Run Tests

```bash
# Unit tests (24 tests, ~0.003s)
cd services/research/replication
python3 -m unittest discover -s . -p "test_*.py" -v

# Smoke tests (5 integration tests)
python3 smoke_test.py
```

### Use the Gate

```python
from services.research.replication import (
    ReplicationGate,
    ReplicationRequest,
    create_promotion_request,
)

# Create gate
gate = ReplicationGate()

# Create candidate from RS-002 research handoff
request = ReplicationRequest(
    candidate_id="cand-momentum-20260406",
    source_task_id="RS-002",
    research_handoff={...},           # From RS-002 output
    proposed_strategy_spec={...},     # Strategy to evaluate
)

# Evaluate
response = gate.evaluate_candidate(request)

# Check result
if response.passed:
    print(f"✓ ADMITTED: {response.candidate_id}")
    promo = create_promotion_request(response, request.proposed_strategy_spec)
    # Pass promo to REG-001 registry intake
else:
    print(f"✗ REJECTED: {response.candidate_id}")
    for result in response.results:
        if not result.passed:
            print(f"  {result.criterion_id}: {result.evidence}")
```

---

## Files

### Core Implementation

- **`gate.py`** (16.9 KB)
  - `ReplicationGate` - Main gate executor
  - Evaluates all 8 admission criteria
  - Logs audit trail
  - Creates promotion requests for admitted candidates

- **`gate_schema.py`** (5.5 KB)
  - `ReplicationRequest` - Input from RS-002
  - `ReplicationResponse` - Gate decision + evidence
  - `ReplicationResult` - Individual criterion result
  - `RegistryPromotionRequest` - Output to REG-001

- **`gate_config.py`** (6.4 KB)
  - `GateConfig` - Criteria definition and thresholds
  - `AdmissionRules` - Decision logic
  - 4 required + 4 optional criteria

### Tests

- **`test_gate.py`** (14 KB)
  - 24 unit tests covering all criteria
  - All passing ✅

- **`smoke_test.py`** (10.8 KB)
  - 5 end-to-end integration tests
  - Realistic research payloads
  - All passing ✅

### Documentation

- **`GATE_CONTRACT.md`** (9 KB)
  - Full gate interface specification
  - Input/output schemas
  - Integration with REG-001
  - Usage examples

- **`ADMISSION_CRITERIA.md`** (12.5 KB)
  - Detailed explanation of each criterion
  - Pass/fail conditions
  - Examples and rationale
  - Decision tree

- **`__init__.py`**
  - Package exports
  - Main API surface

### Other

- **`README.md`** (this file)

---

## Acceptance Criteria ✅

### Criterion 1: Replication gate criteria documented ✅
- See `GATE_CONTRACT.md` - Full gate interface specification
- See `ADMISSION_CRITERIA.md` - Detailed criterion documentation
- See inline code comments in `gate.py` and `gate_config.py`

### Criterion 2: Candidate admission rules defined ✅
- See `ADMISSION_CRITERIA.md` §Decision Logic
- Required criteria: ALL must pass
- Optional criteria: ≥80% pass rate required
- Decision tree with examples
- Implementation in `gate_config.py` class `AdmissionRules`

### Criterion 3: Failed replication cannot reach registry ✅
- Failed candidates generate `ReplicationResponse.passed = False`
- `create_promotion_request()` returns `None` if not admitted
- No path from REJECTED to REG-001 registry intake
- Bypass attempts (`no_live_bypass` criterion) explicitly blocked
- Test coverage: `test_live_bypass_detected()`, `test_bypass_attempt_rejection()`

---

## Admission Criteria Summary

### Required (ALL must pass)

| Criterion | Purpose | Failure Case |
|-----------|---------|--------------|
| `schema_validity` | StrategySpec well-formed | Missing required fields |
| `lineage_complete` | Source traceable | Missing metadata |
| `governance_context` | Governance verified | Compliance != "verified" |
| `no_live_bypass` | No promotion bypass | Bypass fields detected |

### Optional (≥80% pass rate required)

| Criterion | Purpose | Pass Condition |
|-----------|---------|----------------|
| `confidence_score` | High-confidence research | Confidence >= 0.7 |
| `replication_notes_present` | Implementation documented | Notes non-empty |
| `evaluation_hypotheses` | Success criteria defined | Hypotheses non-empty |
| `implementation_ready` | Research ready for testing | Status = ready |

**Decision Logic:**
- Required: ALL must pass
- Optional: pass_rate = (passed / total) >= 0.80

---

## Integration Points

### Input: RS-002 Research Handoff

`RS-002` currently hands RS-003 two aligned payloads:

- a legacy `research_handoff` compatibility envelope for lineage and governance checks
- a canonical `proposed_strategy_spec` validated against `OC-003`

Expected from RS-002 normalization task:

```json
{
  "task_id": "RS-002",
  "source_metadata": {
    "api_endpoint": "https://api.openalex.org/works/...",
    "retrieved_at": "2026-04-06T10:00:00Z",
    "governance_context": "Approved structured source"
  },
  "normalized_findings": {
    "strategy_spec": { "name": "...", "signals": [...] },
    "replication_notes": "Key implementation details",
    "evaluation_hypotheses": "Expected metrics"
  },
  "grok_processing_notes": {
    "normalization_confidence": "high|medium|low",
    "governance_compliance": "verified",
    "downstream_readiness": "ready_for_replication|needs_clarification"
  }
}
```

Canonical `proposed_strategy_spec` example:

```json
{
  "spec_version": "1.0",
  "strategy_id": "strat-momentum-mean-reversion-001",
  "title": "Momentum Mean Reversion Strategy",
  "hypothesis": "Cross-asset momentum entries can be improved by short-term mean reversion filters.",
  "objective": "Validate Sharpe ratio above 1.0 with max drawdown below 20%.",
  "market_scope": {
    "symbols": ["SPY"],
    "asset_classes": ["equities"],
    "frequency": "1d"
  },
  "data_dependencies": [
    { "ref": "OpenAlex:W3052820607", "kind": "paper" }
  ],
  "execution_profile": {
    "signal_schema_version": "1.0",
    "quantity_type": "PERCENT_PORTFOLIO",
    "execution_mode_hint": "research"
  },
  "evaluation_plan": {
    "metrics": ["sharpe_ratio", "max_drawdown"]
  },
  "governance": {
    "approval_required": true
  },
  "provenance": {
    "source_kind": "paper",
    "created_at": "2026-04-06T10:00:00Z"
  }
}
```

### Output: RegistryPromotionRequest

Generated for admitted candidates to pass to REG-001:

```python
RegistryPromotionRequest(
    gate_run_id="d003367b",
    candidate_id="cand-momentum-20260406",
    registry_entry={
        "artifact_type": "strategy_spec",
        "lifecycle_state": "candidate",
        "version": "1.0.0",
        "content": { strategy_spec }
    },
    replication_proof={
        "gate_run_id": "...",
        "replication_results": [...],
        "admission_status": "admitted"
    },
    lineage={
        "source_gate_run": "...",
        "source_candidate_id": "...",
        "replication_timestamp": "..."
    },
    storage_backend="object_store",
    storage_path="research/replication/cand-xyz/strategy_spec_v1.0.0.json"
)
```

---

## Test Results

### Unit Tests (24/24 passing)

```
test_gate.py (24 tests):
✓ TestGateSchema (3 tests)
  - Request creation
  - JSON serialization
  - Response properties
  
✓ TestGateConfig (3 tests)
  - Required criteria list
  - Optional criteria list
  - JSON serialization
  
✓ TestAdmissionRules (4 tests)
  - All required must pass
  - Optional threshold 80%
  - Decision logic
  
✓ TestReplicationGate (12 tests)
  - Valid candidate admission
  - Schema validation
  - Lineage checks
  - Governance compliance
  - Bypass detection
  - Confidence scoring
  - Audit logging
  
✓ TestPromotionRequest (2 tests)
  - Promotion request creation
  - None for rejected candidates
```

### Smoke Tests (5/5 passing)

```
smoke_test.py (5 tests):
✓ Realistic Admission Flow
  - All criteria pass
  - Promotion request generated
  
✓ Low Confidence Handling
  - Optional threshold enforced
  
✓ Bypass Attempt Detection
  - Malicious specs rejected
  
✓ Missing Governance Detection
  - Required criterion blocking
  
✓ Audit Trail
  - Evaluations logged properly
```

**Run tests:**
```bash
cd services/research/replication
python3 -m unittest discover -s . -p "test_*.py" -v  # 24 tests
python3 smoke_test.py                                 # 5 tests
```

---

## Dependencies

**Python Standard Library Only**
- `json` - JSON parsing
- `uuid` - Unique IDs
- `datetime` - Timestamps
- `dataclasses` - Schema definitions
- `enum` - Status enums
- `typing` - Type hints

**No External Packages Required** ✅

This maintains research service isolation and avoids conflicts with:
- DSPy (LP-001)
- Qlib (research integration)
- FinRL (learning plane)
- imitation (LP-002)

---

## Known Limitations

1. **Static Criteria** - Criteria thresholds not configurable per-evaluation (v1.0)
2. **No Caching** - Each evaluation is independent (can add in v2.0)
3. **Single Gate Instance** - No multi-stage gates yet (depends on REG-002 completion)
4. **No Async** - All checks synchronous (can parallelize in v2.0)

---

## Future Enhancements

### v1.1 (Planned)
- Configurable thresholds per gate instance
- Extended metadata in promotion request

### v2.0 (Post-REG-002 Completion)
- Staged gates (candidate → paper → live)
- Backtesting requirement for paper→live
- Integration with EV-001 evaluator results
- Metric-based admission (Sharpe score, etc.)

### v2.1+ (Post-Evaluation)
- Appeal mechanism for borderline candidates
- Weighted optional criteria
- Machine learning-based scoring
- Integration with experiment registry

---

## Troubleshooting

### Test Failures

**If unit tests fail:**
```bash
cd services/research/replication
python3 -m unittest test_gate.TestReplicationGate.test_evaluate_valid_candidate -v
```

**If smoke tests fail:**
```bash
cd services/research/replication
python3 smoke_test.py 2>&1 | head -50
```

### Common Issues

**Issue:** `ImportError: cannot import name 'ReplicationGate'`
- **Fix:** Run from `services/research/replication/` directory or add to PYTHONPATH

**Issue:** Timestamp deprecation warnings
- **Status:** Expected (Python 3.12+ prefers timezone-aware datetimes)
- **Impact:** None — code still works correctly

### Debug Mode

```python
from gate import ReplicationGate

gate = ReplicationGate()
request = ReplicationRequest(...)
response = gate.evaluate_candidate(request)

# Print all criterion results
for result in response.results:
    print(f"{result.criterion_id}: {'PASS' if result.passed else 'FAIL'}")
    print(f"  Evidence: {result.evidence}")
    if result.details:
        print(f"  Details: {result.details}")

# Check audit log
for entry in gate.get_audit_log():
    print(entry)
```

---

## Contribution Guidelines

### Modifying Criteria

1. Update criterion definition in `gate_config.py`
2. Implement check function in `gate.py`
3. Add test case in `test_gate.py`
4. Add smoke test scenario if needed
5. Update `ADMISSION_CRITERIA.md`
6. Request Claude review

### Adding New Criteria

1. Decide: Required or Optional?
2. Update `GateConfig._build_required_criteria()` or `_build_optional_criteria()`
3. Implement in `ReplicationGate._check_<name>()`
4. Add unit tests
5. Update documentation
6. Increment version if threshold changes

### Version Control

- **v1.0:** Initial release with 8 criteria (RS-003)
- **v1.x:** Backward-compatible changes (new optional criteria, lower thresholds)
- **v2.0+:** Breaking changes (new required criteria, new schemas)

---

## References

- **Task:** RS-003 in `current-work.md`
- **Audit:** `audits/oss-alignment/grok_audit.md`
- **Grok Spec:** `services/research/grok_research_intake_spec.md`
- **Research Schema:** `services/research/schema.json`
- **Registry Contract:** `services/registry/contract.md`
- **Roadmap:** `ROADMAP.md` (Epic E: Research Ingestion)

---

## Status

**RS-003 Delivery Status: ✅ COMPLETE**

Acceptance Criteria:
- ✅ Replication gate criteria documented (`GATE_CONTRACT.md`, `ADMISSION_CRITERIA.md`)
- ✅ Candidate admission rules defined (`ADMISSION_CRITERIA.md`, `gate_config.py`)
- ✅ Failed replication cannot reach registry (no bypass path, tests verify)

Test Results:
- ✅ 24 unit tests passing
- ✅ 5 smoke tests passing
- ✅ No external dependencies

Ready for:
- Claude review (reviewer)
- Integration with RS-002 → REG-001 pipeline
- Handoff to Codex for registry implementation

---

## Author

Grok Research Agent  
Pantheon OpenClaw Integration Project  
2026-04-06
