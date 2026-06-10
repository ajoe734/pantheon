# EP5 Canary-Ready Entry Path

Status: prerequisite bundle only; does not claim EP5 canary/live proof

This directory materializes `EP5-001` as executable repo artifacts.

It prepares the entry path that sits between the archived `EP4` governed paper
packet and a later human-gated `EP5-002` proof run:

- real broker / venue config boundary
- broker paper-account / sandbox / test-key order API smoke requirement
- truthful governed datasource boundary for `IBKR`, `Shioaji`, `Kraken`, `FinMind`, and optional `TEJ` gap-fill
- scaled canary capital gate
- explicit runtime-manager promotion gate refs for canary activation
- runnable operator approval checklist
- runnable provider smoke validation
- runnable rollback drill harness
- a machine-readable human-gate packet manifest for closeout and reviewer replay

## What This Bundle Does

1. gives operators a VM-2 scoped canary env template at
   `env/canary-exec.env.example`
2. gives reviewers a concrete config-boundary document in
   `broker-venue-config-boundary.md`
3. gives operators a stepwise checklist in `operator-approval-checklist.md`
4. gives the repo one runnable entrypoint at
   `scripts/run_ep5_canary_readiness.py`
5. feeds the repo-authoritative closeout packet at
   `docs/deployment/app-003-openclaw-closeout-packet.md`

## What This Bundle Does Not Prove

This directory does not raise the repo beyond stable `EP4`.

It does not claim:

- real broker acknowledgement or fills
- completed broker sandbox/test-key order API smoke unless an operator-owned
  packet is archived next to the canary evidence
- slippage / reject / partial-fill evidence
- a production-grade canary runtime package
- operator signoff completion
- first canary/live proof

Those still belong to later gated `EP5` proof work.

## Bundle Layout

| Artifact | Role |
|---|---|
| `broker-venue-config-boundary.md` | documents the VM-2 only broker/venue boundary and required operator-owned metadata |
| `operator-approval-checklist.md` | tells operators which commands to run and what evidence to archive |
| `env/canary-exec.env.example` | repo-local template for canary readiness variables and secret names |
| `scripts/run_ep5_canary_readiness.py` | validates readiness, emits a canary DeploymentPlan artifact, and rehearses the rollback drill |
| `docs/deployment/app-003-openclaw-closeout-packet.md` | consolidates the operator packet, OpenClaw runtime boundary, and event-trace gap disposition for APP-003 closeout |

## Recommended Flow

```bash
cp env/canary-exec.env.example env/canary-exec.env

python3 scripts/run_ep5_canary_readiness.py \
  run-operator-checklist \
  --env-file env/canary-exec.env \
  --check-health \
  --output-dir /tmp/pantheon/ep5-canary-ready/checklist

python3 scripts/run_ep5_canary_readiness.py \
  emit-canary-plan \
  --env-file env/canary-exec.env \
  --output-dir /tmp/pantheon/ep5-canary-ready/plan

python3 scripts/run_ep5_canary_readiness.py \
  run-datasource-smoke \
  --env-file env/canary-exec.env \
  --output-dir /tmp/pantheon/ep5-canary-ready/datasource-smoke

python3 scripts/run_ep5_canary_readiness.py \
  run-rollback-drill \
  --env-file env/canary-exec.env \
  --binding-id rb-canary-active-001 \
  --dry-run \
  --output-dir /tmp/pantheon/ep5-canary-ready/drill

python3 scripts/run_ep5_canary_readiness.py \
  emit-human-gate-packet \
  --checklist-json docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z/operator-checklist.json \
  --datasource-summary-json /tmp/pantheon/ep5-canary-ready/datasource-smoke/summary.json \
  --plan-json docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z/canary-deployment-plan.json \
  --drill-summary-json docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z/rollback-drill-summary.json \
  --broker-smoke-summary-json /tmp/pantheon/ep5-broker-tw-002/sandbox-smoke/summary.json \
  --shioaji-evidence-packet-json /tmp/pantheon/ep5-broker-tw-002/shioaji-sandbox-evidence-packet.json \
  --dual-vm-evidence-dir docs/deployment/evidence/ep5-dual-vm-local/20260424T143020Z \
  --event-trace-status packetized \
  --event-trace-note "Replay-clean event-trace projection evidence still needs a dedicated capture; use the closeout packet for the current gap disposition." \
  --output-dir /tmp/pantheon/ep5-canary-ready/human-gate
```

Use `--dry-run` until a human gate and real canary infrastructure are available.

Do not interpret `--dry-run` as a reason to defer broker API integration.
Broker paper-account, sandbox, simulation, validate-only, or test-key order
smoke should be captured before any production live order/cancel packet.
For the Shioaji broker-side adapter, use
`services/broker/shioaji/sandbox_smoke.py` and feed its `summary.json` into
`emit-human-gate-packet` with `--broker-smoke-summary-json`; feed the
corresponding Shioaji sandbox evidence packet with
`--shioaji-evidence-packet-json`.
Runtime-manager canary activation now requires that broker smoke packet ref,
the Shioaji sandbox evidence packet ref, the human-gate packet ref, risk-owner
approval, operator approval, and policy scale values before it will create a
forward canary `RuntimeBinding`. Live activation remains stricter and
additionally requires a canary observation ref.

The local `run-rollback-drill --dry-run` output is a payload rehearsal only.
Its `summary.json` stays `prepared`, so feeding that file into
`emit-human-gate-packet` yields `incomplete` by design. A
`ready_for_review` human-gate packet must point at an executed rollback drill
summary such as the archived dual-VM evidence bundle above.

## Proof Boundary

The outputs produced here are readiness artifacts only. They are acceptable
closeout evidence for `EP5-001` and datasource-ops bring-up because they
prepare the path, but they must not be cited as the first `EP5` proof packet.

For the current APP-003 closeout, the event-trace read-model surface is
explicitly packetized in `docs/deployment/app-003-openclaw-closeout-packet.md`
instead of being silently treated as closed.
