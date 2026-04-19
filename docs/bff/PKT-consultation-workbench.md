# PKT Consultation Workbench Overview BFF Contract

## Purpose

Provide one truthful overview route for the Consultation Workbench so the UI can show module order, existing support surfaces, and the remaining BFF gaps without pretending that consult requests, committee boards, or red-team memo flows are already live.

## Primary Read Route

- `GET /api/v1/workbench/consultation`

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

- This packet is an overview surface only. It does not claim that `CW-01` to `CW-04` are implemented.
- The UI must not invent consult request forms, committee verdicts, or memo state from packet-family prose.
- Existing support refs and endpoint refs must render verbatim from backend-owned payload fields.
- Missing-contract lists remain backend-owned. The UI must not compress or reorder them into a false “ready” state.
- This packet is read-only and adds no write authority.

## Example Payload

- `docs/examples/PKT-consultation-workbench.json`
