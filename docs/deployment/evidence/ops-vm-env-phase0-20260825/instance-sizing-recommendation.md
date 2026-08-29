# Instance Sizing & Resource Limit Recommendations (Phase 0 Baseline)

- **Measurement Date:** 2026-08-25
- **Baseline Host:** `pantheon-lupin-dev` (GCP Project: `pantheon-lupin-dev-20260719`, Zone: `asia-east1-b`)
- **Governing Rule:** [`vm-dev-staging-prod-management-plan.md`](../../vm-dev-staging-prod-management-plan.md) §6.5 & §16

---

## 1. Measured Profile Resource Consumption

Based on live, non-destructive measurements of active containers on `pantheon-lupin-dev`:

| Profile | Active Containers | Measured Total Memory | Measured Total CPU% | Peak Single Service Memory |
|---|---|---|---|---|
| **`core`** | 13 | **2,314.68 MiB (2.26 GiB)** | 209.47% | `operator-bff` (869.90 MiB), `telemetry` (462.90 MiB), `reconciliation-drift-svc` (340.30 MiB), `postgres` (197.40 MiB) |
| **`workers`** | 16 | **6,866.49 MiB (6.71 GiB)** | 124.42% | `loop-run-projector-scheduler` (6,397.95 MiB), `paper-fleet-reconciler` (127.90 MiB) |
| **`research`** | 12 | **677.92 MiB (0.66 GiB)** | 32.24% | `source-ingest` (136.50 MiB), `search-svc` (134.00 MiB) |
| **`management-ai`** | 6 | **1,348.64 MiB (1.32 GiB)** | 130.38% | `openclaw-gateway-adapter` (581.60 MiB), `openclaw-gateway` (572.50 MiB) |
| **`execution`** | 3 (4 staging/live-only or disabled) | **95.35 MiB (0.09 GiB)** | 6.93% | `broker` (44.04 MiB), `runtime-manager` (40.85 MiB), `signal-store` (10.46 MiB) |
| **Total All Profiles** | **50** (70 across overlays) | **11,303.08 MiB (11.04 GiB)** | **503.44%** | `loop-run-projector-scheduler` (6,397.95 MiB / 6.25 GiB) |

---

## 2. Core Profile Peak & Sizing Calculations

The target VM architecture isolates the **Prod Control VM** to the `core` profile, starting additional profiles only when explicitly required.

### 2.1 Core Memory Peak Calculation
During a blue/green release switch, the Control VM briefly runs two concurrent instances of `operator-bff` (the active `blue` container and the validation `green` candidate):

$$\text{Core Steady Memory} = 2,314.68\text{ MiB } (\approx 2.26\text{ GiB})$$
$$\text{Candidate Green BFF} = 869.90 - 1,000.00\text{ MiB } (\approx 0.85 - 0.98\text{ GiB})$$
$$\text{Host OS / Caddy / Kernel Buffers} = 1,000.00\text{ MiB } (\approx 0.98\text{ GiB})$$
$$\mathbf{\text{Core Peak During Cutover}} = \mathbf{4,184.58 - 4,314.68\text{ MiB } (\approx 4.09 - 4.21\text{ GiB})}$$

### 2.2 Memory Headroom ($\ge 30\%$) Sizing Calculation
The management plan requires at least 30% memory headroom above peak.

Using the standard capacity headroom definition:
$$\text{Headroom \%} = \frac{\text{RAM Capacity} - \text{Peak Memory}}{\text{RAM Capacity}} = 1 - \frac{\text{Peak Memory}}{\text{RAM Capacity}} \ge 30\%$$

Therefore:
$$\text{Minimum RAM Capacity} \ge \frac{\text{Peak Memory}}{1 - 0.30} = \frac{\text{Peak Memory}}{0.70}$$

- **At Measured Peak ($4,184.58\text{ MiB} \approx 4.09\text{ GiB}$):**
  $$\text{Minimum RAM Capacity} = \frac{4,184.58\text{ MiB}}{0.70} = \mathbf{5,977.97\text{ MiB } (\approx 5.84\text{ GiB})}$$

- **At Upper Bound Peak ($4,314.68\text{ MiB} \approx 4.21\text{ GiB}$):**
  $$\text{Minimum RAM Capacity} = \frac{4,314.68\text{ MiB}}{0.70} = \mathbf{6,163.83\text{ MiB } (\approx 6.02\text{ GiB})}$$

*(Note: If calculated via alternative multiplier $1.30 \times \text{Peak}$, the baseline value is $4,184.58 \times 1.30 = 5,439.95\text{ MiB} \approx 5.31\text{ GiB}$. The standard capacity-headroom formula $1 - \frac{\text{Peak}}{\text{Capacity}} \ge 30\%$ yields $5.84\text{ GiB}$, which is safely satisfied by standard 8.0 GiB instances.)*

### 2.3 CPU Sizing & Headroom Reconciliation
The instantaneous Dev baseline measured **209.47% Core CPU** (equivalent to ~2.09 vCPU cores):
- `operator-bff`: `99.83%` (driven by high-frequency multi-agent SSE streams and API queries during development)
- `reconciliation-drift-svc`: `97.73%` (driven by active drift sweep loop)
- `telemetry`: `7.42%`
- `postgres`, `nats`, other core APIs: `4.49%`

