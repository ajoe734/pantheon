# REG-002 Review: Promotion Gate Implementation

## Review Status: ✓ APPROVED

**Reviewer:** Copilot  
**Date:** 2026-04-06T14:36:14Z  
**Task:** Implement candidate, paper, and live promotion gate

---

## Acceptance Criteria Assessment

### 1. Candidate/Paper/Live States Enforced ✓ PASS
- Draft → Candidate: ✓ ALLOWED (requires replication_success + lineage)
- Draft → Live: ✓ REJECTED (invalid transition enforced)
- Candidate → Paper: ✓ ALLOWED (requires evaluation_summary + risk_review_passed + sharpe_ratio)
- Candidate → Live: ✓ REJECTED (invalid transition enforced)
- Paper → Live: ✓ ALLOWED (requires approver + rollback metadata)
- Retired transitions: ✓ ALLOWED from all states where valid

**Implementation Details:**
- `PromotionGate.validate_transition()` enforces allowed transition map
- Each state has distinct metadata requirements
- Live promotion requires explicit rollback metadata (canonical or legacy fallback)
- All 4 unit tests in test_gate.py pass

### 2. Promotion Checks Documented ✓ PASS
- README.md documents all lifecycle rules and transitions
- Candidate requirements section: replication_success + source lineage
- Paper requirements section: evaluation_summary with risk_review_passed and sharpe_ratio
- Live requirements section: approver + lineage + rollback metadata
- Legacy compatibility fallback documented (rollback_target_registry_id + rollback_target)
- Execution projection materialization documented
- CLI usage examples provided

**Documentation Quality:**
- Clear section structure (Scope, Lifecycle Rules, Promotion Requirements, Execution Projection, CLI)
- Inline comments in code explain edge cases
- Examples show both standard and legacy pathways

### 3. Deployment Path Consumes Promotion State ✓ PASS
- CronOrchestrator (OC-002) calls PromotionGate.promote() for all deploy workflows
- Cron deploy workflow routes through REG-002 promotion gate
- ExecutionProjection builder materializes metadata with promotion_state, approver, and rollback
- EX-001 artifact loader validates promotion_state and rejects draft/candidate for live execution
- Smoke tests verify: deploy_live_rejects_without_approver(), deploy_uses_promotion_gate_factory()

**Integration Verification:**
- services/execution/artifact_loader.py uses promotion_state from REG-002 metadata
- services/control-plane/cron/service.py integrates PromotionGate factory
- services/execution/smoke_test_artifact_loader.py validates projection → loader path
- All 29 integration tests passing (promotion: 4, cron: 8, execution: 17)

---

## Detailed Code Review

### gate.py Analysis
**Strengths:**
- Clean separation of concerns (validation, promotion, projection)
- Immutable frozen dataclass for ExecutionProjection
- Proper error handling with custom PromotionError exception
- UTC datetime handling consistent across all timestamps
- Legacy lineage normalization (_normalize_lineage) handles source_run_id → source_run_ids mapping

**Notable Implementation Decisions:**
1. **Rollback validation:** Rejects self-referencing targets (current_registry_id/version match)
   - Rationale: Prevents circular rollback chains
   - Validation occurs both in promote() and build_execution_projection()
   - Two fallback pathways: canonical metadata.rollback + legacy metadata.rollback_target_registry_id
   
2. **Execution projection for draft rejected:**
   - Prevents accidental publication of unpromoted artifacts
   - Enforces promotion gate before loader sees metadata
   
3. **Live projection requires rollback:**
   - Ensures all live artifacts have explicit rollback target
   - Blocks accidental live promotions without recovery plan

### cli.py Analysis
**Strengths:**
- Supports both module import and direct script execution (ImportError fallback)
- Accepts --inplace flag for in-place updates
- Clear error codes: 0 (success), 1 (file not found), 2 (promotion rejected), 3 (other)
- Handles all PromotionState values dynamically

