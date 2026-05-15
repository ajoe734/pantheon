# OSS-004A Review Packet

**Sidecar kind:** `review_packet`
**Sidecar task:** `OSS-004A-SIDECAR-REVIEW`
**Helper parent:** `OSS-004A` — stabilize the runtime auth/authority path for EP4
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Prepared by:** `Claude`
**Date:** `2026-04-19`
**Packet status:** `ready for Codex review; prepared from current repo snapshot`

> Scope constraint: support artifact only. This packet does not edit canonical truth, runtime
> contracts, telemetry schema truth, or the parent implementation. It packages reviewer-focused
> guidance, evidence mapping, and gap analysis for `OSS-004A`.

---

## 1. Purpose

This review packet supports `Codex` as the designated reviewer for `OSS-004A`. It does three things:

1. restates the parent task's stated acceptance criteria and intended proof surface
2. maps current repo evidence to each acceptance criterion and flags partial or missing coverage
3. records review-specific concern areas that are most likely to surface disagreements

This packet is complementary to the acceptance sidecar at
`support/sidecars/OSS-004A/OSS-004A-SIDECAR-ACCEPTANCE.md`, which describes what the parent must
do. This packet describes what the reviewer should check when the parent comes in for review.

---

## 2. Parent Task Summary

From `ai-status.json` and the accepted Phase 7 planning session:

- **Task ID:** `OSS-004A`
- **Owner:** `Claude` (original: `Gemini` per materialization; reassigned by supervisor)
- **Reviewer:** `Codex`
- **Phase:** Phase 7 — EP4 Proof Raising
- **Status at sidecar prep:** `todo`
- **Formal depends-on:** none (unblocked)

**Stated acceptance criteria:**

1. Runtime auth and authority path is explicit
2. Dual-VM paper proof no longer depends on ambiguous identity or token assumptions

**Intended proof surface (from Phase 7 planning):**

- runtime-manager token flow
- paper-runtime identity
- telemetry authority references
- OpenClaw/Pantheon adapter boundary

**Downstream dependency:** `OSS-004A` must close before `OSS-004B` replaces the bootstrap runtime,
which must close before `OSS-004C` runs the integrated governed paper acceptance.

---

## 3. What the Parent Must Deliver

For the reviewer to approve `OSS-004A`, the parent delivery should include at minimum:

| Deliverable | Rationale |
|---|---|
| Explicit authority-path statement | Names the token path, execution-only token owner, runtime identity, telemetry authority refs, and OpenClaw adapter boundary in one place |
| Explicit deferral boundary | Names what is still `OSS-004B` (bootstrap → final package) and `OSS-004C` (integrated governed paper run) scope |
| Evidence list | Points to existing canonical/runtime docs and local test results; does not invent new proof claims |

The parent is **not** required to:

- upgrade `runtime-manager` auth to production-grade JWT validation
- rerun the dual-VM harness as an `EP4` acceptance run (that is `OSS-004C`)
- replace the bootstrap runtime package (that is `OSS-004B`)

---

## 4. Evidence Map

### 4.1 Runtime-manager token flow — partial

| File | What it proves | What it does not prove |
|---|---|---|
| `services/runtime-manager/main.py` | All write routes require a non-empty `Authorization: Bearer` header | Token validity (JWT/issuer/audience/expiry) is not validated; stub-level only |
| `services/runtime-manager/test_runtime_manager.py` | Current local suite passes (`38 passed` in this repo snapshot), including bearer-presence gate coverage | No integration test with real token issuance or full RBAC path |

**Reviewer verdict guidance:** Accept if the parent states explicitly that auth is present-gate only,
names the stub limitation, and defers full token validation beyond `OSS-004A` scope.
Reject if the parent implies full production-grade auth is in place.

### 4.2 Paper-runtime identity — partial (dedicated package, not integrated proof)

