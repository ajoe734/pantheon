# EP5 Canary-Ready Entry Path

Status: prerequisite bundle only; does not claim EP5 canary/live proof

This directory materializes `EP5-001` as executable repo artifacts.

It prepares the entry path that sits between the archived `EP4` governed paper
packet and a later human-gated `EP5-002` proof run:

- real broker / venue config boundary
- scaled canary capital gate
- runnable operator approval checklist
- runnable rollback drill harness

## What This Bundle Does

1. gives operators a VM-2 scoped canary env template at
   `env/canary-exec.env.example`
2. gives reviewers a concrete config-boundary document in
   `broker-venue-config-boundary.md`
3. gives operators a stepwise checklist in `operator-approval-checklist.md`
4. gives the repo one runnable entrypoint at
   `scripts/run_ep5_canary_readiness.py`
5. gives operators one helper to materialize VM-2 env files from Secret
   Manager via `scripts/materialize_exec_env_from_secret_manager.py`
6. gives closeout owners an evidence scaffold in `evidence-packet-template.md`

## What This Bundle Does Not Prove

This directory does not raise the repo beyond stable `EP4`.

It does not claim:

- real broker acknowledgement or fills
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
| `evidence-packet-template.md` | defines the minimum archive layout for the later `EP5-002` packet |
| `env/canary-exec.env.example` | repo-local template for canary readiness variables and secret names |
| `scripts/run_ep5_canary_readiness.py` | validates readiness, emits a canary DeploymentPlan artifact, and rehearses the rollback drill |
| `scripts/materialize_exec_env_from_secret_manager.py` | resolves Secret Manager refs into a machine-local VM-2 env file without tracking raw credentials |

## Recommended Flow

```bash
cp env/canary-exec.env.example env/canary-exec.env

python3 scripts/materialize_exec_env_from_secret_manager.py \
  --template env/canary-exec.env.example \
  --output env/canary-exec.env \
  --project pantheon-493602 \
  --generate-runtime-manager-token

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
  run-rollback-drill \
  --env-file env/canary-exec.env \
  --binding-id rb-canary-active-001 \
  --dry-run \
  --output-dir /tmp/pantheon/ep5-canary-ready/drill
```

Use `--dry-run` until a human gate and real canary infrastructure are available.

## Proof Boundary

The outputs produced here are readiness artifacts only. They are acceptable
closeout evidence for `EP5-001` because they prepare the path, but they must
not be cited as the first `EP5` proof packet.
