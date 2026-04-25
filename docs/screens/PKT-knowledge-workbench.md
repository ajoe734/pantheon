# PKT Knowledge Workbench Overview Screen

## Intent

Render a truthful landing page for the Knowledge Workbench while the module-level browse and lifecycle surfaces remain blocked.

## Required Sections

- Header with workbench label, overview status, and packet-family reference
- Module sequence for `KW-01` to `KW-05`
- Existing support refs
- Next steps

## Rendering Rules

- Render `modules[]` in backend-owned `wave_order`.
- Render `missing_contracts[]` verbatim.
- Treat this as a planning overview, not a fake registry, evidence browser, or strategy-spec viewer.
- If required top-level fields are missing, emit a `bff-gap` handoff instead of synthesizing browse state locally.
