---
name: Pantheon Coordination Bus
about: Structured cross-repo delivery handoff for Pantheon, front-end, runtime, or engine work
title: "[CoordBus] F-xxx <status>"
labels: ["coordination-bus"]
assignees: []
---

<!-- pantheon-bus -->

# Pantheon Coordination Bus

Use this template for feature delivery handoffs that cross repo boundaries.

Recommended fields:

- Feature ID
- Current status label (`needs-bff`, `contract-ready`, `needs-ui`, `needs-runtime`, `needs-engine`)
- Source repo / branch
- Latest `.coordination` payload path
- Counterpart issue URLs
- Allowed comment commands:
  - `/dispatch pantheon-bff F-xxx`
  - `/dispatch front-ui F-xxx`
  - `/needs-runtime F-xxx`
  - `/contract-ready F-xxx`
  - `/approve-engine F-xxx`
