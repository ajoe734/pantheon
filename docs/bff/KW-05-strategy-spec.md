# KW-05 Strategy Spec BFF Contract

## Purpose

Provide the fifth and final browse module for the Knowledge Workbench. This contract defines the strategy-spec list route, versioned detail route, versioning and lifecycle semantics, evidence citation panel, and backend-composed diff or compare contract for the Strategy Spec module.

The canonical `StrategySpec` object exists in `services/control-plane/specs/strategy_spec.schema.json`. That schema defines field-level truth for a single spec document. This contract defines the BFF workbench layer above it: list projection, version browsing, lifecycle state, citation panel assembly, and field-level diff composition. The frontend must not synthesize any of these from raw schema objects.

Upstream dependencies:
- `KW-01` must be Lovable-ready. Strategy-spec lineage anchors reference `entry_id` values from Institutional Memory.
- `KW-03` must be Lovable-ready. The citation panel references `ref_id` values from Evidence Refs. The KW-03 read model is the canonical source for per-citation evidence display.

---

## Strategy Spec Identity and Versioning

### Canonical identifiers

Every strategy spec object has two stable identifiers:

| Identifier | Format | Scope |
|---|---|---|
| `strategy_id` | `strat-{UUID}` | Persistent across all versions of the same spec family |
| `version_id` | `specver-{UUID}` | Unique to a specific version snapshot |

- `strategy_id` is the canonical key for all list browsing, filter references, and citation drilldown.
- `version_id` identifies a specific immutable snapshot. Once a version is committed it must not be mutated.
- The frontend must not guess or construct either identifier.

### Version sequence

Versions within a spec family are ordered by a monotonically increasing `version_seq` (integer, starting at 1). `version_seq` is BFF-assigned and must not be inferred from creation timestamps.

### Ancestry

Each version carries:
- `parent_version_id` — the `version_id` of the immediately preceding version (null for the first version)
- `root_version_id` — the `version_id` of the first version in the family

These ancestry fields allow the compare surface to resolve version pairs without client-side graph traversal. The frontend must not reconstruct ancestry chains from creation timestamps or `version_seq` arithmetic.

---

## Lifecycle States

Strategy specs have a three-state lifecycle. The BFF resolves lifecycle state from the registry projection; the canonical `StrategySpec` JSON schema does not carry this field.

| `lifecycle_state` | Meaning |
|---|---|
| `draft` | Spec is being authored and has not entered governance review |
| `approved` | Spec has passed governance review and is eligible for use in live or paper personas |
| `deprecated` | Spec has been retired; no new bindings should reference it |

Display rules:
- `deprecated` specs remain visible in the list when `include_deprecated=true` is requested. They must not be silently hidden.
- When a spec is `deprecated`, the detail view must show a `deprecated_at` timestamp and a `successor_strategy_id` when one exists.
- The frontend must not derive lifecycle state from `governance.approval_required`, `provenance.source_kind`, or any other schema field.
- Lifecycle transitions are BFF-asserted only. The frontend must not speculatively show approval or deprecation actions unless `allowedActions` explicitly permits them.

---

## Version Selector Semantics

The detail route supports two modes of version selection via query parameter:

| `version` value | BFF behavior |
|---|---|
| omitted or `current` | Return the family's current canonical version snapshot. For active families this is the most recent `approved` version; if none exists, return the most recent `draft` version. For deprecated families, return the most recent `deprecated` version so the deprecation record remains truthful. |
| `{version_id}` | Return the specific version snapshot identified by that `version_id` |
| `{version_seq}` | Return the version with that integer sequence number within the family |

The BFF always returns the resolved `version_id` and `version_seq` in the response so the frontend can construct canonical breadcrumb state. The frontend must not compute "latest version" logic locally.

---

## Read Routes

### 1. List Strategy Specs

**`GET /api/v1/knowledge/strategy-specs`**

**Query parameters:**
- `lifecycle_state` (optional): One of `draft | approved | deprecated | all`. Default `approved`.
- `source_kind` (optional): One of `manual | paper | repo | note | workflow`. Matches `provenance.source_kind`.
- `persona_id` (optional): Filter to specs currently bound to the specified persona.
- `include_deprecated` (optional): Boolean. When `true`, include `deprecated` specs regardless of `lifecycle_state`. Default `false`.
- `page_token` (optional): Opaque cursor for keyset pagination.
- `page_size` (optional): Default 20, max 100.

