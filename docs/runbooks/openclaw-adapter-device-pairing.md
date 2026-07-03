# Runbook: OpenClaw adapter device pairing (assistant `openclaw` provider)

## What this is

The assistant `openclaw` provider in `openclaw-gateway-adapter` reaches the
OpenClaw gateway by running `openclaw agent --agent main --message …` as a
**remote client** over the gateway WebSocket. OpenClaw 2026.6.8 treats every
remote client as a **device** that must be paired and approved once — presenting
the gateway token alone is not enough (the connection is accepted but the turn
fails with `pairing required: device is not approved yet`).

The adapter's device identity lives in `/root/.openclaw`, persisted on the
`openclaw-adapter-data` volume so the approval survives container recreate. You
only need to run this runbook:

- the first time the adapter is deployed, or
- after the `openclaw-adapter-data` volume is wiped (e.g. `docker volume rm`).

A normal redeploy / `docker compose up -d` recreate does **not** wipe the volume,
so pairing persists.

## Symptom that means you need this

`scripts/openclaw-assistant-openclaw-live-smoke.sh` (or a Management-AI turn)
returns a degraded reply whose message contains `pairing required` /
`device is not approved yet`, while readiness still reports `ready` (readiness
checks binary + token + gateway `/readyz`, not a full turn).

## Procedure (on the dev/staging VM)

1. Trigger a pairing request by attempting one turn from the adapter (it creates
   a pending request, then fails closed):

   ```bash
   docker exec pantheon-openclaw-gateway-adapter-1 \
     openclaw agent --agent main --message "pairing probe" --json --timeout 30 || true
   ```

2. Find the pending request id on the gateway:

   ```bash
   docker exec pantheon-openclaw-gateway-1 openclaw devices list
   # look under "Pending (N)"; note the Request id and the IP (the adapter's
   # docker-bridge IP) so you approve the right device.
   ```

3. Approve it (use the request id from step 2, or `--latest`):

   ```bash
   docker exec pantheon-openclaw-gateway-1 openclaw devices approve <requestId>
   ```

4. Verify a live turn now succeeds end-to-end:

   ```bash
   bash scripts/openclaw-assistant-openclaw-live-smoke.sh   # expects sentinel OPENCLAW_LIVE
   ```

## Notes

- `OPENCLAW_ALLOW_INSECURE_PRIVATE_WS=1` (set in `docker-compose.yml`) is required
  because the adapter↔gateway hop uses plaintext `ws://` to a docker-bridge IP
  (non-loopback). This is sanctioned for trusted single-host private networks.
  If the gateway is ever exposed off-host, switch to `wss://` and drop the flag.
- To revoke the adapter's access, `docker exec pantheon-openclaw-gateway-1
  openclaw devices remove <deviceId>` (then it must be re-paired).
- The gateway's own `openclaw agent` works without this because it talks to its
  **loopback** gateway as the trusted host — only remote clients need pairing.

## Follow-up: cron.* WRITE methods need a second scope upgrade (operator.admin)

The procedure above only grants the scope the adapter's `openclaw agent` turn
path requested (`operator.write`). OpenClaw 2026.6.8's Gateway RPC scope table
scopes cron methods unevenly:

- `cron.get` / `cron.list` / `cron.status` / `cron.runs` → `operator.read`
  (satisfied by the `operator.write` grant above — this is why cron.list works
  once the base pairing runbook is done).
- `cron.add` / `cron.update` / `cron.remove` / `cron.run` → `operator.admin`.

So the BFF persona OODA-cron registrar's `cron.add` calls
(`services/control-plane/cron/persona_cron_registrar.py`,
`AdapterCronRuntime.gateway_call`) fail closed even after the base pairing
runbook, with the same-shaped error:

```
gateway connect failed: GatewayClientRequestError: scope upgrade pending approval (requestId: ...)
Gateway call failed: GatewayTransportError: gateway closed (1008): pairing required:
device is asking for more scopes than currently approved
```

Approve the scope upgrade the same way as initial pairing (`device.pair.approve`
grants only what's already pending — it cannot invent a broader grant, and
`device.token.rotate` cannot expand scope beyond what pairing already approved
either, so a fresh pending request is required):

```bash
bash scripts/openclaw-approve-adapter-cron-scope.sh
```

That script triggers a harmless `operator.admin`-scoped probe (`config.schema`,
pure read, no side effects) from the adapter container to create the pending
scope-upgrade request, finds it on the gateway, approves it, and re-verifies.
It is idempotent (a no-op if the adapter already holds the scope) and
reproducible after an `openclaw-data` volume rebuild — the persisted device
identity on `openclaw-adapter-data` re-requests the same scope automatically;
this script just re-runs the approval side.

**This is a deliberate manual/privileged step, run by an operator — it is not
wired into `docker-compose` startup.** `device.pair.approve` is a privilege
grant (widening what the adapter device may do against the shared Gateway),
and OpenClaw's own operator-scopes model treats scope upgrades as always
requiring explicit approval; baking an auto-approve into compose init would
defeat that boundary for any future scope request from this device, not just
`cron.add`.

Verify the full live path with:

```bash
bash scripts/openclaw-cron-write-scope-smoke.sh
```
