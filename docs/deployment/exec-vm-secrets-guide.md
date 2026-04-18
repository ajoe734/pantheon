# Execution VM Secrets Guide

Secrets for the dedicated VM-2 execution-plane stack defined in
[`docker-compose.exec.yml`](/home/edna/code/pantheon/docker-compose.exec.yml:1).

This guide exists for `DEPLOY-008` and focuses on the execution-only boundary:

- `runtime-manager`
- `pantheon-lean` paper runtime bootstrap
- broker / exchange adapter sidecars

Control-plane services such as BFF, persona, registry, promotion, lineage-read,
governance, telemetry, incidents, and postmortems stay on VM-1 and must not
receive broker or exchange credentials.

## Secret Boundary

VM-2 is the only place that should hold:

- broker API keys / secrets
- exchange API keys / secrets
- execution-only runtime-manager bearer token
- any future paper/live account credentials used by LEAN sidecars

VM-1 may know the public execution endpoint URL, but it must not store the raw
broker / exchange secret material.

## Expected Environment File

Use [`env/prod-exec.env.example`](/home/edna/code/pantheon/env/prod-exec.env.example:1)
as the template and create a machine-local `env/prod-exec.env` on VM-2.

Recommended flow:

```bash
cp env/prod-exec.env.example env/prod-exec.env
chmod 600 env/prod-exec.env
```

Then replace at least:

- `PANTHEON_RUNTIME_MANAGER_TOKEN`
- `BROKER_API_KEY`
- `BROKER_API_SECRET`
- `EXCHANGE_API_KEY`
- `EXCHANGE_API_SECRET`
- `PANTHEON_SECRETS_OPTIONAL=false` once real credentials are present

## Current Secret Naming Precedent

The repo already establishes execution-only secret names for nonprod:

- `pantheon-dev-broker-api-key`
- `pantheon-dev-broker-api-secret`

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
export PANTHEON_RUNTIME_MANAGER_TOKEN='...'

docker compose \
  --env-file env/prod-exec.env.example \
  -f docker-compose.exec.yml \
  up -d
```

For a persistent VM-local file, prefer a non-committed `env/prod-exec.env` and
invoke:

```bash
docker compose --env-file env/prod-exec.env -f docker-compose.exec.yml up -d
```

## GCP Secret Manager Example

Populate shell variables from Secret Manager on the VM itself:

```bash
BROKER_API_KEY="$(gcloud secrets versions access latest --secret pantheon-dev-broker-api-key)"
BROKER_API_SECRET="$(gcloud secrets versions access latest --secret pantheon-dev-broker-api-secret)"
EXCHANGE_API_KEY="$(gcloud secrets versions access latest --secret pantheon-dev-exchange-api-key 2>/dev/null || true)"
EXCHANGE_API_SECRET="$(gcloud secrets versions access latest --secret pantheon-dev-exchange-api-secret 2>/dev/null || true)"
PANTHEON_RUNTIME_MANAGER_TOKEN="$(openssl rand -hex 24)"

cat > /tmp/pantheon-exec-secrets.env <<EOF
BROKER_API_KEY=${BROKER_API_KEY}
BROKER_API_SECRET=${BROKER_API_SECRET}
EXCHANGE_API_KEY=${EXCHANGE_API_KEY}
EXCHANGE_API_SECRET=${EXCHANGE_API_SECRET}
PANTHEON_RUNTIME_MANAGER_TOKEN=${PANTHEON_RUNTIME_MANAGER_TOKEN}
EOF
```

Then merge those values into `env/prod-exec.env` with shell tooling or your VM
bootstrap script. Do not write the secret values into tracked files.

## Runtime Notes

The `pantheon-lean-paper` service in `docker-compose.exec.yml` is a VM-split
bootstrap wrapper, not the final per-pool LEAN packaging. It proves that the
execution-plane slice can host:

- a dedicated paper-runtime process
- Pantheon ↔ LEAN bridge imports from `lean/Algorithm.Python/pantheon_algo/`
- runtime-manager adjacency
- broker / exchange sidecar adjacency

Follow-up work can replace that wrapper with a fully built LEAN image without
changing the VM-1 / VM-2 secret boundary established here.

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
```

The VM-2 acceptance bar for `DEPLOY-008` is:

- `runtime-manager` is healthy
- the paper-runtime bootstrap process is healthy
- control-plane services are absent from the compose
- broker / exchange credentials live only on VM-2
