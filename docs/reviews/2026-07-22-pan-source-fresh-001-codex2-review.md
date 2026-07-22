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
