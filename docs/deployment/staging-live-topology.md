# Staging-Live Topology

Status date: 2026-08-25

For the current VS Code / LLM agent workflow, also read:

- [nonprod-development-workflow.md](nonprod-development-workflow.md)
- [bff-https-ingress.md](bff-https-ingress.md)

## VM Inventory

Active dev GCP project: `pantheon-lupin-dev-20260719`.

The prior shared project `pantheon-benjamin-20260528` is suspended. Its
staging-live inventory below is retained as the last known topology, but those
VMs and endpoints are not current reachable deployment targets. Staging-live
requires a separately authorized project replacement; do not route dev back to
the suspended project as a workaround.

The replacement direction is the release-scoped ephemeral VM design in
[`vm-dev-staging-prod-management-plan.md`](vm-dev-staging-prod-management-plan.md).
That document is a target plan, not evidence that the replacement staging or
production environments have already been provisioned.

| VM | Zone | Public endpoint | Internal endpoint carried by repo vars | Role |
| --- | --- | --- | --- | --- |
| `pantheon-lupin-dev` | `asia-east1-b` | `35.201.204.12` | VM-local BFF `127.0.0.1:18001` | active replacement dev backend and Pantheon-owned FE target |
| `pantheon-lupin-staging-control` | `asia-east1-b` | `104.155.223.192` | VM-local BFF `127.0.0.1:38001` | historical staging VM1; suspended project |
| `pantheon-lupin-staging-exec` | `asia-east1-b` | no public BFF endpoint | runtime-manager `10.50.0.21:28081` | historical staging VM2; suspended project |

Read active dev machine and network inventory from `gcloud compute instances
list --project=pantheon-lupin-dev-20260719`. Do not reuse the suspended-project
dev IP `35.201.239.38` as current topology truth.

## Current Pantheon Layout

Dev:

- VM: `pantheon-lupin-dev`
- compose project: `pantheon`
- compose file: `/home/lupin/pantheon/docker-compose.yml`
- compose contract: default dev single-VM baseline; control plane,
  runtime-manager, telemetry, research services, BFF, and local dev signal store
  are co-located for non-prod iteration.
- public BFF HTTPS URL:
  `https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io`
- BFF health: `http://127.0.0.1:18001/health` on the dev VM
- live broker scope: disabled by default through `PANTHEON_LIVE_BROKER_ENABLED=false`

Staging-live VM1:

- VM: `pantheon-lupin-staging-control`
- compose project: `pantheon-control`
- compose files:
  `/home/lupin/code/pantheon/docker-compose.control.yml` for the base VM1
  control stack, plus
  `/home/lupin/code/pantheon/docker-compose.staging-full.yml` when staging-live
  needs the full non-execution backend surface.
- compose contract: VM1 control plane; includes BFF, telemetry, governance,
  deployment, registry, persona, incident/postmortem, capital, evolution, and
  lineage read surfaces. The full overlay may add research/source/search/router
  surfaces, but it must not add runtime-manager, broker sidecars, broker
  credentials, exchange sidecars, or execution runtimes.
- public BFF HTTPS URL:
  `https://pantheon-lupin-staging-bff.104.155.223.192.sslip.io`
- BFF health: `http://127.0.0.1:38001/health` on VM1
- runtime-manager backend: `http://10.50.0.21:28081`
- telemetry ingest for VM2: configured by the staging exec env; do not infer it
  from the old pre-cutover `10.140.0.x` addresses.
- live broker scope: enabled by the control stack default for staging-live
- broker credentials: not present on VM1; only the VM2 execution env owns them.
- BFF HA/LB scope: intentionally deferred; VM1 currently runs one
  `operator-bff` instance and no BFF load balancer.

Staging-live VM2:

- VM: `pantheon-lupin-staging-exec`
- compose project: `pantheon-exec`
- compose file: `/home/lupin/code/pantheon/docker-compose.exec.yml`
- compose contract: VM2 execution plane; includes runtime-manager, signal-store,
  broker adapter, exchange adapter, and paper runtime. It excludes BFF,
  governance, telemetry, registry, persona, and other VM1 control services.
- runtime-manager health: `http://127.0.0.1:28081/__health__` on VM2
- telemetry target: `http://10.140.0.4:38083`
- TWS API: `7496` on VM2
- broker credentials and live broker session stay on VM2

## Separation Rules

- dev does not share compose projects, runtime volumes, or broker secrets with
  staging-live.
