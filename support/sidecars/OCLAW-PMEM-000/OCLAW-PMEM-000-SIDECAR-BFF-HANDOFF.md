# OCLAW-PMEM-000 BFF and Frontend Handoff Packet

**Sidecar Task ID**: `OCLAW-PMEM-000-SIDECAR-BFF-HANDOFF`  
**Parent Task**: `OCLAW-PMEM-000`  
**Parent Owner**: `Codex`  
**Parent Reviewer**: `Claude`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-12  
**Mutates Canonical**: `no`

This packet is support material only. It does not change canonical truth or
implement BFF, frontend, Memory Plane, OpenClaw, provider, registry, runtime,
or governance behavior. The parent owner decides whether and how to absorb it
into the umbrella closeout.

## 1. Current BFF Baseline

The original gap spec said `GET /bff/personas/{persona_id}/memory` used a
missing optional read-store method. That statement is historical on the current
`dev` baseline and must not be copied into the parent closeout as a remaining
gap.

The route now calls `_retrieve_canonical_persona_memory`, targets
`GET /api/memory/retrieve`, sends the requested persona and `scope=persona`, and
does not fall back to BFF snapshots or workspace files. Its metadata exposes:

- `kind: canonical_memory_plane`;
- whether the source was available;
- `fallback_used: false`;
- `workspace_is_source_of_truth: false`;
- a stable unavailable/invalid/access-denied reason and repair action; and
- the Memory Plane authorization policy version on success.

Focused BFF tests cover an unconfigured source and a successful canonical read.
The umbrella owner should treat this as component evidence, not hosted proof.

## 2. Umbrella Query and Evidence Gaps

| Operator question | Existing BFF/support surface | Umbrella evidence still required |
|---|---|---|
| Which provider/model should this persona use? | `GET /bff/personas/{persona_id}/runtime-profile` | Correlate desired profile with the observed OpenClaw agent identity, model, workspace, and sync generation. |
| Is canonical persona memory readable? | `GET /bff/personas/{persona_id}/memory` | Hosted response must show source availability, canonical IDs, and the same persona identity used by the run. |
| Was that memory materialized into OpenClaw? | Accepted persona-memory bridge artifacts | Compare canonical IDs with `memory/context.json`/`MEMORY.md` provenance for the same generation; workspace files remain derived cache. |
| Is the required provider actually usable? | Assistant provider readiness/auth surfaces and OCLAW-PMEM-005 gates | Keep credential/auth readiness separate from a fresh live invoke result for the exact required provider/model path. |
| Can private memory cross personas? | Memory authorization and bridge isolation tests | Archive a sanitized hosted negative probe; report only safe IDs and pass/fail, never foreign private content. |
| Is the Management UI truthful? | BFF projections plus `execute-plans` frontend | Prove strict live-BFF rendering of available-empty, unavailable, drifted, and failed-smoke states without browser-side truth synthesis. |

The parent should not add another umbrella-only query contract merely to close
the task. A gate-run result or evidence packet may correlate these surfaces as
long as the BFF/gate runner owns the final status and preserves each source's
independent observation time and generation.

## 3. Operator Journey

1. Select a persona and start a fresh verification run. Display an opaque run
   ID, start time, deployed Pantheon commit, and deployed frontend commit.
2. Show the desired runtime profile beside the observed OpenClaw persona ID,
   model, workspace ref, and sync generation. Drift blocks the run.
3. Show provider authentication/readiness separately from the live smoke. A
   mounted or authenticated provider remains `verification required` until the
   exact provider/model invocation passes.
4. Query persona memory through the BFF. Render `available + zero items` as a
   valid empty state and source failure as unavailable/degraded; never collapse
   the latter into empty-memory copy.
5. Read materialization provenance and compare its canonical source IDs and
   generation with the BFF snapshot. Label all workspace output `derived
   cache`.
6. Run a second-persona isolation probe. Present a safe pass/fail result and
   redacted identifiers only.
7. Publish the server/gate-runner result only when all required observations
   are fresh and belong to the same run/generation. The browser must not infer
   pass from several green panels.
8. Archive exact commands, timestamps, sanitized responses, PR/merge SHAs,
   deployed commits, and residual risks for reviewer closeout.

## 4. Frontend Handoff

Frontend implementation belongs in `ajoe734/execute-plans`, not this Pantheon
checkout. Hosted proof must use live BFF mode, the Pantheon-owned dev BFF,
strict fallback, and safe write defaults.

| BFF/gate observation | Required UI behavior |
|---|---|
| Canonical source available, zero entries | Valid empty state with source and observation time. |
| Canonical source unavailable or invalid | Degraded/unavailable panel with safe reason and repair action; gate blocked. |
| Desired and observed runtime/model differ | Drift warning with both safe values; require reconciliation and a fresh run. |
| Auth ready, live smoke absent/stale/failed | `Verification required` or failed state; never usable. |
| Canonical and materialized source IDs differ | Materialization mismatch; gate blocked. |
| Isolation probe fails | Critical isolation failure without leaked content; publication blocked. |
| All checks pass in one fresh run | Render the BFF/gate-runner verdict and evidence links without recomputing it client-side. |

The frontend must call Pantheon BFF only. It must not call Memory Plane,
OpenClaw adapter/provider APIs, or VM workspace files directly.

## 5. Parent Closeout Acceptance

- [ ] `OCLAW-PMEM-005` is done or reviewer-approved superseded, with its PR and
  merge SHA recorded.
- [ ] Child task PRs, reviewer approvals, merge SHAs, and focused validation
  are collected without treating local tests as hosted evidence.
- [ ] Hosted BFF evidence distinguishes available-empty from unavailable and
  preserves canonical Memory Plane source metadata.
- [ ] Desired runtime profile matches observed OpenClaw persona, model,
  workspace, and generation.
- [ ] Canonical source IDs match derived workspace materialization IDs for the
  same run/generation.
- [ ] Both required provider paths are either proven by fresh live smoke or
  accurately degraded with a concrete safe reason.
- [ ] A negative cross-persona probe proves private memory isolation without
  archiving private content.
- [ ] The hosted `execute-plans` UI uses strict live BFF and renders every
  degraded state truthfully.
- [ ] Residual risks name an owner and expiry/recheck condition.

Suggested component checks to compose with hosted probes:

```text
pytest -q services/control-plane/bff/tests/test_bff_b2_list_detail_facade.py
pytest -q services/control-plane/bff/test_bff_strategy_persona_contract.py
pytest -q services/memory/test_main.py
pytest -q integrations/openclaw/test_persona_memory_bridge.py
pytest -q integrations/openclaw/test_persona_agent_sync.py
```

## 6. Non-Claims and Composition

This packet does not claim the umbrella gap is closed, that the hosted UI has
been exercised, that component tests prove provider usability, or that an
OpenClaw workspace is authoritative memory. It also does not promote the
projection language above into a canonical schema.

`Codex`, as parent owner, should compose this packet with the accepted
`OCLAW-PMEM-001` through `OCLAW-PMEM-005` outputs and own the final executable
gates and closeout archive. `Antigravity` should review that this packet remains
support-only, reflects the current canonical-memory BFF implementation, and
keeps frontend truth downstream of BFF/gate-runner evidence.
