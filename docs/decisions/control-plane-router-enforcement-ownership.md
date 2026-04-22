# Control Plane Router Enforcement Ownership

Status: draft-canonical
Last updated: 2026-04-22
Source of truth inputs:
- `docs/reviews/Pantheon_Response_to_System_Design_Open_Questions.md`
- `docs/reviews/Pantheon_Response_to_System_Design_Followup_Questions.md`
Tier: L1 Platform Architecture & Policy
Scope: command routing, approval authority, TTL, and enforcement ownership split
Conflict rule: this decision governs gateway/router/governance/domain ownership unless a narrower command-specific decision explicitly overrides one field

## Decision summary

- gateway / edge owns ingress, auth, transport shaping, and transport-level
  protection
- router owns routing decision and intent capture
- governance / promotion / relevant control surfaces own business approval
  authority
- domain services own domain command validity and command execution
- local intent classifier may exist only as degraded fallback

## Canonical ownership matrix

| Concern | Owner | Description |
|---|---|---|
| transport authn | gateway / BFF edge | request identity / session |
| ingress rate limit | gateway | traffic protection |
| domain rate limit | owning service | domain semantic throttling |
| transport TTL | gateway | request validity window |
| domain TTL | owning domain service | command / review / plan expiry |
| route selection | router | intent to target surface |
| approval authority | governance / promotion | approval, review, deploy gates |
| command execution | owning domain service | e.g. runtime-manager, promotion |
| fallback intent classifier | router degraded mode | never canonical truth |

## Detailed ownership rules

### Gateway / edge

Owns:

- authentication / authorization gatekeeping
- transport-level shaping
- request admission
- coarse traffic throttling

Does not own:

- business approval authority
- domain routing truth
- domain command validity

### Router

Owns:

- request routing decision
- intent capture
- route selection

Does not own:

- governance approval authority
- promotion authority
- final business command validity

### Governance / promotion

Owns:

- approval authority
- promotion authority
- review gating logic

### Domain services

Own:

- domain command validity
- domain TTL
- domain semantic throttling
- command execution
- command-specific idempotency handling

## Per-command required fields

Every command route should specify:

- command name
- initiating surface
- routing owner
- approval owner
- execution owner
- TTL owner
- rate-limit owner
- idempotency-key owner
- audit owner

## Local intent classifier

- may remain as degraded fallback
- must not become production canonical truth source

## Consequences

1. Gateway must not be treated as a business authority surface.
2. Router may steer traffic but must not replace governance approval logic.
3. Docs that currently blur gateway, router, governance, and domain-service
   responsibility should be rebaselined to this split.