| File | What it proves | What it does not prove |
|---|---|---|
| `services/execution/lean_runtime/runtime_bootstrap.py` | Paper roles (`pantheon-paper-execution-runtime`, `pantheon-lean-paper-runtime`) dispatch into `paper_runtime.main()`; non-paper sidecars report `stub_mode = False` | Does not by itself prove an integrated governed paper run, broker acknowledgement, or a live execution session |
| `docs/deployment/dual-vm-acceptance-results.md` | Records the current VM-2 surface as a dedicated paper execution runtime package and explicitly says it is not the first integrated governed paper execution packet | Does not claim `OSS-004C` is complete; remains substrate / harness evidence only |
| `docs/deployment/exec-vm-secrets-guide.md` | States VM-2 owns the execution-only bearer token and that the current service is the VM-2 paper execution package for EP4 proof raising | Does not prove that the broader runtime packaging backlog is fully closed |

**Reviewer verdict guidance:** Accept if the parent names the current dedicated paper execution
package truthfully, preserves the VM-2 token boundary, and still defers the integrated governed
paper execution proof to `OSS-004C`. Reject if the parent either falls back to obsolete
"bootstrap-only" wording or implies the current package alone already satisfies `EP4` governed
paper execution.

### 4.3 Telemetry authority references — met

| File | What it proves |
|---|---|
| `services/telemetry/telemetry_event.schema.json` | Requires `binding_id`, `runtime_id`, `capital_pool_id`, `artifact_id`, `artifact_version`, `deployment_stage`, `plan_id`, `persona_capital_binding_id` — full authority chain |
| `docs/deployment/dual-vm-acceptance-results.md` | VM-1 telemetry ingest already accepts events whose binding identity is resolved from VM-2 |

**Reviewer verdict guidance:** This criterion is already met. Reviewer should confirm the parent
does not introduce a renamed authority field (e.g., re-introducing `runtime_binding_id`) that
conflicts with canonical schema naming.

### 4.4 OpenClaw/Pantheon adapter boundary — met

| File | What it proves |
|---|---|
| `integrations/openclaw/integration.md` | OpenClaw is external runtime dependency; `openclaw-gateway-adapter` is the only mapping seam; OpenClaw has no authority over registry, approval, capital, runtime bindings, or LEAN deployment |
| `OPENCLAW_RUNTIME_CONTRACT.md` | Per-agent workspace, per-agent auth profile, no implicit credential sharing; sessions carry full identity fields |

**Reviewer verdict guidance:** This criterion is already met. Reviewer should confirm the parent
does not suggest OpenClaw is a deployment or governance authority.

---

## 5. Review Checklist

Use this checklist when evaluating the parent delivery.

| # | Check | Pass condition | Flag if |
|---|---|---|---|
| R-1 | Auth surface is explicit without overclaiming | Parent states auth is present-gate bearer check and names stub limitation | Parent claims JWT validation or full production auth is in place |
| R-2 | VM-2 token boundary is named correctly | Parent references `docs/deployment/exec-vm-secrets-guide.md` or equivalent; execution-only token is clearly `VM-2` scoped | Parent says VM-1 owns execution token or that cross-plane token sharing is acceptable |
| R-3 | Current paper-runtime identity is named without implying integrated proof | Parent references the current VM-2 paper execution package truthfully and explicitly defers broader packaging / integrated proof closure to `OSS-004B` and `OSS-004C` | Parent implies the current package alone already satisfies the full `EP4` governed paper execution substrate |
| R-4 | Telemetry authority refs use canonical naming | Parent uses `binding_id` as the canonical authority anchor | Parent introduces `runtime_binding_id` or other legacy aliases as canonical |
| R-5 | OpenClaw boundary is reused, not redefined | Parent cites the existing adapter boundary without redefining OpenClaw's authority | Parent assigns OpenClaw any authority over governance, deployment, or binding state |
| R-6 | Deferral boundary is explicit | Parent explicitly names what it does NOT close — i.e., states `OSS-004B` and `OSS-004C` remain out of scope | Parent absorbs `OSS-004B` or `OSS-004C` scope silently |
| R-7 | No `EP4` overclaim | Parent does not claim the integrated governed paper run has been completed | Parent implies `EP4` proof is done when only `OSS-004A` substrate work is complete |
| R-8 | Evidence list is anchored to existing canonical files | Parent evidence list points to files that exist in the current repo state | Parent cites files or runs that do not exist or have not been executed |

