# SRCLIVE-001 - Live Activation Acceptance

Status: live activation proof captured on the dev VM; review and PR closeout
remain.

Recommended owner: Antigravity or Codex

Recommended reviewer: Codex2 or Copilot

Do not assign to Claude or Claude2 while their quota is exhausted.

## Goal

Prove the SRCLIVE-001 official-source live path in the actual dev runtime, not only in code, docs, or local tests.

## Evidence Already Published

- Pantheon PR #2517: SRCLIVE-001: anchor official source live path.
- Merge commit: 8da3d35766a041bfbb7b85aa018ee4ef65114cfd.
- Branch CI gates were green.
- Runbook published at docs/05/srclive/tw-activation-runbook.md.

## Missing Production Evidence

The current audit did not find proof that VM-local source-ingest activation was run and accepted. The missing evidence is:

- exact dev VM deploy source SHA;
- source-ingest activation command and exit status;
- official TWSE/TPEx/MOPS source fetch result;
- health/usage snapshot after activation;
- BFF readback for persona-tw-equity;
- archived evidence path and timestamp.

## Required Execution

1. Confirm the current dev VM source SHA and relevant service versions.
2. Run the activation path from the published TW activation runbook.
3. Capture logs and health snapshots.
4. Verify BFF readback for the target persona/dataset.
5. Record evidence paths in this packet or a dated evidence subdirectory.
6. If activation fails, record the concrete failing command, HTTP response or stack trace, and the service responsible.

## Acceptance Criteria

1. Live activation command succeeds in the dev runtime.
2. Health/usage snapshot shows source-ingest activity from official sources.
3. BFF readback returns current official-source-backed data for persona-tw-equity.
4. Evidence is committed through a clean branch, pushed, reviewed, checked, and merged.
5. Final closeout records PR number, merge commit, deploy/run IDs, and evidence paths.

## 2026-07-07 Dev VM Activation Evidence

Evidence file:

`support/evidence/SRCLIVE-001/live-activation-20260707T151545Z.json`

Raw packet retained on the dev VM:

`/tmp/srclive-001-20260707T151545Z`

The activation ran from host `pantheon-lupin-dev`. The live deployment source
root used by the running compose stack was:

| Field | Value |
|---|---|
| Pantheon dev root | `/home/lupin/pantheon-ci-deploy/dev-root` |
| Pantheon source SHA | `26cd48b380bab7a7475150114c4e07113d8f4816` |
| Source-ingest image id | `sha256:f8f67d9a8b18977cf3426114232fe21615d7dd48d4596244ecfb4208eb034146` |
| BFF image id | `sha256:7ff809d930e898f77896c0bbfe4d1bc33377a0327081cef62aca5537dab8c04d` |
| Frontend deployment commit | `b65b16b5088499bc797d890edd4c5c2b0f17b4ce` |

The active VM source-ingest endpoint was `http://127.0.0.1:18097`. The
published runbook default of `38097` did not match the active compose mapping;
`docker ps` showed `pantheon-source-ingest-1` mapped as
`0.0.0.0:18097->8097/tcp`.

Activation commands and results:

| Step | Result |
|---|---|
| Configure `tw-twse-tpex-official-market` | HTTP 201 |
| Configure `tw-mops-official-disclosures` | HTTP 201 |
| Trigger `tw-twse-tpex-official-market` job with trace `srclive-001-tw-official-20260707T151545Z` | HTTP 201, run `ingest-80004fc3fbc6`, completed |
| Trigger `tw-mops-official-disclosures` job with trace `srclive-001-mops-20260707T151545Z` | HTTP 201, run `ingest-bce9c0c807b8`, completed |

Post-run source-ingest health:

| Source | Status | Last success | Rows | Run |
|---|---:|---:|---:|---|
| `tw-twse-tpex-official-market` | `ok` | `2026-07-07T15:15:47Z` | 1000 | `ingest-80004fc3fbc6` |
| `tw-mops-official-disclosures` | `ok` | `2026-07-07T15:15:54Z` | 8 | `ingest-bce9c0c807b8` |

Authenticated BFF readback against
`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` returned HTTP 200 for
`GET /bff/management/persona-fleet`. `persona-tw-equity` reported:

| Provider | Status |
|---|---|
| `shioaji` | `read_ok` |
| `twse` | `read_ok` |
| `tpex` | `read_ok` |
| `mops` | `read_ok` |
| `finmind` | `read_ok` |

The BFF `data_source_summary` was `state=live_readback_ok`,
`source_health_source=source_ingest`, `live_ingestion_enabled=true`,
`provider_status_counts.read_ok=5`, and `degraded_provider_count=0`.

Verifier:

```bash
BFF_BASE=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
BFF_TOKEN=<dev structured admin token> \
SOURCE_INGEST_BASE=http://127.0.0.1:18097 \
python3 scripts/verify_srclive_readback.py --json
```

Result: passed.

Residual observation: both raw source-ingest job payloads recorded
`source_search_refresh.status=notify_failed` with `error=timed out` for
`http://search-svc:8098`. This is outside the SRCLIVE-001 acceptance gate, which
requires official source-ingest activation, health/usage proof, and BFF
source-health readback; those gates passed.
