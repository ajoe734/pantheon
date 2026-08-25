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
   - Timestamped, redacted machine-readable metrics for host CPU (12 vCPUs), memory (47.04 GiB total, 30.48 GiB available), disk (242 GiB, 88% utilized), uptime (420.2h), Caddy ingress memory (26.0 MiB), per-profile container memory/CPU breakdown, and historical deployment durations.

2. **[`service-inventory.md`](service-inventory.md)**
   - Complete inventory of all 67 Compose services in `docker-compose.yml` (and overlays `docker-compose.control.yml`, `docker-compose.exec.yml`, `docker-compose.staging-full.yml`).
   - Categorized by profile: `core` (14 services), `workers` (17 services), `research` (24 services), `management-ai` (8 services), `execution` (4 services).
   - Details image/build context, exposed ports, dependency chain, and singleton ownership constraints.

3. **[`capability-gap-matrix.md`](capability-gap-matrix.md)**
   - Detailed gap analysis across 4 critical domains: FE/BFF paired release admission, DB migration expand/contract, Worker singleton lifecycle & fencing, and Backup / disaster recovery.
   - Maps current Dev capabilities against required Ephemeral Staging (Phase 2) and Low-Resource Prod (Phase 3).

4. **[`instance-sizing-recommendation.md`](instance-sizing-recommendation.md)**
   - Core peak memory calculation ($4.15\text{ GiB}$ peak during blue/green BFF cutover).
   - $\ge 30\%$ memory headroom calculation ($5.93\text{ GiB}$ minimum RAM).
   - Instance sizing recommendations:
     - **Prod Control VM:** `e2-standard-2` (2 vCPU, 8 GiB RAM $\rightarrow 49.3\%$ headroom).
     - **Prod Execution VM:** `e2-standard-2` (2 vCPU, 8 GiB RAM) or `e2-medium` (2 vCPU, 4 GiB RAM $\rightarrow >85\%$ headroom).
     - **Ephemeral Staging VM:** `e2-standard-2` (2 vCPU, 8 GiB RAM) with 2h debug TTL.
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
| **Active Running Containers** | 50 containers | Profile-isolated startup (`core` = 13 containers in Prod Control) |
| **Core Steady-State Memory** | 2,152.89 MiB (2.10 GiB) | Peak with green BFF candidate: ~4.15 GiB |
| **Prod Control Machine Type** | N/A (Dev is 12 vCPU / 48 GiB) | `e2-standard-2` (2 vCPU, 8 GiB RAM, 49.3% headroom) |
| **Prod Execution Machine Type** | N/A (Dev co-located) | `e2-standard-2` or `e2-medium` (isolated VPC) |
| **Ephemeral Staging Lifetime** | N/A (Staging unavailable) | Release duration + 2-hour failure TTL |
| **Production RPO / RTO** | Not measured | PENDING OPERATOR APPROVAL (< 1h RPO, < 30m RTO recommended) |
| **Real-Capital Execution** | Disabled (`PANTHEON_LIVE_BROKER_ENABLED=false`) | Phase 4 Gate (requires dedicated VM + operator authorization) |
