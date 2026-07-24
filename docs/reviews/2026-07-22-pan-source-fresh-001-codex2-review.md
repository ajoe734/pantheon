# PAN-SOURCE-FRESH-001 — Codex2 independent review

Date: 2026-07-22 UTC

Reviewed branch: `task/PAN-SOURCE-FRESH-001`

Reviewed HEAD: `91c4565c2881cb07ce930fb45c72d6cd930ccb9b`

Owner: Codex

Reviewer: Codex2

Verdict: **CHANGES REQUIRED — NOT APPROVED**

## Outcome

The branch has a strong fail-closed base: external mode defaults to `deny`,
allowed targets are exact HTTPS host names, every DNS answer must be global,
redirect targets are revalidated, the socket is pinned to a revalidated IP,
proxy use is disabled, and the reviewed production connectors use the shared
transport. The bounded profile defaults, typed egress denial, stale projection,
and focused regression suite also work in their covered cases.

The review cannot approve the branch yet because two adversarial checks
reproduced contract-breaking behavior, and three additional delivery gaps leave
the source-time and hosted-gate claims stronger than the implementation.

## Required changes

### 1. Cross-host redirects forward secret-bearing request headers

`services/external_egress.py` validates the redirect target and then delegates
to `urllib.request.HTTPRedirectHandler.redirect_request`. Python copies every
request header except `Content-Length` and `Content-Type` into the redirected
request. A request from `api.example.com` to separately allowlisted
`cdn.example.com` therefore retains `Authorization`.

This matters for real connector call sites: the GitHub adapter supplies
`Authorization`, and provider/broker adapters may supply API-key or other
credential headers. The runbook explicitly permits separately reviewed
redirect hosts, so exact-host admission alone does not prevent credential
disclosure to the second host.

Required resolution:

- preserve credentials only on a same-origin redirect;
- strip `Authorization`, `Proxy-Authorization`, `Cookie`, and API-key/token
  headers on cross-origin redirects, or fail closed when such a redirect would
  carry them;
- add tests for same-host preservation, cross-host stripping/refusal, and a
  redirect escape that proves no secret-bearing request reaches transport.

Reproduction result:

```text
{'redirect_host': 'cdn.example.com', 'authorization_forwarded': True}
```

### 2. A terminal run can exist without its required durable receipt

`services/source_ingestion/main.py::_run_job` receives a terminal scheduler
result, then performs audit, market storage, evidence, and health/usage writes
before calling `_persist_ingest_receipt`. Any failure in those earlier writes
leaves the already-persisted terminal run without a receipt.

The review injected a health-store failure after a successful scheduler result.
The durable schedule store then reported a `completed` run and zero receipts:

```text
{'terminal_runs': [('ingest-bf71c82bbc38', 'completed')], 'receipt_count': 0}
```

Required resolution:

- make the terminal receipt unavoidable across fallible post-fetch persistence;
- ensure a post-processing failure is represented by a secret-free typed
  failure instead of leaving a `completed` run with no receipt;
- add a restart/reload regression test that injects a storage, evidence, or
  health write failure and proves the run/receipt relationship remains truthful.

### 3. Ingest time is still accepted as source time

`_source_timestamp_for_result` falls back to `SourceRecord.created_at` when no
explicit upstream timestamp exists. That timestamp is the local record creation
time, so data with unknown source age is projected as newly fresh. The review
constructed a record with no source-time metadata and observed its local
`created_at` returned verbatim as `derived_source_timestamp`.

In addition, `_connector_freshness_summary` treats a missing source timestamp as
`stale=false`, while a future source timestamp produces age zero. The Agora
projector also falls back from row source time to record `created_at` when
selecting/projecting a row.

Required resolution:

- derive source time only from explicit provider/source fields;
- classify missing, invalid, or materially future source time as explicit
  unknown/degraded/stale truth rather than fresh;
- remove the projector's ingest-time fallback for `asOf`/freshness;
- add missing-time and future-time tests at both connector freshness and Agora
  projection surfaces.

### 4. The deploy command does not wait for or gate on the bounded run

