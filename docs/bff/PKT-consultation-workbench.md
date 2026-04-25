# PKT Consultation Workbench Overview BFF Contract

## Purpose

Provide one truthful overview route for the Consultation Workbench so the UI can
show module order, existing support surfaces, and the remaining frontend
follow-up without downgrading live Consultation routes or pretending that a
published module-local handoff packet is still a backend implementation gap.

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
  - `live_routes[]`
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

- This packet is an overview surface only. `CW-01` through `CW-04` are live
  today, and both `CW-02` and `CW-04` now have published module-local frontend
  activation packets.
- The UI must not invent consult request forms, committee verdicts, or memo state from packet-family prose.
- Existing support refs and endpoint refs must render verbatim from backend-owned payload fields.
- Module summaries and `live_routes[]` remain backend-owned. The UI must not
  compress or reinterpret them into a false “complete” state.
- This packet is read-only and adds no write authority.

## Example Payload

- `docs/examples/PKT-consultation-workbench.json`
