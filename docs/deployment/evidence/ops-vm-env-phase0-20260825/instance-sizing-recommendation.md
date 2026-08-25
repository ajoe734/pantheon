# Instance Sizing & Resource Limit Recommendations (Phase 0 Baseline)

- **Measurement Date:** 2026-08-25
- **Baseline Host:** `pantheon-lupin-dev` (GCP Project: `pantheon-lupin-dev-20260719`, Zone: `asia-east1-b`)
- **Governing Rule:** [`vm-dev-staging-prod-management-plan.md`](../../vm-dev-staging-prod-management-plan.md) §6.5 & §16

---

## 1. Measured Profile Resource Consumption

Based on live, non-destructive measurements of active containers on `pantheon-lupin-dev`:

| Profile | Active Containers | Measured Total Memory | Measured Total CPU% | Peak Single Service Memory |
|---|---|---|---|---|
| **`core`** | 13 | **2,152.89 MiB (2.10 GiB)** | 207.11% | `operator-bff` (870.10 MiB), `telemetry` (462.30 MiB), `postgres` (264.80 MiB) |
| **`workers`** | 16 | **6,840.15 MiB (6.68 GiB)** | 103.01% | `loop-run-projector-scheduler` (6,371.33 MiB), `paper-fleet-reconciler` (127.60 MiB) |
| **`research`** | 12 | **677.85 MiB (0.66 GiB)** | 2.27% | `source-ingest` (136.50 MiB), `search-svc` (134.00 MiB) |
| **`management-ai`** | 6 | **1,440.10 MiB (1.41 GiB)** | 232.16% | `openclaw-gateway-adapter` (672.50 MiB), `openclaw-gateway` (572.40 MiB) |
| **`execution`** | 3 | **96.73 MiB (0.09 GiB)** | 13.95% | `broker` (45.42 MiB), `runtime-manager` (40.85 MiB), `signal-store` (10.46 MiB) |
| **Total All Profiles** | **50** | **11,207.72 MiB (10.95 GiB)** | **558.50%** | `loop-run-projector-scheduler` (6.22 GiB) |

---

## 2. Core Profile Peak & Headroom Sizing Calculation

The target VM architecture isolates the **Prod Control VM** to the `core` profile, starting additional profiles only when explicitly required.

### 2.1 Core Peak Calculation
During a blue/green release switch, the Control VM briefly runs two concurrent instances of `operator-bff` (the active `blue` container and the validation `green` candidate):

$$\text{Core Steady Memory} = 2,152.89\text{ MiB } (\approx 2.10\text{ GiB})$$
$$\text{Candidate Green BFF} = 870.00 - 1,000.00\text{ MiB } (\approx 0.98\text{ GiB})$$
$$\text{Host OS / Caddy / Kernel Buffers} = 1,000.00\text{ MiB } (\approx 0.98\text{ GiB})$$
$$\mathbf{\text{Core Peak During Cutover}} = \mathbf{4,022.89 - 4,152.89\text{ MiB } (\approx 4.06 - 4.15\text{ GiB})}$$

### 2.2 30% Headroom Calculation
The management plan requires at least 30% memory headroom above peak:

$$\text{Minimum RAM Capacity} = \frac{\text{Peak Memory}}{1 - 0.30} = \frac{4.15\text{ GiB}}{0.70} = \mathbf{5.93\text{ GiB}}$$

Or, calculating $1.30 \times \text{Peak}$:
$$\text{Minimum RAM Capacity} = 4.15\text{ GiB} \times 1.30 = \mathbf{5.40\text{ GiB}}$$

---

## 3. Instance Sizing Recommendations

