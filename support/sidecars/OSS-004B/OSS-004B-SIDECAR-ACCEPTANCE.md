# OSS-004B Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `OSS-004B-SIDECAR-ACCEPTANCE`  
**Helper parent:** `OSS-004B` - replace bootstrap paper runtime with final truthful paper execution package  
**Parent owner:** `Codex`  
**Parent reviewer:** `Claude`  
**Prepared by:** `Codex2`  
**Date:** `2026-04-19`  
**Packet status:** `review approved; ready for owner finalization and archival`

> Scope constraint: support artifact only. This packet does not modify canonical truth, L1 policy,
> runtime/deployment contracts, registry or governance truth, or the parent implementation. It
> packages the current OSS-004B acceptance surface, dependency map, and reviewer handoff context.

---

## 1. Purpose

This sidecar exists to reduce archival/review lookup cost for `OSS-004B` by doing four things:

1. restate the active parent-task truth from planning/state without reopening global history
2. map the landed VM-2 runtime-package artifacts to the parent acceptance boundary
3. separate `OSS-004B` proof from the already-closed `OSS-004A` auth substrate and the later
   integrated `OSS-004C` acceptance packet
4. hand `Codex` a compact reviewer surface for sidecar closeout without reopening broad history

This is intentionally narrower than implementation work. It is meant to support archival/review
lookup without widening scope into canonical contract edits or the later EP4 proof run.

---

## 2. Parent Task Truth

From the archived task/state snapshot for the completed parent task:

- owner: `Codex`
- reviewer: `Claude`
- phase: `Phase 7: EP4 Proof Raising`
- status: `done`
- formal dependency: `OSS-004A`
- recorded acceptance:
  - `bootstrap wrapper replaced or formally retired`
  - `paper execution package is the truthful EP4 substrate`

The accepted phase-7 planning session framed `OSS-004B` as the slice that should:

- replace the VM-2 bootstrap paper-runtime role with a truthful paper execution package or
  concrete signal-consumer path
- stop treating DEPLOY-009 as only bootstrap health evidence
- prepare the substrate that `OSS-004C` will later use for the first integrated governed paper
  acceptance run

`OSS-004A` is already archived as `done`, so the authority-path prerequisite is satisfied. The
parent archive note records `OSS-004B` as completed on commit `20c902d7cad521ef64d5ee61042c75aec27bfa8a`,
which means this sidecar should be judged on whether it accurately captures that completed package
replacement truth, not on unresolved auth ambiguity.

---

## 3. Sidecar Scope Boundary

In scope for this sidecar:

- inspect the current repo snapshot for the VM-2 runtime-package artifacts named in the parent
  closeout note
- rerun low-cost verification that directly matches the parent review surface
- assemble an acceptance checklist and dependency map for the assigned sidecar reviewer
- keep the packet support-only so the parent owner can decide whether to absorb it into the main
  review trail

Out of scope:

- editing `services/execution/lean_runtime/*`, `docker-compose.exec.yml`,
  `env/prod-exec.env.example`, deployment docs, or any canonical truth file
- executing a real dual-VM governed paper run
- claiming `OSS-004C` acceptance on behalf of the later integrated proof packet
- finalizing the parent task lifecycle from inside this sidecar

---

## 4. Current Repo Snapshot

### 4.1 Bootstrap-only behavior is retired for the paper runtime role

`services/execution/lean_runtime/runtime_bootstrap.py` now states its purpose explicitly:

- legacy bootstrap-only behavior is retired for the paper runtime role
- paper runtime roles dispatch into the truthful paper runtime package
- backward-compatible role aliases still resolve to the same runtime entrypoint

Current behavior from the file:

- default role is `pantheon-paper-execution-runtime`
- both `pantheon-paper-execution-runtime` and `pantheon-lean-paper-runtime` call `paper_runtime.main()`
- non-paper roles stay lightweight execution sidecars with health-only behavior

That means the old "paper role equals bootstrap stub" interpretation is no longer true in the code
path used by VM-2 paper execution.

### 4.2 A concrete paper execution runtime package now exists

`services/execution/lean_runtime/paper_runtime.py` provides the real package surface the parent
task claimed:

- `PendingSignalStore` integration for pulling pending signals
- `SignalConsumer` wiring for execution consumption
- `RuntimeBindingResolver` to resolve the active `RuntimeBinding` from `runtime-manager`
- `RuntimeTelemetryEmitter` to emit canonical telemetry envelopes with binding/runtime/plan refs
- an in-process paper execution algorithm that simulates fills and maintains positions
- HTTP health/admin surface via `ThreadingHTTPServer` with `"/"`, `"/health"`, and `"/__health__"`

This is materially different from the earlier bootstrap story. The repo now has an execution-side
runtime package with a concrete signal-consumer path and runtime-manager-aware identity/binding
surface.

