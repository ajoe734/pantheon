# AG-DES-SW-REF-001-SIDECAR-ACCEPTANCE — Review Notes

- Reviewer: Claude2
- Review date: 2026-06-21
- Task: AG-DES-SW-REF-001-SIDECAR-ACCEPTANCE
- Owner: Claude
- Decision: **APPROVED**

## Review Verdict

All eight reviewer questions documented in the packet pass. The packet is
accepted as the formal support acceptance and dependency map for
`AG-DES-SW-REF-001`.

## Checklist Pass / Fail

| Reviewer question | Result | Notes |
|---|---|---|
| Support-only boundary preserved | PASS | Only `support/sidecars/AG-DES-SW-REF-001/AG-DES-SW-REF-001-SIDECAR-ACCEPTANCE.md` created. `git status --short` shows zero modified canonical files. |
| Three-identifier model correct | PASS | `strategy_id`, `strategy_spec_registry_id`, `active_strategy_spec_registry_id` each carry distinct semantics with explicit NULL semantics. No fourth identifier invented. Matches §5.1 of the deep design closure. |
| No-parallel-store prohibition correct | PASS | R7 explicitly prohibits copying StrategySpec JSON body, lifecycle state, ExperimentRun truth, and CandidateArtifact truth into Workshop tables. |
| Conclude prohibition on lifecycle promotion correct | PASS | R6 states conclude does not change Registry lifecycle. "Registry/governance retains exclusive control over `draft → candidate → approved → retired`." |
| `strategy_spec_ref` deprecation path correct | PASS | R5 maps `strategy_spec_ref` → `strategy_spec_registry_id` (not `strategy_id`). Marked deprecated. v1.2 OpenAPI must carry the deprecation annotation. |
| `strategy_workshop_version_link` schema correct | PASS | R4 lists all nine required fields including `parent_workshop_version_id` and `source_event_id`. Both UNIQUE constraints present: `(workshop_id, sequence_no)` and `(workshop_id, strategy_spec_registry_id)`. |
| Dependency map parallel (not sequential) | PASS | Mermaid graph shows PRIV-001, REF-001, DB-001 as independent, each pointing directly to AG-BE-SW-001. Prose confirms no strict ordering among design tasks. |
| Broker/capital/RuntimeBinding authority excluded | PASS | Checklist item 13 explicitly guards this. No field or operation in the packet implies live-order routing, capital binding, or RuntimeBinding writes. |

## Observations

- The 13-item acceptance checklist is complete and maps directly to R1–R7
  requirements. The parent task owner has a clear specification target.
- The five documented gaps (opaque `strategy_spec_ref`, missing
  `strategy_id`/`strategy_spec_registry_id` split in schema, undocumented
  `strategy_workshop_version_link`, conclude semantics, `selected_version_id`
  conflict) are each traceable to specific source lines.
- Test reference table (six cases from §10 of design closure) gives the
  AG-BE-SW-001 implementer unambiguous test targets without requiring the
  contract document to implement them.
- Suggested contract document structure (§1–9) is informative but non-binding;
  the parent owner may deviate.

## No Objections Required

No reopen conditions identified. The sidecar boundary is clean. Returned to
owner Claude for finalization.
