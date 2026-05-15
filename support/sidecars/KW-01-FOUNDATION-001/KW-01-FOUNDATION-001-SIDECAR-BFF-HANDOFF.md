# KW-01-FOUNDATION-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `KW-01-FOUNDATION-001` — Publish Institutional Memory browse foundation
**Parent Owner**: Claude
**Parent Reviewer**: Codex
**Parent Status**: `review_approved` pending owner finalization
**Sidecar Owner**: Codex2
**Sidecar Reviewer**: Claude
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-19

> Support artifact only. It does not change canonical truth, L1 policy, or core runtime/registry/governance implementation. It packages the current Knowledge Workbench overview plus KW-01 Institutional Memory implementation state into a frontend-ready and reviewer-ready handoff.

---

## 1. Parent Task Snapshot

`KW-01-FOUNDATION-001` moved the Knowledge Workbench out of pure overview-shell territory by publishing:

- a truthful Knowledge Workbench overview route: `GET /api/v1/workbench/knowledge`
- module-level browse contracts for `KW-01 Institutional Memory`
- screen specs, example payloads, and frontend handoff materials for the first real Knowledge browse module

Primary artifacts already published in the parent slice:

| Artifact | Path | Role |
|---|---|---|
| Overview BFF contract | `docs/bff/PKT-knowledge-workbench.md` | Truthful Knowledge landing-page payload |
| KW-01 BFF contract | `docs/bff/KW-01-institutional-memory.md` | List/detail query shape for institutional memory |
| Overview screen spec | `docs/screens/PKT-knowledge-workbench.md` | Overview-only rendering rules |
| KW-01 screen spec | `docs/screens/KW-01-institutional-memory.md` | List/detail rendering rules |
| KW-01 frontend handoff | `docs/pantheon-handoffs/KW-01-institutional-memory/FRONTEND_CHANGE_SPEC.md` | Front-repo implementation guide |
| Example payloads | `docs/examples/KW-01-institutional-memory-list.json`, `docs/examples/KW-01-institutional-memory.json` | Reference fixtures |

---

## 2. Actual BFF Query Inventory

The repo now contains the following live Knowledge routes in `services/control-plane/bff/main.py`:

| Endpoint | Status in code | Purpose |
|---|---|---|
| `GET /api/v1/workbench/knowledge` | live | Overview shell for Knowledge Workbench |
| `GET /api/v1/knowledge/memory` | live | Paginated Institutional Memory list with backend-shaped filters |
| `GET /api/v1/knowledge/memory/{entry_id}` | live | Institutional Memory detail |

Observed implementation notes:

- All three routes require a read-capable bearer identity through `_require_read_role(...)`.
- The list route applies server-side filtering for `knowledge_type`, `scope`, `scope_filter`, `tags`, `page`, and `page_size`.
- The list route returns backend-owned pagination metadata and `meta.surfaces.memory_list`.
- The detail route returns lifecycle, source event context, usage data, and `meta.surfaces.entry_detail` plus `meta.surfaces.source_context`.
- Current implementation is example-data backed inside the BFF, but the route shape is real and callable.

---

## 3. Frontend Operator Journey

### 3.1 Knowledge Overview

Route:
- UI path: `/knowledge`
- BFF route: `GET /api/v1/workbench/knowledge`

Expected behavior:

- Render this as an overview landing page only.
- Show module order, support refs, missing contracts, and next steps from the backend payload.
- Treat `KW-01` as the only ready module.
- Do not invent browse tables for `KW-02` to `KW-05`.

### 3.2 Institutional Memory List

Route:
- UI path: `/knowledge/memory`
- BFF route: `GET /api/v1/knowledge/memory`

Expected behavior:

- Render a library-style list with backend-owned ordering.
- Pass filters through to the BFF; do not filter locally.
- Use `route_href` exactly as returned for row navigation.
- Show superseded entries instead of hiding them.
- Respect `meta.surfaces.memory_list` for degraded or unavailable handling.

### 3.3 Institutional Memory Detail

Route:
- UI path: `/knowledge/memory/:entry_id`
- BFF route: `GET /api/v1/knowledge/memory/{entry_id}`

Expected behavior:

