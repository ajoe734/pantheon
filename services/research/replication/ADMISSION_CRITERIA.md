# Replication Gate: Candidate Admission Criteria

**Task:** RS-003 Acceptance Criteria #1 & #2  
**Gate Version:** 1.0.0  
**Effective Date:** 2026-04-06

---

## Overview

The replication gate enforces 8 admission criteria:
- **4 Required** (must ALL pass for admission)
- **4 Optional** (must have ≥80% pass rate)

This document details each criterion, its rationale, and implementation.

---

## Required Criteria

### 1. Schema Validity (`schema_validity`)

**Purpose:** Ensure the proposed StrategySpec is well-formed and complete.

**Validation Rules:**
- Input must be a JSON object (dict)
- Must contain ALL required fields:
  - `name` (string): Human-readable strategy name
  - `description` (string): What the strategy does
  - `signals` (array): List of signal names
  - `parameters` (object): Strategy configuration

**Failure Cases:**
- `proposed_strategy_spec` is not a dict/object
- Any required field is missing
- Fields are present but wrong type

**Example: PASS**
```json
{
  "name": "Momentum Mean Reversion",
  "description": "Cross-asset momentum with mean reversion",
  "signals": ["momentum_score", "mean_reversion"],
  "parameters": {
    "lookback": 20,
    "threshold": 1.5
  }
}
```

**Example: FAIL (missing description)**
```json
{
  "name": "Momentum Mean Reversion",
  "signals": ["momentum_score"],
  "parameters": {"lookback": 20}
  // MISSING: description
}
```

**Rationale:**
- Registry (REG-001) requires well-formed entries
- Incomplete specs cannot be promoted safely
- Early validation prevents downstream failures

---

### 2. Lineage Completeness (`lineage_complete`)

**Purpose:** Verify research handoff includes complete source traceability.

**Validation Rules:**
- `research_handoff.source_metadata` must contain:
  - `api_endpoint` (string): Source API URL or reference
  - `retrieved_at` (string, RFC3339): When data was fetched
  - `governance_context` (string): Why this source is approved

**Failure Cases:**
- `source_metadata` missing or not a dict
- Any required metadata field missing
- Metadata empty or all zeros

**Example: PASS**
```json
{
  "source_metadata": {
    "api_endpoint": "https://api.openalex.org/works/W3052820607",
    "retrieved_at": "2026-04-06T10:00:00Z",
    "governance_context": "Approved structured source via OpenAlex adapter"
  }
}
```

**Example: FAIL (missing governance_context)**
```json
{
  "source_metadata": {
    "api_endpoint": "https://api.openalex.org/works/W3052820607",
    "retrieved_at": "2026-04-06T10:00:00Z"
    // MISSING: governance_context
  }
}
```

**Rationale:**
- Audit trail requires complete source information
- Rollback/verification depends on knowing the source
- Governance boundary enforcement requires context
- Registry promotion gate (REG-002) requires lineage

---

### 3. Governance Context (`governance_context`)

**Purpose:** Verify research passed governance checks before admission.

**Validation Rules:**
- `research_handoff.grok_processing_notes` must contain:
  - `normalization_confidence` (enum): "high", "medium", or "low"
  - `governance_compliance` (string): Must equal exactly `"verified"`
  - `downstream_readiness` (enum): "ready_for_replication" or "needs_clarification"

**Failure Cases:**
- `grok_processing_notes` missing or not a dict
- Any required governance field missing
- `governance_compliance` is not `"verified"` (e.g., "pending", "failed", "unchecked")

**Example: PASS**
```json
{
  "grok_processing_notes": {
    "normalization_confidence": "high",
    "governance_compliance": "verified",
    "downstream_readiness": "ready_for_replication"
  }
}
```

**Example: FAIL (compliance not verified)**
```json
{
  "grok_processing_notes": {
    "normalization_confidence": "high",
    "governance_compliance": "pending",  // WRONG: must be "verified"
    "downstream_readiness": "ready_for_replication"
  }
}
```

**Rationale:**
- RS-002 normalization must complete governance checks
- "verified" is a hard contract requirement
- Only verified research advances to registry
- Prevents ungoverned research from entering promotion gate

---

### 4. No Live Bypass (`no_live_bypass`)

**Purpose:** Prevent attempts to circumvent promotion gates and reach live execution.

**Validation Rules:**
- Proposed spec must NOT contain these dangerous fields:
  - `skip_promotion_gate`
  - `live_execution_direct`
  - `force_live`
  - `bypass_registry`
  - Any other suspicious bypass patterns

