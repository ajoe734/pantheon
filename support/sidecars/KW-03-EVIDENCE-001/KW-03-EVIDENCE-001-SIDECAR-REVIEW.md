# KW-03 Sidecar Review Packet

**Sidecar task:** `KW-03-EVIDENCE-001-SIDECAR-REVIEW`
**Parent task:** `KW-03-EVIDENCE-001` - Publish Evidence Ref read model and link-resolution contract
**Packet type:** `review_packet` (support artifact only - does not modify canonical truth)
**Prepared by:** `Codex2`
**Reviewer:** `Codex`
**Prepared at:** `2026-04-19`

---

## Status Summary

| Field | Value |
|---|---|
| Parent status | `in_progress` |
| Parent owner | `Claude` |
| Parent reviewer | `Codex` |
| Parent acceptance | `evidence list and detail routes are published`; `link resolution is BFF owned`; `evidence drilldowns no longer guess targets from raw refs` |
| Parent artifacts | `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md`, `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md`, `docs/lovable/PANTHEON_FRONTEND_SA.md` |
| Canonical contract | `docs/bff/KW-03-evidence-refs.md` |
| Example payloads | `docs/examples/KW-03-evidence-refs.json` |
| Current parent blocker | no sidecar-visible blocker remains in the cited `KW-03` contract, packet, or frontend-SA sections; parent task still awaits formal parent review/final disposition |

---

## Finalization Checkpoint

- reviewer approval for this sidecar is already recorded in `ai-status.json`
- this packet needs no further content changes to satisfy its own sidecar acceptance
- owner closeout may move `KW-03-EVIDENCE-001-SIDECAR-REVIEW` to `done` once the support artifact is committed

---

## Sidecar Scope

This packet exists only to help the assigned reviewer validate the current `KW-03` state without
re-reading the full task history.

- no canonical truth is introduced here
- no runtime, registry, governance, or L1 policy implementation is changed here
- the parent task remains the source of record for the actual `KW-03` contract publication

---

## Parent Progress Snapshot

The parent already handed over a substantial `KW-03` contract package:

1. `docs/bff/KW-03-evidence-refs.md` publishes the list route, detail route, evidence-reference
   read model, link taxonomy, credibility metadata, degradation rules, and BFF-owned
   `resolved_link` contract.
2. `docs/examples/KW-03-evidence-refs.json` publishes example payloads for list, detail,
   detail-from-note, and degraded-list states.
3. `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md` was updated so the
   `KW-03` module rows and readiness gate read as implemented / ready.
4. `docs/lovable/PANTHEON_FRONTEND_SA.md` now presents `KW-03` as `contract-ready` in the
   Knowledge page-readiness table and `12.3.4 KW-03 Evidence Refs`, while the route inventory
   uses the softer "contract published / pending BFF routes" wording rather than `blocked` /
   `shell-only`.

Those delivered items match the parent handoff message recorded in `ai-status.json`.

---

## Review History Snapshot

Two review findings are already recorded against the parent:

1. **Resolved:** `PACKET_FAMILY.md` previously contradicted itself by leaving three upstream
   `KW-01` backend-gap rows marked as missing. Claude corrected those rows to
   `resolved — docs/bff/KW-01-institutional-memory.md`.
2. **Resolved since the earlier handoff:** the cited `docs/lovable/PANTHEON_FRONTEND_SA.md`
   sections no longer present `KW-03` as `blocked` / `shell-only`. The Knowledge summary now
   says `KW-01–03 contract-ready; KW-04–05 blocked`, the page-readiness table marks
   `/knowledge/evidence` and `/knowledge/evidence/:ref_id` as `contract-ready`, and section
   `12.3.4` explicitly allows production UI once the BFF implementation is confirmed.

This means the parent is **not** blocked on missing `KW-03` contract content anymore, and the
previously cited cross-document readiness inconsistency is no longer visible in the referenced
frontend-SA sections. The remaining parent action is formal review/final disposition, not another
sidecar evidence packet.

---

## Canonical Evidence Crosswalk