**Response shape:**
```json
{
  "strategy_specs": [
    {
      "strategy_id": "string (format: strat-{UUID})",
      "current_version_id": "string (format: specver-{UUID})",
      "current_version_seq": "number (integer)",
      "title": "string",
      "lifecycle_state": "draft | approved | deprecated",
      "source_kind": "manual | paper | repo | note | workflow",
      "hypothesis_excerpt": "string (plain text, max 200 chars, BFF-truncated)",
      "market_scope_summary": {
        "symbol_count": "number",
        "frequency": "string",
        "asset_class_labels": ["string"]
      },
      "version_count": "number",
      "last_modified_at": "string (ISO-8601)",
      "approved_at": "string (ISO-8601) | null",
      "deprecated_at": "string (ISO-8601) | null",
      "successor_strategy_id": "string (format: strat-{UUID}) | null",
      "route_href": "string (/knowledge/strategy-specs/{strategy_id})"
    }
  ],
  "filter_metadata": {
    "lifecycle_state_counts": {
      "draft": "number",
      "approved": "number",
      "deprecated": "number"
    },
    "source_kind_counts": {
      "manual": "number",
      "paper": "number",
      "repo": "number",
      "note": "number",
      "workflow": "number"
    },
    "total_count": "number"
  },
  "pagination": {
    "page_size": 20,
    "next_page_token": "string | null",
    "has_more": "boolean"
  },
  "meta": {
    "snapshot_at": "string (ISO-8601)",
    "surfaces": {
      "strategy_spec_list": "ok | degraded | unavailable"
    }
  }
}
```

Display rules:
- `hypothesis_excerpt` is BFF-truncated plain text. The frontend must not truncate it further or render it as markdown.
- `market_scope_summary` is BFF-resolved. Do not derive symbol count or asset class labels from `market_scope.symbols` locally.
- When `meta.surfaces.strategy_spec_list` is `degraded` or `unavailable`, show the canonical PKT-005 non-dismissable degradation banner. An empty `strategy_specs[]` array is not authoritative when the surface is stale.
- `filter_metadata.lifecycle_state_counts` are total corpus counts before the current filter is applied.

---

### 2. Get Versioned Strategy Spec Detail

**`GET /api/v1/knowledge/strategy-specs/{strategy_id}`**

**Query parameters:**
- `version` (optional): `current` (default), a `specver-{UUID}`, or an integer `version_seq`.

**Response shape:**
```json
{
  "strategy_id": "string (format: strat-{UUID})",
  "version_id": "string (format: specver-{UUID})",
  "version_seq": "number (integer)",
  "title": "string",
  "lifecycle_state": "draft | approved | deprecated",
  "approved_at": "string (ISO-8601) | null",
  "deprecated_at": "string (ISO-8601) | null",
  "successor_strategy_id": "string (format: strat-{UUID}) | null",
  "hypothesis": "string",
  "objective": "string",
  "market_scope": {
    "symbols": ["string"],
    "frequency": "string",
    "asset_classes": ["string"],
    "venues": ["string"]
  },
  "data_dependencies": [
    {
      "ref": "string",
      "kind": "dataset | feature_set | paper | repo | note"
    }
  ],
  "execution_profile": {
    "signal_schema_version": "string",
    "quantity_type": "SHARES | CASH_VALUE | PERCENT_PORTFOLIO",
    "rebalance_cadence": "string | null",
    "execution_mode_hint": "research | paper | live | null"
  },
  "evaluation_plan": {
    "metrics": ["string"],
    "candidate_gate": "string | null",
    "paper_gate": "string | null",
    "live_gate": "string | null"
  },
  "governance": {
    "approval_required": "boolean",
    "policy_id": "string | null",
    "risk_profile": "string | null"
  },
  "provenance": {
    "source_kind": "manual | paper | repo | note | workflow",
    "created_at": "string (ISO-8601)",
    "created_by": "string | null",
    "source_refs": ["string"]
  },
  "version_ancestry": {
    "parent_version_id": "string (format: specver-{UUID}) | null",
    "parent_version_seq": "number | null",
    "root_version_id": "string (format: specver-{UUID})",
    "version_count": "number",
    "parent_route_href": "string | null",
    "version_history_href": "string (/knowledge/strategy-specs/{strategy_id}/versions)"
  },
  "citation_bundle": {
    "evidence_refs": [
      {
        "ref_id": "string (format: evref-{UUID})",
        "source_document_title": "string",
        "link_type": "supporting_evidence | counter_evidence | citation | provenance | corroboration",
        "credibility_tier": "primary | secondary | tertiary | unverified",
        "association": "data_dependency | provenance | governance | evaluation",
        "resolved_link": {
          "availability": "available | unavailable | external",
          "route_href": "string | null",
          "display_label": "string",
          "open_in_new_tab": "boolean"
        }
      }
    ],
    "memory_anchors": [
      {
        "entry_id": "string (format: entry-{UUID})",
        "knowledge_type": "string",
        "content_headline": "string",
        "route_href": "string | null"
      }
    ],
    "insight_citations": [
      {
        "insight_id": "string (format: ins-{UUID})",
        "summary": "string",
        "confidence_label": "high | medium | low | insufficient_evidence",
        "route_href": "string | null"
      }
    ]
  },
  "allowedActions": {
    "canSubmitForApproval": "boolean",
    "canDeprecate": "boolean",
    "canCompare": "boolean"
  },
  "meta": {
    "snapshot_at": "string (ISO-8601)",
    "surfaces": {
      "strategy_spec_detail": "ok | degraded | unavailable",
      "citation_bundle": "ok | degraded | unavailable",
      "version_ancestry": "ok | degraded | unavailable"
    }
  }
}
```

