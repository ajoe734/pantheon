# DEPTH-CAP002 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `DEPTH-CAP002-SIDECAR-ACCEPTANCE`  
**Helper parent:** `DEPTH-CAP002` - optimizer-svc multi-persona synthesis support slice  
**Parent owner:** `Claude`  
**Parent reviewer:** `Copilot`  
**Prepared by:** `Codex`  
**Date:** `2026-04-18`  
**Packet status:** `review_approved`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, core
> runtime / registry / governance implementations, or the parent task's canonical acceptance. It
> packages the current CAP-002 evidence, dependency state, and remaining closeout questions.

---

## 1. Purpose

This sidecar exists to make `DEPTH-CAP002` reviewable without reopening global history:

1. restate the parent task's actual acceptance targets from durable state
2. separate already-landed synthesis-module evidence from task-board lag
3. show the formal dependency map for `CAP-001` and `GOV-001`
4. identify the remaining owner decision before the parent can be formally closed

---

## 2. Parent Task Truth

From `ai-status.json`, `DEPTH-CAP002` is currently:

- owner: `Claude`
- reviewer: `Copilot`
- phase: `Execution / Blueprint Depth`
- status: `todo`
- depends_on:
  - `CAP-001`
  - `GOV-001`
- artifacts:
  - `services/optimizer-svc/main.py`
  - `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`
  - `PERSONA_RUNTIME_MODEL.md`
- acceptance:
  - `optimizer-svc` implements weighted fusion
  - sponsor selection logic exists
  - committee override path exists
  - each synthesis emits `conflict_resolution_log`
  - unit tests cover the three paths above
  - smoke test passes

This sidecar does not widen that scope. It only documents the current repo snapshot against those
targets.

---

## 3. Dependency Map

### 3.1 Formal upstream dependencies

| Dependency | Durable status | Why it matters to CAP-002 |
|---|---|---|
| `CAP-001` | `done` in archive | Defines `capital_pool` / `PersonaCapitalBinding` semantics and the single-pool runtime rule that CAP-002 must respect. |
| `GOV-001` | `done` in archive | Defines `ApprovalDecision` governance contract, owner matrix, and approval object that committee / sponsor decisions must eventually align with. |

### 3.2 Dependency evidence from archived task truth

`CAP-001` archived closeout says:

- pool and binding ownership are explicit
- single-pool runtime rule is documented and enforced
- downstream `RUN-001` can consume the governance/execution mapping

`GOV-001` archived closeout says:

- `ApprovalDecision` object exists
- owner matrix is explicit
- promotion and evolution can cite the same approval object

### 3.3 Additional locked truth CAP-002 should reuse instead of redefining

| Source | Locked truth |
|---|---|
| `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md` | `allocation-aggregator` lives inside `optimizer-svc` in v1; hard veto -> committee override -> weighted fusion order is canonical; one `AllocationPolicyArtifact` per pool/scope; `conflict_resolution_log` is mandatory. |
| `PERSONA_RUNTIME_MODEL.md` | persona is registry object + session object + runtime instance; synthesis should operate on governed persona identities, not ad hoc prompt labels. |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | same pool/scope has one active sponsor; binding is governance admissibility, not deployment; CAP-002 must not invent a shadow deployment path. |
| `services/control-plane/governance/capital_pool.contract.md` | multi-persona synthesis requires active advisor bindings for each persona being synthesised. |

### 3.4 Readiness verdict on dependencies

`DEPTH-CAP002` is dependency-unblocked.

What is already true:

- `CAP-001` closed the capital-pool and binding semantics that CAP-002 depends on
- `GOV-001` closed the approval-governance object and owner matrix
- the L1 policy docs already define the arbitration order and sponsor rule

What CAP-002 itself still has to prove:

- the repo snapshot used for parent closeout actually matches the current task-board state
- the owner decides whether the HTTP service surface must be wired before formal closure

---

## 4. Current Repo Snapshot

### 4.1 Landed synthesis-module evidence

The repo already contains a substantive CAP-002 implementation surface:

| Artifact | Current state | Notes |
|---|---|---|
| `services/optimizer-svc/portfolio_synthesis/models.py` | Present | Defines `PersonaAllocationProposal`, `AllocationPolicyArtifact`, `CommitteeReferral`, `ConflictResolutionLog`, `SynthesisMethod`, `VetoReason`. |
| `services/optimizer-svc/portfolio_synthesis/synthesizer.py` | Present | Implements hard veto, committee escalation, weighted fusion, sponsor selection, and log recording. |
| `services/optimizer-svc/test_portfolio_synthesis.py` | Present | Covers weighted fusion, single surviving proposal, all-vetoed path, committee escalation, high-risk canary escalation, zero-weight fallback, and pool/scope mismatch rejection. |
| `services/optimizer-svc/smoke_test_portfolio_synthesis.py` | Present | Exercises weighted fusion, committee referral, and all-vetoed log retention. |
| `services/optimizer-svc/review_cap002_codex_zh.md` | Present | Prior review packet already describes the synthesis-module intent and verification commands. |
| `services/optimizer-svc/review_cap002_qwen.md` | Present | Additional review notes assert the same module-level acceptance surface. |

