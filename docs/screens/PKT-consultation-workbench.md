# PKT Consultation Workbench Overview Screen

## Intent

Render a truthful landing page for the Consultation Workbench while the module-level surfaces remain blocked.

## Required Sections

- Header with workbench label, overview status, and packet-family reference
- Module sequence for `CW-01` to `CW-04`
- Existing support refs
- Next steps

## Rendering Rules

- Render `modules[]` in backend-owned `wave_order`.
- Render `missing_contracts[]` verbatim.
- Treat this as a planning overview, not a fake request form or committee room.
- If required top-level fields are missing, emit a `bff-gap` handoff instead of synthesizing workbench state locally.