- If `lifecycle_state` is present in spec, it must be one of:
  - `"draft"` (acceptable)
  - `"candidate"` (acceptable)
  - Any other value (FAIL): `"paper"`, `"live"`, `"retired"` not allowed here

**Failure Cases:**
- Any bypass field detected in spec
- `lifecycle_state` pre-set to `"paper"` or `"live"`
- Attempt to set the state before registry admission

**Example: PASS**
```json
{
  "name": "Strategy",
  "description": "...",
  "signals": [...],
  "parameters": {}
  // No bypass fields, no lifecycle_state
}
```

**Example: FAIL (bypass attempt)**
```json
{
  "name": "Strategy",
  "skip_promotion_gate": true,  // BLOCKED: bypass field
  "description": "...",
  "signals": [...],
  "parameters": {}
}
```

**Example: FAIL (pre-set lifecycle)**
```json
{
  "name": "Strategy",
  "lifecycle_state": "live",  // BLOCKED: only registry can set this
  "description": "...",
  "signals": [...],
  "parameters": {}
}
```

**Rationale:**
- REG-002 promotion gate is the ONLY path to live execution
- Research must not bypass governance layers
- Malicious or buggy research could attempt to set state directly
- This criterion protects the entire promotion pipeline

---

## Optional Criteria

### 5. Confidence Score (`confidence_score`)

**Purpose:** Encourage high-confidence research while allowing lower-confidence candidates through.

**Validation Rules:**
- Extract `research_handoff.grok_processing_notes.normalization_confidence`
- Map to numeric score:
  - `"high"` → 0.9
  - `"medium"` → 0.7
  - `"low"` → 0.5
  - Default (missing) → 0.5

- Criterion passes if score >= 0.7

**Failure Cases:**
- Confidence is `"low"` (score 0.5 < 0.7)

**Example: PASS**
```json
{
  "grok_processing_notes": {
    "normalization_confidence": "high"  // 0.9 >= 0.7
  }
}
```

**Example: FAIL**
```json
{
  "grok_processing_notes": {
    "normalization_confidence": "low"  // 0.5 < 0.7
  }
}
```

**Rationale:**
- Research confidence affects downstream evaluation
- High-confidence research should have priority
- Low-confidence research not blocked, but noted
- Optional to allow research exploration

---

### 6. Replication Notes (`replication_notes_present`)

**Purpose:** Encourage researchers to document how to replicate their findings.

**Validation Rules:**
- Check if `research_handoff.normalized_findings.replication_notes` exists
- Must be non-empty string (after stripping whitespace)

**Failure Cases:**
- Field missing
- Field empty or whitespace-only

**Example: PASS**
```json
{
  "normalized_findings": {
    "replication_notes": "Strategy needs 20-day bars. Requires liquid markets."
  }
}
```

**Example: FAIL (missing notes)**
```json
{
  "normalized_findings": {
    // No replication_notes field
  }
}
```

**Rationale:**
- Helps RS-003 gate understand implementation requirements
- Downstream research (RS-002) can use notes for testing
- Optional to allow brief research summaries

---

### 7. Evaluation Hypotheses (`evaluation_hypotheses`)

**Purpose:** Ensure researchers define expected performance and risks.

**Validation Rules:**
- Check if `research_handoff.normalized_findings.evaluation_hypotheses` exists
- Must be non-empty string (after stripping whitespace)

**Failure Cases:**
- Field missing
- Field empty or whitespace-only

**Example: PASS**
```json
{
  "normalized_findings": {
    "evaluation_hypotheses": "H1: Sharpe > 1.0. H2: Max drawdown < 20%. H3: Latency < 100ms."
  }
}
```

**Example: FAIL (missing hypotheses)**
```json
{
  "normalized_findings": {
    // No evaluation_hypotheses field
  }
}
```

**Rationale:**
- Links to EV-001 (evaluator contracts)
- Defines success criteria before backtesting
- Optional to allow exploratory research

---

### 8. Implementation Readiness (`implementation_ready`)

**Purpose:** Surface research that's not ready for replication yet.

**Validation Rules:**
- Extract `research_handoff.grok_processing_notes.downstream_readiness`
- Passes if value exactly equals `"ready_for_replication"`

**Failure Cases:**
- Field missing
- Value is `"needs_clarification"` or other state

**Example: PASS**
```json
{
  "grok_processing_notes": {
    "downstream_readiness": "ready_for_replication"
  }
}
```