---

## 6. Known Risk Areas

These are the areas most likely to produce a review failure or a reopen request:

### 6.1 Auth overclaim

The strongest risk is that the parent says "runtime auth and authority path is explicit" in a way
that implies production-grade validation. The reviewer must confirm this is scoped to bearer
presence only.

### 6.2 Legacy packaging wording / EP4 conflation

The second risk is that the existing VM-2 package evidence is miscited as `EP4` governed paper
execution proof. The current repo snapshot now has a dedicated paper execution package on VM-2, but
the documentation still explicitly stops short of claiming the first integrated governed paper run.
The parent should use this only as substrate evidence, not as `OSS-004C` completion.

### 6.3 Canonical naming drift

If the parent introduces any new field aliases for the telemetry authority chain, it may conflict
with the schema canonical truth. The reviewer should check that `binding_id` is used as the anchor,
not any deprecated alias.

### 6.4 Scope bleed into OSS-004B

If the parent starts closing runtime packaging debt, compose topology, or "final package" claims
instead of just making the auth/authority path explicit, it may be absorbing `OSS-004B` work. That
is a scope violation for this task.

---

## 7. Scoping Boundary Summary

| In scope for `OSS-004A` | Out of scope — belongs to `OSS-004B` or later |
|---|---|
| Naming and documenting the token flow | Implementing JWT or production-grade token validation |
| Naming the VM-2 execution-only token boundary | Re-scoping or silently closing the remaining runtime packaging backlog |
| Naming the current paper-runtime identity and VM-2 package boundary | Running a full governed paper execution acceptance |
| Citing the existing telemetry authority schema | Publishing the EP4 evidence packet (`OSS-004D`) |
| Citing the existing OpenClaw adapter boundary | Assigning OpenClaw any new execution authority |

---

## 8. Reviewer Disposition Guide

When this task enters `review`, `Codex` should evaluate the delivery against the checklist above
and choose one of:

- **Approve** (`approve` command): all R-1 through R-8 pass; the parent has delivered an explicit
  authority-path summary that is correctly bounded and does not overclaim
- **Reopen** (`reopen` + required changes): one or more checklist items fail; state the specific
  failing item and what concrete change is required before re-review
- **Blocker** (`blocker` command): a required canonical file or execution artifact referenced by
  the parent is missing from the repo and cannot be verified

After approval, return the task to `Claude` (parent owner) for final `done` close.

---

## 9. Related Artifacts

| Artifact | Role |
|---|---|
| `support/sidecars/OSS-004A/OSS-004A-SIDECAR-ACCEPTANCE.md` | Acceptance checklist expansion and dependency map prepared by `Codex`; complementary to this packet |
| `services/runtime-manager/main.py` | Canonical runtime auth surface |
| `services/runtime-manager/test_runtime_manager.py` | Local auth gate test suite |
| `services/execution/lean_runtime/runtime_bootstrap.py` | Current paper-runtime identity |
| `docs/deployment/exec-vm-secrets-guide.md` | VM-2 secret and token boundary plus current paper execution package wording |
| `docs/deployment/dual-vm-acceptance-results.md` | Cross-plane harness and package evidence; still not the integrated EP4 proof packet |
| `services/telemetry/telemetry_event.schema.json` | Telemetry authority schema |
| `integrations/openclaw/integration.md` | OpenClaw adapter boundary |
| `OPENCLAW_RUNTIME_CONTRACT.md` | OpenClaw session isolation policy |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | Canonical deployment chain policy |
| `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` | EP3/EP4 evidence bar definitions |

---

## 10. Handoff

This packet is ready for `Codex` as reviewer.

Reviewer next action: hold this packet until `OSS-004A` enters `review` state, then use sections
5 and 6 of this document as the primary review guide.

Parent owner next action: `Claude` may absorb section 3 (what to deliver) and section 8 (reviewer
disposition guide) as implementation guidance for the parent closeout shape.
