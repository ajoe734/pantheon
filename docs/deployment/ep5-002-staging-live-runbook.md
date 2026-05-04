# EP5-002 Staging-Live Broker Rehearsal Runbook

Status date: 2026-04-26

## Goal

Run EP5-002 on staging-live before any production environment exists:

1. prove the staging Lovable UI reaches the staging VM1 BFF
2. prove VM1 reaches VM2 execution/runtime-manager
3. prove the live IBKR/TWS broker path is readable
4. prepare the runtime-manager-originated packet before any live side effect
5. run the smallest operator-supervised live order/cancel proof only after the
   read-only checks, safety gates, and explicit human approval pass

## Preconditions

- staging VM1 control stack is healthy
- staging VM2 execution stack is healthy
- TWS live login is active on VM2
- `TWS API` is enabled on port `7496`
- `PANTHEON_LIVE_BROKER_ENABLED=true` only on staging-live BFF
- dev BFF still has `PANTHEON_LIVE_BROKER_ENABLED=false`
- staging Lovable app uses `VITE_PANTHEON_ENV=staging-live`
- staging Lovable app uses the staging BFF HTTPS URL
- staging BFF CORS allowlist includes only the staging Lovable origin
- operator auth, governance gate, and kill switch state are verified

## Startup Order

1. Start or verify VM2 execution stack:

```bash
gcloud compute ssh edna@pantheon-exec-vm2-20260424 --zone=asia-east1-a --project=pantheon-493602 -- \
  'cd /home/lupin/code/pantheon-ep5 && docker compose -p pantheon-exec -f docker-compose.exec.yml up -d && curl -fsS http://127.0.0.1:28081/__health__'
```

2. Start or verify VM1 control stack:

```bash
gcloud compute ssh edna@pantheon-taiwan --zone=asia-east1-b --project=pantheon-493602 -- \
  'cd /home/lupin/code/pantheon && PANTHEON_RUNTIME_MANAGER_URL=http://10.140.0.5:28081 PANTHEON_RUNTIME_MANAGER_TOKEN=runtime-control-internal PANTHEON_INTERNAL_API_URL=http://10.140.0.5:28081 PANTHEON_LIVE_BROKER_ENABLED=true docker compose -f docker-compose.control.yml up -d && curl -fsS http://127.0.0.1:38001/health && curl -fsS http://10.140.0.5:28081/__health__'
```

3. Publish the staging Lovable app from a verified commit.

4. Confirm the staging UI header shows `STAGING LIVE BROKER`.

## Read-Only Broker Path

Use the existing closeout as the baseline:

- [ibkr-live-tws-api-closeout.md](/home/lupin/code/pantheon/docs/deployment/ibkr-live-tws-api-closeout.md)

Before live order/cancel, re-check:

- TWS session is still active
- managed account is still the expected account
- VM1 can reach VM2
- telemetry and runtime-manager are healthy
- kill switch status is known and actionable

## Live Order/Cancel Proof

Use the runtime-manager packet prep boundary first:

- [ep5-002-runtime-manager-proof-packet.md](/home/lupin/code/pantheon/docs/deployment/ep5-002-runtime-manager-proof-packet.md)

The direct IBKR harness can capture broker acknowledgement and cancel facts, but
EP5-002 proof requires the packet to also archive runtime-manager origin,
lifecycle, telemetry, operator, and closeout evidence:

- [ibkr-minimal-live-order-cancel-manual.md](/home/lupin/code/pantheon/docs/deployment/ibkr-minimal-live-order-cancel-manual.md)
- `scripts/run_ibkr_live_order_cancel.py`
- `scripts/validate_ep5_live_order_cancel.py`

Guardrails:

- instrument: `AAPL`
- action: `BUY`
- quantity: `1`
- type: `LMT`
- limit price: operator-selected and intentionally far from market
- no market order
- cancel immediately after accepted/open status is observed
- do not continue if the order fills unexpectedly; record exact fill facts

## Evidence Packet

Capture one timestamped evidence folder with:

- runtime-manager command envelope dry-run template
- IBKR packet manifest
- runtime-manager lifecycle schema
- operator checklist
- validator expectations
- closeout template
- frontend URL
- BFF HTTPS URL
- VM1 identity and health output
- VM2 identity and health output
- runtime binding id
- broker account mode and account ref
- order submit request/response
- cancel request/response
- telemetry event trace
- runtime-manager event excerpt
- incident or kill switch state
- operator note

Validate the packet:

```bash
python3 scripts/validate_ep5_live_order_cancel.py validate \
  --packet-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp> \
  --output-dir docs/deployment/evidence/ibkr-live-order-cancel/<timestamp>/validation
```

## Rollback and Stop

Stop staging UI exposure by unpublishing or repointing the staging Lovable app.

Stop staging VM1 BFF:

```bash
gcloud compute ssh edna@pantheon-taiwan --zone=asia-east1-b --project=pantheon-493602 -- \
  'cd /home/lupin/code/pantheon && docker compose -f docker-compose.control.yml stop operator-bff'
```

Stop staging execution:

```bash
gcloud compute ssh edna@pantheon-exec-vm2-20260424 --zone=asia-east1-a --project=pantheon-493602 -- \
  'cd /home/lupin/code/pantheon-ep5 && docker compose -p pantheon-exec -f docker-compose.exec.yml stop'
```

If a broker order remains open, cancel it in TWS first and then record the
manual cancel evidence before stopping services.
