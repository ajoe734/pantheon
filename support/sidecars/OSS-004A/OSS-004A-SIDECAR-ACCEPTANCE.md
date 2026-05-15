# OSS-004A Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OSS-004A-SIDECAR-ACCEPTANCE`
**Helper parent:** `OSS-004A` - stabilize the runtime auth/authority path for EP4
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Prepared by:** `Codex`
**Date:** `2026-04-18`
**Packet status:** `prepared from current repo snapshot; ready for Gemini review and Claude reuse`

> Scope constraint: support artifact only. This packet does not edit canonical truth, runtime
> contracts, telemetry schema truth, or the parent implementation. It packages the current
> acceptance surface, dependency map, and evidence boundaries for `OSS-004A`.

---

## 1. Purpose

This sidecar exists to reduce restart cost for `OSS-004A` by doing three things:

1. restate the parent task truth and scope boundary from planning/state
2. map the current repo evidence to the four named proof surfaces in the task summary
3. separate what is already explicit from what still blocks truthful `EP4` claims

This packet is intentionally narrower than implementation work. It is meant to help `Claude`
finish the parent slice without widening scope into `OSS-004B` or `OSS-004C`.

---

## 2. Parent Task Truth

From `ai-status.json`, the parent task is currently:

- owner: `Claude`
- reviewer: `Codex`
- phase: `Phase 7: EP4 Proof Raising`
- status: `todo`
- formal dependencies: none
- recorded acceptance:
  - `runtime auth and authority path is explicit`
  - `dual-VM paper proof no longer depends on ambiguous identity or token assumptions`

From the accepted phase-7 planning session, the intended `OSS-004A` proof surface is:

- `runtime-manager` token flow
- paper-runtime identity
- telemetry authority references
- OpenClaw/Pantheon adapter boundary

This means the parent slice is not "implement EP4." It is the proof-raising substrate that should
make the authority chain explicit before:

- `OSS-004B` replaces the VM-2 bootstrap runtime with the final truthful paper execution package
- `OSS-004C` runs the integrated governed paper acceptance

---

## 3. Sidecar Scope Boundary

In scope for this sidecar:

- inspect the current repo state for the four named `OSS-004A` proof surfaces
- run low-cost verification that is directly relevant to the runtime auth path
- assemble a dependency map and acceptance checklist expansion
- hand the packet to `Gemini` as reviewer and to `Claude` as parent-owner support

Out of scope:

- editing `services/runtime-manager/*`, `services/telemetry/*`, `docker-compose.exec.yml`, or any
  canonical contract/policy file
- replacing the paper runtime bootstrap wrapper
- claiming a fresh dual-VM or live OpenClaw smoke result from this sidecar
- finalizing `OSS-004A` lifecycle on behalf of the parent owner

---

## 4. Current Repo Snapshot

### 4.1 Runtime-manager auth path is explicit, but still stub-level

Current repo evidence shows the runtime-manager write surface is already explicit:

- `services/runtime-manager/main.py` documents itself as the authoritative HTTP surface for
  `RuntimeBinding` write operations
- every write route requires `Authorization: Bearer ...`
- the file is explicit that token content is **not** validated in v1 and still says:
  `Add JWT validation before production`

Local verification run for this sidecar:

```bash
python3 -m unittest services/runtime-manager/test_runtime_manager.py
```

Observed result:

- command exited `0`
- `8` tests passed
- this is enough to confirm the current auth gate is enforced as a non-empty bearer token check
- this is **not** enough to claim final production-grade runtime auth

### 4.2 Execution-side token and secret boundary is explicit

Current repo evidence already isolates execution credentials onto VM-2:

- `docs/deployment/exec-vm-secrets-guide.md` states VM-2 is the only place that should hold:
  - broker / exchange secrets
  - execution-only `runtime-manager` bearer token
  - future paper/live account credentials
- `docker-compose.exec.yml` wires `PANTHEON_RUNTIME_MANAGER_TOKEN` into VM-2 services such as:
  - `runtime-manager`
  - `pantheon-lean-paper`
  - execution sidecars

This is useful parent input because it means `OSS-004A` should reuse an already-defined execution
secret boundary instead of inventing a new cross-plane token story.

### 4.3 Paper-runtime identity is explicit, but still bootstrap-only

The current paper-runtime identity is already named and machine-visible:

- `services/execution/lean_runtime/runtime_bootstrap.py` emits:
  - `runtime_role = "pantheon-lean-paper-runtime"`
  - `runtime_mode`
  - `runtime_manager_url`
  - imported module status
  - `stub_mode = True`
- `docs/deployment/dual-vm-acceptance-results.md` says the VM-2 paper runtime is still the
  bootstrap harness from `DEPLOY-008`, not the final execution package
