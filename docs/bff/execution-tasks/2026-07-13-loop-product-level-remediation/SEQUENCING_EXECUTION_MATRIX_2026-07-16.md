# Loop Remediation Sequencing & Execution Matrix (2026-07-16)

Status: Authoritative sequencing mapping implementing the re-sequencing mandated by `docs/04/pantheon_loop_product_level_remediation_2026-07-13/REMEDIATION_SEQUENCING_ADDENDUM_2026-07-16.md`.

This matrix maps all 48 catalog tasks to their updated execution waves and dependency lists. It decouples the security/governance tasks (G1/attestation) from the functional loops, ensuring that a complete end-to-end permissive paper-trade loop (G2) is achieved and verified before opening the strict Hardening Wave.

---

## 1. High-Level Wave Structure

| Execution Phase | Waves | Gating Policy & Posture |
|---|---|---|
| **Phase A: Permissive Paper-Trade Closeout** | Wave 0 — Wave 4 | Runs under permissive auth (`AUTH_MODE=permissive`, `AUTH_STUB=true`). Security and attestation dependencies are removed to allow functional verification of signal-to-telemetry loops. Ends at `LOOP-PROD-CLOSE-001`. |
| **Phase B: Strict Hardening Wave** | Wave 5 — Wave 7 | Opens ONLY after G2 paper-trade loop is proven. Re-enables strict auth, MFA, browser cutover, attestation roots, and final Human/Ops sign-off. Ends at `LOOP-PROD-CLOSE-002`. |

---

## 2. Detailed Task Matrix

### Wave 0 — Functional Substrate (Permissive Auth)

| Task ID | Title | Summary / Purpose | Dependency Updates (Overlay vs Catalog) |
|---|---|---|---|
| `LOOP-PROD-000` | Canonical loop inventory and OODA overlay truth | Define the 12 loops and composite OODA overlay. | `[]` (Unchanged) |
| `LOOP-PROD-001` | Durable controller truth substrate | Set up tenant/env-scoped controller records. | `["LOOP-PROD-000"]` (Unchanged) |
| `LOOP-PROD-002` | Product evidence schema and anti-false-close gate | Define structured evidence schema rules. | `["LOOP-PROD-000", "LOOP-PROD-001"]` (Unchanged) |
| `LOOP-PROD-REC-001` | Full-stack loop recovery and fault-injection | Set up restart/recovery & fault injection. | `["LOOP-PROD-001", "LOOP-PROD-002"]` (Unchanged) |

### Wave 1 — Canonical Loops & Side Effects (Permissive Auth)