**Example: FAIL (needs clarification)**
```json
{
  "grok_processing_notes": {
    "downstream_readiness": "needs_clarification"
  }
}
```

**Rationale:**
- RS-002 normalization signals if research needs work
- Optional to allow "provisional" candidates through
- Helps identify research that needs clarification before promotion

---

## Decision Logic

### Admission Decision Tree

```
START: Evaluate candidate
  │
  ├─ Check Required Criteria
  │  ├─ schema_validity? → FAIL? REJECT
  │  ├─ lineage_complete? → FAIL? REJECT
  │  ├─ governance_context? → FAIL? REJECT
  │  └─ no_live_bypass? → FAIL? REJECT
  │
  ├─ All Required Passed? → NO? REJECT
  │
  ├─ Check Optional Criteria
  │  ├─ confidence_score (1/4)
  │  ├─ replication_notes_present (1/4)
  │  ├─ evaluation_hypotheses (1/4)
  │  └─ implementation_ready (1/4)
  │
  ├─ Optional Pass Rate >= 80%? → NO? REJECT
  │
  └─ ADMIT → Create RegistryPromotionRequest
```

### Examples

**Scenario 1: Perfect Candidate**
- All required: PASS
- All optional: PASS
- **Result: ADMITTED** (4/4 optional = 100%)

**Scenario 2: Good Candidate, Missing Notes**
- All required: PASS
- Optional: 3/4 PASS (missing: replication_notes)
- **Result: ADMITTED** (3/4 = 75%... wait, 75% < 80%)
- **Actually: REJECTED** (75% < 80% threshold)

**Scenario 3: Good Candidate, Low Confidence**
- All required: PASS
- Optional: 3/4 PASS (failing: confidence_score)
- **Result: REJECTED** (3/4 = 75% < 80%)

**Scenario 4: Missing Governance**
- Required: schema_validity PASS, lineage PASS, **governance FAIL**, bypass PASS
- **Result: REJECTED** (required criterion failed)
- Optional criteria not even checked

---

## Pass Rate Calculation

For optional criteria with pass/fail results:

```
pass_count = number of optional criteria that PASS
total_count = total number of optional criteria (4)
pass_rate = pass_count / total_count

ADMIT if pass_rate >= 0.80 (80%)
REJECT if pass_rate < 0.80
```

**Examples:**
- 4/4 = 1.0 = 100% → ADMIT ✓
- 3/4 = 0.75 = 75% → REJECT ✗
- 4/5 = 0.80 = 80% → ADMIT ✓ (if 5 optional criteria)
- 3/5 = 0.60 = 60% → REJECT ✗

---

## Implementation Details

### File: `gate_config.py`

Defines criteria list:
```python
class GateConfig:
    ADMISSION_THRESHOLD_REQUIRED = 1.0  # ALL required must pass
    ADMISSION_THRESHOLD_OPTIONAL = 0.80  # >= 80% optional
    
    def get_required_criteria() -> List[ReplicationCriteria]
    def get_optional_criteria() -> List[ReplicationCriteria]
```

### File: `gate.py`

Implements checks:
```python
class ReplicationGate:
    def _check_schema_validity(request) -> ReplicationResult
    def _check_lineage_completeness(request) -> ReplicationResult
    def _check_governance_context(request) -> ReplicationResult
    def _check_no_live_bypass(request) -> ReplicationResult
    def _check_confidence_score(request) -> ReplicationResult
    def _check_replication_notes(request) -> ReplicationResult
    def _check_evaluation_hypotheses(request) -> ReplicationResult
    def _check_implementation_readiness(request) -> ReplicationResult
```

---

## Future Adjustments

Criteria can be adjusted based on:
1. **Feedback from RS-002 normalizers** - Are candidates getting stuck?
2. **Feedback from REG-002 promotion gate** - Are admitted candidates failing promotion?
3. **Feedback from evaluation plane (EV-001)** - Are admitted specs performing well?

**To adjust criteria:**
1. Update `gate_config.py` GateConfig class
2. Update criterion description in this document
3. Run full test suite (`test_gate.py`, `smoke_test.py`)
4. Update gate version number if thresholds change
5. Request review from Claude (reviewer)

---

## References

- **Gate Contract**: `GATE_CONTRACT.md`
- **Gate Implementation**: `gate.py`
- **Gate Configuration**: `gate_config.py`
- **Test Suite**: `test_gate.py`, `smoke_test.py`
- **Related**: RS-001, RS-002, REG-001, REG-002
