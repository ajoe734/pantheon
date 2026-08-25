# Phase 0 VM Baseline Inventory & Measurement Evidence

- **Task ID:** `OPS-VM-ENV-PHASE0-20260825`
- **Owner:** Antigravity
- **Reviewer:** Codex
- **Date:** 2026-08-25
- **Status:** Complete (Phase 0 Baseline Established)
- **Governing Document:** [`docs/deployment/vm-dev-staging-prod-management-plan.md`](../../vm-dev-staging-prod-management-plan.md)

---

## 1. Overview

This directory contains the Phase 0 baseline measurements and operational analysis for the Pantheon multi-environment VM management rollout. The measurements were conducted on the active Dev VM (`pantheon-lupin-dev`) using non-destructive, read-only inspection to inform instance sizing, Compose service profiles, capability gap remediation, and production governance gates.

---

## 2. Artifact Index

1. **[`measurements-baseline.json`](measurements-baseline.json)**
   - Timestamped, redacted machine-readable metrics for host CPU (12 vCPUs), memory (47.04 GiB total, 30.31 GiB available [32.55 GB decimal]), disk (241.13 GiB, 88% utilized), uptime (420.2h), startup duration (35.06s system boot, observable container creation-to-start latencies of 8.5s - 15.5s), Caddy ingress memory (26.0 MiB), per-profile container memory/CPU breakdown (50 containers, 11.04 GiB total), and historical deployment durations (BFF rebuild: 2m05s / 125s benchmark, root stack run 27357842338: 10m50s / 650s workflow duration and 10m17s / 617s job duration, paired dev deploy run 32679566250: 22m32s total workflow, 18m48s switch job).

2. **[`service-inventory.md`](service-inventory.md)**
   - Complete inventory of all 70 Compose services defined across `docker-compose.yml` (67 services), `docker-compose.control.yml` (21 services), `docker-compose.exec.yml` (6 services), and `docker-compose.staging-full.yml` (15 services).
   - Categorized by profile: `core` (14 services), `workers` (17 services), `research` (24 services), `management-ai` (8 services), `execution` (7 services, including exec overlay sidecars `broker-adapter`, `exchange-adapter`, and `pantheon-lean-live`).
   - Details image/build context, exposed ports, dependency chain, and singleton ownership constraints.

3. **[`capability-gap-matrix.md`](capability-gap-matrix.md)**
   - Detailed gap analysis across 4 critical domains: FE/BFF paired release admission, DB migration expand/contract, Worker singleton lifecycle & fencing, and Backup / disaster recovery.
   - Maps current Dev capabilities against required Ephemeral Staging (Phase 2) and Low-Resource Prod (Phase 3).

4. **[`instance-sizing-recommendation.md`](instance-sizing-recommendation.md)**
   - Core peak memory calculation ($4.09 - 4.21\text{ GiB}$ / $4,184.58 - 4,314.68\text{ MiB}$ peak during blue/green BFF cutover).
   - $\ge 30\%$ memory headroom calculation ($5.84\text{ GiB}$ minimum RAM).
   - CPU peak reconciliation: reconciles 209.47% steady core CPU + 100% green BFF cutover peak (~3.1 vCPUs peak).
   - Instance sizing recommendations:
     - **Prod Control VM:** `e2-standard-2` (2 vCPU, 8.0 GiB RAM $\rightarrow 48.9\%$ memory headroom; requires bounded drift intervals) or `e2-standard-4` (4 vCPU, 16.0 GiB RAM $\rightarrow 74.5\%$ memory headroom [73.7% at upper bound peak], $>22.6\%$ cutover peak CPU headroom; recommended for unconstrained workloads).
     - **Prod Execution VM:** `e2-standard-2` (2 vCPU, 8.0 GiB RAM) or `e2-medium` (2 vCPU, 4.0 GiB RAM $\rightarrow >85\%$ headroom).
     - **Ephemeral Staging VM:** `e2-standard-2` (2 vCPU, 8.0 GiB RAM) with 2h debug TTL.
     - **Dev VM:** Retains `pantheon-lupin-dev` full-stack multi-agent workspace.
   - Recommended per-container CPU/Memory resource limits.

5. **[`production-gates.md`](production-gates.md)**
   - Formally records operator-approved vs explicitly pending gates without inference.
   - RPO (< 1h), RTO (< 30m), Real-Capital Scope (Phase 4), and On-Call Owner are explicitly recorded as **PENDING OPERATOR APPROVAL**.
   - Broker secret isolation and 30-minute observation window recorded as approved design contracts.
   - Read-only compliance certification.

6. **[`evidence.json`](evidence.json)**
   - Canonical machine-readable task execution manifest for reviewer attestation.

---

## 3. Key Baseline Summary

| Metric / Dimension | Measured Dev VM Baseline | Target Recommendation / Gate |
|---|---|---|
| **Active Running Containers** | 50 containers (11,303.08 MiB / 11.04 GiB) | Profile-isolated startup (`core` = 13 containers in Prod Control) |
| **Core Steady-State Memory** | 2,314.68 MiB (2.26 GiB) | Peak with green BFF candidate: 4.09 - 4.21 GiB (4,184.58 - 4,314.68 MiB) |
| **Prod Control Machine Type** | N/A (Dev is 12 vCPU / 48 GiB) | `e2-standard-2` (48.9% memory headroom) or `e2-standard-4` (74.5% memory headroom, unconstrained CPU headroom) |
| **Prod Execution Machine Type** | N/A (Dev co-located) | `e2-standard-2` or `e2-medium` (isolated VPC) |
| **Ephemeral Staging Lifetime** | N/A (Staging unavailable) | Release duration + 2-hour failure TTL |
| **Production RPO / RTO** | Not measured | PENDING OPERATOR APPROVAL (< 1h RPO, < 30m RTO recommended) |
| **Real-Capital Execution** | Disabled (`PANTHEON_LIVE_BROKER_ENABLED=false`) | Phase 4 Gate (requires dedicated VM + operator authorization) |