Display rules:
- `version_ancestry` is BFF-resolved. The frontend must not reconstruct the version chain from multiple requests.
- `citation_bundle.evidence_refs[].resolved_link` is the only valid source for navigable citation links. Follow the same CS-05 resolution rule as KW-03.
- `citation_bundle.evidence_refs[].association` indicates which section of the spec this evidence supports. The frontend must not infer this from `link_type` alone.
- `allowedActions.canCompare` must be `true` before the frontend displays a "Compare versions" control. The frontend must not derive compare eligibility from `version_count` or `version_ancestry` independently.
- When `meta.surfaces.citation_bundle` is `degraded`, show an inline partial-data indicator within the citation panel. Do not hide the entire panel.
- When `meta.surfaces.strategy_spec_detail` is `degraded` or `unavailable`, show the canonical PKT-005 non-dismissable degradation banner.

---

### 3. List Version History

**`GET /api/v1/knowledge/strategy-specs/{strategy_id}/versions`**

**Response shape:**
```json
{
  "strategy_id": "string (format: strat-{UUID})",
  "versions": [
    {
      "version_id": "string (format: specver-{UUID})",
      "version_seq": "number (integer)",
      "lifecycle_state": "draft | approved | deprecated",
      "created_at": "string (ISO-8601)",
      "created_by": "string | null",
      "change_summary": "string | null (BFF-authored plain text summary of major changes)",
      "parent_version_id": "string (format: specver-{UUID}) | null",
      "route_href": "string (/knowledge/strategy-specs/{strategy_id}?version={version_id})"
    }
  ],
  "meta": {
    "snapshot_at": "string (ISO-8601)",
    "surfaces": {
      "version_history": "ok | degraded | unavailable"
    }
  }
}
```

Display rules:
- Versions are returned in descending `version_seq` order (most recent first).
- `change_summary` is BFF-authored and may be null for the first version or for versions created before summary capture was enabled.
- The frontend must not compute version ordering from `created_at` timestamps.
- When `meta.surfaces.version_history` is `degraded` or `unavailable`, show the canonical degradation banner.

---

### 4. Compare Two Versions

**`GET /api/v1/knowledge/strategy-specs/{strategy_id}/compare`**

**Query parameters:**
- `base_version` (required): A `specver-{UUID}` or integer `version_seq`. The "before" version in the diff.
- `target_version` (required): A `specver-{UUID}` or integer `version_seq`. The "after" version in the diff.

**Response shape:**
```json
{
  "strategy_id": "string (format: strat-{UUID})",
  "base_version": {
    "version_id": "string (format: specver-{UUID})",
    "version_seq": "number",
    "lifecycle_state": "draft | approved | deprecated",
    "created_at": "string (ISO-8601)"
  },
  "target_version": {
    "version_id": "string (format: specver-{UUID})",
    "version_seq": "number",
    "lifecycle_state": "draft | approved | deprecated",
    "created_at": "string (ISO-8601)"
  },
  "field_diffs": [
    {
      "field_path": "string (e.g. 'hypothesis', 'market_scope.frequency', 'evaluation_plan.metrics[0]')",
      "display_label": "string (BFF-authored human-readable field label)",
      "change_type": "added | removed | modified | unchanged",
      "base_value": "any | null",
      "target_value": "any | null",
      "significance": "high | medium | low",
      "significance_reason": "string | null (BFF-authored plain text)"
    }
  ],
  "summary": {
    "total_fields_changed": "number",
    "high_significance_changes": "number",
    "change_areas": ["string (e.g. 'market_scope', 'evaluation_plan', 'governance')"]
  },
  "meta": {
    "snapshot_at": "string (ISO-8601)",
    "surfaces": {
      "spec_compare": "ok | degraded | unavailable"
    }
  }
}
```

