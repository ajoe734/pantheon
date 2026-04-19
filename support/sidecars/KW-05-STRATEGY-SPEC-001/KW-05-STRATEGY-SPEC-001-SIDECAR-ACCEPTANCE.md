# KW-05 Strategy Spec Acceptance and Dependency Map (Sidecar)

**Parent Task**: `KW-05-STRATEGY-SPEC-001` - Publish Strategy Spec versioning and compare contract  
**Parent Owner**: `Copilot`  
**Parent Reviewer**: `Codex`  
**Parent Status**: `todo`  
**Sidecar Task**: `KW-05-STRATEGY-SPEC-001-SIDECAR-ACCEPTANCE`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Codex`  
**Helper Kind**: `acceptance_packet`  
**Generated**: `2026-04-19`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or runtime / registry / governance implementations. It prepares a
> reviewable acceptance packet and dependency map for the parent `KW-05` task.

---

## 1. Executive Summary

`KW-05-STRATEGY-SPEC-001` is the final Knowledge Workbench contract slice needed
to make Strategy Spec surfaces truthful. The parent task exists to publish the
missing BFF-owned contract for:

1. strategy-spec list browse
2. versioned detail/viewer
3. lifecycle and ancestry semantics
4. backend-owned diff or compare payload
5. evidence-backed citation panel

Repo truth currently says `KW-05` is still blocked at the contract layer, while
its upstream citation and synthesis dependencies are already landed:

- `KW-03-EVIDENCE-001`: done
- `KW-04-INSIGHT-001`: done

This packet gives the parent owner and reviewer a concise crosswalk from the
recorded acceptance criteria to the already-established upstream constraints and
the still-missing `KW-05` contract surface.

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Canonical task board; confirms parent ownership, sidecar scope, and that `KW-03` / `KW-04` are complete dependencies |
| `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md` | Records the four architecture-team gaps that `KW-05` must close |
| `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md` | Defines `KW-05` surface scope, packetization prerequisite, readiness gate, and workbench dependency ordering |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | Confirms the frontend currently treats Strategy Spec pages as blocked shell-only surfaces pending the contract landing |
| `WORKBENCH_DELIVERY_BACKLOG.md` | States the backlog truth: `KW-05` overview exists, but module readiness still depends on a versioned browse contract |
| `docs/bff/KW-03-evidence-refs.md` | Published upstream evidence-ref contract that `KW-05` citation drilldown must build on |
| `docs/bff/KW-04-insight-cards.md` | Published upstream insight-card contract that `KW-05` may reference for linked-source and citation semantics |
| `docs/examples/KW-03-evidence-refs.json` | Example evidence payloads proving the upstream citation rail is already shaped |
| `docs/examples/KW-04-insight-cards.json` | Example insight payloads showing `strategy_spec` linkage already appears in upstream read models |

---

## 3. Acceptance Checklist Crosswalk

Parent acceptance recorded in `ai-status.json`:

| Parent acceptance criterion | What must be true for the criterion to be honestly met |
|---|---|
| `strategy spec list and detail routes are published` | `KW-05` must publish a list route and a version-aware detail route over strategy specs; both must be BFF-owned browse surfaces rather than schema-only references |
| `versioning and lifecycle semantics are explicit` | the contract must lock `strategy_id`, version selector semantics, ancestry behavior, and lifecycle values `draft | approved | deprecated` |
| `diff and citation behavior are backend owned` | the compare payload must be server-composed, and the citation panel must rely on resolved evidence/link bundles rather than client-side joins or raw JSON diffing |

Derived verification checklist for the parent owner:

| Check | Repo-visible basis today | Status |
|---|---|---|
| Missing list route is explicitly identified | gap matrix + packet family both name a missing strategy-spec list route | PENDING parent |
| Missing versioned detail route is explicitly identified | gap matrix + packet family both name a missing versioned detail route | PENDING parent |
| Lifecycle and ancestry semantics are defined as required scope | gap matrix requires lifecycle, ancestry, and version selector semantics | PENDING parent |
| Backend-owned compare behavior is mandatory | gap matrix and frontend SA both forbid client-side raw spec JSON compare | PENDING parent |
| Evidence-backed citation rail has an upstream contract to build on | `KW-03` BFF contract and examples are already published | READY upstream |
| Insight/linked-source context already has an upstream contract to build on | `KW-04` BFF contract and examples are already published | READY upstream |

---

## 4. Dependency Map

### 4.1 Direct dependencies already satisfied

| Task ID | Status | Why `KW-05` needs it |
|---|---|---|
| `KW-03-EVIDENCE-001` | `done` | `KW-05` citation bundles and evidence drilldowns must reuse the canonical evidence-ref identity, link-resolution, and credibility model |
| `KW-04-INSIGHT-001` | `done` | `KW-05` strategy-spec detail may reference insight-derived linked sources; the upstream aggregation and linked-source vocabulary is already shaped |

### 4.2 Earlier family prerequisites established in packet truth

These are not recorded as direct blockers in the sidecar task entry, but the
Knowledge Workbench packet family says `KW-05` conceptually builds on them:

| Upstream module | Why it matters to `KW-05` |
|---|---|
| `KW-01 Institutional Memory` | provides lineage anchors and durable `entry_id` identity for knowledge graph linkage |
| `KW-03 Evidence Refs` | provides the backing citation rail and link-resolution contract |

### 4.3 Downstream surfaces still blocked on the parent contract

| Surface | Current truthful state | What `KW-05` must unlock |
|---|---|---|
| `/knowledge/strategy-specs` | blocked shell only | publish list route contract and filter / pagination semantics |
| `/knowledge/strategy-specs/:strategy_id` | blocked shell only | publish versioned detail route, lifecycle state, ancestry, and citation bundle |
| `/knowledge/strategy-specs/:strategy_id/compare` | blocked shell only | publish backend-owned compare payload; forbid client JSON diffing |

---

## 5. Contract Shape That Must Exist Before Parent Review

From the gap matrix and packet family, the minimum truthful `KW-05` contract
surface must lock the following:

| Contract area | Required shape |
|---|---|
| Identity | stable `strategy_id` |
| Browse identity | list entries include `title`, `spec_version`, provenance source, and lifecycle state |
| Version selection | detail route must support explicit version selection semantics rather than assuming "latest only" |
| Lifecycle | `draft | approved | deprecated` |
| Ancestry | version relationship / compare ancestry semantics must be explicit |
| Citation bundle | evidence and provenance chain must be BFF-resolved, not flattened from raw ids |
| Compare payload | backend-composed field diff between two versions of the same strategy spec |
| Degradation behavior | frontend must respect surface health and not treat stale local data as authoritative |

Two explicit non-goals are already locked by the cited sources:

1. The frontend must not compare raw strategy-spec JSON locally.
2. The frontend must not invent citation strings or derive evidence links from
   raw refs or storage metadata.

---

## 6. Parent Readiness Snapshot

Current repo truth for the parent is internally consistent:

| File | Current statement about `KW-05` |
|---|---|
| `WORKBENCH_DELIVERY_BACKLOG.md` | overview packet is live, but module remains not ready because versioned strategy-spec browse contract is missing |
| `PACKET_FAMILY.md` | `KW-05` is not ready; list/detail/versioning/diff contracts are all still missing |
| `PANTHEON_FRONTEND_SA.md` | strategy-spec list/detail/compare pages remain blocked shell-only surfaces |

That means the parent should not claim readiness yet, but it also does not need
to rediscover upstream dependency truth. The real open work is narrowly scoped:
publish the `KW-05` BFF contract and examples without changing canonical L1
policy.

---

## 7. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar creates only `support/sidecars/KW-05-STRATEGY-SPEC-001/KW-05-STRATEGY-SPEC-001-SIDECAR-ACCEPTANCE.md` |
| No canonical truth edited | PASS | No L0/L1/L2 canonical contract or runtime file was modified |
| Parent acceptance is faithfully restated | PASS | Acceptance crosswalk uses the exact parent criteria from `ai-status.json` |
| Dependency map matches repo truth | PASS | Direct deps `KW-03` and `KW-04` match the sidecar task entry and cited workbench docs |
| Handoff is actionable for parent owner | PASS | Packet isolates the required `KW-05` contract surface and already-resolved upstream rails |

---

## 8. Handoff to Reviewer (`Codex`)

This sidecar is ready for review as the acceptance packet for
`KW-05-STRATEGY-SPEC-001`.

What it gives you:

1. a clean acceptance crosswalk from the parent task criteria to the actual
   missing `KW-05` contract surface
2. a dependency map showing that the citation and insight prerequisites are
   already done
3. a reviewer-ready summary of what still blocks Strategy Spec list/detail and
   compare pages from moving beyond shell-only status

Recommended reviewer stance:

1. approve this sidecar if it accurately reflects the current repo-visible
   `KW-05` gap and its satisfied upstream dependencies
2. keep the parent task focused on supportable contract publication:
   list route, versioned detail route, lifecycle or ancestry semantics, and
   backend-owned compare or citation behavior

---

*Generated by Codex2 as a sidecar `acceptance_packet` helper for `KW-05-STRATEGY-SPEC-001`. This file is a support artifact and does not modify canonical truth.*
