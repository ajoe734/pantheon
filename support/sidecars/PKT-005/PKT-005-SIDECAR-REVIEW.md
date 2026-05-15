# PKT-005 Review Packet (Sidecar)

**Task ID**: `PKT-005-SIDECAR-REVIEW`
**Parent Task**: `PKT-005` — Packetize the global degradation banner and SSE/live reconciliation slice
**Parent Owner**: `Claude`
**Parent Reviewer**: `Codex`
**Parent Status**: `done` (archived 2026-04-15T00:03:54Z, commit `494efdc`)
**Sidecar Owner**: `Claude` (auto-reassigned from Codex after Codex dispatch-pause 2026-04-14)
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `review_packet`
**Generated**: `2026-04-15T00:00:00Z`

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or the main runtime / registry / governance implementation.

Shared-truth sources used in this packet:

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/pkt_005_sidecar_review.md`
- `ai-status.json` (live truth for this sidecar task)
- `ai-task-archive/tasks/PKT-005.json` (archived parent task with delivery record)
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/PKT-005-degradation-banner-sse-packet-family.md`
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/execution-materialization.md`
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md`
- `support/sidecars/PKT-005/PKT-005-SIDECAR-ACCEPTANCE.md` (status: done, approved by Codex)
- `docs/reviews/PKT-005-review-codex.md` (Codex review record: changes requested → approved)
- `support/sidecars/APP-002-W5-SSE-LIVE/APP-002-W5-SSE-LIVE-SIDECAR-BFF-HANDOFF.md`

---

## 1. Current Snapshot

- Parent `PKT-005` is **done** (archived 2026-04-15T00:03:54Z). Commit `494efdc` ("PKT-005: finalize degradation banner and SSE substrate packet family").
- Both formal upstream dependencies — `LOOP-001` and `LOOP-003` — were `done` before the parent task advanced to review.
- The companion `PKT-005-SIDECAR-ACCEPTANCE` is also `done` (approved by Codex 2026-04-14). It provides the verified evidence framework for all three acceptance criteria and the seven reviewer gates.
- The parent task went through two review cycles:
  1. **Cycle 1**: Codex requested changes — three blocking issues (split-read aggregation rule missing for PKT-002, per-surface `status` enum misaligned, SSE banner authority conflict).
  2. **Cycle 2**: Codex requested changes again — one residual blocking issue remained: `docs/screens/PKT-005-degradation-banner.md` still stated banner updates on "SSE snapshot event", directly conflicting with the backend-authority rule in the SSE substrate and packet-family documents.
  3. **Final cycle**: Residual conflict fixed; Codex approved. Parent task finalized to `done`.
- The two cross-cutting substrates packetized:
  - **Global Degradation Banner** (`PKT-005-degradation-banner`) — no dedicated endpoint; derived from `meta.staleness` / `meta.surfaces.*` in every existing composed view response.
  - **SSE Reconciliation Substrate** (`PKT-005-sse-substrate`) — three live BFF SSE endpoints from the approved `APP-002-W5-SSE-LIVE` sidecar.

---

## 2. Parent Acceptance Map

| Parent acceptance criterion | Evidence source | Status at close |
|---|---|---|
| AC-1: Global degradation banner rules are defined once and referenced by all operator-facing packets | `PKT-005-degradation-banner-sse-packet-family.md` §Surface Inventory + §Banner states table; `.coordination/responses/PKT-005-degradation-banner-contract-ready.yaml`; `docs/screens/PKT-005-degradation-banner.md` (final revision) | ✅ Accepted — five-variant banner table established with triggering conditions and copy strings; no dedicated endpoint; backend-authority rule consistent across all documents |
| AC-2: SSE, reconnect, replay, and stale-state reconciliation are described as a frontend integration slice rather than pure visual work | `PKT-005-degradation-banner-sse-packet-family.md` §SSE Reconciliation Substrate + §Cross-Cutting Inheritance Rules; `APP-002-W5-SSE-LIVE-SIDECAR-BFF-HANDOFF.md`; `.coordination/responses/PKT-005-sse-substrate-contract-ready.yaml`; `docs/screens/PKT-005-sse-substrate.md` | ✅ Accepted — three live SSE endpoints defined with wire format, replay semantics (last_event_id, maxlen-500 buffer), reconnect manager (exponential backoff 1 s → 30 s with jitter), and idempotent reconciler |
| AC-3: Screen packets call out where live state is required and where polling fallback is acceptable | `PKT-005-degradation-banner-sse-packet-family.md` §Relationship to Other Packets; `PKT-005-SIDECAR-ACCEPTANCE.md` §1 AC-3 | ✅ Accepted — PKT-001 and PKT-002 classified as required-SSE; PKT-003 and PKT-004 classified as optional-SSE; "never show none" guard and subscription lifecycle rule present |

