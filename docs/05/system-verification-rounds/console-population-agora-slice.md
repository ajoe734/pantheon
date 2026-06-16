# Console population - Agora projection slice

**Date:** 2026-06-15
**Branch:** `task/CONSOLE-DATA-AGORA`
**Task:** `CONSOLE-DATA-AGORA`

## Fix

This slice wires consultation producer records into the Agora BFF read surfaces.

- `scripts/project_consultation_to_bff_agora_surfaces.py` reads real
  consultation service requests, transcripts, memos, and gate handoffs from
  `CONSULTATION_URL` or `CONSULTATION_DATA_DIR`.
- The script writes BFF read stores for `agora_signals`, `agora_sessions`,
  `agora_handoffs`, `agora_training_examples`, `research_tickets`,
  `research_notes`, `insight_cards`, `decision_journal_entries`, and
  `postmortems`.
- `ReadSurfaceStore` now treats decision journal and remaining Agora support
  stores as service-backed datasets, so strict live mode can report
  `source=service_store` instead of falling back to local snapshots.
- `docker-compose.yml` sets operator-BFF defaults for the new `/data/bff/*.json`
  store paths.

The projection does not fabricate rows. Every output record is derived from a
consultation request, transcript, memo, or handoff returned by the producer.

## Local producer verification

The local consultation service was reachable:

```text
curl http://127.0.0.1:18096/health
-> {"status":"ok","service":"consultation"}
```

I created a real consultation lifecycle through the service API:

```text
request_id: cr-430a59573e9e
memo_id: mem-ddb8921b461a
handoff_id: gh-48713d4d6166
```

Projection command:

```text
CONSULTATION_URL=http://127.0.0.1:18096 \
OUT_DIR=/tmp/console-data-agora-bff \
python3 scripts/project_consultation_to_bff_agora_surfaces.py
```

Projection result:

```text
projected 1 signals, 1 sessions, 1 handoffs, 1 training examples,
1 inbox research tasks, 1 notes, 2 insights, 1 journal entries,
1 postmortems -> /tmp/console-data-agora-bff
```

With BFF fallback disabled and the projected stores mounted, the local BFF
contract path returned:

| Path | Count | Surface status | Source |
|---|---:|---|---|
| `/bff/agora/signals` | 1 | ok | service_store |
| `/bff/agora/sessions` | 1 | ok | service_store |
| `/bff/agora/insights` | 2 | ok | service_store |
| `/bff/agora/notes` | 1 | ok | service_store |
| `/bff/agora/journal` | 1 | ok | service_store |
| `/bff/agora/handoffs` | 1 | ok | service_store |
| `/bff/agora/training-examples` | 1 | ok | service_store |
| `/bff/agora/postmortems` | 1 | ok | service_store |
| `/bff/agora/inbox` | 4 | ok | composed |

## Remote dev pre-deploy observation

Before this branch is deployed and the projection is run on the dev BFF host,
the remote dev BFF still reports missing Agora stores:

```text
GET https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/signals
Authorization: Bearer op-dev:admin:mfa
-> total=0, agora_signal_list.status=unavailable, source=missing

GET https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/inbox
Authorization: Bearer op-dev:admin:mfa
-> itemCounts signal=0 insight=0 research_task=0, surfaces source=missing
```

This is expected until the task branch is merged/deployed and
`project_consultation_to_bff_agora_surfaces.py` is run against the dev
consultation service into the operator-BFF `/data/bff` volume.

## Validation

```text
python3 -m py_compile scripts/project_consultation_to_bff_agora_surfaces.py \
  services/control-plane/bff/read_store.py \
  services/control-plane/bff/tests/test_console_data_agora_projection.py

python3 -m pytest services/control-plane/bff/tests/test_console_data_agora_projection.py -q
python3 -m pytest services/control-plane/bff/test_bff_agora_core_contract.py \
  services/control-plane/bff/test_bff_agora_extended_contract.py -q
python3 -m pytest services/consultation/test_compose_activation.py -q
git diff --check
```

Results: all listed validation passed.