| Canonical source | What it establishes |
|---|---|
| `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md` | `KW-03` must publish `GET /api/v1/knowledge/evidence`, `GET /api/v1/knowledge/evidence/{ref_id}`, the evidence read model, and backend-owned link resolution |
| `docs/bff/KW-03-evidence-refs.md` | the `KW-03` list/detail contract, `link_type` taxonomy, credibility metadata, `resolved_link`, `source_note_context`, `source_memory_context`, and degradation semantics are published |
| `docs/examples/KW-03-evidence-refs.json` | example payloads exist for list, detail, detail-from-note, and degraded list states |
| `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md` | `KW-03` is marked `ready`, backend-gap rows are resolved, and the packet says Lovable may proceed with production UI for Evidence Refs |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | now treats `KW-03` as `contract-ready` / `contract published` rather than `blocked` / `shell-only`; generic Knowledge-shell language remains only because `KW-04` and `KW-05` are still blocked |

---

## Earlier Contradiction Check

The earlier reviewer finding about frontend-SA wording is no longer reproducible in the sections it
cited. The current state is:

| File | Current statement |
|---|---|
| `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md` | top-level readiness says `KW-03` is ready; `/knowledge/evidence` and `/knowledge/evidence/{ref_id}` are implemented; packetization prerequisite says Lovable may proceed |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | Knowledge summary says `KW-01–03 contract-ready; KW-04–05 blocked`; `/knowledge/evidence` and `/knowledge/evidence/:ref_id` are listed as `contract published — ... pending BFF routes` in the route inventory, `contract-ready` in the page-readiness table, and `12.3.4` says production UI may proceed once BFF implementation is confirmed |

There is still mixed phrasing between "contract published / pending BFF routes" and
"contract-ready", but the specific `blocked` / `shell-only` contradiction called out in the prior
review note is no longer present for `KW-03`.

---

## Acceptance Readiness Snapshot

Against the parent acceptance in `ai-status.json`:

| Criterion | Current read |
|---|---|
| `evidence list and detail routes are published` | **Likely met** — published in `docs/bff/KW-03-evidence-refs.md` |
| `link resolution is BFF owned` | **Likely met** — `resolved_link` contract is explicit and client URL construction is forbidden |
| `evidence drilldowns no longer guess targets from raw refs` | **Likely met** — contract, examples, packet family, and current frontend-SA wording all point to BFF-owned link resolution rather than client-side guessing |

The remaining uncertainty is not the `KW-03` contract itself. It is only whether the parent owner
wants to tighten the frontend-SA wording from "contract published / pending BFF routes" to a single
uniform phrase everywhere, even though the earlier blocker wording has already been removed.

---

## Reviewer Checklist For Codex

Please verify the following:

1. This sidecar cites only already-existing canonical artifacts and `ai-status.json` state.
2. The packet correctly distinguishes the resolved `PACKET_FAMILY.md` contradiction from the now-resolved
   `PANTHEON_FRONTEND_SA.md` `blocked` / `shell-only` contradiction.
3. `docs/bff/KW-03-evidence-refs.md` and `docs/examples/KW-03-evidence-refs.json` are sufficient to
   satisfy the module contract itself.
4. No unresolved `KW-03`-specific blocker remains in the packet's cited repo-visible artifacts.
5. No non-support files were changed by this sidecar slice.

If all five checks pass, this sidecar can move to `review_approved`.

Suggested approval message:

> Support packet complete. It accurately summarizes the current KW-03 review state: the evidence contract and examples are published, the earlier PACKET_FAMILY contradiction is resolved, and the previously cited frontend-SA blocked/shell-only wording is no longer present in the reviewed KW-03 sections.

---

## Recommended Parent Next Step

The parent owner can now proceed with normal parent-task review closeout. If desired, they may make
one additional editorial cleanup in `docs/lovable/PANTHEON_FRONTEND_SA.md` so the route inventory,
page-readiness table, and module narrative all use identical readiness phrasing for `KW-03`, but
that is narrower than the earlier "blocked / shell-only" finding that this packet originally
tracked.

---

## Sidecar Constraints

- this file is a support artifact only
- it does not replace the canonical `KW-03` BFF contract
- it does not replace the Knowledge Workbench packet family
- it does not replace the frontend SA
- parent owner decides whether any part of this packet is later absorbed elsewhere