---

## 3. Evidence Summary

### 3.1 Delivered Artifacts At Close

| Artifact | Type | Location |
|---|---|---|
| Packet-family canonical document | Spec | `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/PKT-005-degradation-banner-sse-packet-family.md` |
| Banner screen spec (final) | Screen spec | `docs/screens/PKT-005-degradation-banner.md` |
| SSE substrate screen spec | Screen spec | `docs/screens/PKT-005-sse-substrate.md` |
| Banner BFF contract | BFF contract | `docs/bff/PKT-005-degradation-banner.md` |
| SSE substrate BFF contract | BFF contract | `docs/bff/PKT-005-sse-substrate.md` |
| Banner example payload | Example | `docs/examples/PKT-005-degradation-banner.json` |
| SSE substrate example payload | Example | `docs/examples/PKT-005-sse-substrate.json` |
| Banner contract-ready handoff | Coordination | `.coordination/responses/PKT-005-degradation-banner-contract-ready.yaml` |
| Banner Lovable UI task | Coordination | `.coordination/responses/PKT-005-degradation-banner-lovable-ui-task.yaml` |
| SSE substrate contract-ready handoff | Coordination | `.coordination/responses/PKT-005-sse-substrate-contract-ready.yaml` |
| SSE substrate Lovable UI task | Coordination | `.coordination/responses/PKT-005-sse-substrate-lovable-ui-task.yaml` |

### 3.2 Review History and Resolution

| Cycle | Reviewer note | Resolution |
|---|---|---|
| **Cycle 1 — changes requested** | Three blocking issues: (1) split-read aggregation rule missing for PKT-002 Incident Home; (2) per-surface `status` enum misaligned (`stale` and `partial` were being used as surface status values instead of banner-level derived variants); (3) SSE banner authority conflict — SSE substrate and packet-family said banner is backend-owned but banner screen spec had a sentence implying SSE events can refresh banner state | Fixed in the second implementation pass: split-read aggregation rule added to BFF contract and screen spec; per-surface enum locked to `ok \| degraded \| unavailable`; main SSE text and packet-family inheritance rule updated to say banner authority is backend-only |
| **Cycle 2 — changes requested** | One residual blocking issue: `docs/screens/PKT-005-degradation-banner.md:80` still said "Banner state is updated on every composed view response refresh (poll or SSE snapshot event)", directly contradicting the backend-authority rule now in effect everywhere else | Fixed: line 80 rewritten to say "Banner state is updated when the screen receives a fresh BFF meta snapshot — from initial load, polling, or an explicit full refetch triggered after a significant SSE event. SSE event payloads do not carry meta snapshots and must not be used to update banner state directly." |
| **Final — approved** | Codex confirmed: residual backend-authority conflict resolved; no blocking findings remain | Parent task finalized to `done` (commit `494efdc`) |

### 3.3 Non-Blocking SSE Caveats Carried Forward

| Caveat | Detail | Consumer impact |
|---|---|---|
| Runtime stream path not filtered server-side | `GET /api/v1/runtime/{runtime_id}/events/stream` — BFF does not filter events per `runtime_id` server-side; clients must filter by `data.runtime_id` | PKT-001 (Deployment Review Console) must filter client-side; caveat must appear in all downstream screen specs that subscribe to the runtime SSE stream |
| In-memory buffer drops on BFF restart | BFF process restart drops replay history; clients must handle partial-buffer reconnect | All SSE-subscribing screens must handle graceful reconnect with incomplete replay; carry as named caveat in all downstream packet specs that depend on replay guarantees |

### 3.4 Inherited Cross-Cutting Rules (All Downstream Packets Must Honor)

| Rule | Description |
|---|---|
| Banner from BFF meta only | Banner state must be derived from `meta.staleness` and `meta.surfaces.*` in a full BFF read response. SSE event payloads do not carry `meta` snapshots and must never be used to update banner state. |
| SSE subscription lifecycle | Screens subscribe on mount, unsubscribe on unmount, using `SSEReconnectManager`. |
| Never show none | A screen must never render empty-success state when its data source is unavailable or degraded. |
| Initial fetch first | SSE is an incremental update layer applied on top of the initial BFF composed view response. SSE must not substitute for the initial fetch. |
| Idempotent reconnect | Reconciler skips already-applied events by `id` on reconnect replay. |

---

## 4. Reviewer Gates

Before the reviewer approves this sidecar, confirm:

| Gate | Question | Expected outcome |
|---|---|---|
| G1 | Does the packet confirm the Global Degradation Banner derives state from `meta.staleness` / `meta.surfaces.*` present in every existing composed view response — with no dedicated health-check endpoint added? | Yes — `endpoints: []` in the contract-ready artifact; "No dedicated endpoint required" stated in packet-family document |
| G2 | Are all five banner variants (`none`, `degraded`, `stale`, `partial`, `critical`) defined with triggering conditions and copy strings? | Yes — the banner states table maps each system condition to a variant and copy string |
| G3 | Does the packet describe the SSE substrate as a frontend integration slice (not just a visual component), with wire format, replay semantics, reconnect manager spec, and idempotent reconciler rules? | Yes — AC-2 items 2.1–2.9 are all covered in the acceptance sidecar and packet-family document |
| G4 | Does the relationship table classify downstream packets by required-SSE vs. optional-SSE vs. banner-only, and include the "never show none" guard? | Yes — PKT-001 and PKT-002 required; PKT-003 and PKT-004 optional; "never show none" rule present in cross-cutting inheritance rules |
| G5 | Are the two known non-blocking SSE caveats (server-side filtering deferred, buffer drops on restart) carried as named non-blocking annotations? | Yes — both caveats present in packet-family §Known caveats and in §3.3 of this sidecar |
| G6 | Does the packet avoid re-specifying composed view surfaces owned by PKT-001, PKT-002, PKT-003, and PKT-004? | Yes — PKT-005 defines only the shared banner substrate and SSE transport layer; composed view specs remain with their owning packets |
| G7 | Is the final banner screen spec (`docs/screens/PKT-005-degradation-banner.md`) free of wording that implies SSE events directly update banner state? | Yes — resolved in the final implementation pass; line 80 explicitly forbids using SSE payloads to update banner state; the backend-authority rule is consistent across all PKT-005 documents |

---

## 5. Handoff Packet To Reviewer

**From**: Claude
**To**: Codex
**For**: `PKT-005-SIDECAR-REVIEW` reviewer inspection

### Delivered In This Sidecar

1. A current snapshot confirming PKT-005 is archived as `done` (commit `494efdc`), with both upstream dependencies (LOOP-001, LOOP-003) satisfied.
2. A parent acceptance map verifying all three AC items are accepted and consistent across the full packet set.
3. A full review history documenting the two cycles of changes-requested and the final resolution of the banner-authority conflict.
4. A surface-by-surface artifact index for all delivered screen specs, BFF contracts, example payloads, and coordination files.
5. Two non-blocking SSE caveats (server-side filtering, in-memory buffer on restart) that must carry forward as named annotations in all downstream packet specs.
6. Five cross-cutting inheritance rules that all downstream Operator Console screens must honor.
7. Seven reviewer gates tied to the AC requirements and the critical banner-authority consistency check.

### Recommended Review Outcome Logic

- **Approve this sidecar** if the evidence summary, review history, and reviewer gate framework are accurate and useful as a post-completion support record for `PKT-005`.
- **Parent task `PKT-005` is already done** — sidecar approval does not affect the parent. This packet serves as a structured reviewer evidence record and a reference for downstream packet authors.
- **Reject this sidecar** only if a factual error is found in the evidence summary or if the seven reviewer gates do not accurately reflect the accepted PKT-005 packet state.

### Downstream Applicability

Any future Operator Console packet or workbench screen that inherits the degradation banner or SSE substrate should use this sidecar (and the companion acceptance sidecar) as the primary evidence reference for:

- Banner state derivation rules (five variants, `meta.staleness` / `meta.surfaces.*` only, no dedicated endpoint)
- SSE integration contract (three live streams, wire format, `SSEReconnectManager`, idempotent reconciler, initial-fetch-first rule)
- The two non-blocking caveats (client-side runtime filtering, buffer drop on BFF restart)

Downstream consumers confirmed in the packet-family document:

```text
PKT-005 -> PKT-001  (Deployment Review Console — runtime SSE + banner)
PKT-005 -> PKT-002  (Incident Response Console — incident + kill-switch SSE + banner)
PKT-005 -> PKT-003  (Post-Incident and Evolution — banner; optional SSE)
PKT-005 -> PKT-004  (Persona Management — banner; optional SSE)
PKT-005 -> WB-001   (Operator Console backlog — substrate spec locked)
PKT-005 -> WB-008   (Evolution Workbench backlog — SSE substrate spec shared)
```

---

*Prepared by Claude for the `PKT-005-SIDECAR-REVIEW` sidecar slice. This file is intentionally support-only and does not modify canonical truth.*
