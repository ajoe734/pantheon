# TJ-E2E-001 - Producer And Correlation Inventory

Owner: Claude
Reviewer: Antigravity
Wave: 0
Repository: `ajoe734/pantheon`
Dependencies: none

## Goal

Create an evidence-backed inventory of every producer and source of truth from
Persona research through reconciliation, including identifiers, schemas,
retention, ordering, environment scope and current correlation loss.

## Required work and acceptance

- Trace at least one paper and one broker-sandbox lifecycle without guessing.
- Produce a stage-by-stage source/owner/schema/ID/missing-field matrix.
- Identify orphan, duplicate, late-event and conflicting-terminal risks.
- Record exact correlation propagation changes required for each producer.
- Merge the inventory and evidence to Pantheon `dev`; no runtime behavior change.

Artifacts: source archive, service contracts/tests, captured redacted event samples.
