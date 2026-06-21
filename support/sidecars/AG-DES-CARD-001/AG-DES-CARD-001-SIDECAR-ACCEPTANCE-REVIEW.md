# Review: AG-DES-CARD-001-SIDECAR-ACCEPTANCE

**Reviewer:** Claude2
**Reviewed at:** 2026-06-21
**Task:** AG-DES-CARD-001-SIDECAR-ACCEPTANCE
**Artifact:** `support/sidecars/AG-DES-CARD-001/AG-DES-CARD-001-SIDECAR-ACCEPTANCE.md`
**Verdict:** APPROVED — no required changes

---

## Review Summary

The acceptance packet is complete, accurately scoped, and ready to hand off to the parent reviewer of AG-DES-CARD-001.

**Scope compliance:** The packet creates only a support artifact. No L1 canonical truth, OpenAPI contracts, schemas, or service implementations were modified. ✓

**Completeness:** All five acceptance checklist sections are covered: schema completeness, card-type field-level payload definitions, BFF route contract, no-parse frontend rule, and integration gate (AG-XR-OPENAPI-004). ✓

---

## Section-by-Section Findings

### §1 – What AG-DES-CARD-001 Delivers

Deliverable table correctly maps prose contract, schema draft, OpenAPI delta, and bundle index reference to their canonical target paths. The distinction between "committed (round2 bundle)" and "awaits AG-XR-OPENAPI-004" is accurate.

All 12 card types are enumerated and match the enum in `workshop_card.schema.json`. No additional or missing types found.

### §2 – Current State

The "not yet created" items (canonical v4 schema path, `agora_v1_3.openapi.yaml`, `bundle_index.v1_3.json`, frontend generated types) are correctly attributed to the AG-XR-OPENAPI-004 unblock gate. The draft schema is correctly identified as sitting in `design-closure-round2/schemas/`, not yet at the canonical `services/control-plane/specs/agora/v4/` path.

### §3 – Dependency Map

**Upstream (§3.1):** All six upstream dependencies are correct:
- SW-DB → `workshop_id`, `workshop_version_id`, `sequence_no` references ✓
- VERS → `version_patch_proposal`, `version_compare` card payloads ✓
- RS → `research_plan_proposal`, `research_progress`, `research_result` payloads ✓
- SSE → card-update event type alignment ✓
- TR → three-gate `readiness_gate` and consultation refs via `consult_result` ✓
- OPENAPI-004 → canonical schema/OpenAPI/bundle merge ✓

**Downstream (§3.2):** The four downstream tasks (AG-FE-SW-001, AG-FE-SW-002, AG-FE-RS-001, AG-FE-TR-001) and their unblock conditions are consistent with `07_dispatch_unblock_matrix.md`.

**Integration gate note (§3.3):** The prohibition on downstream tasks citing section numbers (must cite merged path + bundle hash + generated commit) is important and correctly stated.

### §4 – Acceptance Checklist

**§4.1 Schema completeness:** All seven criteria are precise and verifiable post-merge. The `additionalProperties: false` at root criterion is correctly paired with `additionalProperties: true` for `payload` — this is not contradictory (root enforces envelope, payload allows card-type extension).

**§4.2 Card-type coverage table:** All 12 card types have payload source and key field checks. One note: `version_patch_proposal` lists `validation state` as a key field — this is slightly informal; "validation_state" as a string key would be more precise. This is a cosmetic issue; it does not block approval.

**§4.3 BFF route contract:** Five criteria cover route presence, schema ref, auth scope, inline embed, and SSE card-id-only behavior. All are actionable post-AG-XR-OPENAPI-004.

**§4.4 No-parse frontend rule:** Three criteria are correctly stated and trace directly to the design intent that cards are BFF projections, not LLM markdown parsing outputs.

**§4.5 Integration gate:** Four criteria tie back to the canonical schema path, the OpenAPI grep, the bundle hash entry, and the bundle extension chain. These are the right checks.

### §5 – Reviewer Attention Points

All six attention points correctly surface ambiguities that belong to the parent reviewer or parent task scope:

1. **Payload extensibility** (`additionalProperties: true` on payload) — correctly flagged; BFF-layer validation strategy is a parent task decision. ✓
2. **Twelve types exhaustive for v1.3** — correctly draws the v1.4 additive contract boundary. ✓
3. **`servant_reconstruction` list separation** — `causal_chain[]` vs `servant_inferences[]` must not be flattened; UI design implication correctly flagged. ✓
4. **`research_result` backend mode label** — payload location (`payload.backend_mode`) is called out. This note is sufficient; parent reviewer should confirm whether this field needs to surface at the envelope level for UI label rendering. No change required to the packet.
5. **Card staleness trigger boundary** — correctly scoped to SSE + completeness-update layer, not CARD layer. ✓
6. **This packet does not gate AG-XR-OPENAPI-004** — correctly noted; ensures there is no circular dependency. ✓

---

## Minor Observations (no changes required)

- `version_patch_proposal` key field `validation state` (§4.2) could be spelled `validation_state` for precision. Non-blocking.
- §5 attention point #4 could additionally note whether the parent task brief specifies a label contract for `backend_mode` (e.g., "paper / fixture / stub" label set). This is a follow-up suggestion for the parent reviewer, not a defect in this packet.

---

## Approval Statement

The acceptance packet fulfills its stated scope: support artifact only, no canonical truth modified, comprehensive checklist, and clear integration gate documentation. The parent reviewer of AG-DES-CARD-001 has sufficient detail to proceed with schema review, BFF route verification, and downstream unblock decisions.

**This sidecar task is approved for `review_approved` and owner finalization.**

LLM-Agent: Claude2
Reviewer-of: AG-DES-CARD-001-SIDECAR-ACCEPTANCE
Task-ID: AG-DES-CARD-001-SIDECAR-ACCEPTANCE
