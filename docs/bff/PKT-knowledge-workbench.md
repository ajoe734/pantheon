# PKT Knowledge Workbench Overview BFF Contract

## Purpose

Provide one truthful overview route for the Knowledge Workbench so the UI can show module order, existing canonical support artifacts, and the remaining browse or lifecycle gaps without pretending that memory browse, evidence refs, or strategy-spec versioning are already implemented.

## Primary Read Route

- `GET /api/v1/workbench/knowledge`

Required response fields:

- `workbench_id`
- `label`
- `route_href`
- `overall_status`
- `headline`
- `summary`
- `packet_family.family_id`
- `packet_family.path`
- `packet_family.lovable_readiness`
- `packet_family.note`
- `module_counts.total`
- `module_counts.ready`
- `module_counts.not_ready`
- `modules[]`
  - `module_id`
  - `label`
  - `status`
  - `wave_order`
  - `summary`
  - `missing_contracts[]`
  - `next_gate`
  - `upstream_dependencies[]`
- `support_refs[]`
  - `ref_id`
  - `label`
  - `ref_type`
  - `value`
  - `note`
- `next_steps[]`
- `meta.snapshot_at`
- `meta.surfaces.overview`
- `meta.surfaces.packet_family`

## Design Rules

- This packet is an overview surface. `KW-01 Institutional Memory` is now a ready first-class browse module; the UI may navigate to `/knowledge/memory` for list and detail views. `KW-02` to `KW-05` are not yet implemented.
- The UI must not synthesize registry, evidence, or note browse screens from raw schemas or storage hints.
- Support refs and missing-contract lists remain backend-owned and must render verbatim.
- This packet is read-only and introduces no write authority.

## Example Payload

- `docs/examples/PKT-knowledge-workbench.json`
