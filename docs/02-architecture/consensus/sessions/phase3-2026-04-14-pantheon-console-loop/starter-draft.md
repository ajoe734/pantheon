# Starter Draft

Current rule: only `Codex` edits this file directly.

## Shared Draft

- Objective: turn Pantheon's existing `.coordination` and APP-002 sidecars into a formal Pantheon <-> Lovable <-> front feedback <-> Pantheon closed loop, and expand Pantheon Console into an 8-workbench execution backlog.
- Scope boundary: planning-only. This session defines the protocol, GitHub automation target, packetization backlog, and execution slices. It does not directly implement `.coordination` code or GitHub workflows.
- Proposed wave order:
  1. Closed-loop infra: extend `.coordination`, define feedback and delivery payloads, and bootstrap GitHub dispatch plus front-repo prerequisites.
  2. Packetize the APP-002-backed screens that already have Pantheon-side BFF support: Governance/Promotion Review, Incident Response/Control, Post-Incident/Evolution, Persona Management/Catalog, and global degradation plus SSE.
  3. Expand those slices into the full 8-workbench planning backlog, including the workbenches that currently have no packet-ready Pantheon spec.
  4. Only after human gate approval, materialize `LOOP-*`, `PKT-*`, and `WB-*` tasks into `ai-status.json`.
- Proposed task slices:
  - `LOOP-001` to `LOOP-003`: protocol, GitHub automation, front repo bootstrap.
  - `PKT-001` to `PKT-005`: turn existing APP-002 sidecars into canonical screen packet families.
  - `WB-001` to `WB-008`: define the 8 workbench backlogs with module inventory, existing support, missing specs, Lovable readiness, backend dependencies, and execution wave.
- Current evidence-backed decisions:
  - `.coordination` already exists as the machine-readable handoff bus and should remain canonical.
  - `contract-ready -> lovable-ui-task -> bff-gap/ui-done` is already implemented on the Pantheon side.
  - only `F-042` currently exists as a true Lovable-ready packet; the rest of APP-002 is mostly sidecar handoff truth, not canonical screen packets.
  - `front-ai-trading-system` is positioned in Pantheon blueprint as `Pantheon Console`, with 8 named workbenches.
- Open disagreements:
  - whether `contracts_version` and `sdk_version` stay mandatory in `backend-delivery` when the front repo is still using direct BFF client wiring rather than a published SDK
  - whether front-repo GitHub dispatch should be the primary trigger immediately, or only after the legacy GitHub issue bus and labels are stabilized
  - how much of Operator Home, Research, Knowledge, Trainer, and Consultation should be packetized before backend gaps are closed
