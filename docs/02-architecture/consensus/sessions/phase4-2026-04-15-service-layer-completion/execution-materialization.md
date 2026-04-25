# Execution Materialization

Status: not yet accepted for materialization
Session: `phase4-2026-04-15-service-layer-completion`

This file is the bridge contract from discussion planning into execution.

Current seed slices:

1. `SVC-BASELINE`
2. `SVC-RUNTIME-CONTROL`
3. `SVC-GOVERNANCE-API`
4. `SVC-EVIDENCE`
5. `SVC-SURFACES`
6. `SVC-COMPOSE`

Materialization rule:

- do not convert these rows into `ai-status.json` until the session reaches accepted consensus and human gate approval
- the accepted materialization should keep `source_plane = planning`
- every execution task should keep `source_ref` back to `planning-session.json`, `consensus-packet.md`, and this file

Pending reviewer focus:

- verify whether the six slices are the correct first-wave boundary for closing the operational gaps across roadmap phases 2 through 5
- decide whether phase 5 workbench expansion and phase 6 real OSS integrations should stay out of the first materialization wave