### README.md Analysis
**Strengths:**
- Clear layout with Scope, Lifecycle Rules, Promotion Requirements, Execution Projection, CLI sections
- Enumerates specific metadata fields required for each state
- Documents canonical vs legacy compatibility fallback
- Provides working CLI examples
- References dependency contracts (REG-001, EX-001)

---

## Dependency Alignment

### REG-001 Alignment ✓
- Uses lifecycle_state field defined in REG-001 contract
- Validates registry_id, version, artifact_type per contract
- Respects REG-001 versioning semantics

### EX-001 Alignment ✓
- build_execution_projection() materializes EX-001 metadata envelope
- Canonical Object Store keys: openclaw/registry/{strategy_id}/{version}/(metadata.json|artifact.bin)
- Metadata shape includes all fields required by EX-001 loader:
  - registry_id, strategy_id, version, artifact_type, promotion_state, checksum, lineage, created_at, approver, rollback

### OC-002 Integration ✓
- CronOrchestrator.deploy_workflow() instantiates PromotionGate
- Deploy path routes through gate.promote() for all lifecycle transitions
- Smoke test verifies: deploy_live_rejects_without_approver()

### REG-003 Ready ✓
- REG-003 (rollback/lineage requirements) depends on REG-002
- Current implementation enforces lineage in candidate/paper/live
- Rollback metadata structure ready for REG-003 to reference

---

## Test Coverage

| Test Suite | Tests | Status | Coverage |
|---|---|---|---|
| services/registry/promotion/test_gate.py | 4 | PASS ✓ | state transitions, legacy rollback fields, execution projection |
| services/registry/promotion/smoke_test_gate.py | 6 assertions | PASS ✓ | candidate→paper→live flow, invalid transitions, rollback validation |
| services/control-plane/cron/test_cron.py | 8 (includes deploy_live tests) | PASS ✓ | cron integration with promotion gate |
| services/execution/test_artifact_loader.py | 17 (includes projection validation) | PASS ✓ | execution projection materialization, loader rejection rules |
| Syntax validation | py_compile | PASS ✓ | gate.py, cli.py, README.md references |

**Total: 29 tests passing, 0 failures**

---

## Known Considerations

### Legacy Rollback Compatibility
The implementation retains a temporary fallback for legacy rollback format:
- Canonical: metadata.rollback.target_registry_id + metadata.rollback.target_version
- Legacy fallback: metadata.rollback_target_registry_id (top-level) + rollback_target (top-level)

This is acceptable as a transitional shim because:
1. EX-001 artifact loader normalizes to canonical form before projection write
2. No direct file I/O or storage bypass occurs
3. Both pathways are validated identically
4. Acceptable per EX-001 review closure (Claude approval)

### Lineage Normalization
The implementation normalizes legacy source_run_id to source_run_ids array:
- Prevents breaking changes in datasets using old schema
- Normalized before any external visibility
- All downstream consumers (loader, projection) see canonical form

---

## Downstream Readiness

✓ **REG-003** (rollback/lineage requirements) can proceed - REG-002 provides enforcement foundation  
✓ **FB-003** (execution telemetry) can proceed - REG-002 projection provides structured metadata  
✓ **EV-001** (evaluator contracts) can proceed - REG-002 ensures artifacts carry lineage  
✓ **Deployment** can proceed - REG-002 gate integrated into cron deploy path  

---

## Conclusion

**REG-002 meets all acceptance criteria:**

1. ✓ Candidate/paper/live states are strictly enforced with clear transition rules
2. ✓ Promotion requirements are fully documented with examples and rationale
3. ✓ Deployment path (cron + EX-001) consumes promotion state end-to-end
4. ✓ All 29 integration tests pass
5. ✓ Dependencies (REG-001, EX-001) aligned and verified
6. ✓ Ready for downstream consumers (REG-003, FB-003, EV-001, deployment)

**Recommendation: APPROVE for merge**

The implementation is production-ready, well-tested, and provides the governance foundation for registry promotion workflows in Pantheon.