- Render full detail, not a summary card.
- Use `source_event.href` exactly as returned; do not reconstruct source links.
- Show lifecycle state and `superseded_by` when present.
- Render structured payload as structured content, not stringified JSON.
- Keep the page read-only.

---

## 4. Current Query Gaps

### 4.1 Closed for KW-01

These are no longer open query gaps in repo reality:

- `GET /api/v1/knowledge/memory`
- `GET /api/v1/knowledge/memory/{entry_id}`
- overview route `GET /api/v1/workbench/knowledge`

### 4.2 Still Open Beyond KW-01

These remain blocked and should stay out of frontend scope:

| Module | Still-missing backend truth |
|---|---|
| `KW-02 Research Notes` | create/list/detail routes and ownership or attachment contract |
| `KW-03 Evidence Refs` | browse/detail routes and linked-entity projection |
| `KW-04 Insight Cards` | aggregation list/detail routes and filter taxonomy |
| `KW-05 Strategy Spec` | list/detail/version compare routes and versioned browse contract |

### 4.3 Important Truth Mismatch

Some published planning and handoff material still says the KW-01 BFF routes are pending implementation:

- `WORKBENCH_DELIVERY_BACKLOG.md`
- `docs/screens/KW-01-institutional-memory.md`
- `docs/pantheon-handoffs/KW-01-institutional-memory/FRONTEND_CHANGE_SPEC.md`
- the Knowledge section in `docs/lovable/PANTHEON_FRONTEND_SA.md`

That is now stale relative to repo implementation. Parent owner should decide whether to:

1. update those canonical or near-canonical readiness statements to reflect that the routes are live, or
2. keep the conservative wording until the BFF stops serving example-backed data and is wired to a non-demo source

This sidecar does not resolve that truth boundary; it only flags it.

---

## 5. Frontend Consumption Notes

Frontend can now consume the Knowledge slice in two layers:

| Surface | Backend dependency | Frontend readiness |
|---|---|---|
| Knowledge overview | `GET /api/v1/workbench/knowledge` | ready now |
| KW-01 memory list | `GET /api/v1/knowledge/memory` | route shape ready; final readiness depends on parent owner stance on example-backed data |
| KW-01 memory detail | `GET /api/v1/knowledge/memory/{entry_id}` | route shape ready; same caveat as list |

Required frontend rules:

- Keep `KW-02` to `KW-05` shell-only.
- Do not derive memory detail links from raw ids when `route_href` is present.
- Do not derive source-event URLs from event type and id.
- If any required field is absent, write a `bff-gap` coordination file instead of patching around it in UI code.

---

## 6. Verification Snapshot

Repo evidence checked for this handoff:

| Check | Evidence | Result |
|---|---|---|
| Overview route exists | `services/control-plane/bff/main.py` | PASS |
| KW-01 list route exists | `services/control-plane/bff/main.py` | PASS |
| KW-01 detail route exists | `services/control-plane/bff/main.py` | PASS |
| Overview contract test exists | `services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py` | PASS |
| KW-01 list/detail contracts, examples, and screen specs exist | `docs/bff/*`, `docs/screens/*`, `docs/examples/*` | PASS |
| Sidecar stays support-only | file under `support/sidecars/` only | PASS |

Coverage note:

- I did not find a dedicated automated test file for the two KW-01 memory routes in this pass.
- The overview route has a targeted contract test; KW-01 route verification in this packet is implementation inspection plus published examples.

---

## 7. Recommended Parent Closeout Notes

If Claude finalizes the parent task, the checkpoint message should acknowledge:

- Knowledge Workbench overview is live and truthful.
- KW-01 Institutional Memory list/detail routes are present in the BFF.
- The remaining open item is readiness wording reconciliation for documents that still describe the memory routes as pending.

Suggested handoff destination after this sidecar:

- reviewer: `Claude`
- decision needed: whether `review_approved -> done` for the parent can proceed with the current example-backed implementation, or whether readiness wording must be tightened before final closeout

---

## 8. Reviewer Checklist

Review against these claims:

- This packet does not invent new Knowledge contracts beyond the parent artifacts.
- The three Knowledge routes listed in section 2 exist in `services/control-plane/bff/main.py`.
- The flagged truth mismatch in section 4.3 is real and limited to wording or readiness semantics, not missing route code.
- No L1 or runtime implementation was changed by this sidecar.
