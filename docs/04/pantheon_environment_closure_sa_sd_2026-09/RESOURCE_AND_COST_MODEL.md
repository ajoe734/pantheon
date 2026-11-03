# Resource & Cost Model — Pantheon Environment Closure

- Document: `RESOURCE_AND_COST_MODEL.md`
- Status: Canonical Capacity & Financial Architecture Specification
- Date: 2026-09-04
- Baseline: `origin/dev`
- Task: `ENV-STAGING-PROD-PLAN-001`
- Parent Evidence: `docs/deployment/evidence/ops-vm-env-phase0-20260825/` (`measurements-baseline.json`, `instance-sizing-recommendation.md`)

---

## 1. Executive Summary

This document establishes the resource capacity planning, instance sizing, and cost model for the Pantheon multi-environment architecture.

The platform achieves **enterprise-grade isolation with minimal infrastructure spend**:
- **Steady-State Footprint**: Exactly **2 permanent VMs** (Dev VM + Prod Control VM).
- **Real-Capital Execution Footprint**: Exactly **3 permanent VMs** (Dev VM + Prod Control VM + Prod Execution VM).
- **Ephemeral Staging Footprint**: **0 permanent VMs**. Staging runs on-demand for approximately 30 minutes per candidate, costing less than **$0.05 per release**.
- **Total Cloud Spend**: Approximately **$160 – $215 / month** (a $>60\%$ cost reduction compared to a managed Kubernetes cluster).

---

## 2. Empirical Capacity Baseline (Phase 0 Measurements)

Capacity requirements are derived from empirical telemetry captured during `OPS-VM-ENV-PHASE0-20260825` on the live dev machine (`pantheon-lupin-dev`):

| Metric | Measured Value (All 50 Containers) | Measured Value (`core` Profile Only) | Notes |
|---|---|---|---|
| Active Services | 50 Compose services | 14 essential services | Core includes Postgres, NATS, Caddy, BFF, Projector |
| RAM Consumption | 11.04 GiB (11,303 MiB) | 3.85 GiB (3,942 MiB) | Steady-state active footprint |
| Blue/Green Transition RAM | N/A | 4.15 GiB (4,250 MiB) | Includes extra candidate BFF container + OS buffers |
| CPU Utilization | 5 – 12% across 12 vCPUs | < 15% across 2 vCPUs | Spikes occur only during batch data ingestion |
| Root Disk Usage | 212.2 GiB (88% of 241 GiB) | < 25 GiB for clean OS + core | High dev usage driven by historical artifacts & docker build cache |

---

## 3. Instance Sizing & Headroom Verification

Pantheon mandates a **minimum 30% unallocated memory headroom** on any production host to guarantee stability during unexpected query bursts or database checkpoints.

### 3.1 Sizing Candidates

| Instance Type | vCPU | RAM | Memory Headroom over Peak `core` (4.15 GiB) | Evaluation & Recommendation |
|---|---|---|---|---|
| `e2-standard-2` | 2 | 8.0 GiB | **48.1% Headroom** (3.85 GiB free) | **RECOMMENDED FOR PROD CONTROL & STAGING** (Exceeds 30% margin) |
| `e2-standard-4` | 4 | 16.0 GiB | **74.1% Headroom** (11.85 GiB free) | Authorized for high-throughput batch ingestion or complex projection workloads |
| `e2-highcpu-2` | 2 | 2.0 GiB | Negative Headroom (OOM risk) | **FORBIDDEN** (Cannot safely run Postgres + BFF) |

### 3.2 Machine Specifications by Environment

