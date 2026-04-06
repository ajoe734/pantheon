# Replication Gate Contract

**Task:** RS-003  
**Owner:** Grok  
**Reviewer:** Claude  
**Status:** IMPLEMENTED — Ready for review

---

## 1. Purpose

The Replication Gate validates research candidates discovered through RS-001/RS-002 before they are admitted to the registry (REG-001).

It ensures:
- Discovered research meets basic validation standards
- Candidates have traceable lineage and governance context
- No attempts to bypass promotion gates
- Confidence in research quality before registry entry

**This gate sits between:**
- **Input:** Research handoff from RS-002 normalization
- **Output:** Candidates ready for registry admission or rejection back to research intake

---

## 2. Gate Interface

### Input: ReplicationRequest

```python
@dataclass
class ReplicationRequest:
    candidate_id: str                    # Unique ID for this research candidate
    source_task_id: str                  # e.g., "RS-002" - where research came from
    research_handoff: Dict[str, Any]    # Research metadata + normalized findings
    proposed_strategy_spec: Dict[str, Any]  # The strategy candidate proposes
    metadata: Optional[Dict[str, Any]]  # Optional context (paper ID, etc.)
    timestamp: str                       # ISO8601 timestamp
```

**research_handoff expected structure** (from RS-002):
```json
{
  "task_id": "RS-002",
  "source_metadata": {
    "api_endpoint": "...",
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

### Output: ReplicationResponse

```python
@dataclass
class ReplicationResponse:
    gate_run_id: str                   # Unique gate execution ID
    candidate_id: str                  # Input candidate_id
    admission_status: CandidateAdmissionStatus  # ADMITTED|REJECTED|NEEDS_CLARIFICATION
    replication_status: ReplicationStatus       # PASSED|FAILED|BLOCKED|PENDING
    results: List[ReplicationResult]   # Individual criterion results
    summary: str                       # Human-readable summary
    metadata: Dict[str, Any]           # Gate metadata
    timestamp: str                     # ISO8601 timestamp
    
    # Properties
    .passed: bool                      # True if admission_status == ADMITTED
```

---

## 3. Admission Criteria

### Required Criteria (ALL must pass for admission)

| Criterion | Description | Failure Case |
|-----------|-------------|--------------|
| `schema_validity` | Proposed StrategySpec has required fields: name, description, signals, parameters | Any required field missing |
| `lineage_complete` | Research handoff includes complete source metadata: api_endpoint, retrieved_at, governance_context | Missing metadata fields |
| `governance_context` | Grok processing notes include: normalization_confidence, governance_compliance (verified), downstream_readiness | Missing fields or compliance != "verified" |
| `no_live_bypass` | No suspicious bypass fields present; lifecycle_state not pre-set to live state | Fields like skip_promotion_gate or lifecycle_state="live" detected |

**Decision Rule:**
- ALL required criteria must pass
- If any required criterion fails → **REJECTED**

### Optional Criteria (soft requirements for quality gates)

| Criterion | Description | Pass Condition |
|-----------|-------------|----------------|
| `confidence_score` | Research confidence >= 0.7 | "high" (0.9) or "medium" (0.7) |
| `replication_notes_present` | Replication notes provided | Notes field non-empty |
| `evaluation_hypotheses` | Evaluation hypotheses defined | Hypotheses field non-empty |
| `implementation_ready` | Research indicates downstream_readiness | Status = "ready_for_replication" |

**Decision Rule:**
- Pass rate must be >= 80% (4/5 = 80%, 3/4 = 75% = FAIL)
- If optional pass rate < 80% → **REJECTED**

---

## 4. Admission Decisions

### ADMITTED
- All 4 required criteria: PASS
- Optional criteria pass rate >= 80%
- **Next Step:** Create RegistryPromotionRequest for REG-001 intake

### REJECTED
- Any required criterion: FAIL
- Optional criteria pass rate < 80%
- **Next Step:** Send back to RS-001 for clarification or rejection

### NEEDS_CLARIFICATION (future state)
- All required pass but optional pass rate = 0%
- **Next Step:** Request additional information before re-evaluation

---

## 5. Gate Execution Flow

```
ReplicationRequest (from RS-002)
    ↓
[ Evaluate Required Criteria ]
    ↓
  All pass? ──NO──→ REJECTED
    ↓ YES