| Task ID | Title | Summary / Purpose | Dependency Updates (Overlay vs Catalog) |
|---|---|---|---|
| `LOOP-PROD-SRC-001` | Source requirement reconciler | Default scheduler & reconciler. | `["LOOP-PROD-001", "LOOP-PROD-002", "LOOP-PROD-REC-001"]`<br>*(Removed: `LOOP-PROD-AUTH-001`)* |
| `LOOP-PROD-DIST-001` | Strategy Distillation event consumer | Distillation queue handler. | `["LOOP-PROD-SRC-001"]` (Unchanged) |
| `LOOP-PROD-ALPHA-001` | Alpha Replication worker | Strategy replication processor. | `["LOOP-PROD-DIST-001"]` (Unchanged) |
| `LOOP-PROD-TEACH-001` | Persona Teaching | teaching processor on authoritative data. | `["LOOP-PROD-SRC-001", "LOOP-PROD-REC-001", "LOOP-PROD-ALPHA-001"]` (Unchanged) |
| `LOOP-PROD-AGORA-001` | Agora interaction evidence worker | Agora handoff and dataset worker. | `["LOOP-PROD-001", "LOOP-PROD-002", "LOOP-PROD-REC-001", "LOOP-PROD-TEACH-001", "AG-GAP-014"]`<br>*(Removed: `LOOP-PROD-AUTH-001`)* |
| `LOOP-PROD-CONS-001` | Participant Consultation workflow | Reconcile participant consultation steps. | `["LOOP-PROD-001", "LOOP-PROD-002", "LOOP-PROD-REC-001", "LOOP-PROD-AGORA-001"]` (Unchanged) |
| `LOOP-PROD-AGORA-002` | Deferred Strategy Workshop ops | Implement deferred workshop operations. | `["LOOP-PROD-CONS-001", "LOOP-PROD-ALPHA-001", "AG-GAP-005"]`<br>*(Removed: `LOOP-PROD-ATTEST-001`)* |
| `LOOP-PROD-AGORA-003` | Hosted Strategy Workshop client UI | Expose workshop controls on execute-plans. | `["LOOP-PROD-AGORA-002", "AG-GAP-013", "AG-GAP-014", "OPS-EP-DEV-MAIN-RECONCILE-001"]`<br>*(Removed: `LOOP-PROD-FE-001`)* |
| `LOOP-PROD-IMIT-001` | Human Imitation shadow evaluation | shadow evaluation algorithm. | `["LOOP-PROD-AGORA-001", "LOOP-PROD-TEACH-001", "LOOP-PROD-REC-001", "LOOP-PROD-CONS-001", "LOOP-PROD-AGORA-002"]` (Unchanged) |
| `LOOP-PROD-DEP-001` | Canonical deployment dispatcher | Core dispatcher with RuntimeBinding. | `["LOOP-PROD-001", "LOOP-PROD-002", "LOOP-PROD-REC-001", "LOOP-PROD-IMIT-001"]` (Unchanged) |
| `LOOP-PROD-CAP-001` | DecisionSignalProducer | Bounded paper order placement. | `["LOOP-PROD-DEP-001", "LOOP-PROD-REC-001", "TJ-E2E-014"]` (Unchanged) |
| `LOOP-PROD-TEL-001` | Telemetry reconciliation | Telemetry capture & incident pipeline. | `["LOOP-PROD-001", "LOOP-PROD-002", "LOOP-PROD-REC-001", "LOOP-PROD-CAP-001"]` (Unchanged) |
| `LOOP-PROD-TEL-002` | Lifecycle projector | Loop-run/Trade Journey lifecycle projection. | `["LOOP-PROD-CAP-001", "LOOP-PROD-TEL-001", "TJ-E2E-014"]` (Unchanged) |
| `LOOP-PROD-EVO-001` | Evolution target-plane dispatcher | Triggers evolution rounds. | `["EVOCHAIN-011", "LOOP-PROD-DEP-001", "LOOP-PROD-TEL-001", "LOOP-PROD-REC-001", "LOOP-PROD-TEL-002"]` (Unchanged) |
| `LOOP-PROD-BFF-001` | BFF health monitoring | Exposes loop metrics & health. | `["LOOP-PROD-001", "LOOP-PROD-TEL-001", "LOOP-PROD-REC-001", "LOOP-PROD-EVO-001"]`<br>*(Removed: `LOOP-PROD-AUTH-001`)* |
| `LOOP-PROD-OODA-001` | Per-Persona OODA schedule | Multi-persona reconciliation schedule. | `["LOOP-PROD-000", "LOOP-PROD-001", "LOOP-PROD-002", "LOOP-PROD-REC-001", "OPENCLAW-CRON-WRITE-SCOPE", "OPENCLAW-PERSONA-CRON-BACKFILL", "OPENCLAW-OODA-PACKET-CLOSURE", "LOOP-PROD-BFF-001"]` (Unchanged) |

### Wave 2 — Cross-Loop Paths & Verifiers (Permissive Auth)

| Task ID | Title | Summary / Purpose | Dependency Updates (Overlay vs Catalog) |
|---|---|---|---|
| `LOOP-PROD-PER-001` | Persona provisioning | Binding and evaluation loop setup. | `["LOOP-PROD-DEP-001", "LOOP-PROD-CAP-001", "LOOP-PROD-OODA-001", "PPL-ALLOC-010", "PPL-ALLOC-011"]` (Unchanged) |
| `LOOP-PROD-TJ-001` | Trade Journey backend | Journey-tracking api. | `["LOOP-PROD-DEP-001", "LOOP-PROD-CAP-001", "LOOP-PROD-TEL-002", "LOOP-PROD-EVO-001", "TJ-E2E-014", "LOOP-PROD-PER-001"]`<br>*(Removed: `LOOP-PROD-AUTH-001`)* |
| `LOOP-PROD-TJ-002` | Hosted Trade Journey controls | Expose journey controls on FE. | `["LOOP-PROD-TJ-001", "MGMT-SSE-001", "OPS-EP-DEV-MAIN-RECONCILE-001", "LOOP-PROD-AGORA-003"]`<br>*(Removed: `LOOP-PROD-FE-001`)* |
| `LOOP-PROD-MAI-001` | Hosted Management AI repair backend | Dev-bridge backend verification. | `["LOOP-PROD-001", "LOOP-PROD-002", "LOOP-PROD-REC-001", "LOOP-PROD-TJ-001"]`<br>*(Removed: `LOOP-PROD-AUTH-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-BROWSER-AUTH-001`)* |
| `LOOP-PROD-MAI-002` | Hosted Management AI repair UI | Management console UI for repair. | `["LOOP-PROD-MAI-001", "MGMT-SSE-001", "OPS-EP-DEV-MAIN-RECONCILE-001", "LOOP-PROD-TJ-002"]`<br>*(Removed: `LOOP-PROD-FE-001`)* |
| `LOOP-PROD-VERIFY-KNOW-001` | Knowledge verifier | Verify knowledge routing. | `[Wave 1/2 dependencies]` (Unchanged) |
| `LOOP-PROD-VERIFY-EXEC-001` | Execution verifier | Verify execution routing. | `[Wave 1/2 dependencies]` (Unchanged) |
| `LOOP-PROD-VERIFY-HUMAN-001` | Human interaction verifier | Verify human action/learning. | `[Wave 1/2 dependencies]` (Unchanged) |
| `LOOP-PROD-VERIFY-OODA-001` | OODA verifier | Verify OODA schedule. | `[Wave 1/2 dependencies]` (Unchanged) |

