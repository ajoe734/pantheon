# Execution Materialization

## Summary

This document locks the order for turning the phase3 planning packet into execution tasks.

Rules:

1. closed-loop infra first
2. APP-002-backed packetization second
3. 8-workbench backlog definition third
4. only after human gate approval may `scripts/planning-state.sh materialize` write these tasks into `ai-status.json`

## Sequencing

### Step 1: Closed-Loop Infra

| Order | Task | Goal | Depends on |
|---|---|---|---|
| 1 | `LOOP-001` | extend `.coordination` and define the new loop payloads | none |
| 2 | `LOOP-002` | define Pantheon and front-repo GitHub dispatch workflows | `LOOP-001` |
| 3 | `LOOP-003` | bootstrap front-repo prerequisites, labels, checkout, and mirror validation | `LOOP-001` |

Exit criteria:

- the closed-loop protocol is stable enough to packetize screens against it
- replay and failure handling are explicit
- front-repo prerequisites are documented as hard dependencies

### Step 2: APP-002 Packetization

| Order | Task | Goal | Depends on |
|---|---|---|---|
| 4 | `PKT-001` | governance and deployment review packet family | `LOOP-001`, `LOOP-003` |
| 5 | `PKT-002` | incident response and incident control packet family | `LOOP-001`, `LOOP-003` |
| 6 | `PKT-003` | post-incident and evolution packet family | `LOOP-001`, `LOOP-003` |
| 7 | `PKT-004` | persona management and catalog packet family | `LOOP-001`, `LOOP-003` |
| 8 | `PKT-005` | degradation banner and SSE packet family | `LOOP-001`, `LOOP-003` |

Exit criteria:

- every existing APP-002-backed screen family has a canonical packet plan
- existing sidecars are upgraded into explicit screen inventories and packet requirements
- `F-042` is correctly demoted from "the admin front end" to one screen inside a larger Governance Workbench

### Step 3: Workbench Backlog Definition

| Order | Task | Goal | Depends on |
|---|---|---|---|
| 9 | `WB-001` | Operator Console backlog | `PKT-001`, `PKT-002`, `PKT-003`, `PKT-005` |
| 10 | `WB-002` | Persona Workbench backlog | `PKT-004` |
| 11 | `WB-003` | Research Workbench backlog | `LOOP-001` |
| 12 | `WB-004` | Knowledge Workbench backlog | `LOOP-001` |
| 13 | `WB-005` | Trainer Workbench backlog | `LOOP-001` |
| 14 | `WB-006` | Consultation Workbench backlog | `LOOP-001` |
| 15 | `WB-007` | Governance Workbench backlog | `PKT-001` |
| 16 | `WB-008` | Evolution Workbench backlog | `PKT-003`, `PKT-005` |

Exit criteria:

- all 8 workbenches have module inventories
- each workbench records existing Pantheon support, missing canonical screen spec, Lovable readiness, backend dependency, and recommended wave
- the backlog is detailed enough that later sessions can materialize workbench-specific execution waves without reopening blueprint intent

## Materialization Gate

Before materializing:

- all required lane readouts must be submitted or waived
- at least one cross-review round must exist
- open disagreements must be resolved or escalated as tracking or human-required items
- the consensus packet must be in `ready_for_human` or `accepted`
- the human gate must be explicitly approved

After materializing:

- every task must carry `source_plane = planning`
- every task must carry a `source_ref` pointing back to this session
- `execution_materialization` must be populated in `materialization_ref`

## Notes

- `LOOP-*` tasks are prerequisites for the closed loop itself and should not be skipped just because `.coordination` exists today.
- `PKT-*` tasks are the bridge between today’s sidecar truth and tomorrow’s Lovable-ready screen packets.
- `WB-*` tasks define backlog structure, not full UI implementation.
