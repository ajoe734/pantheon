# PFG-MGMT-BFF-PERF-20260820 evidence

This change removes three repeated Management BFF read costs while preserving
the existing product contracts and source-of-truth boundaries.

- Cockpit now builds alerts and runtime-health once and shares those completed
  projections with its Operator Home section.
- A single-persona operations read now composes from the requested canonical
  persona and direct league entry instead of materialising the 500-row Persona
  Fleet list just to select one row.  The list surface itself retains its
  bounded page contract and shares a single telemetry-summary projection across
  its calculations when that canonical projection has rows.  The historical
  per-runtime lookup is retained only for older no-list fixtures.
- The data-sources surface reads Source Ingest's canonical registry through a
  two-slot, 750ms-default bounded worker read.  Timeout or saturation returns a
  typed unavailable state with a reason; it does not retain or invent a healthy
  cached connector list.

The task is deliberately not a generic caching or performance-platform change.
Source Ingest remains the registry authority; ReadSurfaceStore remains the BFF
read adapter; and no product schemas, frontend contracts, background refresh
loop, or write path is introduced.

Focused regression tests establish the functional budgets at the composition
boundary: a Cockpit request performs one alerts and one health build, a
single-persona operations request forbids full-fleet materialisation,
multi-runtime attribution forbids per-runtime telemetry reads when the bulk
projection is present, and a 300ms registry read degrades within the 50ms test
budget (with an endpoint envelope budget of 250ms).  See `evidence.json` for
the acceptance mapping and reviewer handoff.