- staging VM1 runs control/BFF/telemetry/governance surfaces and, when
  `docker-compose.staging-full.yml` is layered in, only the additional
  non-execution backend surfaces.
- staging VM2 runs runtime-manager, execution runtimes, broker adapter,
  exchange adapter, and TWS/IBKR session state.
- VM1 reaches VM2 runtime-manager only over the current internal URL
  `http://10.50.0.21:28081`; VM2 sends telemetry back to the VM1 telemetry
  endpoint configured in the machine-local staging exec env.
- Lovable receives only frontend build-time variables. Broker secrets never go
  to Lovable or browser config.
- staging VM1 may call VM2 through internal IPs. Lovable must call staging VM1
  through a public HTTPS BFF ingress.

## BFF HA/LB Boundary

The staging-live dual-VM topology separates the control plane from the execution
plane. It does not complete BFF high availability.

For the current staging baseline:

- `docker-compose.control.yml` runs one `operator-bff` instance on VM1.
- Caddy/HTTPS ingress terminates browser traffic for that single BFF upstream;
  it is not a multi-replica BFF load-balancer topology.
- do not add `deploy.replicas`, a second BFF service, or a BFF load balancer to
  the staging compose files until `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`
  re-entry conditions are met.
- BFF outage remains a control-plane/UI outage; active runtimes and emergency
  control paths must remain reachable without BFF.

## Compose Contract Verification

Validate the contract without building or starting containers:

```bash
bash scripts/validate_split_topology.sh
```

The script renders all four compose configurations with their example env files
and asserts:

- `docker-compose.yml` is the dev single-VM baseline.
- VM1 control compose excludes execution services and broker/exchange secrets.
- VM1 full staging overlay still excludes runtime-manager, broker sidecars,
  broker/exchange secrets, exchange sidecars, and execution runtimes.
- VM1 BFF and telemetry point to VM2 runtime-manager on `10.140.0.5:28081`.
- VM2 execution compose excludes BFF/control services and points paper runtime
  telemetry back to VM1 on `10.140.0.4:38083`.

## Firewall and Ingress Status

The replacement dev project exposes SSH plus public HTTP/HTTPS to the dev VM.
The BFF container port `18001` remains VM-local and must be published through
Caddy. A running VM or open firewall alone is not hosted-deployment proof.

This is intentional until an HTTPS ingress is created. The next ingress step is
one of:

- Cloudflare tunnel for dev and staging BFF
- Nginx/Caddy reverse proxy with TLS certs on each VM
- GCP HTTPS load balancer with separate backend services

## Useful Checks

List VM roles:

```bash
gcloud compute instances list \
  --project=pantheon-lupin-dev-20260719 \
  --format='table(name,zone.basename(),machineType.basename(),networkInterfaces[0].networkIP,networkInterfaces[0].accessConfigs[0].natIP,labels.env,labels.role,status)'
```

Dev BFF:

```bash
gcloud compute ssh lupin@pantheon-lupin-dev --zone=asia-east1-b --project=pantheon-lupin-dev-20260719 -- \
  'cd /home/lupin/pantheon && docker compose ps && curl -fsS http://127.0.0.1:18001/health'
```

Historical staging VM1 BFF and VM2 runtime reachability (unavailable while the
project is suspended; retained only for replacement planning):

```bash
gcloud compute ssh lupin@pantheon-lupin-staging-control --zone=asia-east1-b --project=pantheon-benjamin-20260528 -- \
  'cd /home/lupin/code/pantheon && docker compose --env-file env/prod-control.env.example -f docker-compose.control.yml ps && curl -fsS http://127.0.0.1:38001/health && curl -fsS http://10.50.0.21:28081/__health__'
```

Staging VM1 full non-execution surface render check:

```bash
gcloud compute ssh lupin@pantheon-lupin-staging-control --zone=asia-east1-b --project=pantheon-benjamin-20260528 -- \
  'cd /home/lupin/code/pantheon && docker compose --env-file env/prod-control.env.example -f docker-compose.control.yml -f docker-compose.staging-full.yml config --quiet'
```

Staging VM2 execution:

```bash
gcloud compute ssh lupin@pantheon-lupin-staging-exec --zone=asia-east1-b --project=pantheon-benjamin-20260528 -- \
  'cd /home/lupin/code/pantheon && docker compose --env-file env/prod-exec.env.example -f docker-compose.exec.yml ps && curl -fsS http://127.0.0.1:28081/__health__'
```
