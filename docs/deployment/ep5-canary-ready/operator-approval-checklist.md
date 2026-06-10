# Operator Approval Checklist For EP5-001

This checklist is the operator-facing runbook for the prerequisite-only
`EP5-001` slice.

## 1. Prepare The VM-2 Env File

```bash
cp env/canary-exec.env.example env/canary-exec.env
chmod 600 env/canary-exec.env
```

Fill in the machine-local file with:

- real broker / exchange secrets on VM-2 only
- broker account ref and venue ref
- governed provider refs for `IBKR`, `Shioaji`, `Kraken`, `FinMind`, and optional `TEJ` gap-fill
- the chosen US market-data provider ref (`Massive / Polygon` when enabled, otherwise explicit `IBKR market data` fallback)
- approval, pool, persona-binding, and fallback artifact refs
- promotion gate refs: human-gate packet, broker sandbox smoke packet, Shioaji sandbox evidence packet, risk-owner approval, and operator approval

## 2. Run The Readiness Checklist

```bash
python3 scripts/run_ep5_canary_readiness.py \
  run-operator-checklist \
  --env-file env/canary-exec.env \
  --check-health \
  --output-dir /tmp/pantheon/ep5-canary-ready/checklist
```

Expected outcome:

- config boundary passes
- canary capital gate passes
- runtime-manager health passes
- broker / exchange sidecar health passes when published
- governed datasource provider matrix is recorded truthfully in the output bundle
- output bundle contains `operator-checklist.json`

## 3. Run The Provider Smoke Validation

```bash
python3 scripts/run_ep5_canary_readiness.py \
  run-datasource-smoke \
  --env-file env/canary-exec.env \
  --output-dir /tmp/pantheon/ep5-canary-ready/datasource-smoke
```

Expected artifacts:

- `datasource-smoke.json`
- `summary.json`

Review before any later human gate:

- `IBKR` order and market-data payloads are materialized from the env-backed boundary
- `Shioaji` order and quote-subscription payloads are materialized from the env-backed boundary
- `Kraken` order and venue quote payloads are materialized from the env-backed boundary
- `FinMind` dataset normalization stays on the primary `research_grade` vendor boundary
- `TEJ` dataset normalization, when configured, stays on the optional historical gap-fill boundary

## 3A. Run The Read-Only Market-Data Credential Smoke

Run this on VM-2, or another credentialed runtime, when provider credentials or
quote readback files are available:

```bash
python3 scripts/run_marketdata_credential_smoke.py \
  --env-file env/canary-exec.env \
  --allow-network \
  --output-dir /tmp/pantheon/ep5-canary-ready/marketdata-credential-smoke
```

Expected artifacts:

- one JSON packet for each governed read-only provider:
  `Massive / Polygon`, `TWSE`, `TPEx`, `MOPS`, `FinMind`, `TEJ`, `CoinGecko`,
  `Kraken`, `IBKR`, and `Shioaji`
- `summary.json`

Review before any later human gate:

- credentialed providers show `read_ok` or explicit unavailable-credential
  evidence without raw secret material
- public official/reference providers show `read_ok` or explicit read-unavailable
  evidence
- every provider packet includes non-secret `rate_limit` / quota evidence and
  `session_provenance`; absent headers, disabled network, or repo-local quote
  readback files must record an explicit unavailable / not-observed reason
- `IBKR`, `Shioaji`, and `Kraken` order paths remain disabled in this smoke;
  broker order API evidence belongs to `P2-BROKER-SANDBOX-ORDER-001`

## 4. Emit The Canary DeploymentPlan Artifact

```bash
python3 scripts/run_ep5_canary_readiness.py \
  emit-canary-plan \
  --env-file env/canary-exec.env \
  --output-dir /tmp/pantheon/ep5-canary-ready/plan
```

Expected artifacts:

- `canary-deployment-plan.json`
- `canary-execution-projection.json`
- `summary.json`

Review before any later human gate:

- `target_stage = canary`
- `capital_scale_pct <= 5`
- `gross_scale_pct <= 25`
- `rollback.action_type = pause_then_replace` unless a stricter rule is approved
- fallback artifact refs are present

## 5. Rehearse The Rollback Drill

Dry-run first:

```bash
python3 scripts/run_ep5_canary_readiness.py \
  run-rollback-drill \
  --env-file env/canary-exec.env \
  --binding-id rb-canary-active-001 \
  --dry-run \
  --output-dir /tmp/pantheon/ep5-canary-ready/drill
```

Real rehearsal only after human gate:

```bash
python3 scripts/run_ep5_canary_readiness.py \
  run-rollback-drill \
  --env-file env/canary-exec.env \
  --binding-id rb-canary-active-001 \
  --output-dir /tmp/pantheon/ep5-canary-ready/drill
```

Expected real-drill outcomes:

- kill-switch leaves the pool in `paused`
- rollback response returns `action_type = pause_then_replace`
- old binding becomes `retired`
- replacement binding becomes `active`
- archived drill output contains request/response JSON and `summary.json`

## 6. Degraded-Path Fallback

If the BFF is unavailable, operators may still rehearse the same boundary via
the admin CLI / internal API path already documented elsewhere.

Example dry-run commands:

```bash
python3 tools/pantheon_admin/cli.py --dry-run deployment approve plan-canary-001

python3 tools/pantheon_admin/cli.py --dry-run rollback execute rb-canary-active-001 \
  --target-type runtime \
  --rollback-to-version 1.1.9 \
  --action-type pause_then_replace \
  --verify-before-executing

python3 tools/pantheon_admin/cli.py --dry-run kill-switch activate \
  --scope pool \
  --scope-id pool-canary-001 \
  --severity high \
  --force
```

## 7. Scope Guardrail

Closing this checklist does not mean `EP5` is achieved.

It means:

- the repo has a prepared canary entry path
- operator-owned prerequisites are explicit
- rollback rehearsal commands are archived

It does not mean:

- the repo has a first canary/live proof packet
- real broker fills or slippage are proven
- operator signoff under live conditions is complete