### 4.3 VM-2 packaging and env surfaces now match the truthful runtime story

`docker-compose.exec.yml` now describes the VM-2 stack as:

- `runtime-manager`
- a truthful paper execution runtime package
- mock broker/exchange sidecars behind an execution-only secret boundary

The `pantheon-paper-runtime` service now sets:

- `PANTHEON_RUNTIME_ROLE: pantheon-paper-execution-runtime`
- runtime-manager URL/token
- workspace and auth-profile refs inherited from `OSS-004A`
- signal-store URL and queue key
- broker/exchange adapter URLs
- explicit paper runtime id

`env/prod-exec.env.example` aligns with that compose surface:

- names the dedicated VM-2 execution-plane stack
- keeps runtime-manager token and execution secrets on VM-2
- defines `PANTHEON_PAPER_RUNTIME_ID`, signal queue key, and broker/exchange runtime ids
- preserves the later live-runtime profile as optional, instead of conflating it with paper proof

### 4.4 Deployment docs and smoke harness stop claiming bootstrap-only behavior

The deployment-facing evidence was updated consistently with the runtime code:

- `docs/deployment/dual-vm-acceptance-results.md` says the repo now ships a dedicated VM-2 paper
  execution runtime package on top of the DEPLOY-008 split
- that same doc explicitly says the package still does not prove the first integrated governed
  paper execution packet; `OSS-004C` still owns that proof
- `scripts/smoke_test_dual_vm.sh` now says the VM-2 paper runtime exposes the truthful paper
  execution package, not the old bootstrap-only stub
- the smoke summary records `"paper_runtime_bootstrap_stub": false`
- the smoke note repeats that `OSS-004C` remains the packet for the first integrated governed
  paper execution run

This is the correct boundary: `OSS-004B` upgrades the VM-2 substrate truthfully without silently
claiming the later end-to-end EP4 packet.

### 4.5 Sidecar verification rerun aligns with the parent review note

I reran the low-cost verification named in the parent task summary:

```bash
python3 -m unittest \
  services.execution.lean_runtime.test_signal_consumer \
  services.execution.lean_runtime.test_runtime_identity \
  services.execution.lean_runtime.test_paper_runtime

python3 -m py_compile \
  services/execution/lean_runtime/pending_signal_store.py \
  services/execution/lean_runtime/paper_runtime.py \
  services/execution/lean_runtime/runtime_bootstrap.py \
  services/execution/lean_runtime/test_paper_runtime.py

timeout 2s env SIGNAL_STORE_URL='' \
  PANTHEON_RUNTIME_ROLE='pantheon-paper-execution-runtime' \
  PANTHEON_RUNTIME_MANAGER_URL='http://runtime-manager:8081' \
  PANTHEON_RUNTIME_MANAGER_TOKEN='runtime-control-internal' \
  PANTHEON_WORKSPACE_REF='workspace-paper' \
  PANTHEON_AUTH_PROFILE_REF='auth-profile-paper' \
  PANTHEON_PAPER_RUNTIME_ID='paper-runtime-001' \
  python3 services/execution/lean_runtime/runtime_bootstrap.py
```

Observed results:

- the unittest command exited `0`
- `12` tests passed
- `py_compile` exited `0`
- the `timeout 2s` bootstrap launch exited `124`, which is expected for a long-running runtime
  process being intentionally cut off after startup confirmation

This sidecar does not add a new production proof claim. It confirms the current parent review note
still matches the repo snapshot.

---

## 5. Acceptance Checklist

This checklist expands the short parent acceptance into reviewer-facing gates.

| Check | What "done" means for `OSS-004B` | Current snapshot |
|---|---|---|
| AC-1 Bootstrap paper-runtime role retired | The VM-2 paper runtime role no longer resolves to a bootstrap-only import/health stub | Met |
| AC-2 Truthful paper runtime package exists | Repo contains a concrete signal-consumer/runtime package, not only wrapper health checks | Met |
| AC-3 Runtime package can resolve binding and emit telemetry-ready envelopes | Binding lookup and telemetry envelope surfaces exist in the paper runtime package | Met |
| AC-4 VM-2 packaging matches the new runtime role | `docker-compose.exec.yml` and `env/prod-exec.env.example` wire the paper execution package and execution-only secret boundary coherently | Met |
| AC-5 Deployment docs stop overclaiming bootstrap-only behavior | Deployment docs and smoke harness describe the package truthfully and stop calling it only a stub | Met |
| AC-6 Verification evidence matches the claimed package surface | Unit tests and syntax/import verification still pass on the current repo snapshot | Met |
| AC-7 Later EP4 acceptance remains separated | The runtime-package upgrade does not falsely claim the first integrated governed paper run; that stays in `OSS-004C` | Met |

### Acceptance interpretation

Based on the current repo snapshot, the `OSS-004B` implementation remains consistent with its
already-recorded completion/archive note:

- the bootstrap wrapper is retired for the paper-runtime role
- the truthful VM-2 paper execution package is present and wired into compose/env/docs
- the sidecar rerun matches the verification story already recorded in parent state

The remaining gate for this helper slice is reviewer approval of the sidecar packet itself. This
file does not change parent-task truth; it packages the evidence so the sidecar reviewer does not
need to reconstruct it from scratch.

---

## 6. Dependency Map

### 6.1 Hard upstream task dependency

| Task | Status | Relevance |
|---|---|---|
| `OSS-004A` | `done` (archived) | supplies the explicit runtime auth/authority path, workspace/auth-profile refs, and adapter-boundary clarity that `OSS-004B` now consumes |

### 6.2 Repo-local implementation dependencies

| Input | Why it matters |
|---|---|
| `services/execution/lean_runtime/runtime_bootstrap.py` | owns role dispatch and proves the paper-runtime role is no longer bootstrap-only |
| `services/execution/lean_runtime/paper_runtime.py` | provides the concrete paper execution runtime package |
| `services/execution/lean_runtime/pending_signal_store.py` | gives the runtime a real pending-signal retrieval surface |
| `services/execution/lean_runtime/signal_consumer.py` | gives the runtime a concrete signal-consumer path |
| `services/runtime-manager/runtime_manager_client.py` | supports runtime-side binding resolution against authoritative runtime-manager state |
| `docker-compose.exec.yml` | packages the VM-2 execution-plane services around the new paper runtime |
| `env/prod-exec.env.example` | documents the execution-only env boundary and runtime ids needed to run the package |
| `scripts/smoke_test_dual_vm.sh` | exposes the updated VM-2 runtime proof surface to later dual-VM acceptance work |
| `docs/deployment/dual-vm-acceptance-results.md` | documents what the new package proves and what still belongs to `OSS-004C` |

### 6.3 Downstream tasks unblocked or clarified by this parent

| Task | Relation | Why it matters |
|---|---|---|
| `OSS-004C` | hard downstream | needs `OSS-004B` complete so the first integrated governed paper acceptance runs against the truthful VM-2 runtime package instead of a bootstrap stub |
| `OSS-004D` | transitive downstream | later EP4 evidence publication depends on the integrated `OSS-004C` run, which in turn depends on `OSS-004B` |

### 6.4 Sequencing summary

```text
OSS-004A (authority path explicit, done)
        |
        v
OSS-004B (truthful VM-2 paper execution package, done)
        |
        v
OSS-004C (first integrated governed paper acceptance packet)
        |
        v
OSS-004D (publish EP4 evidence packet and reconcile status truth)
```

Practical meaning:

- `OSS-004A` removed ambiguity about identity/auth/authority
- `OSS-004B` replaces the VM-2 runtime substrate
- `OSS-004C` is still the first task allowed to claim the integrated governed paper run

---

## 7. Reviewer Handoff Notes

For `Codex` as the assigned sidecar reviewer:

1. confirm this packet matches the parent archive note: runtime bootstrap retired, truthful paper
   package added, compose/env/docs updated, and verification rerun still green
2. confirm the dependency map keeps `OSS-004A` as satisfied upstream truth and `OSS-004C` as the
   later integrated proof owner
3. confirm the packet stays support-only and does not try to rewrite canonical EP4 semantics
4. approve the sidecar if it is useful as an acceptance/archival support packet for the completed
   parent task

Suggested review commands:

```bash
AI_AGENT=Codex python3 scripts/ai_status.py approve OSS-004B-SIDECAR-ACCEPTANCE \
  "Acceptance packet approved: OSS-004B support snapshot accurately captures the completed truthful VM-2 paper execution package, dependency chain, and remaining OSS-004C boundary."
```

If corrections are needed:

```bash
AI_AGENT=Codex python3 scripts/ai_status.py reopen OSS-004B-SIDECAR-ACCEPTANCE \
  "Describe the specific packet corrections needed."
```

Reviewer disposition on this snapshot:

- `2026-04-19`: sidecar reviewer approved the packet as an accurate support-only summary of the
  completed truthful VM-2 paper execution package, dependency chain, and retained `OSS-004C`
  boundary
- remaining lifecycle action is owner finalization of the sidecar task to `done`

---

## 8. Sidecar Scope Declaration

This file is the only artifact created by this sidecar pass.

- no L1 or L2 canonical document was modified
- no runtime, deployment, registry, governance, or telemetry implementation file was modified by
  this sidecar
- no global summary file was edited manually
- parent-task ownership and review state remain unchanged
- whether to absorb this packet into the parent review trail remains a parent-owner decision

*Prepared by Codex2 for the `OSS-004B-SIDECAR-ACCEPTANCE` sidecar slice. This file is
intentionally support-only and does not modify canonical truth.*