The opt-in root deployment starts the profile with `docker compose up -d
--build` and then continues through unrelated root-stack checks. There is no
`docker compose wait`, exit-code inspection, or receipt/projection readback for
`source-ingest-scheduler` and `source-ingest-agora-projector` before the deploy
can report success.

The Compose dependency correctly prevents the projector from starting unless
the scheduler exits successfully, but detached startup does not make the deploy
itself fail when either one-shot service later fails.

Required resolution:

- when `source-ingest-scheduler` is selected, wait for both bounded one-shot
  services and require exit code zero;
- fail the deployment if the scheduler fails, the projector never starts, or
  the expected new receipt/projection readback is absent;
- cover both successful and failed one-shot paths in the deploy contract tests.

### 5. The documented allowlist cannot complete the documented connector run

The runbook exports only `openapi.twse.com.tw`. The configured
`tw-twse-tpex-official-market` request defaults to both `TWSE` and `TPEx`, whose
price endpoints use `openapi.twse.com.tw` and `www.tpex.org.tw`. The documented
command therefore succeeds on the first venue and is denied on the second.

Update the example to include every exact host required by the bounded request,
or provide and document a governed way to limit that run to TWSE only. Keep the
text clear that redirect hosts remain separately reviewed additions.

## Verification

Passed on the reviewed HEAD:

```text
/home/lupin/pantheon/.venv/bin/python -m pytest -q \
  services/test_external_egress.py \
  services/source_ingestion/tests/test_scheduled_connector.py \
  services/source_ingestion/tests/test_bounded_ingestion.py \
  services/source_ingestion/tests/test_external_source_connectors.py \
  services/source_ingestion/tests/test_finmind_taiwan_connectors.py \
  scripts/test_project_market_data_to_bff_agora_surfaces.py \
  services/source_ingestion/test_compose_activation.py \
  scripts/test_source_ingest_deploy_diagnostics_contract.py

93 passed, 1 warning in 35.49s

bash -n scripts/deploy_nonprod_vm.sh
git diff --check
COMPOSE_PROFILES= docker compose -f docker-compose.yml config --quiet
PANTHEON_EXTERNAL_EGRESS=allowlist \
PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=openapi.twse.com.tw,www.tpex.org.tw \
SOURCE_INGEST_CONTROLLER_MAX_TICKS=1 \
SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY=1 \
SOURCE_INGEST_MAX_RECORDS=100 \
COMPOSE_PROFILES=source-ingest-scheduler \
docker compose -f docker-compose.yml config --quiet
```

The first attempt with the system Python could not start because that
interpreter has no `pytest`; the repository's existing virtual environment was
then used. No external request, deployment, provider credential, production
state, or live-capital surface was touched during review.

## Governed lifecycle transition

After pushing this changes-required review, the reviewer attempted the required
governed transition with:

```text
AI_NAME=Codex2 $PANTHEON_COMMAND_ROOT/scripts/ai-status.sh reopen \
  PAN-SOURCE-FRESH-001 "Changes required; review commit 4f8fd7769 ..."
```

The wrapper rejected the command before any canonical state mutation because
the supervisor-provided `PANTHEON_COMMAND_RUNTIME_SHA` was
`bb482ac3905ef860febd1f8fb48176406389c5e6`, while the same supervisor-provided
command root had advanced to `a95047594c869bdecdcf748da579802168547c74`.
The task therefore remains in `review` until the supervisor renews the command
runtime binding and replays the `reopen` transition. No generated state file
was edited manually, and the runtime pin was not bypassed.

### Re-dispatch resolution

The supervisor later re-dispatched the unchanged implementation for review.
The branch had advanced from `91c4565c2` to `49fb2453f` only through the two
review-evidence commits; no product implementation file changed, so all five
required changes above remain applicable.

With the renewed command-runtime binding at
`5004450c5493aa8aef284cf42439c9b27ef54235`, the reviewer successfully ran the
governed `reopen` command. Authoritative task-state journal sequence 360 records
`PAN-SOURCE-FRESH-001` as `in_progress` at `2026-07-22T22:19:40Z`, owned by
Codex with Codex2 as reviewer, and carries the five required changes back to
the owner. The lagging `ai-status.json` projection was not edited manually.

