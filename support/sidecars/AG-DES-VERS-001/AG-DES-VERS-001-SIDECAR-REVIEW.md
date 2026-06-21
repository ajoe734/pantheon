# AG-DES-VERS-001 Review Packet

**Sidecar kind:** review_packet
**Parent task:** AG-DES-VERS-001 — Strategy versioning: patch + version-compare + readiness contract (v1.3)
**Prepared by:** Claude2 (AG-DES-VERS-001-SIDECAR-REVIEW)
**Prepared at:** 2026-06-21
**Target reviewer:** Claude

---

## 1. Scope of Review

AG-DES-VERS-001 added three JSON Schema files into `services/control-plane/specs/agora/v4/`:

| Schema | Path |
|---|---|
| `version_patch_proposal.schema.json` | `services/control-plane/specs/agora/v4/version_patch_proposal.schema.json` |
| `version_compare.schema.json` | `services/control-plane/specs/agora/v4/version_compare.schema.json` |
| `strategy_readiness.schema.json` | `services/control-plane/specs/agora/v4/strategy_readiness.schema.json` |

Authority reference: `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/01_strategy_versioning_patch_readiness.md`

---

## 2. Integrity Check

**Byte-identical comparison with design-closure reference schemas:**

```
diff docs/04/.../schemas/version_patch_proposal.schema.json \
     services/control-plane/specs/agora/v4/version_patch_proposal.schema.json
→ IDENTICAL

diff docs/04/.../schemas/version_compare.schema.json \
     services/control-plane/specs/agora/v4/version_compare.schema.json
→ IDENTICAL

diff docs/04/.../schemas/strategy_readiness.schema.json \
     services/control-plane/specs/agora/v4/strategy_readiness.schema.json
→ IDENTICAL
```

All three deployed schemas are byte-for-byte identical to their counterparts in
`docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/`.

**Frozen-file check:** Commit `14fd43a0` confirms the following files were not touched:
- `bundle_index.json`
- `bundle_index.v1_1.json`
- `bundle_index.v1_2.json`
- `agora_v1.openapi.yaml`
- `agora_v1_1.openapi.yaml`
- `agora_v1_2.openapi.yaml`

**Hash alignment with bundle_index.v1_3.template.json:**

The commit message records sha256 prefixes matching the template:
- `version_patch_proposal` → `5d7b4ca3...8da8c` ✓
- `version_compare` → `732829b7...dcba2` ✓
- `strategy_readiness` → `5ab691bb...b816` ✓

---

## 3. Design Spec Compliance — Section by Section

### §A1 Path Allowlist (version_patch_proposal)

The deployed `patch_operation.path` regex enforces exactly the §A1 allowlist:

```json
"pattern": "^/(title|hypothesis|objective|market_scope|data_dependencies|execution_profile|evaluation_plan|governance|evidence_refs|code_refs|metadata)(/.*)?$"
```

Forbidden system-owned paths (`/spec_version`, `/strategy_id`, `/lifecycle_state`, `/provenance`) are absent from the allowlist — compliant.

### §A2 Restricted RFC 6902 (version_patch_proposal)

`patch_operation.op` enum contains exactly `["add", "remove", "replace", "test"]`. `move` and `copy` are absent — compliant.

The `allOf` constraint correctly requires `value` for `add`, `replace`, and `test` operations; `remove` does not require `value` — compliant.

### §A3 VersionPatchProposal Lifecycle

Schema `status` enum: `["draft", "validating", "validated", "invalid", "accepted", "rejected", "superseded"]`

This maps precisely to the §A3 state machine:
- `draft → validating → validated → accepted/rejected`
- `validating → invalid`
- `draft | validated → superseded`

Required fields include `proposal_id` (prefix-pattern `^vpp_[A-Za-z0-9_-]+$`), `base_document_sha256` (64-hex pattern), `source_event_ids` (minItems: 1), and `patch_format` locked to `"rfc6902-restricted-v1"` — compliant.

### §A4 Version Comparison (version_compare)

- `candidate_versions`: minItems: 1, maxItems: 4 → 1 base + up to 4 candidates — compliant.
- Diff separation: `field_diffs`, `metric_diffs`, `risk_diffs`, `readiness_diffs` all present as distinct top-level arrays — compliant.
- `evidence_class` enum: `["predicted", "backtested_in_sample", "backtested_oos", "paper_observed"]` — four classes, matches §A5 — compliant.
- `recommendation.decision_authority` enum: `["trader"]` only — compliant.
- `comparison_id` prefix-pattern: `^vcmp_[A-Za-z0-9_-]+$` — compliant.

### §A5 Evidence Class Separation

`metric_diffs[].evidence_class` is required and enumerated to the four classes. The schema does not merge predicted and observed values — compliant. (Rendering policy enforcement is application-level; the schema correctly carries the class label so renderers can act on it.)

### §A6 Readiness State Machine (strategy_readiness)

**Gate enum:** `["preliminary_research", "full_validation", "trading_room"]` — three gates required — compliant.

**Gate state enum:** `["not_assessed", "blocked", "conditional", "ready", "stale"]` — five states match §A6 — compliant.

