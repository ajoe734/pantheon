# PFG-DEV-INTEGRATION-20260820: dev Compose integration evidence

This task owns the root dev Compose integration layer only. It composes the
already repaired functional owners without creating a second scheduler,
reconstruction worker, paper runtime, management read model, or provider path.

## Delivered topology

- Source starts in `reconcile_only` with `MAX_TICKS=0`; the documented operator
  `docker compose run --rm -e SOURCE_INGEST_CONTROLLER_MAX_TICKS=1
  source-ingest-scheduler` is the only bounded pull entry point.
- Source persists the canonical latest-normalized snapshot below the existing
  `source-ingest-data` volume. The dynamic paper fleet and the artifact signal
  producer both use `http://source-ingest:8097` and wait for Source readiness.
- `operator-bff` remains the composition point for Agora reconstruction and
  Research outbox consumption, Management reads/actions, and Management AI. It
  has explicit URLs, durable `bff-data`, exact downstream health targets, and
  health-gated dependencies for the relevant owners.
- The paper fleet is the only unprofiled paper runtime owner. The static paper
  runtime stays an explicit compatibility profile; live broker and canary
  execution remain false in the root dev topology.

## Legacy-profile audit

No profile was removed. The audit found no zero-caller profile eligible for
retirement after this topology proof:

| Profile | Current caller / purpose | Disposition |
| --- | --- | --- |
| `static-paper-runtime` | explicit compatibility/test runtime; referenced by runtime-manager Compose contract tests | retain, never default |
| `source-ingest-scheduler` | explicit one-shot Source-to-Agora projector path | retain, operator/profile-only |
| `openclaw` | optional upstream gateway and durable gateway-state initializer | retain, adapter degrades honestly without it |
| `smoke`, `activation-ready-smoke`, `source-search-bounded`, `dormant-smoke` | named test/validation profiles | retain, never default |

The task therefore makes only the missing artifact-producer-to-Source owner
connection explicit; it does not broaden Source pulling, enable broker capital,
or delete compatibility paths whose callers still exist.

## Verification

Completed from the repository root:

```bash
docker compose config -q
.venv-pantheon/bin/python3 -m pytest -q \
  tests/integration/test_product_functional_compose_contract.py \
  services/source_ingestion/test_compose_activation.py \
  services/execution/runtime-manager/test_paper_fleet_reconciler.py \
  services/control-plane/bff/tests/test_assistant_dev_compose_flags.py \
  services/openclaw-gateway-adapter/test_compose_activation.py
POSTGRES_PORT=25432 SOURCE_INGEST_PORT=28097 \
  docker compose -p pfgdevint20260821 up -d --build --wait source-ingest
curl --fail --silent --show-error http://127.0.0.1:28097/readyz
POSTGRES_PORT=25432 SOURCE_INGEST_PORT=28097 \
  docker compose -p pfgdevint20260821 down --remove-orphans
```

The focused suite passed 58 tests. The isolated readiness probe returned
`ready: true` and reported the canonical
`/data/source-ingest/latest_market_snapshots.jsonl` state path. Temporary
containers were removed after the probe; task-local named volumes were retained
to avoid deleting test data.