## Remediation re-review

Reviewed remediation commits: `f193b33bae834f7e3de82fd83c603769f15bcfb5`
and `53c5f99634f5f43fa7e237ceb8fd186f782277f0`

Reviewed branch tip: `0f90bfffe32583366ad3bc382fdff797fba25550`

Verdict: **CHANGES REQUIRED — NOT APPROVED**

The credential redirect remediation is accepted: same-origin credentials are
preserved, cross-origin authorization/cookie/API-key/token headers are removed,
and an unallowlisted redirect is rejected before request construction. The
Agora projector no longer falls back to record creation time, missing/invalid/
future provider time is stale, and the bounded TWSE/TPEx example plus preflight
require both exact provider hosts.

Two failure paths still contradict the delivered contract.

### A. Receipt-finalization failure leaves nonterminal receipt truth

`_run_job` first writes a `processing` receipt and later overwrites it with the
terminal receipt. Its exception handler deliberately skips the typed-failure
write when `post_processing_stage == "receipt_finalize"`. If that final append
fails, the durable store therefore contains a completed run paired with a
`processing` receipt and no typed failure after reload.

Independent fault injection against the second `upsert_receipt` call produced:

```text
{'summary': {'total_ran': 0, 'total_failed': 1, 'total_enqueued': 1, ...},
 'run_status': 'completed',
 'receipt_status': 'processing',
 'typed_failure': None,
 'receipt_writes': 2}
```

Required resolution:

- make receipt terminalization and terminal run truth converge after a failed
  final receipt append or process restart;
- never leave a completed run permanently paired with `processing` and no
  typed failure;
- add a reload regression that fails the final receipt write itself, not only a
  preceding health/evidence write.

### B. The bounded forced connector is not an exclusive run scope

The controller forwards `SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS`, but
`_run_scheduled_connectors` uses that set only to bypass cadence for matching
connectors. It still iterates and enqueues every other enabled due schedule.
With the bounded TWSE/TPEx allowlist, an unrelated due connector can be denied
and make the controller exit nonzero through `total_failed`, or can perform an
unrequested fetch if it shares an allowed host.

Independent two-schedule reproduction produced:

```text
{'forced': 1,
 'total_enqueued': 2,
 'total_ran': 2,
 'ran': ['target', 'unrelated']}
```

Required resolution:

- give the bounded one-shot path an explicit exclusive connector selection,
  while preserving existing non-exclusive scheduler semantics for ordinary
  callers if needed;
- prove an unrelated enabled/due schedule is neither enqueued nor run;
- retain fail-closed reporting when the exclusively selected connector is
  missing, disabled, or fails.

### Re-review verification

Passed:

```text
/home/lupin/pantheon/.venv/bin/python -m pytest -q \
  services/test_external_egress.py \
  services/source_ingestion/tests/test_scheduled_connector.py \
  services/source_ingestion/tests/test_controller_worker.py \
  scripts/test_project_market_data_to_bff_agora_surfaces.py \
  services/source_ingestion/test_compose_activation.py \
  scripts/test_source_ingest_deploy_diagnostics_contract.py

120 passed, 1 warning in 49.99s

bash -n scripts/deploy_nonprod_vm.sh
COMPOSE_PROFILES= docker compose -f docker-compose.yml config --quiet
PANTHEON_EXTERNAL_EGRESS=allowlist \
PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=openapi.twse.com.tw,www.tpex.org.tw \
SOURCE_INGEST_BOUNDED_CONNECTOR_ID=tw-twse-tpex-official-market \
SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS=1800 \
SOURCE_INGEST_CONTROLLER_MAX_TICKS=1 \
SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY=1 \
SOURCE_INGEST_MAX_RECORDS=100 \
COMPOSE_PROFILES=source-ingest-scheduler \
docker compose -f docker-compose.yml config --quiet
git diff --check
```

No external request, provider credential, deployment, production state, or
live-capital surface was touched during re-review.

## Final remediation re-review

