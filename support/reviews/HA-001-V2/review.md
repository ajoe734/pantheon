# Review: HA-001-V2 — BFF HA topology doc (Part D2)

Reviewer: Claude
Date: 2026-05-19
Artifact: docs/bff/bff_ha_topology.md
Tests: tests/docs/test_bff_ha_topology_doc.py

## Verdict: APPROVED

## Checklist

- [x] Mermaid diagram covers all required components: Client/UI, HTTPS LB, 3 BFF replicas (A/B/C), Auth/OIDC/JWKS, Shared Idempotency + Audit Store, SSE Event Source/Fanout, Registry/Governance, Runtime Manager, Telemetry/Incident.
- [x] All replica → shared-store edges present (LB→BFFn, BFFn→Store, BFFn→SSE, BFFn→RegistryGov, BFFn→Runtime, BFFn→Telemetry).
- [x] Component responsibilities table is complete with "Must not own" column aligned with BFF boundary policy.
- [x] Request flow section documents Reads, Commands, and SSE reconnect semantics correctly.
- [x] Fail-closed boundaries table documents all 6 conditions: IDEMPOTENCY_UNAVAILABLE, AUDIT_UNAVAILABLE, REGISTRY_GOVERNANCE_UNAVAILABLE, RUNTIME_MANAGER_UNAVAILABLE, TELEMETRY_UNAVAILABLE, and SSE fanout expired.
- [x] "No command dispatch" posture correctly applied to idempotency, audit, registry, and runtime unavailability.
- [x] Document correctly scopes itself as a pre-gate production topology artifact: "per BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md, current compose deployments may remain single-replica until the HA PoC, evidence, and HA-PROD-001-V2 human gate are approved."
- [x] Delivery boundary section cleanly enumerates follow-up tasks (HA-002-V2 through HA-PROD-001-V2).
- [x] No compose, deployment, L1 canonical policy, or production cutover change introduced.
- [x] Doc tests pass: `pytest tests/docs/test_bff_ha_topology_doc.py -q` → 2 passed.

## L1 Policy Alignment

`BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` § 0 explicitly defers the multi-replica plus LB production topology until the re-entry gate is approved. This document is correctly scoped as a planning/blueprint artifact that documents the target topology without materializing it. The final line confirms: "No compose, deployment, L1 canonical policy, or production cutover change is introduced by this document."

The fail-closed boundary design aligns with § 5 (degradation strategy) — typed rejection instead of silent best-effort fallback.

## No Required Changes

The artifact is complete, accurate, and properly bounded. Returning to Codex2 for closeout.