**Deployment Cutover Peak:** During a blue/green cutover, starting and healthchecking the green candidate `operator-bff` adds an estimated transient **+50% to +100% vCPU** load during initialization, bringing aggregate instantaneous cutover peak to **260% - 310% vCPU (2.6 - 3.1 cores)**.

**Reconciliation & Sizing Gate:**
1. **On `e2-standard-2` (2 vCPUs = 200% capacity):**
   - Memory headroom is ample ($47.3\% - 48.9\%$, exceeding $\ge 30\%$).
   - However, CPU is oversubscribed at instantaneous peak (209.47% > 200%), causing transient CPU throttling and potential readiness check latency if drift reconciliation coincides with green candidate cutover.
   - `e2-standard-2` is viable **only** if drift reconciliation interval is tuned/bounded (e.g. interval $\ge 30\text{s}$) and concurrent multi-agent polling is absent on production control plane, keeping steady-state CPU $< 80\%$ (leaving $> 1.2$ vCPUs free for cutovers).
2. **On `e2-standard-4` (4 vCPUs, 16.0 GiB RAM = 400% capacity):**
   - Accommodates full 209.47% steady load + 100% green BFF cutover load ($309.47\% / 400\% = 77.37\%$ peak utilization).
   - Provides **$22.63\%$ CPU headroom at extreme peak** and **$> 45\%$ CPU headroom under standard operating conditions**.
   - Provides **$74.5\%$ memory headroom at measured peak ($73.7\%$ at upper bound peak $4.21\text{ GiB}$)** ($1 - 4.09\text{ GiB} / 16.0\text{ GiB}$ to $1 - 4.21\text{ GiB} / 16.0\text{ GiB}$).
   - **Recommended** for unconstrained production Control VM workloads.

---

## 3. Instance Sizing Recommendations

### 3.1 Prod Control VM (Primary Target)
- **Baseline Minimal (Tuned Intervals):** `e2-standard-2` (2 vCPUs, 8,192 MiB / 8.0 GiB RAM).
  - **Memory Headroom:** $\mathbf{48.9\%}$ at measured peak (at upper bound peak: $\mathbf{47.3\%}$), exceeding $\ge 30\%$.
  - **Prerequisite:** Drift sweep frequency bounded to $\ge 30\text{s}$ to avoid CPU contention.
- **Recommended Production Target (Unconstrained Headroom):** `e2-standard-4` (4 vCPUs, 16,384 MiB / 16.0 GiB RAM).
  - **Memory Headroom:** $\mathbf{74.5\%}$ at measured peak (at upper bound peak: $\mathbf{73.7\%}$).
  - **CPU Headroom:** $> 22.6\%$ at peak blue/green cutover, $> 45\%$ steady-state.
  - **Persistent Disk:** 50 GiB Standard Persistent Disk / Balanced SSD (Postgres data volume on separate disk).
  - **Cost Profile:** ~\$97.60 / month (asia-east1).

### 3.2 Prod Execution VM (Phase 4 Real-Capital Boundary)
- **Recommended Machine Type:** `e2-standard-2` (2 vCPUs, 8.0 GiB RAM) or `e2-medium` (2 vCPUs, 4.0 GiB RAM)
- **Measured Working Set:** `runtime-manager` (40.85 MiB) + `broker` (44.04 MiB) + `signal-store` (10.46 MiB) + adapters = $\approx 150 - 250\text{ MiB}$.
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
| `operator-bff` | `core` | 2.0 vCPU | 1.5 GiB | 869.90 MiB |
| `postgres` | `core` | 2.0 vCPU | 1.5 GiB | 197.40 MiB |
| `telemetry` | `core` | 1.0 vCPU | 800 MiB | 462.90 MiB |
| `nats` | `core` | 0.5 vCPU | 256 MiB | 67.98 MiB |
| `minio` | `core` | 0.5 vCPU | 256 MiB | 90.14 MiB |
| `reconciliation-drift-svc` | `core` | 0.5 vCPU | 512 MiB | 340.30 MiB |
| Standard Control APIs (each) | `core` | 0.5 vCPU | 128 MiB | 37.77 - 43.89 MiB |
| `loop-run-projector-scheduler` | `workers` | 2.0 vCPU | 8.0 GiB (limit: 16 GiB) | 6,397.95 MiB |
| `paper-fleet-reconciler` | `workers` | 0.5 vCPU | 256 MiB | 127.90 MiB |
| Standard Schedulers/Consumers | `workers` | 0.25 vCPU | 128 MiB | 15.33 - 57.04 MiB |
| `openclaw-gateway-adapter` | `management-ai` | 2.0 vCPU | 1.0 GiB | 581.60 MiB |
| `openclaw-gateway` | `management-ai` | 1.0 vCPU | 1.0 GiB | 572.50 MiB |
| `runtime-manager` | `execution` | 0.5 vCPU | 256 MiB | 40.85 MiB |
| `signal-store` (redis) | `execution` | 0.5 vCPU | 256 MiB | 10.46 MiB |