**`gates` array constraints:** minItems: 3, maxItems: 3 — exactly three gates required — compliant.

**`assessment_id` pattern:** `^ready_[A-Za-z0-9_-]+$` — compliant.

**Requirement hardness field:** `["hard", "soft"]` — compliant with §A6 table (hard/no-hard columns).

**Requirement state enum:** `["missing", "partial", "satisfied", "waived", "stale"]` — covers all operational states.

**`highest_ready_gate`:** nullable string enum over the three gate names plus `null` — correctly allows null when no gate is ready.

**Waiver sub-object:** `waived_by`, `reason`, `expires_at` — correctly scoped with `additionalProperties: false`.

### §A7 Staleness

`staleness_reasons` array is present at the top level of `StrategyReadinessAssessment`. The schema supports `stale` as a gate state. Application-level triggers (StrategySpec version change, artifact supersession, etc.) are policy, not schema — the schema correctly exposes the surfaces for them — compliant.

### §A8 Error Codes

Error codes are operational contract, not schema structure. The schema defines the state machine surfaces (status fields, blocking_requirement_ids, conflicts, warnings) that map to each error code. No schema gaps found against the §A8 code list.

---

## 4. Schema Quality Observations

### Strengths

1. **`additionalProperties: false`** on all objects — no unintended extension surface.
2. **Shared `evidence_ref` definition** duplicated faithfully across all three schemas; each schema is self-contained (Draft-07 `$ref` resolution within the same document).
3. **Pattern constraints on IDs** (`vpp_*`, `vcmp_*`, `ready_*`) prevent cross-type ID collisions.
4. **`base_document_sha256`** on `VersionPatchProposal` and `version_ref` enforces tamper-evident base-version binding.
5. **Nullable `[number, null]`** on metric delta fields — handles not-yet-computed deltas without schema violations.
6. **`minLength: 1`** on all open string fields — no empty-string IDs.

### Minor Observations (non-blocking)

1. **`requirement.state` does not include `blocked`**: Gate-level `state` has `blocked`, but individual `requirement.state` uses `missing/partial/satisfied/waived/stale`. This is consistent with the design spec (§A6 does not define a per-requirement `blocked` state) but reviewers should confirm intentional omission vs. future need.

2. **`version_compare.schema.json` does not list `risk_diffs` in `required`**: `risk_diffs` has `default: []` and is not required. This is consistent with the design closure (risk diff is optional in the schema), but reviewers should confirm this matches the intended API contract — a comparison with no risk diff is valid by schema.

3. **`VersionPatchProposal.accepted_workshop_version_id` and `accepted_strategy_spec_registry_id`** are optional strings with no pattern constraint. They appear only post-acceptance; consider whether a pattern constraint (matching Workshop version ID format) would add useful validation.

4. **`validation` sub-object is entirely optional** on `VersionPatchProposal`. The task brief says validation results are recorded after the `validating` lifecycle step. Optional here is correct, but reviewers should confirm that the application layer enforces its presence before status transitions to `validated` or `invalid`.

---

## 5. Freeze Boundary Compliance

Confirmed from commit `14fd43a0`:
- All changes are additive into `services/control-plane/specs/agora/v4/`.
- No modifications to v1/v1.1/v1.2 frozen files.
- No modifications to existing OpenAPI YAML files.
- The `bundle_index.v1_3.template.json` in the design closure is a template requiring computed hashes — it was not itself deployed, consistent with the task brief instruction.

---

## 6. Evidence Summary

| Check | Result |
|---|---|
| Reference schema diff (all 3 files) | IDENTICAL |
| Frozen file modification | None |
| Restricted RFC 6902 ops | Compliant |
| §A1 path allowlist regex | Compliant |
| §A3 lifecycle states | Compliant |
| §A4 candidate cap (≤4) | Compliant |
| §A4 decision_authority = trader | Compliant |
| §A5 evidence_class enum | Compliant |
| §A6 gate states (3 gates, 5 states) | Compliant |
| additionalProperties: false (all objects) | Compliant |
| ID prefix patterns | Compliant |
| SHA-256 hash binding on versions | Compliant |

---

## 7. Reviewer Recommendation

The three schemas land the §A1–§A8 design spec faithfully and without modification. All structural constraints, lifecycle states, path allowlists, evidence classes, gate machines, and frozen-file boundaries are correct.

The four minor observations (§4) are non-blocking clarifications. Items 1 and 2 should be answered by the canonical reviewer (Claude) from the design spec authority; items 3 and 4 are application-layer enforcement questions outside the schema boundary.

**Recommendation: approve AG-DES-VERS-001 as delivered, with the four minor observations noted for the handoff packet.**

---

## 8. Handoff Note

This packet is ready for Claude (AG-DES-VERS-001 reviewer as listed in `ai-status.json`) to use as supporting evidence for the formal review of the parent task. The parent task owner (Claude) should incorporate these findings into the task's canonical review decision.

This sidecar does not modify any L1 canonical document, registry schema, or runtime contract. All output is scoped to `support/sidecars/AG-DES-VERS-001/`.
