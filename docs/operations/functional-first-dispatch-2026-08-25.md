# Functional-first dispatch and hosted acceptance separation

Date: 2026-08-25

## Problem

The product-closure DAG used one terminal dependency for three different
claims: code functionality, hosted FE/BFF identity proof, and
`operator-live/write-proof`. A missing hosted credential therefore prevented
independent implementation work from dispatching. The shared dev VM lease also
made routine worker verification serial.

## Design

The existing task IDs, artifact ownership, and lifecycle remain unchanged.
Tasks may now expose two explicit completion tracks:

| Track | Meaning | Shared dev lease |
| --- | --- | --- |
| `functional` | Worktree-local paper/replay tests, component tests, and reviewed code | No |
| `hosted` | One exact FE/BFF pair on the Pantheon-owned dev host | Yes, final controller only |

`depends_on` remains terminal by default. A consumer can opt one dependency
into a track with `dependency_tracks`. A track is satisfied only by an
audited `completion_tracks.<track>.status=done` record; a task's ordinary
`done` status is not inferred as functional or hosted proof.

When a producer leaves the active board, its terminal fact retains the
bounded `functional`/`hosted` track statuses (status and timestamp only). This
keeps track-dependent consumers deterministic without reopening or reading an
archive snapshot; detailed evidence and narrative remain in the archive.

`operator-live/write-proof` is an external hosted evidence item. If its
credential is unavailable, the track records `external_wait`; the supervisor
continues functional dispatch and never enables capital writes to bypass it.

The hosted verifier now has two explicit profiles without duplicating the
deployment checks:

- `--profile hosted-functional` proves the exact deployed FE/BFF pair, live
  readiness, authenticated paper-only journeys, and durable restart readback.
  It does not require an independent reviewer credential or operator-live
  write-proof.
- `--profile privileged` retains the full independent operator/reviewer,
  negative-control, and rollback qualification.

Both profiles require a real Firebase/BFF browser session and keep
`execution_authority=none`; hosted-functional is not simulated or an auth
stub. The privileged profile remains a separate optional proof claim.

## Current migration

The functional-closure catalog opts the Agora journey dependency into
`functional` for BE/FE consolidation. The final hosted acceptance opts that
same dependency into `hosted`. Existing Management and L12 terminal facts keep
their original terminal semantics; no duplicate or superseding task is created.

The migration is applied to the live TaskStore only through the audited
`dependency-track` command after this tooling change is promoted. Workers then
record verified milestones with the `milestone` command and evidence paths.

## Environment rule

Workers must run routine validation in their isolated worktree and local
paper/replay service profile. Only the final hosted controller may acquire the
shared dev lease, switch the exact FE/BFF pair, and run hosted acceptance. A
failed hosted run blocks promotion evidence, not independent code review or
functional implementation.

## Required regression coverage

- An explicit `functional` milestone releases a consumer while the dependency
  task remains `blocked` on hosted evidence.
- A terminal dependency does not satisfy a `functional` dependency without an
  explicit milestone.
- Missing or invalid dependency-track declarations fail closed.
- A `done` milestone requires evidence references.
- Existing string-only dependencies and old bridge packets remain compatible.
