# Pantheon Environment Closure SA/SD Specification (2026-09)

- Status: Canonical Architecture & System Design Closure
- Date: 2026-09-04
- Baseline: `origin/dev`
- Task: `ENV-STAGING-PROD-PLAN-001`
- Parent Audit & Spec: `pkt-pantheon-structural-closure-functional-v2-20260903` (`GAP-ENV-06`, `SA §3.2`, `SD §10`)
- Delivery Target: `docs/deployment/vm-dev-staging-prod-management-plan.md`, `docs/04/pantheon_environment_closure_sa_sd_2026-09/**`

---

## 1. Executive Summary

This specification establishes the canonical System Architecture (SA) and System Design (SD) for closing the multi-environment gap across **Dev**, **Staging**, and **Production** in the Pantheon platform.

In accordance with `ENV-STAGING-PROD-PLAN-001`, this package:
1. **Re-verifies and enforces the current unavailability of Staging and Production**: Staging is ephemeral and currently unprovisioned (`status: unavailable`); Production is currently unprovisioned (`status: unavailable`). No retired environment (`pantheon-benjamin-20260528`, released IP `104.155.223.192`) may be reused.
2. **Defines the Target Topology**:
   - **Dev**: Single permanent VM (`pantheon-lupin-dev`) in `pantheon-lupin-dev-20260719` for rapid integration.
   - **Ephemeral Staging**: Provisioned on-demand per release candidate, strictly time-bounded (TTL), verified with sanitized data, and automatically destroyed (zero persistent idle VMs).
   - **Production**: Segregated into **Prod Control VM** (Caddy reverse proxy, single-host blue/green request-facing candidate, persistent disk) and **Prod Execution VM** (private VPC only, no public ingress, hosting runtime-manager, broker adapters, execution telemetry, and broker secrets).
3. **Establishes Authority, Threat, Resource/Cost, Promotion, and Rollback Models**:
   - Explicit authority boundaries separating product runtime, development tooling, and delivery infrastructure.
   - Strict network and credential segregation preventing live-capital exposure or cross-environment data leakage.
   - Verified resource sizing based on Phase 0 live measurements (`e2-standard-2` / `e2-standard-4` with $\ge 30\%$ memory headroom).
   - Exact-pair release manifest (`release_id`, frontend commit + artifact checksum, backend commit + image digest) with baseline-before-switch and atomic rollback.
4. **Materializes Implementation as Separately Privileged Future Packets**:
   - Implementation work is decomposed into three distinct, privileged packets (`ENV-STG-EPHEMERAL-IMPL-001`, `ENV-PROD-CONTROL-IMPL-001`, `ENV-PROD-EXEC-ISOLATE-001`).
   - All future packets remain **undispatched** and **unprovisioned** pending explicit operator MFA authorization and formal governance sign-off.
5. **Enforces Zero Side Effects**:
   - This task is strictly documentation and architecture design. No cloud resources, credentials, ingress rules, production data paths, or live-capital capabilities are created.

---

## 2. Artifact Directory Structure

The environment closure package contains the following authoritative documents:

| File | Purpose | Key Content |
|---|---|---|
| [`INDEX.md`](INDEX.md) | Package Index & Navigation | Overview, governance rules, document roadmap |
| [`REPORT.md`](REPORT.md) | Unavailability Audit & Repudiation | Current environment probe results, IP reallocation evidence, deletion of retired reuse assumptions |
| [`SA.md`](SA.md) | System Architecture | Architectural invariants, three-plane separation, environment topologies, domain boundaries |
| [`SD.md`](SD.md) | System Design | Ephemeral staging lifecycle, Prod Control blue/green, Prod Execution private isolation, profile definitions |
| [`AUTHORITY_AND_THREAT_MODEL.md`](AUTHORITY_AND_THREAT_MODEL.md) | Authority & Threat Model | Operator gates, GitHub Environment constraints, WIF/IAM, network segregation, broker credential confinement |
| [`RESOURCE_AND_COST_MODEL.md`](RESOURCE_AND_COST_MODEL.md) | Resource & Cost Model | Sizing from Phase 0 measurements, memory headroom, zero-idle staging economics, quota and alert rules |
| [`PROMOTION_AND_ROLLBACK_SPEC.md`](PROMOTION_AND_ROLLBACK_SPEC.md) | Promotion & Rollback Spec | Immutable release manifest schema, state machine, baseline-before-switch, dual-endpoint switch compensation, database migration protocol |
| [`FUTURE_PACKETS.md`](FUTURE_PACKETS.md) | Future Implementation Packets | Formal task specs for `ENV-STG-EPHEMERAL-IMPL-001`, `ENV-PROD-CONTROL-IMPL-001`, `ENV-PROD-EXEC-ISOLATE-001` (undispatched) |
| [`TRACEABILITY.md`](TRACEABILITY.md) | Requirements Traceability | Mapping to `GAP-ENV-06`, `SA §3.2`, `SD §10`, `EXECUTION_TASKS.md`, and acceptance criteria |

---

## 3. Normative Rules & Non-Negotiable Constraints

1. **Unavailability Is Truth**: Development test passes, PR merges, or historical documentation do NOT constitute staging or production readiness. Staging and production remain explicitly `UNAVAILABLE`.
2. **Mandatory Repudiation of Suspended Project & Released IPs**:
   - `pantheon-benjamin-20260528` is suspended and dead.
   - `104.155.223.192` has been confirmed reallocated by GCP to an unrelated third party (Kubernetes cluster). Routing traffic to it is forbidden.
   - Static staging VMs (`pantheon-lupin-staging-control`, `pantheon-lupin-staging-exec`) are retired.
3. **Zero Provisioning in Planning Phase**: No worker, script, or automated tool may provision GCP infrastructure, create IAM keys, or modify DNS/CORS records under this planning task.
4. **Separation of Concerns**:
   - Development Tooling (`V2 TaskStore`, supervisor, auto-workers) does not deploy to production.
   - Product Runtime (BFF, OpenClaw, Agora) does not manage git worktrees or cloud provisioning.
   - Delivery Infrastructure (GitHub Actions, WIF, gcloud) enforces exact-pair admission and rollback baselines.
5. **No Live-Capital Side Effects**: Live broker credentials (`IBKR/TWS`, exchange API keys) are strictly restricted to the physically isolated Prod Execution VM (Phase 4), blocked by default, and require explicit operator authorization.
