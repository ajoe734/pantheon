# AG-GAP-002 — durable Trading Room Postgres store

## Delivered boundary

`TradingRoomStore` remains the in-memory default for unit tests and explicitly
disabled deployments. `PostgresTradingRoomStore` persists the same collections
through a transaction-locked JSONB state row. It does not add broker orders,
RuntimeBinding mutation, or capital-binding authority.

Pantheon dev selects the durable backend with:

- `AGORA_TRADING_ROOM_STORE_BACKEND=postgres`
- `AGORA_TRADING_ROOM_STORE_DSN` set to the internal Postgres service DSN
- `AGORA_TRADING_ROOM_STORE_SCHEMA=agora`

Startup logs report only backend, class, and schema; the DSN is never logged.

## Preserved invariants

- Decision events still require `agora_decision_support_only`.
- Trading intents still require `agora_intent_record_only`.
- Governed handoffs still require `agora_request_only_no_order_route`.
- Workspace `dashboardVersion` and router `If-Match`/ETag behavior are unchanged;
  stored payloads round-trip without rewriting versions.
- Writes lock the singleton state row within a Postgres transaction, preventing
  concurrent BFF writers from silently overwriting a state snapshot.

## Verification

- `python3 -m pytest agora/trading_room/test_postgres_store.py agora/trading_room/test_trading_room.py -q`
- `python3 -m pytest tests/test_agora_workshop_dev_deploy_config.py -q`
- `docker compose -f docker-compose.yml config --quiet`
- `docker compose -f docker-compose.control.yml config --quiet`

The focused Postgres test creates one store, writes a proof-bearing decision
event, then creates a second store against the same database and reads it back.
It runs when `TEST_DATABASE_URL` is available and otherwise reports a skip.

## Live restart evidence gate

After merge and dev deployment, create a Trading Room decision-support record,
restart `operator-bff`, and read the same identifier through the public BFF
route. Also confirm the running container has the Postgres backend selected and
the startup log names `PostgresTradingRoomStore`. Record the identifier and
deployed merge SHA here; until then, the implementation is merge-ready but the
live restart proof remains an environment acceptance step.

## Owner finalization

Reviewer approval covers implementation commit `0f210d767` with no blocking
findings. During owner closeout, the focused repository checks completed with
`3 passed, 1 skipped`:

- `python3 -m pytest -q services/control-plane/bff/agora/trading_room/test_postgres_store.py services/control-plane/bff/tests/test_agora_workshop_dev_deploy_config.py`
- `git diff --check`

The skipped case is the environment-gated Postgres integration proof described
above; it does not convert the outstanding live restart evidence into a local
claim.