Reviewed remediation commits: `efc71382f0cc89993496376e6dacb9df291635b8`
and `721bf4bc1b0bca0ac6b37d1af455d065f3f47cca`

Reviewed branch tip: `7e159061f44bed8a37c0c7d9e027ff403212302a`

Verdict: **APPROVED — RETURN TO OWNER FOR FINALIZATION**

Both remaining findings from the prior remediation re-review are resolved:

- Receipt finalization now retries the terminal typed-failure receipt when the
  success receipt append fails. If both terminal appends fail, JSONL replay
  detects the durable `completed` run paired with a `processing` receipt and
  appends an idempotent `post_processing_interrupted` terminal failure before
  exposing the reloaded store. The focused tests inject both a one-time final
  append failure and persistent terminal append failures, then reload twice to
  prove convergence and stable replay.
- The bounded path now carries an explicit exclusive connector set from deploy
  environment through Compose, controller, scheduler worker, and API. It
  filters schedule enqueue and existing frontier claims while leaving ordinary
  non-exclusive scheduler calls unchanged. The regression proves an unrelated
  enabled/due schedule is excluded, its pre-existing due frontier remains
  queued, and no unrelated receipt is created. Missing schedules, disabled
  schedules/connectors, and selected-connector fetch failures remain explicit
  terminal controller failures.

Independent final verification on the reviewed branch tip passed:

```text
/home/lupin/pantheon/.venv/bin/python -m pytest -q \
  services/test_external_egress.py \
  services/source_ingestion/tests/test_scheduled_connector.py \
  services/source_ingestion/tests/test_controller_worker.py \
  scripts/test_project_market_data_to_bff_agora_surfaces.py \
  services/source_ingestion/test_compose_activation.py \
  scripts/test_source_ingest_deploy_diagnostics_contract.py

127 passed, 1 warning in 33.95s

/home/lupin/pantheon/.venv/bin/python -m compileall -q \
  services/source_ingestion/main.py \
  services/source_ingestion/scheduler.py \
  services/source_ingestion/scheduler_worker.py \
  services/source_ingestion/controller_worker.py

bash -n scripts/deploy_nonprod_vm.sh
COMPOSE_PROFILES= docker compose -f docker-compose.yml config --quiet
PANTHEON_EXTERNAL_EGRESS=allowlist \
PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=openapi.twse.com.tw,www.tpex.org.tw \
SOURCE_INGEST_BOUNDED_CONNECTOR_ID=tw-twse-tpex-official-market \
SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS=1800 \
SOURCE_INGEST_CONTROLLER_MAX_TICKS=1 \
SOURCE_INGEST_SCHEDULER_MAX_CONCURRENCY=1 \
SOURCE_INGEST_MAX_RECORDS=100 \
COMPOSE_PROFILES=source-ingest-scheduler \
docker compose -f docker-compose.yml config --quiet
git diff --check
```

The branch had no open PR at review time. `origin/dev` advanced after the owner
created the reviewed tip, so owner finalization must integrate the then-current
`dev`, re-run appropriate checks, create the task PR, and merge it before
`done`. Hosted source/projection proof remains owner/`AG-HOSTED-CLOSE-001`
closeout work; this approval does not claim a deployment. No external request,
provider credential, deployment, production state, or live-capital surface was
touched during final re-review.

### Governed approval transition blocker

The final approval evidence was committed and pushed at
`a743a69a49f7c25d34bb3e86e4f87a4893dad09b`. The reviewer then invoked the
governed `approve` command with `AI_NAME=Codex2`, this review file, and the
three final review notes. The wrapper rejected the command before canonical
state mutation because the supervisor-provided command root was
`cbbc0a02e415f4aae2e0fbf12c22b0646af0c884`, while this dispatch remained
pinned to `PANTHEON_COMMAND_RUNTIME_SHA=35d7e572445dab5f4702670771e50560955de49e`.

The authoritative task row therefore remains in `review` despite the durable
approved verdict. The command checkout and runtime SHA were not altered, no
generated state file was edited manually, and `done` was not attempted. The
supervisor must renew the command-runtime binding and replay the reviewer
`approve` transition before owner finalization can begin.