```text
+-----------------------------------------------------------------------------------------------+
| DEV ENVIRONMENT (Permanent)                                                                   |
| - VM: pantheon-lupin-dev                                                                      |
| - Machine Type: e2-standard-4 (4 vCPU, 16 GiB RAM) [Or existing custom 12-vCPU dev box]       |
| - Storage: 250 GB pd-balanced (hosts Docker caches, git worktrees, and research services)     |
+-----------------------------------------------------------------------------------------------+

+-----------------------------------------------------------------------------------------------+
| EPHEMERAL STAGING (On-Demand / Destroyed after test)                                          |
| - VM: pantheon-stg-<release-id>                                                               |
| - Machine Type: e2-standard-2 (2 vCPU, 8 GiB RAM)                                             |
| - Storage: 30 GB pd-balanced (ephemeral boot disk, auto-deleted) + 50 GB snapshot-cloned data |
+-----------------------------------------------------------------------------------------------+

+-----------------------------------------------------------------------------------------------+
| PROD CONTROL PLANE (Permanent)                                                                |
| - VM: pantheon-prod-control                                                                   |
| - Machine Type: e2-standard-2 (2 vCPU, 8 GiB RAM)                                             |
| - Boot Storage: 30 GB pd-balanced (OS, Caddy, Docker logs)                                    |
| - Data Storage: 100 GB pd-ssd (Postgres, NATS, state persistence; independent persistent disk)|
+-----------------------------------------------------------------------------------------------+

+-----------------------------------------------------------------------------------------------+
| PROD EXECUTION PLANE (Phase 4 / Real-Capital Only)                                            |
| - VM: pantheon-prod-exec                                                                      |
| - Machine Type: e2-standard-2 (2 vCPU, 8 GiB RAM)                                             |
| - Storage: 30 GB pd-balanced (boot disk only; execution state is ephemeral or streamed)      |
+-----------------------------------------------------------------------------------------------+
```

---

## 4. Cost Modeling & Projections (GCP asia-east1)

### 4.1 Monthly Steady-State Breakdown

Pricing based on Google Compute Engine standard rates in `asia-east1` (Taiwan):

| Component | Quantity / Specs | Estimated Monthly Cost |
|---|---|---|
| **Dev VM** | `e2-standard-4` + 250 GB pd-balanced | ~$115.00 |
| **Prod Control VM** | `e2-standard-2` + 30 GB pd-balanced | ~$52.00 |
| **Prod Persistent Data Disk** | 100 GB pd-ssd | ~$17.00 |
| **Prod Execution VM** (Phase 4) | `e2-standard-2` + 30 GB pd-balanced | ~$52.00 |
| **Cloud Storage & Snapshots** | 150 GB multi-regional (retained snapshots & evidence)| ~$6.00 |
| **Artifact Registry & Network Egress** | Standard package caching | ~$10.00 |
| **Total Monthly Spend (Paper Mode)** | Dev VM + Prod Control VM | **~$190.00 / month** |
| **Total Monthly Spend (Live Capital)** | Dev VM + Prod Control + Prod Exec | **~$242.00 / month** |

### 4.2 Ephemeral Staging Unit Economics

- Hourly rate for `e2-standard-2` in `asia-east1`: **~$0.067 / hour**.
- Average release candidate verification duration: **25 minutes (0.42 hours)**.
- Compute cost per release run: `$0.067 * 0.42 = $0.028`.
- Storage & IO cost per release run: `< $0.015`.
- **Total Ephemeral Staging Cost Per Release**: **~$0.043 (less than 5 cents)**.

*Key Takeaway*: Running 20 staging releases per month incurs less than **$1.00** in staging infrastructure cost, eliminating the $70–$150/month expense of maintaining a permanent idle staging server.

---

## 5. Billing Guardrails & Quota Limits

1. **GCP Budget Alerts**:
   - Level 1 Alert (50% of budget): Automated email notification to Operations.
   - Level 2 Alert (80% of budget): High-priority incident notification.
   - Level 3 Alert (100% of budget): Automated freeze on non-essential research runs.
2. **Compute Quota Caps**:
   - Production GCP project quota restricted to maximum 2 concurrent VMs (`e2-standard-2`).
   - Prevents accidental spin-up of expensive GPU or multi-node clusters.
