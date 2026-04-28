# Staging-Live Topology

Status date: 2026-04-26

For the current VS Code / LLM agent workflow, also read:

- [nonprod-development-workflow.md](/home/edna/code/pantheon/docs/deployment/nonprod-development-workflow.md)
- [bff-https-ingress.md](/home/edna/code/pantheon/docs/deployment/bff-https-ingress.md)

## VM Inventory

| VM | Zone | Machine | Internal IP | External IP | Role |
| --- | --- | --- | --- | --- | --- |
| `pantheon-dev-vm1` | `asia-east1-b` | `e2-highmem-2` | `10.140.0.6` | `35.236.178.81` | dev backend |
| `pantheon-taiwan` | `asia-east1-b` | `e2-highmem-4` | `10.140.0.4` | `34.81.225.122` | staging VM1 control/BFF |
| `pantheon-exec-vm2-20260424` | `asia-east1-a` | `e2-highmem-4` | `10.140.0.5` | `35.189.185.53` | staging VM2 execution/broker |
| `legendflow-20260417-031358` | `asia-east1-a` | `e2-medium` | `10.140.0.2` | `35.187.154.112` | unrelated frontend/dev host |

`legendflow-20260417-031358` was resized down to `e2-medium` to stay within the
current 12 vCPU GCP quota. The current vCPU allocation is:

- dev: 2
- staging VM1: 4
- staging VM2: 4
- legendflow: 2

## Current Pantheon Layout

Dev:

- VM: `pantheon-dev-vm1`
- compose project: `pantheon`
- compose file: `/home/edna/code/pantheon/docker-compose.yml`
- public BFF HTTPS URL:
  `https://pantheon-dev-bff.35.236.178.81.sslip.io`
- BFF health: `http://127.0.0.1:18001/health` on the dev VM
- live broker scope: disabled by default through `PANTHEON_LIVE_BROKER_ENABLED=false`

Staging-live VM1:

- VM: `pantheon-taiwan`
- compose project: `pantheon-control`
- compose file: `/home/edna/code/pantheon/docker-compose.control.yml`
- public BFF HTTPS URL:
  `https://pantheon-staging-bff.34.81.225.122.sslip.io`
- BFF health: `http://127.0.0.1:38001/health` on VM1
- runtime-manager backend: `http://10.140.0.5:28081`
- live broker scope: enabled by the control stack default for staging-live

Staging-live VM2:

- VM: `pantheon-exec-vm2-20260424`
- compose project: `pantheon-exec`
- compose files: `/home/edna/code/pantheon-ep5/docker-compose.exec.yml` and `/home/edna/code/pantheon-ep5/pantheon/docker-compose.exec.yml`
- runtime-manager health: `http://127.0.0.1:28081/__health__` on VM2
- TWS API: `7496` on VM2
- broker credentials and live broker session stay on VM2

## Separation Rules

- dev does not share compose projects, runtime volumes, or broker secrets with
  staging-live.
- staging VM1 runs control/BFF/telemetry/governance surfaces.
- staging VM2 runs execution, broker adapter, exchange adapter, and TWS/IBKR
  session state.
- Lovable receives only frontend build-time variables. Broker secrets never go
  to Lovable or browser config.
- staging VM1 may call VM2 through internal IPs. Lovable must call staging VM1
  through a public HTTPS BFF ingress.

## Firewall and Ingress Status

The current GCP firewall exposes only default SSH/RDP/ICMP/internal rules. BFF
ports `18001` and `38001` are not publicly exposed.

This is intentional until an HTTPS ingress is created. The next ingress step is
one of:

- Cloudflare tunnel for dev and staging BFF
- Nginx/Caddy reverse proxy with TLS certs on each VM
- GCP HTTPS load balancer with separate backend services

## Useful Checks

List VM roles:

```bash
gcloud compute instances list \
  --project=pantheon-493602 \
  --format='table(name,zone.basename(),machineType.basename(),networkInterfaces[0].networkIP,networkInterfaces[0].accessConfigs[0].natIP,labels.env,labels.role,status)'
```

Dev BFF:

```bash
gcloud compute ssh edna@pantheon-dev-vm1 --zone=asia-east1-b --project=pantheon-493602 -- \
  'cd /home/edna/code/pantheon && docker compose ps && curl -fsS http://127.0.0.1:18001/health'
```

Staging VM1 BFF and VM2 runtime reachability:

```bash
gcloud compute ssh edna@pantheon-taiwan --zone=asia-east1-b --project=pantheon-493602 -- \
  'cd /home/edna/code/pantheon && docker compose -f docker-compose.control.yml ps && curl -fsS http://127.0.0.1:38001/health && curl -fsS http://10.140.0.5:28081/__health__'
```

Staging VM2 execution:

```bash
gcloud compute ssh edna@pantheon-exec-vm2-20260424 --zone=asia-east1-a --project=pantheon-493602 -- \
  'cd /home/edna/code/pantheon-ep5 && docker compose -p pantheon-exec -f docker-compose.exec.yml ps && curl -fsS http://127.0.0.1:28081/__health__'
```