### Wave 3 — Convergence Tasks (Permissive Auth)

| Task ID | Title | Summary / Purpose | Dependency Updates (Overlay vs Catalog) |
|---|---|---|---|
| `LOOP-PROD-PPL-001` | Persona promotion closeout | Closeout allocation/promotion goals. | `["PPL-ALLOC-009", "PPL-ALLOC-010", "PPL-ALLOC-011", "PPL-ALLOC-012", "PPL-ALLOC-013", "LOOP-PROD-PER-001", "LOOP-PROD-VERIFY-EXEC-001"]`<br>*(Removed: `LOOP-PROD-FE-001`)* |
| `LOOP-PROD-TJ-003` | Trade Journey closeout | Reconcile trade journey e2e. | `[Wave 1/2 dependencies]` (Unchanged) |
| `LOOP-PROD-PINT-001` | Reconciled hosted product closeout | Closeout PINT requirements. | `["OPS-EP-DEV-MAIN-RECONCILE-001", "PINT-010-R2", "LOOP-PROD-AGORA-003", "LOOP-PROD-VERIFY-HUMAN-001"]`<br>*(Removed: `LOOP-PROD-FE-001`)* |
| `LOOP-PROD-MAI-003` | Management AI closeout | Reconcile management console scopes. | `[Wave 1/2 dependencies]` (Unchanged) |

### Wave 4 — G2 Foundational Checkpoint (Permissive Auth)

| Task ID | Title | Summary / Purpose | Dependency Updates (Overlay vs Catalog) |
|---|---|---|---|
| `LOOP-PROD-CLOSE-001` | Baseline 12-loop product checkpoint | End of the permissive paper-trade loop. | `["LOOP-PROD-002", "LOOP-PROD-REC-001", "LOOP-PROD-VERIFY-KNOW-001", "LOOP-PROD-VERIFY-EXEC-001", "LOOP-PROD-VERIFY-HUMAN-001", "LOOP-PROD-VERIFY-OODA-001", "LOOP-PROD-PPL-001", "LOOP-PROD-TJ-003", "LOOP-PROD-PINT-001", "LOOP-PROD-MAI-003"]`<br>*(Removed: `LOOP-PROD-AUTH-001`, `LOOP-PROD-FE-001`)* |

---

### Wave 5 — Hardening Wave: Security & Release Cutover (Strict Auth)

*This wave opens only after `LOOP-PROD-CLOSE-001` is completed.*

