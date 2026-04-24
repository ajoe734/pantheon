# Broker And Venue Config Boundary For EP5-001

This note defines the concrete boundary for the canary-ready execution path.

## Boundary Rule

Broker / venue secrets remain VM-2 only.

VM-1 and repo-tracked docs may know:

- public runtime-manager URL
- public telemetry ingest URL
- broker account reference
- venue reference
- secret names

VM-1 and repo-tracked docs must not hold:

- raw broker API keys
- raw broker API secrets
- raw exchange API keys
- raw exchange API secrets

This keeps `EP5-001` aligned with the existing execution-only secret model from
`docs/deployment/exec-vm-secrets-guide.md`.

## Governed US Datasource Boundary

For the current EP5 canary-prep slice, the truthful US path is:

| Plane | Provider | Truth boundary |
|---|---|---|
| Execution broker | `IBKR` | broker intents, account boundary, execution-sync quote fallback, fill reconciliation |
| Research / historical ingest | `Massive / Polygon` | governed market-data ingest for US history, aggregates, and corporate-actions-adjacent normalization inputs |
| Temporary market-data fallback | `IBKR market data` | allowed only while `Massive / Polygon` activation is still incomplete; do not treat as the long-term research-grade primary |

Guardrails that follow from this split:

- `IBKR` credentials remain VM-2 only and are execution-owned.
- `Massive / Polygon` API credentials also remain VM-2 only; repo docs may record only secret names and provider refs.
- Operator-facing canary packets must describe whether a run used `Massive / Polygon` ingest or the temporary `IBKR market data` fallback.
- Research-grade US datasets should be normalized into data-plane `internal_can` artifacts before downstream production use.

## Required Operator-Owned Inputs

The canary env template expects the following inputs to exist before any real
canary rehearsal:

| Variable | Why it matters |
|---|---|
| `PANTHEON_EXECUTION_MODE=canary` | makes the intended deployment stage explicit |
| `BROKER_ADAPTER_MODE=real` | prevents canary rehearsal from silently using mock broker semantics |
| `EXCHANGE_ADAPTER_MODE=real` | prevents canary rehearsal from silently using mock venue semantics |
| `PANTHEON_SECRETS_OPTIONAL=false` | blocks bring-up if real secret material is missing |
| `CANARY_BROKER_ACCOUNT_REF` | identifies the broker account / subaccount boundary |
| `CANARY_VENUE_REF` | identifies the venue or routing profile boundary |
| `BROKER_API_*` and `EXCHANGE_API_*` or their secret-name refs | ties the boundary to VM-2 secret injection only |
| `US_MARKET_DATA_PROVIDER=polygon` or equivalent provider ref | records that US research/history comes from the governed `Massive / Polygon` lane when enabled |
| `US_MARKET_DATA_SECRET_REF` | points to the VM-2-only secret name for the market-data vendor |

## Capital Gate Boundary

`EP5-001` stops at the canary gate, not the full live envelope.

The entry path therefore requires:

- `0 < capital_scale_pct <= 5`
- `0 < gross_scale_pct <= 25`
- an explicit rollback target artifact
- an explicit rollback action type

These limits come from `PAPER_CANARY_LIVE_POLICY.md` and
`services/control-plane/governance/deployment_plan.contract.md`.

## Runtime Boundary Note

The current repo already models `paper`, `canary`, `live`, and `frozen` in the
governance and runtime-binding layers. This bundle uses those existing stage
semantics and runtime-manager endpoints.

It does not claim that the repo already has the final truthful canary runtime
package or broker-fill proof. That later proof still belongs to `EP5-002`.