### 3.1 Prod Control VM (Primary Target)
- **Recommended Machine Type:** `e2-standard-2` (2 vCPUs, 8.0 GiB RAM)
- **Memory Capacity:** 8,192 MiB (8.0 GiB)
- **Peak Utilization:** $4,153\text{ MiB} / 8,192\text{ MiB} = 50.7\%$
- **Headroom Provided:** $\mathbf{49.3\%}$ (well exceeds the $\ge 30\%$ requirement).
- **Persistent Disk:** 50 GiB Standard Persistent Disk / Balanced SSD (Postgres data volume on separate disk).
- **Cost Profile:** ~\$48.80 / month (asia-east1).
- **Note:** If the async projector (`loop-run-projector-scheduler`) is co-located with Control rather than deployed on a worker VM, size up to `e2-standard-4` (4 vCPUs, 16.0 GiB RAM) to accommodate the 6.2 GiB projector working set.

### 3.2 Prod Execution VM (Phase 4 Real-Capital Boundary)
- **Recommended Machine Type:** `e2-standard-2` (2 vCPUs, 8.0 GiB RAM) or `e2-medium` (2 vCPUs, 4.0 GiB RAM)
- **Measured Working Set:** `runtime-manager` (40.85 MiB) + `broker` (45.42 MiB) + `signal-store` (10.46 MiB) + adapters = $\approx 150 - 250\text{ MiB}$.
- **Headroom Provided:** $> 90\%$ on `e2-standard-2`, $> 85\%$ on `e2-medium`.
- **Isolation:** Dedicated VPC subnet, strict firewall ingress (only from Prod Control VM internal IP), no public IP.

### 3.3 Ephemeral Staging VM (Phase 2 CI/CD Release Rehearsal)
- **Recommended Machine Type:** `e2-standard-2` (2 vCPUs, 8.0 GiB RAM) for standard releases; `e2-standard-4` (4 vCPUs, 16.0 GiB RAM) for full multi-profile release smoke tests.
- **Lifecycle Policy:** Auto-created on release dispatch; destroyed immediately upon verification success or after 2-hour debug TTL on failure.

### 3.4 Dev VM (Current Baseline)
- **Current Instance:** `pantheon-lupin-dev` (12 vCPUs, 48 GiB RAM).
- **Role:** Full-stack development VM running all 50 active containers across all profiles concurrently to support multi-agent parallel workflows and live testing.

---

## 4. Recommended Container Resource Limits (Phase 1 Target)

To prevent individual containers from exhausting host memory (e.g. unconstrained memory growth in workers or projectors), the following resource limits should be codified in Compose profiles in Phase 1:

| Service | Category | Recommended CPU Limit | Recommended Memory Limit | Current Measured Baseline |
|---|---|---|---|---|
| `operator-bff` | `core` | 2.0 vCPU | 1.5 GiB | 870.10 MiB |
| `postgres` | `core` | 2.0 vCPU | 1.5 GiB | 264.80 MiB |
| `telemetry` | `core` | 1.0 vCPU | 800 MiB | 462.30 MiB |
| `nats` | `core` | 0.5 vCPU | 256 MiB | 68.13 MiB |
| `minio` | `core` | 0.5 vCPU | 256 MiB | 90.21 MiB |
| `reconciliation-drift-svc` | `core` | 0.5 vCPU | 256 MiB | 111.50 MiB |
| Standard Control APIs (each) | `core` | 0.5 vCPU | 128 MiB | 37 - 44 MiB |
| `loop-run-projector-scheduler` | `workers` | 2.0 vCPU | 8.0 GiB (limit: 16 GiB) | 6,371.33 MiB |
| `paper-fleet-reconciler` | `workers` | 0.5 vCPU | 256 MiB | 127.60 MiB |
| Standard Schedulers/Consumers | `workers` | 0.25 vCPU | 128 MiB | 16 - 57 MiB |
| `openclaw-gateway-adapter` | `management-ai` | 2.0 vCPU | 1.0 GiB | 672.50 MiB |
| `openclaw-gateway` | `management-ai` | 1.0 vCPU | 1.0 GiB | 572.40 MiB |
| `runtime-manager` | `execution` | 0.5 vCPU | 256 MiB | 40.85 MiB |
| `signal-store` (redis) | `execution` | 0.5 vCPU | 256 MiB | 10.46 MiB |
