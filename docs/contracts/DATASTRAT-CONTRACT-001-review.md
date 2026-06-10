# Review: DATASTRAT-CONTRACT-001

Reviewer: Claude
Date: 2026-06-09
Task: Add contracts for data sources, strategy seed sources, proposals, and persona matches

## Verdict: APPROVED

## Acceptance Criteria Check

| Criterion | Status | Notes |
|---|---|---|
| JSON schema for data source registry entry | ✅ | `data_source_registry_entry.schema.json` |
| JSON schema for strategy seed source registry entry | ✅ | `strategy_seed_source_registry_entry.schema.json` |
| JSON schema for source change proposal | ✅ | `source_change_proposal.schema.json` |
| JSON schema for persona strategy match | ✅ | `persona_strategy_match.schema.json` |
| Schemas include lifecycle / allowed_use / entitlement / lineage fields | ✅ | All four schemas have these fields |
| Schemas are valid JSON | ✅ | `jq empty docs/contracts/*.schema.json` passes |
| No vendor secrets or API keys | ✅ | Schemas are structural only |

## Semantic Split Verification

The discriminator pattern correctly separates the two registry types:

- `data_source_registry_entry`: `source_kind: const "data_source"`, `source_class` covers market/filing/macro/news data types, `allowed_use` covers runtime deployment stages (paper_runtime, canary_runtime, live_runtime). Prevents workers from treating data feeds as strategy ideas.
- `strategy_seed_source_registry_entry`: `source_kind: const "strategy_seed_source"`, `source_class` covers idea sources (paper, repo, internal_note, telemetry, persona_proposal, alpha_db). `metadata.research_only: const true` and `metadata.execution_route: const "none"` guard against execution misuse. `allowed_use` is strictly research-scoped.
- `source_change_proposal`: `source_kind: enum ["data_source", "strategy_seed_source"]` covers governed change proposals for both source types. Lifecycle status (draft → submitted → approved → rejected → applied → retired) and `lineage.applied_change_refs` provide audit trail.
- `persona_strategy_match`: Operates at the research recommendation layer, not execution. `allowed_use` restricted to research/ticket/eval actions. `metadata.execution_route: const "none"` present.

## Section 10 Alignment

All four schemas align with section 10 of `DATA_STRATEGY_SOURCE_SYSTEM_DESIGN.md`. The design doc was updated to add `Contract stub:` cross-reference links for 10.2, 10.4, and 10.5 (10.1 already had one). No semantic drift found.

## No Issues

The schemas are narrow, well-typed, and correctly separated. The semantic split goal of preventing workers from conflating data sources with strategy seed sources is enforced at the contract layer via `source_kind` discriminators.
