#!/usr/bin/env bash
# Grant the openclaw-gateway-adapter device the operator.admin scope it needs
# for cron.add / cron.update / cron.remove / cron.run.
#
# Why: OpenClaw 2026.6.8's Gateway RPC scope table (core-descriptors) scopes
# cron.get / cron.list / cron.status / cron.runs as operator.read, but
# cron.add / cron.update / cron.remove / cron.run as operator.admin. The
# adapter's paired device (see docs/runbooks/openclaw-adapter-device-pairing.md)
# only ever requested/received operator.write during initial pairing, so
# cron.list works live while cron.add fails closed with:
#   GatewayClientRequestError: scope upgrade pending approval (requestId: ...)
#   GatewayTransportError: gateway closed (1008): pairing required: device is
#   asking for more scopes than currently approved
#
# This is a one-time (per openclaw-data volume lifetime) privileged grant: it
# widens what the adapter device is allowed to do on the shared Gateway. Run it
# deliberately, not as part of unattended automation.
#
# Approving is deliberately a MANUAL, explicit action, not baked into compose
# startup — device.pair.approve is a privilege grant, and scope upgrades must
# stay an explicit human/operator decision (see docs/gateway/operator-scopes.md
# in the openclaw-gateway image). This script only makes that decision fast
# and reproducible so it does not depend on tribal knowledge of the CLI shape.
#
# Reproducible across an `openclaw-data` volume rebuild: rerun this script; it
# re-triggers a fresh pairing request (old request ids expire after ~5 min)
# and re-approves it, so the adapter never has to stay stuck read-only.
#
# Usage:
#   bash scripts/openclaw-approve-adapter-cron-scope.sh
set -euo pipefail

GATEWAY_CONTAINER="${OPENCLAW_GATEWAY_CONTAINER:-pantheon-openclaw-gateway-1}"
ADAPTER_CONTAINER="${OPENCLAW_ADAPTER_CONTAINER:-pantheon-openclaw-gateway-adapter-1}"
GATEWAY_WS_URL="${OPENCLAW_GATEWAY_WS_URL:-ws://openclaw-gateway:18789}"

_extract_json() {
  # Mirrors assistant_openclaw_provider._extract_gateway_json: the CLI prints
  # banner/doctor noise before pretty-printed JSON.
  python3 -c '
import json, sys
text = sys.stdin.read()
dec = json.JSONDecoder()
for i, ch in enumerate(text):
    if ch not in "{[":
        continue
    try:
        val, _ = dec.raw_decode(text[i:])
    except json.JSONDecodeError:
        continue
    print(json.dumps(val))
    break
else:
    print("{}")
'
}

echo "=== 1/4 check current adapter device scope (harmless admin-scoped read) ==="
PROBE_OUT="$(docker exec "$ADAPTER_CONTAINER" sh -c \
  'openclaw gateway call config.schema --url "'"$GATEWAY_WS_URL"'" --token "$OPENCLAW_GATEWAY_TOKEN" --json' 2>&1 || true)"

if ! printf '%s' "$PROBE_OUT" | grep -q "pairing required\|scope upgrade pending"; then
  echo "OK: adapter device already has operator.admin (or an equivalent) scope; nothing to approve."
  exit 0
fi

echo "Adapter requested a scope upgrade (expected on first run / after a volume rebuild)."

echo "=== 2/4 find the adapter's pending operator.admin request ==="
DEVICES_JSON="$(docker exec "$GATEWAY_CONTAINER" sh -c 'openclaw devices list --json' 2>&1 | _extract_json)"
REQUEST_ID="$(python3 -c '
import json, sys
d = json.loads(sys.argv[1])
for p in d.get("pending", []):
    if "operator.admin" in (p.get("scopes") or []):
        print(p.get("requestId", ""))
        break
' "$DEVICES_JSON")"

if [ -z "$REQUEST_ID" ]; then
  echo "FAIL: no pending operator.admin request found on the gateway." >&2
  echo "      devices list output: $DEVICES_JSON" >&2
  exit 1
fi
echo "Pending request: $REQUEST_ID"

echo "=== 3/4 approve the scope upgrade ==="
docker exec "$GATEWAY_CONTAINER" sh -c "openclaw devices approve '$REQUEST_ID' --json"

echo "=== 4/4 verify the adapter device now holds operator.admin ==="
VERIFY_OUT="$(docker exec "$ADAPTER_CONTAINER" sh -c \
  'openclaw gateway call config.schema --url "'"$GATEWAY_WS_URL"'" --token "$OPENCLAW_GATEWAY_TOKEN" --json' 2>&1 || true)"
if printf '%s' "$VERIFY_OUT" | grep -q "pairing required\|scope upgrade pending"; then
  echo "FAIL: adapter device still cannot reach operator.admin-scoped RPCs." >&2
  echo "      output: $VERIFY_OUT" >&2
  exit 1
fi
echo "OK: adapter device holds operator.admin scope; cron.add/update/remove/run are now reachable."
