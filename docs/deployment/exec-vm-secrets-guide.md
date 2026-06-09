# Execution VM Secrets Guide

Secrets for the dedicated VM-2 execution-plane stack defined in
[`docker-compose.exec.yml`](/home/lupin/code/pantheon/docker-compose.exec.yml:1).

This guide exists for `DEPLOY-008` and focuses on the execution-only boundary:

- `runtime-manager`
- `pantheon-paper-runtime` paper execution package
- broker / exchange adapter sidecars
- governed datasource bring-up for `IBKR`, `Shioaji`, `Kraken`, `FinMind`, and optional `TEJ` gap-fill

Control-plane services such as BFF, persona, registry, promotion, lineage-read,
governance, telemetry, incidents, and postmortems stay on VM-1 and must not
receive broker or exchange credentials.

## Secret Boundary

VM-2 is the only place that should hold:

- broker API keys / secrets
- exchange API keys / secrets
- `Shioaji` API key / secret
- `Kraken` API key / secret
- `FinMind` API token
- optional `TEJ` API key for historical gap-fill
- execution-only runtime-manager bearer token
- any future paper/live account credentials used by LEAN sidecars

VM-1 may know the public execution endpoint URL, but it must not store the raw
provider secret material.

## Expected Environment File

Use [`env/prod-exec.env.example`](/home/lupin/code/pantheon/env/prod-exec.env.example:1)
as the template and create a machine-local `env/prod-exec.env` on VM-2.

Recommended flow:

```bash
cp env/prod-exec.env.example env/prod-exec.env
chmod 600 env/prod-exec.env
```

Then replace at least:

- `PANTHEON_RUNTIME_MANAGER_TOKEN`
- `PANTHEON_TELEMETRY_URL`
- `EXECUTION_BROKER_PROVIDER=IBKR`
- `TW_EXECUTION_PROVIDER=Shioaji`
- `CRYPTO_EXECUTION_PROVIDER=Kraken`
- `TW_RESEARCH_PROVIDER=FinMind`
- `TW_HISTORICAL_BACKFILL_PROVIDER=TEJ`
- `US_MARKET_DATA_PROVIDER`
- `CANARY_BROKER_ACCOUNT_REF`
- `CANARY_VENUE_REF`
- `BROKER_API_KEY`
- `BROKER_API_SECRET`
- `EXCHANGE_API_KEY`
- `EXCHANGE_API_SECRET`
- `SHIOAJI_API_KEY`
- `SHIOAJI_SECRET_KEY`
- `KRAKEN_API_KEY`
- `KRAKEN_API_SECRET`
- `FINMIND_API_TOKEN`
- `TEJ_API_KEY`
- `PANTHEON_SECRETS_OPTIONAL=false` once real credentials are present

## Current Secret Naming Precedent

The repo already establishes execution-only secret names for nonprod:

- `pantheon-dev-broker-api-key`
- `pantheon-dev-broker-api-secret`
- `pantheon-dev-shioaji-api-key`
- `pantheon-dev-shioaji-secret-key`
- `pantheon-dev-kraken-api-key`
- `pantheon-dev-kraken-api-secret`
- `pantheon-dev-finmind-api-token`
- `pantheon-dev-tej-api-key`
- `pantheon-dev-us-market-data`

Those come from the GCP bootstrap baseline and should remain execution-scoped.
If exchange-specific secrets are added, keep the same convention:

- `pantheon-dev-exchange-api-key`
- `pantheon-dev-exchange-api-secret`

Rules:

- include the environment prefix in the secret name
- do not reuse the same secret container across `dev`, `paper`, and `prod`
- grant secret access at the secret level, not project-wide

## Local Injection Pattern

For direct Docker Compose bring-up on a VM:

```bash
export BROKER_API_KEY='...'
export BROKER_API_SECRET='...'
export EXCHANGE_API_KEY='...'
export EXCHANGE_API_SECRET='...'
export SHIOAJI_API_KEY='...'
export SHIOAJI_SECRET_KEY='...'
export KRAKEN_API_KEY='...'
export KRAKEN_API_SECRET='...'
export FINMIND_API_TOKEN='...'
export TEJ_API_KEY='...'
export PANTHEON_RUNTIME_MANAGER_TOKEN='...'
export PANTHEON_TELEMETRY_URL='http://<vm1-ip>:38083'

docker compose \
  --env-file env/prod-exec.env \
  -f docker-compose.exec.yml \
  up -d
```

For a persistent VM-local file, prefer a non-committed `env/prod-exec.env` and
invoke:

```bash
docker compose --env-file env/prod-exec.env -f docker-compose.exec.yml up -d
```

For local single-host split-stack proof runs, Docker services on VM-2 can also
reach a host-run telemetry service via:

```bash
export PANTHEON_TELEMETRY_URL='http://host.docker.internal:18083'
```

`docker-compose.exec.yml` now injects the `host.docker.internal` gateway alias
so the paper runtime can emit canonical telemetry back to a host-run or
control-plane telemetry service without collapsing the execution-only secret
boundary.

## GCP Secret Manager Example

Populate shell variables from Secret Manager on the VM itself:

```bash
BROKER_API_KEY="$(gcloud secrets versions access latest --secret pantheon-dev-broker-api-key)"
BROKER_API_SECRET="$(gcloud secrets versions access latest --secret pantheon-dev-broker-api-secret)"
EXCHANGE_API_KEY="$(gcloud secrets versions access latest --secret pantheon-dev-exchange-api-key 2>/dev/null || true)"
EXCHANGE_API_SECRET="$(gcloud secrets versions access latest --secret pantheon-dev-exchange-api-secret 2>/dev/null || true)"
SHIOAJI_API_KEY="$(gcloud secrets versions access latest --secret pantheon-dev-shioaji-api-key 2>/dev/null || true)"
SHIOAJI_SECRET_KEY="$(gcloud secrets versions access latest --secret pantheon-dev-shioaji-secret-key 2>/dev/null || true)"
KRAKEN_API_KEY="$(gcloud secrets versions access latest --secret pantheon-dev-kraken-api-key 2>/dev/null || true)"
KRAKEN_API_SECRET="$(gcloud secrets versions access latest --secret pantheon-dev-kraken-api-secret 2>/dev/null || true)"
FINMIND_API_TOKEN="$(gcloud secrets versions access latest --secret pantheon-dev-finmind-api-token 2>/dev/null || true)"
TEJ_API_KEY="$(gcloud secrets versions access latest --secret pantheon-dev-tej-api-key 2>/dev/null || true)"
PANTHEON_RUNTIME_MANAGER_TOKEN="$(openssl rand -hex 24)"

cat > /tmp/pantheon-exec-secrets.env <<EOF
BROKER_API_KEY=${BROKER_API_KEY}
BROKER_API_SECRET=${BROKER_API_SECRET}
EXCHANGE_API_KEY=${EXCHANGE_API_KEY}
EXCHANGE_API_SECRET=${EXCHANGE_API_SECRET}
SHIOAJI_API_KEY=${SHIOAJI_API_KEY}
SHIOAJI_SECRET_KEY=${SHIOAJI_SECRET_KEY}
KRAKEN_API_KEY=${KRAKEN_API_KEY}
KRAKEN_API_SECRET=${KRAKEN_API_SECRET}
FINMIND_API_TOKEN=${FINMIND_API_TOKEN}
TEJ_API_KEY=${TEJ_API_KEY}
PANTHEON_RUNTIME_MANAGER_TOKEN=${PANTHEON_RUNTIME_MANAGER_TOKEN}
EOF
```

Then merge those values into `env/prod-exec.env` with shell tooling or your VM
bootstrap script. Do not write the secret values into tracked files.

## Runtime Notes

The `pantheon-paper-runtime` service in `docker-compose.exec.yml` is the VM-2
paper execution package for EP4 proof raising. It proves that the
execution-plane slice can host:

- a dedicated paper-runtime process with a concrete signal-consumer path
- Pantheon ↔ LEAN bridge imports from `lean/Algorithm.Python/pantheon_algo/`
- runtime-manager adjacency
- broker / exchange sidecar adjacency

Follow-up work can still swap the implementation underneath this package for a
full LEAN image without changing the VM-1 / VM-2 secret boundary established here.

## Verification

After secrets are injected:

```bash
docker compose --env-file env/prod-exec.env -f docker-compose.exec.yml config
docker compose --env-file env/prod-exec.env -f docker-compose.exec.yml up -d
docker compose --env-file env/prod-exec.env -f docker-compose.exec.yml ps
curl -fsS http://127.0.0.1:28081/__health__
curl -fsS http://127.0.0.1:28110/__health__
curl -fsS http://127.0.0.1:28097/__health__
curl -fsS http://127.0.0.1:28098/__health__
python3 scripts/run_ep5_canary_readiness.py \
  run-datasource-smoke \
  --env-file env/prod-exec.env \
  --output-dir /tmp/pantheon/exec-vm-datasource-smoke
```

The VM-2 acceptance bar for `DEPLOY-008` is:

- `runtime-manager` is healthy
- the paper execution runtime package is healthy
- control-plane services are absent from the compose
- provider credentials live only on VM-2
- datasource smoke emits governed provider payloads for `IBKR`, `Shioaji`, `Kraken`, `FinMind`, and optional `TEJ` gap-fill
