# RS-002 StrategySpec Normalization

`RS-002` consumes governed `RS-001` discovery handoffs and turns them into the canonical objects defined by `OC-003`:

1. `StrategySpec` validated against [services/control-plane/specs/strategy_spec.schema.json](../../control-plane/specs/strategy_spec.schema.json)
2. `WorkflowHandoff` validated against [services/control-plane/specs/workflow_handoff.schema.json](../../control-plane/specs/workflow_handoff.schema.json)
3. A legacy replication compatibility envelope so the current `RS-003` gate can keep running while it migrates to the canonical handoff

`models.py` is the typed StrategySpec domain model for code paths that need
schema-backed construction, validation, or round-trip serialization. It does
not grant execution authority; paper, canary, and live hints still require
governance approval before any downstream deployment work.

## Why this exists

`RS-001` emits a governed research handoff, but that handoff still uses a research-first shape:

- `normalized_findings.strategy_spec.name`
- `normalized_findings.strategy_spec.description`
- `signals`
- `parameters`

`OC-003` is the repo-wide source of truth for normalized strategy intent. `RS-002` is the bridge between those two layers.

## Normalization rules

| RS-001 field | RS-002 canonical field | Rule |
|---|---|---|
| `normalized_findings.strategy_spec.name` | `title` | Used directly |
| `normalized_findings.strategy_spec.description` | `hypothesis` | First sentence is kept, then signal and parameter hints are folded into the thesis text |
| `evaluation_hypotheses` | `objective` | Used directly when present; otherwise RS-002 emits a governed replication objective |
| `source_paper` / `doi` / `source_repository` / `api_endpoint` | `data_dependencies[]`, `provenance.source_refs[]`, `registry_hints.lineage_ref` | All lineage-bearing refs are preserved |
| `signals`, `parameters` | `replication_handoff.normalized_findings.strategy_spec` | Preserved in the compatibility projection while OC-003 stays intentionally execution-light |
| `grok_processing_notes.normalization_confidence` + `downstream_readiness` | `registry_hints.initial_lifecycle_state` | `candidate` only when governance is verified and the material is ready for replication; otherwise `draft` |
| missing symbols / venues / cadence | `market_scope` | RS-002 emits explicit research sentinel values such as `RESEARCH_UNIVERSE` instead of inventing broker-ready detail |

## Registry-ready fields included

Every canonical output includes:

- stable `strategy_id`
- `registry_hints.artifact_type = strategy_spec`
- `registry_hints.initial_lifecycle_state`
- lineage reference and producer run id
- `governance.approval_required = true`
- `governance_context.execution_context = research`

That means downstream registry and execution work consume the same governed `StrategySpec`, not an ad hoc research note.

## Evidence and code refs lineage

`lineage.py` is the package surface for attaching governed source evidence and
repo code references to a StrategySpec without creating registry or execution
side effects.

- `build_strategy_spec_lineage_refs()` accepts a `StrategySpecSeed` and optional
  governed `SourceRecord` / `EvidenceItem` inputs from the seed lineage.
- `evidence_refs[]` carries the seed evidence bundle plus supporting
  `EvidenceItem` refs.
- `code_refs[]` carries allowlisted repository, path, commit, symbol, and line
  references from source/evidence metadata or from repo-source fallback fields.
- `attach_lineage_refs_to_strategy_spec_payload()` returns a payload copy with
  the refs attached and preserves `provenance.source_refs`.
- `StrategySpecLineageRefs.to_lineage_edge()` emits the normalized
  `strategy_spec_evidence_code_linked` edge for downstream lineage read models.

The helper rejects `SourceRecord` or `EvidenceItem` inputs that are outside the
seed lineage. It is evidence/linkage plumbing only; it does not write registry
state, launch experiments, create deployment plans, or route orders.

## Compatibility note

The current replication gate still expects a legacy research handoff envelope for lineage/governance checks. `RS-002` therefore emits:

- canonical `workflow_handoff` for future registry/execution consumers
- `replication_handoff` for the existing `RS-003` gate

The compatibility projection is intentionally narrow. It preserves `signals`, `parameters`, and reviewer-oriented notes while keeping the canonical `StrategySpec` as the primary object.
