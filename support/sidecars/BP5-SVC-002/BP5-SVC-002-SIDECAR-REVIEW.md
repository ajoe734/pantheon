# BP5-SVC-002 Review Packet

**Sidecar Kind:** review_packet
**Helper Parent:** BP5-SVC-002
**Prepared by:** Claude
**Reviewer:** Codex
**Prepared at:** 2026-04-15
**Parent Task Status:** done (archived)
**Parent Commit:** `7e7cff4385626c31dcb56984cd6774616df665bc`
**Commit Subject:** `BP5-SVC-002: realize registry artifact-state and deployment-stage split API`

> **Scope note:** This is a support-only artifact summarizing the review evidence for BP5-SVC-002.
> No canonical truth files were modified. This packet is advisory input for downstream consumers
> and records the formal review outcome for audit purposes.

---

## 1. Review Outcome

| Field | Value |
|-------|-------|
| Reviewer | Codex |
| Review date | 2026-04-15 |
| Review file | `.coordination/reviews/BP5-SVC-002-review.md` |
| Outcome | **Approved — no remaining findings** |

### Reviewer Verdict (Codex)

> "No remaining findings. The earlier API error-mapping issue is resolved."

Key findings addressed before approval:

1. `RegistryNotFoundError` is a dedicated subclass in `services/registry/split_api.py:36`; missing-entry paths raise it consistently from `get()`, `advance_artifact_state()`, and `update_deployment_summary()`.
2. The FastAPI adapter in `services/registry/service.py:110` returns `404` for missing registry entries and `400` for governed split-model validation failures on both write endpoints — not conflated.
3. Regression coverage in `services/registry/test_service.py:524` includes all four API error paths raised in the previous review round.

---

## 2. Test Evidence Summary

| Suite | Command | Result |
|-------|---------|--------|
| Smoke tests | `python3 services/registry/smoke_test.py` | **40 passed, 0 failed** |
| Unit / API tests | `pytest -q services/registry/test_service.py` | **38 passed in 2.71s** |

---

## 3. Acceptance Criteria Verification

| AC # | Criterion | Evidence | Status |
|------|-----------|----------|--------|
| AC-1 | Registry service surfaces expose `artifact_state` separately from `deployment_stage` | All six read/write endpoints in `services/registry/service.py` and `split_api.py` expose the two fields as distinct top-level members. `resolve_deployment_view()` derives `deployment_stage` from the actually-deployed approved entry. | **MET** |
| AC-2 | Read/write schemas, storage projection, and smoke tests all use the split model | `models.py` defines `ArtifactState` and `DeploymentStage` as separate enums. `storage.py` projects them independently. 40 smoke scenarios and 38 pytest cases exercise split-model paths including forbidden transitions and API error mapping. | **MET** |

---

## 4. Delivered Artifacts

| File | Role |
|------|------|
| `services/registry/split_api.py` | Core registry operations with `RegistryNotFoundError` / `RegistryError` distinction |
| `services/registry/service.py` | FastAPI adapter; 404 for missing entries, 400 for validation failures |
| `services/registry/models.py` | `ArtifactState` and `DeploymentStage` enums; `RegistryEntry`, `RegistryEntryView`, `DeploymentView` |
| `services/registry/storage.py` | In-memory `RegistryStore`; persists both split fields independently |
| `services/registry/smoke_test.py` | 40 end-to-end smoke scenarios |
| `services/registry/test_service.py` | 38 pytest cases including all four API error-mapping paths |
| `services/registry/contract.md` | Contract document reflecting `artifact_state` / `deployment_stage` separation |
| `services/registry/registry_entry_schema.json` | Machine-readable schema for registry entries |

---

## 5. L1 Contract Alignment

| L1 Document | Alignment |
|-------------|-----------|
| `TARGET_ARCHITECTURE.md` | Split model matches §artifact governance; registry is the governed lifecycle truth. `artifact_state` remains the governed lifecycle; `deployment_stage` is exposed only as derived deployment/read-model state. |
| `PAPER_CANARY_LIVE_POLICY.md` | `artifact_state=approved` gate before deployment stage progression is consistent with the policy. |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | `deployment_stage` as a derived deployment-read-model field is consistent with binding ownership being in the runtime plane, not the registry plane. |
| `PERSONA_RUNTIME_MODEL.md` | Registry entries are persona-agnostic artifacts; no conflict introduced. |
| `services/registry/contract.md` | Directly updated by BP5-SVC-002; reflects the split model. |

---

## 6. Open Questions for Downstream Consumers

These are scoping inputs for future tasks — not blockers for declaring BP5-SVC-002 done.

| OQ # | Question | Owner | Priority |
|------|----------|-------|----------|
| OQ-1 | Storage layer is currently in-memory (`RegistryStore`). Persistent backend (SQLite/Postgres) is not yet wired. BP5-SVC-016 compose topology must decide whether to bring up a DB sidecar or defer persistence to a later wave. | Codex | High — blocks BP5-SVC-016 compose design |
| OQ-2 | `resolve_deployment_view()` depends on `DeploymentView` data from the control plane. The handshake between registry and runtime-manager (BP5-SVC-007) for writing `deployment_stage` updates back into the registry is not yet implemented. | Claude / BP5-SVC-007 owner | High — blocks end-to-end governance path |
| OQ-3 | `services/registry/contract.md` is still marked `DRAFT`. Once OQ-1/OQ-2 are resolved, a minor update to the contract status is warranted. | Codex | Low |
| OQ-4 | `PAPER_CANARY_LIVE_POLICY.md` references the split model but does not yet call out the `resolve_deployment_view()` API surface. Advisory: add a pointer so policy readers know where the runtime projection lives. | Codex | Low |
| OQ-5 | Promotion sub-path in `services/registry/promotion/` was not modified in BP5-SVC-002. Verify that promotion logic is consistent with the new split model before BP5-SVC-016 smoke. | Codex | Medium |

---

## 7. Companion Sidecar Artifacts

| Artifact | Path |
|----------|------|
| Acceptance packet | `support/sidecars/BP5-SVC-002/BP5-SVC-002-SIDECAR-ACCEPTANCE.md` |
| Formal review file | `.coordination/reviews/BP5-SVC-002-review.md` |

---

## 8. Handoff Notes for Codex

1. **AC-1 and AC-2 are both MET.** The implementation is complete and review-approved.
2. **OQ-1 (persistent storage) and OQ-2 (registry–runtime-manager handshake)** are the two load-bearing open questions before BP5-SVC-016 can close end-to-end compose smoke. They are not retroactive blockers for BP5-SVC-002.
3. **OQ-5 (promotion sub-path)** should be verified before BP5-SVC-016 is marked `in_progress`.
4. This packet may be absorbed into the main delivery evidence or linked from the parent task archive — that decision is with the parent owner (Codex).
5. No canonical truth files were created or modified by this sidecar.

---

*Support artifact only. Do not edit L1 canonical files based on this document.*
