# Broker Credential Vault Readiness

Status: operational spec for `BrokerCredentialReadiness.v1`
Source: 2026-05-19 broker live activation supplement Part B5

This spec defines the evidence packet required before broker credentials can
be treated as ready for a canary or live broker activation proposal. It does
not approve activation, read secret values, rotate secrets, or start a broker
session.

## Scope

`BrokerCredentialReadiness` answers one narrow question: can the activation
packet prove that broker credentials are stage-scoped, vault-backed,
rotatable, revocable, and injectable only into the VM-2 execution environment?

It is an upstream input to the live activation criteria and operator checklist.
It is not a replacement for risk-owner approval, operator approval, broker SDK
order lifecycle evidence, capital authorization, or kill-switch drills.

## Schema

The machine-readable schema is implemented in
`services/broker/live_activation/credential_readiness.py`.

Required fields:

| Field | Meaning |
|---|---|
| `schema_version` | Must be `BrokerCredentialReadiness.v1`. |
| `source` | Human-readable source for the Part B5 rule. |
| `broker` | Broker or venue adapter family, for example `shioaji` or `ibkr`. |
| `stage` | One of `paper`, `canary`, or `live`. |
| `account_ref` | Stage-scoped account or subaccount reference; never a secret. |
| `venue_ref` | Venue or routing profile reference. |
| `vault_secret_refs` | Secret reference URIs only, such as `secret://...`. |
| `injection_target` | VM-2 execution target, for example `vm2_execution_env`. |
| `permission_scope` | Explicit broker permission set. |
| `not_shared_with_stages` | Other deployment stages this credential does not share with. |
| `rotation_interval_days` | Maximum interval between rotations; must be 90 days or less. |
| `last_rotated_at` | ISO-8601 timestamp with timezone. |
| `next_rotation_due_at` | ISO-8601 timestamp with timezone. |
| `rotation_policy_ref` | Runbook or policy ref for routine and emergency rotation. |
| `revocation_procedure_ref` | Runbook ref for disabling the credential. |
| `operator_verification_ref` | Operator or broker-admin verification evidence. |
| `entitlement_evidence_ref` | Broker entitlement/account permission evidence. |
| `sandbox_smoke_ref` | Broker sandbox/test-key smoke evidence before production side effects. |
| `status` | One of `ready`, `verified`, or `active` for readiness. |

## Fail-Closed Rules

The validator rejects readiness when any of these are true:

- raw broker secret material is present anywhere in the packet;
- `vault_secret_refs` contains a value that is not a secret reference URI;
- credential injection targets VM-1, BFF, frontend, browser, telemetry,
  artifact payloads, launch manifests, or OpenClaw;
- canary/live credentials lack `account_read`, `market_data_read`,
  `order_submit`, or `order_cancel`;
- permission scope includes broad grants such as `admin`, `all`, `root`, or
  `full_access`;
- the stage does not explicitly isolate credentials from the other deployment
  stages;
- rotation interval is longer than 90 days, timestamps are invalid, or the
  next rotation is outside the declared rotation interval;
- account, venue, rotation, revocation, operator verification, entitlement, or
  sandbox smoke fields point at secret material;
- readiness `status` is not `ready`, `verified`, or `active`.

## Stage Isolation

Credential containers must not be shared across stages:

| Stage | Required isolation statement |
|---|---|
| `paper` | `not_shared_with_stages` includes `canary` and `live`. |
| `canary` | `not_shared_with_stages` includes `paper` and `live`. |
| `live` | `not_shared_with_stages` includes `paper` and `canary`. |

This means a valid canary credential does not become a valid live credential by
inheritance. Live activation still needs a fresh account boundary, permission
review, operator verification, and capital authorization.

## Rotation And Revocation

Routine rotation cadence must be 90 days or less. Emergency rotation is
required after any operator access change, broker API compromise, permission
scope change, incident involving credential exposure, or stage boundary change.

The packet must link both:

- `rotation_policy_ref` for routine and emergency rotation;
- `revocation_procedure_ref` for immediate disablement and recovery.

The validator checks the declared timestamps only. It does not call the vault
or broker admin API.

## Allowed Secret Flow

Allowed:

1. Operator or broker admin stores broker credentials in the approved vault.
2. The readiness packet records secret refs, not values.
3. VM-2 execution environment resolves the refs at runtime.
4. Runtime Manager or broker sidecar uses the injected values inside VM-2 only.
5. Evidence packets keep account refs, entitlement refs, smoke refs, and audit
   refs, but never raw secrets.

Forbidden:

- storing broker credentials in VM-1, BFF, frontend, Lovable, browser config,
  telemetry payloads, artifacts, launch manifests, or OpenClaw memory;
- committing `.env` files or secret values;
- reusing a paper or canary credential as live authorization;
- treating `BrokerCredentialReadiness` as approval to activate production live
  broker execution.