[ Evaluate Optional Criteria ]
    ↓
  Pass rate >= 80%? ──NO──→ REJECTED
    ↓ YES
[ Create RegistryPromotionRequest ]
    ↓
ADMITTED → registry intake
```

---

## 6. Error Handling

Each criterion check returns:
```python
@dataclass
class ReplicationResult:
    criterion_id: str
    passed: bool
    evidence: str                      # Explanation of pass/fail
    details: Optional[Dict[str, Any]] # Additional diagnostic info
    timestamp: str
```

If criterion check throws exception:
- Result: FAILED with error message in evidence
- Does NOT halt evaluation (all criteria checked regardless)
- Exception details logged for debugging

---

## 7. Integration with Registry (REG-001)

Admitted candidates generate RegistryPromotionRequest:

```python
@dataclass
class RegistryPromotionRequest:
    gate_run_id: str                   # Proof of gate passage
    candidate_id: str
    registry_entry: Dict[str, Any]     # Entry for REG-001
    replication_proof: Dict[str, Any]  # Gate results evidence
    lineage: Dict[str, Any]            # Lineage from research → registry
    storage_backend: str               # "object_store", "gcs", "db", "inline"
    storage_path: str                  # Where to store the spec
```

Registry entry structure:
```json
{
  "artifact_type": "strategy_spec",
  "lifecycle_state": "candidate",
  "version": "1.0.0",
  "content": { strategy_spec }
}
```

---

## 8. Audit and Logging

Gate maintains audit log of all evaluations:

```python
gate = ReplicationGate()
response = gate.evaluate_candidate(request)

audit_log = gate.get_audit_log()
# [
#   {
#     "gate_run_id": "d003367b",
#     "timestamp": "2026-04-06T...",
#     "candidate_id": "cand-001",
#     "admission_status": "admitted",
#     "required_passed": 4,
#     "optional_passed": 4
#   },
#   ...
# ]
```

---

## 9. Usage Example

```python
from services.research.replication import (
    ReplicationGate,
    ReplicationRequest,
    create_promotion_request,
)

# Create gate instance
gate = ReplicationGate()

# Create request from RS-002 research handoff
request = ReplicationRequest(
    candidate_id="cand-momentum-20260406",
    source_task_id="RS-002",
    research_handoff={
        "source_metadata": {...},
        "normalized_findings": {...},
        "grok_processing_notes": {...}
    },
    proposed_strategy_spec={
        "name": "...",
        "description": "...",
        "signals": [...],
        "parameters": {...}
    }
)

# Evaluate candidate
response = gate.evaluate_candidate(request)

# Check admission decision
if response.passed:
    print(f"✓ Candidate admitted: {response.candidate_id}")
    
    # Create promotion request for registry
    promo = create_promotion_request(response, request.proposed_strategy_spec)
    print(f"Registry path: {promo.storage_path}")
else:
    print(f"✗ Candidate rejected: {response.candidate_id}")
    print(f"Summary: {response.summary}")
    
    # Send back to research intake
    for result in response.results:
        if not result.passed:
            print(f"  - {result.criterion_id}: {result.evidence}")
```

---

## 10. Future Enhancements

1. **Staged admission** - Separate "candidate" → "paper" gates with different criteria
2. **Metric-based evaluation** - Require backtesting results before promotion
3. **Confidence weighting** - Optional criteria weighted by importance
4. **Appeal mechanism** - Re-evaluation with additional evidence
5. **Integration with evaluation plane** - Direct link to EV-001 contract

---

## 11. Contract Stability

**Status:** v1.0 LOCKED for RS-003 delivery

**What can change (backward-compatible):**
- Adding new optional criteria (default: pass)
- Lowering pass thresholds
- Extending metadata fields

**What triggers v2.0:**
- Changing required criteria
- Changing decision thresholds
- Changing input/output schemas (ReplicationRequest/ReplicationResponse)

---

## 12. References

- **Grok Audit**: `audits/oss-alignment/grok_audit.md`
- **Admission Rules**: `gate_config.py` 
- **Implementation**: `gate.py`
- **Tests**: `test_gate.py`, `smoke_test.py`
- **Related Tasks**: RS-001, RS-002, REG-001, REG-002
