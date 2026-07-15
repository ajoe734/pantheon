# Immutable audit archive incident — 2026-07-15

Status: **incident only; no repair action is authorized**.

Independent review of draft PR #3677 found that its claimed corrupt object is
not present in the current authoritative status root. The currently observable
object at the same path instead has SHA-256
`229007353bfe5f521c8a114a6b3dd9582442398697bbf30022fb49839bb5b6dc` and
passes strict gzip/JSONL parsing. The prior `47d…` / `b16…` pins are therefore
withdrawn before merge, not reinterpreted as a repair target.

The machine contract
[archive-audit-archive-incident.v1.json](fixtures/archive-audit-archive-incident.v1.json)
binds the actual observation, two-person/supervisor boundary, scratch-only
state root, and prohibition on normal `ai_status`/outbox use. The task rendering
is [LOOP-PROD-AUDIT-ARCHIVE-INCIDENT-001.md](LOOP-PROD-AUDIT-ARCHIVE-INCIDENT-001.md).

This incident packet changes no archive, active audit log, status/outbox,
runtime/supervisor code, scheduler, or deployment state.
