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

## 3a. Screen Contribution Test (SD_STRATEGY_SCREEN_ENTRY_EXIT §4)

Applies only to a candidate whose `strategy_logic` has a `screen` (or whose evaluation
carries `candidate_pool_snapshots`). Candidates without a screen take the unchanged
primary path.

* **Inputs**: the exact `strategy_artifact` and `backtest_dataset` (with
  `candidate_pool_market`) in `research_handoff`/`proposed_strategy_spec`. Absent inputs
  fail closed (`screen_contribution_inputs_missing`); nothing is synthesized.
* **Control**: the same artifact with only `screen` removed and `parameters.symbols` set to
  every symbol of the governed market, run through the same protocol, costs and split.
  The control run is persisted in the backtest run store.
* **Decision**: the screened candidate is admissible only if its out-of-sample after-cost
  Sharpe exceeds the control's by more than `min_screen_oos_sharpe_improvement` (default
  0). If either Sharpe is non-finite (zero-variance window) the comparison falls back to
  out-of-sample after-cost total return, strictly greater; `comparison_metric` records which.
* **Control definition**: the candidate's own entry/exit rules with every selection step
  removed. The governed daily boundary exclusions (suspension, liquidity, history, stale
  data) stay in force: the control keeps the screen's eligibility block and `min_size` with
  a single pass-everything filter, so it trades exactly the eligible boundary.
* **Screen adds nothing**: status `screen_dropped`; criterion fails; `re_registration`
  carries the entry-only artifact, checksum, control run id and the reason. The
  revalidation worker consumes it and durably registers the entry-only candidate in
  `entry_only_registrations.json` (idempotent on tenant + parent spec + entry-only
  checksum; record holds reason and lineage to the screened parent and both runs;
  readable via `list_entry_only_registrations`). The run metadata carries the
  `registration_id`.
* **Resubmitting an entry-only registration**: the gate recognises the registered artifact
  structurally, never from a caller flag: its screen must be the fixed point of the
  whole-boundary control construction (eligibility block, `min_size`, single
  pass-everything filter, no selection step). Such a candidate skips only the
  screen-contribution comparison (one trial, no `screen_contribution` block); every primary
  admission check still runs. Any artifact with a selection step stays a screened candidate.
* **Control cannot run**: status `control_unavailable`; fails closed.
* **Trials**: the screened evaluation (screen variant) and the control (entry variant) each
  increment the family counter; `trial_accounting` records both.
* **Survivorship**: a backtest with status `survivorship_limited` is never admitted; the
  criterion fails with `status: survivorship_limited` and the reason, which is echoed in
  the response summary.
* **Record**: `real_backtest_validation.details.screen_contribution` (also in
  `metadata.backtest_summary`) states the measured contribution.

## 3b. Mechanism Stage (SD_DISCOVERY_DEMAND §3.2, operator decision O-15 = b)

Runs first in `evaluate_candidate`, before every criterion and so before the backtest that
produces the out-of-sample window. Implementation: `mechanism_tests.py`.

* **Inputs** (handoff or spec): `hypothesis_predictions` (the card's `predictions[]`, or
  `hypothesis_card.predictions`), `backtest_dataset` (governed prices, `records` or
  `candidate_pool_market`), `mechanism_inputs` (`{dataset: [rows]}` with `symbol`,
  `event_time`/`available_time` and the observable field). Nothing is generated: a
  prediction whose data is absent, too thin, malformed or `needs_data:*` is recorded
  `inconclusive` with `testable: false` and a reason, and never counts as support.
