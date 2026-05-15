# Review: SVC-STAGING-DUAL-VM-COMPOSE-CONTRACT

Reviewer: Claude
Date: 2026-04-30
Outcome: approved

## Acceptance Criteria Evaluation

| Criterion | Result | Evidence |
|---|---|---|
| Default compose labelled and verified as dev single-VM baseline | Pass | `x-pantheon-compose-contract.topology: dev-single-vm-baseline` in docker-compose.yml; validator grep + required services (operator-bff, runtime-manager, governance, deployment, telemetry, signal-store) all pass |
| Control compose excludes execution and broker secrets, includes required control-plane services | Pass | `secret_boundary: no-broker-or-exchange-secrets`; validator forbids runtime-manager/broker-adapter/exchange-adapter/paper-runtime/signal-store/router; validator requires operator-bff/telemetry/governance/deployment/incidents/postmortems/capital/evolution/lineage-read/registry/persona — all confirmed in docker-compose.control.yml |
| Exec compose excludes BFF/control services, hosts runtime-manager/broker-adapter/exchange-adapter/paper-runtime | Pass | `control_plane_boundary: no-bff-or-governance-services`; validator requires signal-store/runtime-manager/broker-adapter/exchange-adapter/pantheon-paper-runtime; forbids operator-bff/persona/registry/governance/telemetry/incidents/postmortems/capital/evolution/evaluation/feedback/memory/optimizer-svc/deployment; pantheon-lean-live gated behind `live` profile only |
| Control env example wires runtime-manager to VM2 internal URL without broker secrets | Pass | `PANTHEON_RUNTIME_MANAGER_URL=http://10.140.0.5:28081`; `PANTHEON_INTERNAL_API_URL=http://10.140.0.5:28081`; no BROKER_API_KEY/BROKER_API_SECRET/EXCHANGE_API_KEY/EXCHANGE_API_SECRET/SHIOAJI_*/KRAKEN_*/TEJ_* entries |
| Topology docs and compose comments no longer contradict governance or runtime placement | Pass | staging-live-topology.md clearly documents VM1=control/BFF, VM2=execution/broker, separation rules, and verification command; compose header comments align |
| Compose config and split-topology verification script pass | Pass | Sidecar evidence confirms docker compose config --quiet exit 0 for root/control/exec; `bash scripts/validate_split_topology.sh` outputs "ok  dev single-VM and staging dual-VM compose contract validated" |

## Validator Coverage

The `scripts/validate_split_topology.sh` script provides strong machine-verifiable coverage:
- Service presence/absence checks for all required and forbidden services in each compose slice
- Env key value checks for BFF staging-live settings, runtime-manager URL pointing to VM2, CORS origins
- Env key forbid checks for all broker/exchange secret key names in VM1
- Env key value checks for VM2 telemetry pointing back to VM1

## Notes

- `PANTHEON_RUNTIME_MANAGER_TOKEN=replace-me-runtime-manager-token` in prod-control.env.example is intentionally a placeholder; `require_env_nonempty` check passes because the value is non-empty. Operator must replace before production deployment. This is correct for an example file.
- `pantheon-lean-live` in docker-compose.exec.yml is correctly gated behind the `live` profile and does not start by default, so the validator's `forbid_service "$CONTROL_JSON"` check for it in VM1 is not needed in the exec context.
- Firewall and ingress are intentionally not exposed yet; this is documented in staging-live-topology.md as a next step.

## Disposition

All 6 acceptance criteria met. Review approved. Returning to Codex for closeout.
