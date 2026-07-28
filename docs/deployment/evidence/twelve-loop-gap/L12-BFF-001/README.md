# L12-BFF-001 closeout evidence

Status: merged implementation accepted for closeout; Antigravity independently
approved the prior closeout evidence cut, and this review-receipt recut requires
fresh exact-head review before merge.

This owner cut v2.1.0 does not restart BFF implementation. It recuts the
task-scoped record against immutable [PR #4274](https://github.com/ajoe734/pantheon/pull/4274)
head `414546226003bce04a60f2d5941d999e96afd075`, merged to `dev` as
`7ba7b5e19fbd16aa36bf569c6a46d244eb9da3e1` at
`2026-07-27T22:14:45Z`.

The canonical task-state scan boundary is journal sequence 3988, committed at
`2026-07-28T20:31:20Z`: owner `Codex`, reviewer `Antigravity`, status
`review_approved`, and review file
`docs/deployment/evidence/twelve-loop-gap/L12-BFF-001/evidence.json`.

Antigravity independently approved PR #4316 exact head
`3c0aae0d95a020e0fc225d9bcb27f9e1c2911549` at
`2026-07-28T19:49:05Z`. The governed GitHub review bridge recorded canonical
status `Pantheon canonical review gate` as success with status id
`51244708326`. Because v2.1.0 commits that previously external decision into
the task review manifest, it changes the PR head and does not reuse that
approval for the new head.

## Accepted implementation

The merged BFF health controller:

- emits the strict non-trading `pantheon.infrastructure-health/1` contract
  without fabricated RuntimeBinding identity;
- enumerates the complete configured downstream registry and triggers on
  interval failures and error-rate spikes;
- persists probe windows, target state, error windows, delivery intent,
  incident mappings, claims, retries, dead letters, and replay audit in a
  shared SQLite WAL store;
- uses stable source event IDs across telemetry, incident-open, and
  incident-resolve delivery with dependency ordering; and
- exposes an operator-only, MFA- and approval-bound exact DLQ replay path.

The incidents service owns
`POST /api/incidents/consume-infrastructure-health`. It creates a real
non-trading `IncidentCase`, treats exact replay as idempotent, rejects
conflicting replay, ignores caller-supplied fake RuntimeBinding fields, and
resolves recovery through the canonical incident status route.

## Accepted proof

The merged repair-acceptance record in PR #4305 independently revalidated the
PR #4274 delivery with:

- 168 focused BFF, telemetry, and incidents tests;
- nine L12-specific drills covering strict admission, restart and two-replica
  dedupe, retry/DLQ/replay, registry coverage, error-rate triggering, local
  target stop/recovery, durable incident mapping, and recovery after retention;
- five incidents application-route tests covering non-trading create, exact
  replay, conflict rejection, fake RuntimeBinding isolation, and canonical
  resolution; and
- the ten-rule product-evidence validator and companion checksums.

The recovery-after-retention drill ages and prunes delivered history, retains
the active incident-open dependency, restarts the monitor on the same durable
store, and completes recovery telemetry plus incident resolution with zero
delivery backlog.

PR #4274's final head passed Commit trailers, Runtime mirror guard, Python
packaging provision, and Smoke acceptance. The Pantheon canonical review gate
and the Human/Ops root merge-freeze context also passed before merge.

## Closeout boundary

This closeout changes only the task evidence under this directory. It does not
claim that the hosted dev BFF currently serves this implementation, that a
protected BFF service JWT is provisioned, that `BFF_DATA_DIR` is retained by
the hosted manifest, or that the program-level hosted restart drill has run.
Those claims remain with `L12-MANIFEST-001` and `L12-VERIFY-OBS-001`.

The companion `evidence.json` binds this README and the unchanged implementation
sources through an anchor receipt, records the immutable PR #4274 head and
merge, preserves Antigravity's independent decision on v2.0.0 head
`3c0aae0d95a020e0fc225d9bcb27f9e1c2911549`, and leaves fresh exact-head review
pending for the v2.1.0 evidence-only follow-up.