* **In-sample only**: the window is the backtest protocol's own: `split_idx =
  int(min target-instrument bar count * in_sample_ratio)` and EVERY instrument (targets,
  benchmark, others) keeps only its first `split_idx` sessions, whatever its length or start
  date. The cut happens before any price is converted to a number; events and forward windows
  reaching past an instrument's last in-sample session are dropped. `in_sample_cutoff` reports
  the latest in-sample session. The stage never calls the backtest.
* **Fixed test per `test_kind`**: `event_window` (abnormal window return vs the symbol's
  in-sample mean, one-sample t across events), `regime_split` (sign must hold in every
  regime with enough days; 0050 above/below its prior-200-session mean, or point-in-time
  bands from `mechanism_inputs`), `dose_response` (Spearman of value vs forward return),
  `lead_lag` (input leads returns, and the reverse direction must not be stronger),
  `cross_symbol_sign` (one-sided binomial over per-symbol Spearman signs).
* **Holm correction** over the testable predictions of the card, `mechanism_alpha`
  (default 0.05). Adjusted p <= alpha in the expected direction: `supported`; in the
  opposite direction (or any significant effect for expected sign `0`): `contradicted`;
  otherwise `inconclusive`. Each record carries the statistic, `p_value`, `holm_p_value`.
* **Admission**: the primary rule (§3, §3a) is unchanged. Additionally, any
  `contradicted` prediction refuses admission (`rejected`, reason in the summary and in the
  `mechanism_stage` result; the primary criteria still run and are recorded).
* **Record**: `metadata.mechanism_status` (`contradicted` | `mechanism_untested` | `tested`),
  `metadata.mechanism_support` = supported / testable (null when nothing was testable),
  `metadata.mechanism` (all predictions), the `mechanism_stage` result, and, on admission,
  `registry_entry.mechanism_support|mechanism_status` and `replication_proof.mechanism`.
* **Untested**: no testable prediction (including no predictions, e.g. template-derived
  candidates) is admitted as `mechanism_untested` when the primary checks pass.
* **Ranking**: `persona_strategy_match` adds `3 * mechanism_support` to the existing
  `evidence_quality` factor; untested adds nothing, so it ranks below an otherwise equal
  supported candidate; a `contradicted` record is the hard blocker `mechanism_contradicted`.
* **Known limits**: tests are in-sample on short real histories and overlapping forward
  windows are not de-overlapped, so a card needs enough events/observations
  (`mechanism_min_*`) or it is `inconclusive`. Carrying `predictions[]` from the compiled
  spec into `hypothesis_predictions` is the spec producer's job; the revalidation worker
  forwards `raw_spec.hypothesis_predictions` / `hypothesis_card.predictions` /
  `mechanism_inputs` and the approved run's dataset.

## 3c. Intraday (Minute) Replication (SD_INTRADAY_MINUTE_BARS §7, §9 K6)

Minute candidates (`bar_frequency` in 1m/5m/15m/30m/60m) use the same criterion
`real_backtest_validation`, but windows and minimum samples are counted in **clean sessions and
trades**, not days.

| Threshold (GateConfig) | Default / floor |
|---|---|
| `min_clean_sessions` | 60 |
| `min_in_sample_trades` | 300 (closed round trips) |
| `min_oos_sessions` | 20 |
| `min_oos_trades` | 100 (closed round trips) |

* Defaults are **floors**: `GateConfig` raises `ValueError` if any value is set lower. Thin minute
  history is reported as `insufficient_history` with the exact shortfall; it never relaxes the gate.
* With the 70/30 split, `floor(0.7 n)` in-sample sessions leave `n - floor(0.7 n)` out-of-sample
  sessions, so the 20-OOS-session floor means in practice **n >= 66 clean sessions**; 60 is a floor,
  not sufficient on its own.
* A *clean session* has the full regular bar count for every target instrument. Any unclean session in
  the window refuses the evidence (`intraday_evidence_unqualified`); sessions are not silently dropped.
* Windows (IS, OOS, 3 walk-forward folds) are cut on session boundaries by
  `run_intraday_replication_protocol`, which runs the K5 engine (next-bar-open fills, flat by 13:25,
  frequency-correct annualisation, statutory costs on every fill). No daily or synthetic fallback.
* Identity is preserved and checked: `evidence_mode == "replay"`, `live_or_paper_evidence == false`,
  `replay_clock == "bar_end"`, availability ceiling, `source_snapshot` (`tw_price_1m`,
  `reconciled_only`, `content_sha256`), `bar_frequency` equal to the strategy's declared frequency.
  Quarantined / waived / isolated / unreconciled data (any such flag) is **not** replication evidence.
* Replay is never live/paper evidence. The admission record reports `details.intraday` (counts,
  thresholds, snapshot hash).
* Trials: every evaluated variant (each frequency and wrapper) increments the family trial counter,
  so the deflated Sharpe threshold counts them; no walk-forward longer than 12 + 6 months is claimed.
* Screen strategies remain daily-only.

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