Display rules:
- `field_diffs` is the complete, authoritative list of differences. The frontend must not compute additional diffs by comparing `base_version` and `target_version` payloads directly.
- Unchanged fields are omitted from `field_diffs`; the compare route is not a full field inventory.
- `field_path` uses dot-notation for nested fields and bracket notation for array elements. The frontend must render the BFF-provided `display_label` rather than converting `field_path` to a label locally.
- `significance` is BFF-assigned. The frontend must not reclassify significance based on field name or value magnitude.
- `significance_reason` is BFF-authored and may be null for `low` significance changes.
- When `meta.surfaces.spec_compare` is `degraded` or `unavailable`, show the canonical PKT-005 non-dismissable degradation banner. Do not show partial diffs as authoritative.

---

## Citation Bundle Semantics

The citation bundle in the detail response assembles three evidence classes:

| Class | Source | Association |
|---|---|---|
| `evidence_refs` | KW-03 Evidence Refs | Linked to specific spec fields via `association` |
| `memory_anchors` | KW-01 Institutional Memory | Institutional context that informed the spec |
| `insight_citations` | KW-04 Insight Cards | Synthesized insights that back the spec hypothesis or evaluation plan |

Rules:
- All citation drilldown targets are BFF-resolved. The frontend must not reverse-resolve `ref_id`, `entry_id`, or `insight_id` values into routes locally.
- `evidence_refs[].association` links each evidence item to the spec section it supports (`data_dependency | provenance | governance | evaluation`). The frontend renders this association label without mapping it to field paths.
- The order of citation items within each class is BFF-determined. The frontend must not re-sort citation lists.

---

## Non-Goals — The Frontend Must Not

- Compare raw `StrategySpec` JSON between two versions to produce a diff.
- Derive `lifecycle_state` from `governance.approval_required`, `provenance.source_kind`, or any other schema field.
- Infer version ordering from `created_at` timestamps or construct version sequences locally.
- Construct any URL or link from raw `strategy_id`, `version_id`, `ref_id`, `entry_id`, or `insight_id` values outside of the BFF-provided `route_href` fields.
- Reconstruct the version ancestry chain by making multiple sequential detail requests.
- Resolve citation targets by calling KW-01, KW-03, or KW-04 routes directly from the citation panel context.
- Show or enable compare controls unless `allowedActions.canCompare` is `true`.
- Show or enable approval or deprecation actions unless `allowedActions.canSubmitForApproval` or `allowedActions.canDeprecate` is `true`.
- Truncate, reformat, or rephrase `hypothesis_excerpt`, `change_summary`, or `significance_reason` fields.

---

## Design Rules

- **All versioning is BFF-owned.** Version sequencing, ancestry resolution, and current-version selection are BFF-resolved. The frontend renders what the BFF returns.
- **Lifecycle state is BFF-asserted.** The `lifecycle_state` field is a registry projection. The frontend must not infer it from governance or provenance fields in the canonical schema.
- **Diff is backend-composed.** The compare route returns a `field_diffs[]` array. The frontend renders this array; it must not compute diffs from raw spec payloads.
- **Citation links follow the CS-05 precedent.** `citation_bundle.evidence_refs[].resolved_link` is BFF-resolved; no client-side URL construction is permitted.
- **Surface health signals must come from `meta.surfaces`.** An empty field-diffs array or an empty citation bundle is not proof of a healthy surface when `meta.surfaces.*` indicates degradation.
- **Authority gates are explicit.** The `allowedActions` object is the single source of truth for which write-adjacent controls are visible.

---

## Relationship to Other Modules

- **KW-01**: Version ancestry and citation bundle may include `entry_id` anchors from Institutional Memory. The KW-01 lifecycle semantics govern those anchors.
- **KW-03**: Citation bundle evidence refs consume `ref_id` values from Evidence Refs. The KW-03 read model is the canonical authority for evidence link resolution and credibility metadata.
- **KW-04**: Citation bundle insight citations reference `insight_id` values from Insight Cards. The KW-04 confidence and lifecycle semantics govern those references.
- **PKT-005**: All surface degradation must flow through the canonical SSE/degradation banner mechanism. `meta.surfaces.strategy_spec_list`, `meta.surfaces.strategy_spec_detail`, `meta.surfaces.citation_bundle`, `meta.surfaces.version_history`, and `meta.surfaces.spec_compare` are the authoritative triggers.
