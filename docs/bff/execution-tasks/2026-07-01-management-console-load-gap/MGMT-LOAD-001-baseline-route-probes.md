# MGMT-LOAD-001 - Management Load Baseline And Route-Ready Probes

Owner: Gemini2
Reviewer: Codex
Parent: `MGMT-GAP-010`
Depends on: `MGMT-GAP-001`, `MGMT-GAP-002`

## Problem

The load gap was diagnosed with manual browser and curl probes. That is not
production-level evidence. The fleet needs checked-in route probes that separate
route content readiness from SSE `networkidle`, and BFF fanout probes that show
whether `/health` and Evidence remain responsive under concurrent shell reads.

## Scope

- Add a hosted browser probe for `/management/evidence` that records document
  load, shell visible, route heading visible, primary Evidence API completion,
  first row or empty state visible, request waterfall, and startup request count.
- Add a BFF fanout probe that concurrently requests `/health`,
  `/bff/management/evidence`, `/bff/alerts`, `/bff/approvals`, and `/bff/jobs`.
- Make the probe treat `/bff/events/stream` as a realtime stream, not a reason
  to wait for `networkidle`.
- Archive the first baseline JSON and Markdown under the load gap archive.

## Acceptance

- Probe artifacts include route timing JSON, request waterfall JSON, and a short
  Markdown summary.
- The probe fails if it only proves readiness through `networkidle`.
- Baseline evidence names FE commit, BFF host, token shape without secret value,
  and probe timestamp.
- Local validation and hosted dev output are linked from the task closeout.
