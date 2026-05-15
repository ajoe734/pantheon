# BP5-SVC-002 Acceptance Packet

**Sidecar Kind:** acceptance_packet
**Helper Parent:** BP5-SVC-002
**Prepared by:** Claude (helper draft)
**Finalized by:** Codex
**Reviewer:** Claude
**Prepared at:** 2026-04-15
**Parent Task Status:** done (archived)
**Parent Commit:** `7e7cff4385626c31dcb56984cd6774616df665bc`
**Commit Subject:** `BP5-SVC-002: realize registry artifact-state and deployment-stage split API`
**Sidecar Review Status:** approved by Claude on 2026-04-15

> **Scope note:** This is a support-only artifact. No canonical truth files were modified.
> It is advisory input for downstream consumers and the parent-task owner (Codex).

---

## 1. Acceptance Criteria Verification

| AC # | Criterion | Evidence | Status |
|------|-----------|----------|--------|
| AC-1 | Registry service surfaces expose `artifact_state` separately from `deployment_stage` | All six read/write API endpoints in `services/registry/service.py` and `split_api.py` expose the two fields as distinct top-level members. `resolve_deployment_view()` derives `deployment_stage` from the actually-deployed approved entry rather than conflating it with lifecycle state. | **MET** |
| AC-2 | Read/write schemas, storage projection, and smoke tests all use the split model | `services/registry/models.py` defines `ArtifactState` and `DeploymentStage` as separate enums. `services/registry/storage.py` projects them independently. `smoke_test.py` (40/40 pass) and `test_service.py` (38/38 pass) exercise split-model paths including forbidden transitions and API error mapping. | **MET** |

---

## 2. Delivery Summary

### Implemented Files

| File | Role |
|------|------|
| `services/registry/split_api.py` | Core registry operations: `register()`, `get()`, `list_by_strategy()`, `advance_artifact_state()`, `resolve_latest_approved()`, `resolve_deployment_view()`. Includes `RegistryNotFoundError` (HTTP 404) and `RegistryError` (HTTP 400) distinction. |
| `services/registry/service.py` | FastAPI adapter; maps `RegistryNotFoundError` → 404, `RegistryError` → 400, exposes six REST endpoints. |
| `services/registry/models.py` | `ArtifactState` enum (`draft`, `candidate`, `approved`, `retired`), `DeploymentStage` enum (`paper`, `canary`, `live`, `none`), `RegistryEntry`, `RegistryEntryCreate`, `RegistryEntryView`, `DeploymentView`. |
| `services/registry/storage.py` | In-memory `RegistryStore`; persists `RegistryEntry` objects with both split fields. |
| `services/registry/smoke_test.py` | 40 end-to-end smoke scenarios covering creation, state transitions, deployment-view resolution, and error paths. |
| `services/registry/test_service.py` | 38 pytest cases including forbidden-transition rejection, split-semantics regression, and all four API error-mapping paths validated in review rounds. |
| `services/registry/contract.md` | Contract document; reflects `artifact_state` / `deployment_stage` separation. |
| `services/registry/registry_entry_schema.json` | Machine-readable schema for registry entries. |

### Key Semantic Rules Implemented

1. `register()` rejects creation in `approved` or `retired` state — only `draft` / `candidate` are allowed at creation time.
2. `advance_artifact_state()` enforces governed transitions via `ALLOWED_ARTIFACT_TRANSITIONS`; registry does not touch `deployment_stage`.
3. `resolve_deployment_view()` derives `deployment_stage` from the **actually deployed approved artifact** (not the latest approved entry in isolation).
4. `advance_state()` returns HTTP 404 (entry not found) or HTTP 400 (invalid transition) — not conflated.
5. `update_deployment_summary()` returns HTTP 404 (not found) or HTTP 400 (not in approved state) — not conflated.

---

## 3. Dependency Map

```
BP5-SVC-001 (done)
    └─► BP5-SVC-002 (done — 7e7cff4)          ← this task
            ├─► BP5-SVC-016 (todo — Docker/compose/smoke topology)
            │       depends_on: BP5-SVC-002, BP5-SVC-003, BP5-SVC-005,
            │                   BP5-SVC-009, BP5-SVC-010, BP5-SVC-015
            └─► (registry read model consumers in BP5-SVC-010, governance in BP5-SVC-012)
```

**Upstream blocker cleared:** BP5-SVC-001 is done; BP5-SVC-002 is done.
**Downstream unblocked:** BP5-SVC-016 may now treat the registry split API as a resolved input for the compose/smoke topology, subject to the open questions below.

---

## 4. Open Questions for Downstream Consumers

| OQ # | Question | Owner | Priority |
|------|----------|-------|----------|
| OQ-1 | Storage layer is currently in-memory (`RegistryStore`). Persistent backend (SQLite/Postgres) is not yet wired. BP5-SVC-016 compose topology must decide whether to bring up a DB sidecar or defer persistence to a later wave. | Codex | High — blocks BP5-SVC-016 compose design |
| OQ-2 | `resolve_deployment_view()` depends on `DeploymentView` data from the control plane. The handshake between registry and runtime-manager (BP5-SVC-007) for writing `deployment_stage` updates back into the registry is not yet implemented. | Claude / BP5-SVC-007 owner | High — blocks end-to-end governance path |
| OQ-3 | `services/registry/contract.md` is still marked `DRAFT`. Once OQ-1/OQ-2 are resolved, a minor update to the contract status is warranted. | Codex | Low |
| OQ-4 | `PAPER_CANARY_LIVE_POLICY.md` references the split model but does not yet call out the `resolve_deployment_view()` API surface. Advisory: add a pointer so policy readers know where the runtime projection lives. | Codex | Low |
| OQ-5 | Promotion sub-path in `services/registry/promotion/` was not modified in BP5-SVC-002. Verify that promotion logic is consistent with the new split model before BP5-SVC-016 smoke. | Codex | Medium |

---

## 5. Contract Alignment Check

| L1 Document | Alignment |
|-------------|-----------|
| `TARGET_ARCHITECTURE.md` | Split model matches §artifact governance; registry is the governed lifecycle truth. |
| `PAPER_CANARY_LIVE_POLICY.md` | `artifact_state=approved` gate before deployment stage progression is consistent with the policy. |
| `PERSONA_RUNTIME_MODEL.md` | Registry entries are persona-agnostic artifacts; no conflict introduced. |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | `deployment_stage` as a derived deployment-read-model field is consistent with binding ownership being in the runtime plane, not the registry plane. |
| `services/registry/contract.md` | Directly updated by BP5-SVC-002; reflects the split. |

---

## 6. Advisory Handoff Notes for Codex (Owner)

1. **AC-1 and AC-2 are both MET.** No further implementation is needed from this sidecar.
2. **OQ-1 (persistent storage) and OQ-2 (registry–runtime-manager handshake) are the two load-bearing open questions** before BP5-SVC-016 can close the end-to-end compose smoke. These are not blockers for declaring BP5-SVC-002 done — they are scoping inputs for future tasks.
3. **OQ-5 (promotion sub-path)** should be verified before BP5-SVC-016 is marked in-progress.
4. The review file at `.coordination/reviews/BP5-SVC-002-SIDECAR-ACCEPTANCE-review.md` records Claude's approval of this sidecar packet; this document remains the structured companion for future reference.
5. This packet may be absorbed into the main delivery evidence or linked from the parent task archive — that decision is with the parent owner (Codex).

---

*Support artifact only. Do not edit L1 canonical files based on this document.*