| Task ID | Title | Summary / Purpose | Updated Waves & Dependencies |
|---|---|---|---|
| `LOOP-PROD-AUTH-001` | Strict dev auth cutover | Enable token verification on BFF. | **Wave 5** (was 0)<br>Depends: `["LOOP-PROD-002", "LOOP-PROD-CLOSE-001"]` |
| `LOOP-PROD-FE-001` | Gate-before-deploy release | Remove browser development tokens. | **Wave 5** (was 0)<br>Depends: `["LOOP-PROD-002", "LOOP-PROD-AUTH-001"]` |
| `LOOP-PROD-DELIVERY-001` | Fleet delivery provenance | Ensure worker run/scope signing. | **Wave 5** (was 0)<br>Depends: `["LOOP-PROD-002", "LOOP-PROD-CLOSE-001"]` |
| `LOOP-PROD-AUTH-BOOT-001` | Auth credential bootstrap | Load strict credentials to vault. | **Wave 5** (was 0)<br>Depends: `["LOOP-PROD-002", "LOOP-PROD-DELIVERY-001"]` |
| `LOOP-PROD-WORKER-001` | Exact-CAS worker outcome | Fleet container outcome and CAS. | **Wave 5** (was 1)<br>Depends: `["LOOP-PROD-001", "LOOP-PROD-002", "LOOP-PROD-DELIVERY-001"]` |
| `LOOP-PROD-LEASE-001` | Mutation lease | Coordinated dev write lock lease. | **Wave 5** (was 1)<br>Depends: `["LOOP-PROD-AUTH-BOOT-001", "LOOP-PROD-AUTH-001", "LOOP-PROD-WORKER-001"]` |
| `LOOP-PROD-FLEET-001` | Fleet admission | Quota & resource admission boundaries. | **Wave 5** (was 1)<br>Depends: `["LOOP-PROD-WORKER-001"]` |
| `LOOP-PROD-ATTEST-001` | Attestation trust root | Cryptographic task evidence signer. | **Wave 5** (was 1)<br>Depends: `["LOOP-PROD-002", "LOOP-PROD-WORKER-001", "LOOP-PROD-LEASE-001"]` |
| `LOOP-PROD-AUTH-OPS-001` | Privileged lifecycle | MFA/Ops credentials rotation. | **Wave 5** (was 1)<br>Depends: `["LOOP-PROD-AUTH-BOOT-001", "LOOP-PROD-AUTH-001", "LOOP-PROD-LEASE-001", "LOOP-PROD-ATTEST-001"]` |
| `LOOP-PROD-BROWSER-AUTH-001` | Browser auth cutover | BFF cookie/session strict protection. | **Wave 5** (was 1)<br>Depends: `["LOOP-PROD-AUTH-BOOT-001", "LOOP-PROD-AUTH-001", "LOOP-PROD-FE-001", "LOOP-PROD-DELIVERY-001", "LOOP-PROD-LEASE-001", "LOOP-PROD-AUTH-OPS-001"]` |

### Wave 6 — Hardening Wave: Verification & Sign-off

| Task ID | Title | Summary / Purpose | Updated Waves & Dependencies |
|---|---|---|---|
| `LOOP-PROD-FE-EVID-001` | Attestation consumer | Frontend checks attestation digests. | **Wave 6** (was 3)<br>Depends: `["LOOP-PROD-FE-001", "LOOP-PROD-ATTEST-001", "LOOP-PROD-AGORA-003", "LOOP-PROD-TJ-002", "LOOP-PROD-MAI-002"]` |
| `LOOP-PROD-FE-BUILD-001` | Qualified product build | strict build budget verification. | **Wave 6** (was 3)<br>Depends: `["LOOP-PROD-FE-001", "LOOP-PROD-FE-EVID-001", "LOOP-PROD-AGORA-003", "LOOP-PROD-TJ-002", "LOOP-PROD-MAI-002"]` |
| `LOOP-PROD-SIGNOFF-001` | Human/Ops sign-off | Enforces manual signoff gating. | **Wave 6** (was 4)<br>Depends: `["LOOP-PROD-CLOSE-001", "LOOP-PROD-WORKER-001", "LOOP-PROD-ATTEST-001"]` |

### Wave 7 — Hardening Wave: Program Final Verdict

| Task ID | Title | Summary / Purpose | Updated Waves & Dependencies |
|---|---|---|---|
| `LOOP-PROD-CLOSE-002` | Final program closeout | Authority closure of the entire 48 tasks. | **Wave 7** (was 5)<br>Depends: `["EVOCHAIN-011", "EVOLOOP-009", "EVOLOOP-011", "LOOP-PROD-CLOSE-001", "LOOP-PROD-DELIVERY-001", "LOOP-PROD-WORKER-001", "LOOP-PROD-LEASE-001", "LOOP-PROD-BROWSER-AUTH-001", "LOOP-PROD-FLEET-001", "LOOP-PROD-ATTEST-001", "LOOP-PROD-AUTH-OPS-001", "LOOP-PROD-FE-EVID-001", "LOOP-PROD-FE-BUILD-001", "LOOP-PROD-SIGNOFF-001"]` |
