# EVOCHAIN-003 review remediation ledger

Status: Claude re-review approved; owner closeout evidence recorded

Original finding author: Codex

Current owner: Codex2

Current reviewer: Claude

This task-scoped record materializes the findings recorded after PR #3682, the
owner response, and Claude's final approval after PR #3699. The lifecycle
`done` transition remains a separate owner action after this record is merged.

## Required changes and owner response

1. **Postgres writes were not database-level CAS.** The old owner path compared
   process-local state and could overwrite unrelated or concurrently changed
   aggregates.

   Response: each mutation now names its exact aggregate type, record ID, and
   expected JSONB snapshot. `PostgresIncidentStore` never infers a target from
   whole-cache differences. It locks/checks parent Incident snapshots with
   `SELECT ... FOR SHARE` and CAS-writes the Postmortem in the same transaction.
   Read refreshes share the write `RLock`, and failed persistence restores the
   guarded cache snapshot. Regressions cover stale target writes, cross-row ABA,
   read-during-write refresh, and a parent change before publication.

2. **Delivery admission and completion were not claimed or monotonic.** Two
   workers could deliver the same due record and blind-write conflicting
   completions. Concurrent exact first-hop calls could yield `201` plus a false
   permanent `422`.

   Response: workers CAS-lease due records and conditionally complete the exact
   claim. Prepare/activate cannot clear a live claim, and stale completion cannot
   overwrite a later published result. The first-hop inbox reserves event ID and
   idempotency key before domain mutation, exposes in-progress work as retryable
   `503`, and allows `200` replay only from a durable `applied` receipt. Expired
   reservations can be reclaimed. Receipt completion CASes the exact claimed
   reservation snapshot/token, so a stale claimant cannot finalize a successor
   lease. CAS disappearance/contention raises a typed transient error and
   returns `503`; checksum/result divergence remains semantic `409`.

3. **A failed incident CAS could strand or race cleanup of its prepared
   delivery intent.** The event identity was per incident while attempted
   `terminal_status` changed between resolve and close.

   Response: the deterministic event now represents the terminal boundary and
   keeps one stable, backward-compatible `resolved` classification. A losing
   request activates an already visible terminal winner or preserves the inert
   intent; it never check-then-deletes a record that a concurrent winner may
   already be using. A later close reuses and activates the same intent.

4. **Postmortem consumer races could lose data or become permanent DLQ.** Mere
   deterministic-record existence was treated as proof of event application;
   manual/legacy result IDs conflicted; draft CAS loss became `422`; and a stale
   generated merge could replace a concurrent operator edit.

   Response: only the inbox receipt proves application. The consumer reuses the
   existing postmortem's actual ID, enforces one postmortem per incident across
   cooperating stores, and uses collision-resistant generated IDs when input
   sanitization is required. Cache-visible/DB-visible create races and draft CAS
   loss are retryable. Draft updates pass the exact merge basis as
   `expected_snapshot`, preserving concurrent edits. Concrete HTTP regressions
   exercise preexisting random/deterministic IDs, sanitized-ID collision,
   concurrent create, and real stale-draft interleaving.

5. **Published postmortems could regress or be republished.** A published
   aggregate could transition back to draft/review/approved or replace its
   original timestamp/event identity.

   Response: `published` is terminal. Exact replay preserves the original
   timestamp and event marker; regression or republication with a different
   identity fails closed.

6. **Control compose and evidence were stale.** Incidents and postmortems lacked
   complete runtime-manager configuration; the task artifact omitted PR #3682
   and claimed an earlier closeout.

   Response: both services now receive `PANTHEON_RUNTIME_MANAGER_URL` and
   `PANTHEON_RUNTIME_MANAGER_TOKEN`. The task artifact records PR #3682, anchor
   `4e5562d42`, exact current validation, superseded history, PR #3699, and the
   final Claude review. `services/evolution/postmortem_bridge.py` remains
   unchanged.