- `docs/deployment/exec-vm-secrets-guide.md` repeats that the service is a VM-split bootstrap
  wrapper rather than final per-pool LEAN packaging

This means the parent can already point to a stable runtime identity, but it cannot use that
identity alone to claim `EP4` execution completion. That remains `OSS-004B` scope.

### 4.4 Telemetry authority references are already explicit

The current telemetry contract already encodes the authority chain needed by `OSS-004A`.

`services/telemetry/telemetry_event.schema.json` requires:

- `binding_id`
- `runtime_id`
- `capital_pool_id`
- `artifact_id`
- `artifact_version`
- `deployment_stage`
- `plan_id`
- `persona_capital_binding_id`

The schema description is explicit that every event must prove:

- binding identity
- deployment stage
- the governance/deployment chain needed for joins

`docs/deployment/dual-vm-acceptance-results.md` also records that VM-1 telemetry ingest already
accepts events whose binding identity is resolved from VM-2.

This is the strongest existing evidence that the telemetry side of the authority path is already
well shaped, even though the full governed paper-runtime loop has not been rerun as one `EP4`
acceptance packet.

### 4.5 OpenClaw/Pantheon boundary is already explicit and governed

The repo already has a clear OpenClaw boundary that `OSS-004A` should reuse rather than redefine.

`integrations/openclaw/integration.md` currently states:

- Pantheon integrates OpenClaw as an external runtime dependency
- the governed seam is the Pantheon-side `openclaw-gateway-adapter`
- the adapter is the only allowed place to map OpenClaw runtime objects into Pantheon objects
- OpenClaw never receives authority over:
  - registry state
  - approval state
  - capital pools
  - runtime bindings
  - LEAN deployment

`OPENCLAW_RUNTIME_CONTRACT.md` also requires:

- per-agent workspace
- per-agent auth profile
- no implicit credential sharing
- runtime sessions carrying `persona_id`, `session_id`, `trace_id`, `request_id`, `actor_type`,
  and `environment`

The parent should therefore treat OpenClaw as an already-governed runtime substrate, not as the
owner of execution authority.

### 4.6 Current gap is integration of these pieces into one explicit authority story

The repo now has strong partial evidence for each named surface, but the planning packet still
correctly records two blocking gaps:

- `runtime-manager` auth remains stub-level, not final token validation
- there is not yet one integrated `EP4` run proving the final authority path from approved plan to
  paper execution runtime

That means the parent acceptance is currently **not yet satisfied**, even though several pieces are
already explicit.

---

## 5. Acceptance Checklist Expansion

The parent acceptance is short. This sidecar expands it into a reviewer-friendly checklist.

| Check | What "done" means for `OSS-004A` | Current snapshot |
|---|---|---|
| AC-1 Runtime-manager auth path is explicit | The parent packet or implementation states exactly what token path is required and where validation stops today | Partial |
| AC-2 Execution token ownership is explicit | VM-1 vs VM-2 secret boundary is named and the runtime-manager token is clearly execution-scoped | Partial |
| AC-3 Paper runtime identity is explicit | The runtime identity, role, mode, and current bootstrap limitation are all named without pretending the final package exists | Partial |
| AC-4 Telemetry authority refs are explicit | The authority chain from binding/runtime/plan/persona binding into telemetry is cited directly | Met |
| AC-5 OpenClaw boundary is explicit | The adapter boundary clearly says OpenClaw is runtime substrate, not deployment/governance authority | Met |
| AC-6 Dual-VM paper proof no longer depends on ambiguous identity assumptions | The parent removes ambiguity about which token, which runtime identity, which binding identity, and which authority objects are involved in the paper path | Not met |
| AC-7 Parent does not overclaim beyond `EP4` substrate prep | `OSS-004A` closes only the authority-path gap and does not silently absorb `OSS-004B` or `OSS-004C` | Open |

### Acceptance summary

Current parent acceptance is **not yet met**.

What is already solid:

- runtime-manager write authority boundary is explicit
- telemetry authority references are explicit
- OpenClaw boundary is explicit
- execution-side secret isolation is already documented

What still blocks closeout:

- runtime auth is still stub-level rather than final validation
- the parent has not yet consolidated these surfaces into one stable authority-path packet
- the repo still lacks the integrated governed paper proof that would show the ambiguity is
  actually removed end-to-end

---

## 6. Dependency Map

### 6.1 Formal task dependency truth

Per `ai-status.json`, `OSS-004A` has no formal durable `depends_on` entries.

This sidecar should not invent a blocker in task state that is not actually recorded there.

### 6.2 Practical evidence dependencies the parent should reuse

Even without formal task blockers, the parent depends on the following repo truth surfaces:

| Surface | Why it matters to `OSS-004A` |
|---|---|
| `services/runtime-manager/main.py` | defines the current auth gate and authoritative runtime write surface |
| `services/runtime-manager/test_runtime_manager.py` | proves the current auth requirement and route behavior at local test level |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | locks `ApprovalDecision -> DeploymentPlan -> RuntimeBinding` as the only allowed deployment chain |
| `services/execution/runtime-manager/contract.md` | locks Runtime Manager as sole writer of `RuntimeBinding` and execution-side state |
| `docs/deployment/exec-vm-secrets-guide.md` | locks VM-2 secret boundary and execution-only token placement |
| `docker-compose.exec.yml` | shows the actual execution-plane env wiring for the bootstrap runtime and sidecars |
| `services/execution/lean_runtime/runtime_bootstrap.py` | exposes the current paper-runtime identity and its bootstrap limitation |
| `docs/deployment/dual-vm-acceptance-results.md` | records what the current dual-VM harness proves and does not prove |
| `services/telemetry/telemetry_event.schema.json` | defines the authority refs telemetry must carry |
| `integrations/openclaw/integration.md` | locks the governed OpenClaw adapter boundary |
| `OPENCLAW_RUNTIME_CONTRACT.md` | locks session isolation and runtime audit requirements |
| `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` | prevents `EP3` harness evidence from being misclaimed as `EP4` |

### 6.3 Downstream tasks that benefit from a clean `OSS-004A` closeout

| Downstream task | Status | Why `OSS-004A` matters |
|---|---|---|
| `OSS-004B` | `todo` | needs the authority story settled before replacing the bootstrap runtime with the truthful package |
| `OSS-004C` | `todo` | needs the final identity/token/authority story before running the integrated governed paper acceptance |
| `OSS-004D` | `todo` | can only publish truthful `EP4` evidence after `OSS-004C` completes against a stable authority path |

---

## 7. Reviewer Focus Areas

These are the highest-signal checks for `Gemini` and later for the parent owner.

### 7.1 Do not let bearer presence masquerade as final auth

The repo currently proves "write routes require a non-empty bearer token."

It does **not** yet prove:

- JWT or equivalent production validation
- issuer/audience constraints
- rotation/expiry semantics

If the parent says "runtime auth is complete" without narrowing that claim, it will overstate the
current evidence.

### 7.2 Keep bootstrap runtime identity distinct from truthful execution package

The parent may document the current paper-runtime identity, but it should not imply that the
bootstrap wrapper is already the final execution substrate. That is exactly the `OSS-004B`
boundary.

### 7.3 Reuse canonical telemetry naming

The current telemetry schema is canonical on `binding_id`.

The parent should avoid reintroducing `runtime_binding_id` as the canonical authority name. Legacy
compatibility aliases may still exist in read adapters, but the acceptance packet should anchor to
the canonical schema truth.

### 7.4 Do not move execution authority into OpenClaw

The governed boundary already says OpenClaw is an external runtime dependency and the adapter is the
only mapping seam. The parent should not suggest direct OpenClaw ownership of deployment, binding,
or telemetry truth.

### 7.5 Keep `EP4` and `EP3` evidence separate

`docs/deployment/dual-vm-acceptance-results.md` is still `EP3`-level harness evidence.

The parent may reuse it as substrate evidence, but should not claim that the dual-VM harness alone
already satisfies the governed paper execution bar.

---

## 8. Suggested Parent Closeout Shape

If `Claude` wants the shortest path to a reviewable `OSS-004A`, this sidecar suggests delivering:

1. one compact authority-path note or matrix that names:
   - caller token path
   - execution-only token owner
   - runtime identity owner
   - telemetry authority refs
   - OpenClaw adapter boundary
2. one explicit statement of what remains deferred to `OSS-004B` and `OSS-004C`
3. one evidence list that points only to already-existing canonical/runtime docs and the tested
   runtime-manager route surface

That would be enough to make the parent task reviewable without editing L1 truth.

---

## 9. Local Verification Performed For This Sidecar

Executed in the current workspace:

```bash
python3 -m unittest services/runtime-manager/test_runtime_manager.py
```

Result:

- `8` tests passed

Not claimed as fresh evidence in this sidecar:

- a new dual-VM run
- a new live OpenClaw gateway smoke
- a new paper-runtime execution proof

The packet therefore relies on existing repo evidence for those broader surfaces and does not widen
the proof claim beyond what was actually rerun here.

---

## 10. Handoff

Recommended reviewer disposition:

- reviewer: `Gemini`
- parent owner to absorb or ignore: `Claude`

Suggested handoff summary:

> `OSS-004A` already has explicit partial truth for runtime auth presence, VM-2 token isolation,
> bootstrap paper-runtime identity, telemetry authority refs, and the OpenClaw adapter boundary.
> The remaining parent work is to consolidate those into one explicit authority-path packet without
> overclaiming beyond the current `EP3` harness and without absorbing `OSS-004B` / `OSS-004C`.