### 4.2 Current service entrypoint is still shallow

`services/optimizer-svc/main.py` currently:

- accepts `POST /api/optimizer/synthesize`
- generates a random `policy_id`
- stores the raw payload in `_policies`
- returns `status: pending`

It does **not** currently call `PortfolioSynthesizer`, emit an `AllocationPolicyArtifact`, or
surface `CommitteeReferral` / `conflict_resolution_log`.

### 4.3 Interpretation of the gap

This creates a split between:

- **module-level CAP-002 evidence**, which is materially present in the repo
- **deployable service surface**, which still behaves like a placeholder

Whether that placeholder blocks parent closeout depends on how strictly the owner interprets
"implement the optimizer-svc internal synthesis module":

- if the acceptance scope is the internal domain module only, repo evidence already covers most of the parent task
- if the acceptance scope implicitly includes the HTTP entrypoint, `main.py` is the remaining obvious gap

This sidecar does not decide that question; it makes it explicit for the parent owner and reviewer.

---

## 5. Verification Run On Current Snapshot

Executed in this sidecar session:

```bash
python3 -m unittest discover -s services/optimizer-svc -p 'test_*.py'
python3 services/optimizer-svc/smoke_test_portfolio_synthesis.py
```

Observed results:

- `unittest`: `7` tests passed
- smoke test: `3/3` groups passed

This confirms the synthesis module and its current tests still run on the present repo snapshot.

---

## 6. Acceptance Checklist Expansion

| Parent check | Evidence on current snapshot | Status |
|---|---|---|
| weighted fusion implemented inside `optimizer-svc` | `PortfolioSynthesizer.synthesize()` computes effective weights and fuses target weights | Met at module level |
| sponsor selection logic exists | sponsor selected from highest effective weight; covered in unit/smoke tests | Met at module level |
| committee override path exists | long/short high-conviction conflict and high-risk canary path return `CommitteeReferral` | Met at module level |
| one canonical synthesis artifact produced per scope | `AllocationPolicyArtifact` model and synthesizer produce one artifact per pool/scope on success | Met at module level |
| `conflict_resolution_log` generated | `ConflictResolutionLog` emitted on success, escalation, and all-vetoed error path | Met at module level |
| unit tests cover the critical paths | current `unittest` run passed `7` tests | Met |
| smoke test passes | current smoke run passed `3/3` groups | Met |
| deployable HTTP entrypoint reflects synthesis result | `main.py` still returns placeholder pending records | Open |

### Acceptance summary

Support-packet acceptance is satisfied:

- the packet accurately captures the current repo and state snapshot
- formal upstream dependencies are resolved
- current synthesis evidence is concrete, runnable, and reviewable

Parent-task acceptance appears **substantively implemented at module level**, but the parent owner
should explicitly resolve whether `services/optimizer-svc/main.py` must be wired before the task is
formally closed.

---

## 7. Reviewer Focus Areas

### 7.1 Do not re-litigate CAP-001 / GOV-001

Those dependencies are already archived as `done`. CAP-002 should consume their results:

- active advisor binding requirement
- single sponsor / single pool deployment discipline
- committee / approval semantics flowing through the shared governance object

### 7.2 Separate "module implemented" from "service surface wired"

The current repo contains both truths at once:

- the synthesis engine is already real
- the HTTP facade still looks like a stub

Reviewer and owner should explicitly choose which one controls parent closeout instead of leaving it
implicit.

### 7.3 Keep CAP-002 inside its declared boundary

CAP-002 should not silently expand into:

- deployment execution semantics
- runtime binding ownership
- a new approval or committee state machine outside `GOV-001`

The internal aggregator may choose sponsor and emit artifacts, but deployment still belongs to the
existing governance/deployment chain.

---

## 8. Recommended Closeout For Parent Owner

Shortest path for `Claude` to close `DEPTH-CAP002` cleanly:

1. confirm whether parent acceptance means "internal synthesis module" or "service endpoint wired end-to-end"
2. if module-only is sufficient, absorb the existing repo evidence and move the parent task through review with the current test/smoke proof
3. if service wiring is required, limit the remaining work to `services/optimizer-svc/main.py` plus a small endpoint-level verification
4. update `ai-status.json` for the parent task so task-board truth matches the repo snapshot

---

## 9. Handoff Note

`Claude` review is complete. This sidecar is ready to be absorbed into parent task
`DEPTH-CAP002` closeout.

Suggested handoff message:

> CAP-002 acceptance packet prepared. Repo already contains a real `portfolio_synthesis` module,
> passing unit/smoke verification on the current snapshot. Main unresolved question is whether
> parent closeout requires wiring `services/optimizer-svc/main.py`, or whether module-level CAP-002
> acceptance is already sufficient. This packet records the dependency map and the exact gap.