7. **Recovery audit found mixed-rollout and downstream CAS gaps.** A #3682
   direct-close request could leave `terminal_status=closed` prepared under the
   same deterministic identity that the normalized producer now emits as
   `resolved`. The new Postgres CAS could also escape a concurrent drift-report
   evidence merge as an unclassified `500`.

   Response: the producer adopts only the exact inert, unclaimed,
   never-attempted legacy direct-close shape while every other divergent stable
   field remains rejected. Drift evidence merges retry three times against
   refreshed durable state and then return retryable `503`. Regressions cover
   legacy adoption, successful CAS retry, retry-budget exhaustion, and
   divergent first-hop HTTP replay.

## Current verification

```sh
python3 -m py_compile \
  services/foundation/postgres_json_store.py \
  services/foundation/reliable_delivery.py \
  services/incident/incident.py services/incident/pg_store.py \
  services/incidents/main.py \
  services/postmortems/consumer.py services/postmortems/main.py

INCIDENTS_DATA_DIR=/tmp/evochain003-root-focused-final2-$$ \
POSTMORTEMS_DATA_DIR=/tmp/evochain003-root-focused-final2-$$ \
PANTHEON_RUNTIME_MANAGER_URL=http://runtime-manager.test \
PANTHEON_RUNTIME_MANAGER_TOKEN=test-token \
TEST_DATABASE_URL="${TEST_DATABASE_URL:?set isolated Postgres test DSN}" \
/tmp/evochain-003-venv/bin/python -m pytest -q \
  services/foundation/test_reliable_delivery.py \
  services/foundation/tests/test_control_plane_postgres_owner_stores.py \
  services/incident/test_incident_store_concurrency.py \
  services/incident/test_pg_store_integration.py \
  services/incidents/test_evochain_003_delivery.py \
  services/incidents/test_evochain_003_compose.py \
  services/incidents/test_main_routes.py \
  services/postmortems/test_evochain_003_delivery.py \
  services/postmortems/test_main_routes.py
# 134 passed, 4 warnings in 130.61s
```

The broad service/governance suite, independent-process chain, compose
rendering, bridge hash, and exact tested task checkpoints are recorded in the
task artifact. PR #3699 merged at
`7d031fb1fb1327b6b1c00c0ec71d0234fd304613`, and the Claude gate is complete.
The warnings are the existing FastAPI `on_event` deprecations.

Real Postgres proof:

```sh
TEST_DATABASE_URL="${TEST_DATABASE_URL:?set isolated Postgres test DSN}" \
/tmp/evochain-003-venv/bin/python -m pytest -q \
  services/incident/test_pg_store_integration.py -vv
# 5 passed in 3.82s
```

## Final reviewer decision and owner closeout

Claude independently confirmed all seven remediation responses against the
merged change. The precise PR #3699 patch is
`d72c705baf8357c3897f9bb11474d922666a2e14..1105d45236a47724b7e1d36f64bf19e54d286bf4`;
the merge commit is `7d031fb1fb1327b6b1c00c0ec71d0234fd304613`.
The reviewer reran the seven-file compile check and 116 non-Postgres tests,
accepted the owner's separately recorded five-test real-Postgres proof, and
approved the task at 2026-07-15T12:06:43Z.

Owner closeout reran the nine-path focused suite without `TEST_DATABASE_URL`
(`129 passed, 5 expected Postgres skips, 4 existing FastAPI warnings`), the
independent HTTP chain (`1 passed`), both compose renders, the seven-file
compile check, and the bridge zero-diff/checksum check. The bridge remains
byte-identical to `origin/dev` with SHA-1
`b68b4924a6e59ca472fe8103804b2b82c3985d7d`.

The reviewer recorded two non-blocking notes: the postmortem-publish CAS-loss
path has no repair-intent call analogous to the incident path, and
`ReliableOutboxStore.discard_prepared` has no production caller. Neither note
invalidates this task's accepted scope.

## Residual P2 hardening

- Replace application/table-lock one-postmortem uniqueness with a schema-level
  unique incident key and define migration behavior for legacy duplicates.
- Bound worker claim batches for very large backlogs.
- Add safe compaction for obsolete prepared postmortem publish intents.
- Define migration-safe handling for manual IDs that collide with the legacy
  deterministic `pm-<incident-id>` namespace.

These remain follow-up hardening and do not invalidate the completed reviewer
approval. The owner still must merge this closeout record and run the canonical
`done` transition.
