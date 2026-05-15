# APP-003-CLOSEOUT-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-003-CLOSEOUT-001` - closeout synchronization for already-reviewed operator surfaces
**Parent Scope Source**: `docs/reviews/2026-04-19-depth-rebase-001.md`
**Sidecar Task**: `APP-003-CLOSEOUT-001-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: Codex2
**Sidecar Reviewer**: Codex
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-19
**Mutates canonical**: no

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime, registry, governance, or contract implementations.
> It packages the current APP-003 closeout surface for Wave 2 operator packets,
> summarizes the remaining BFF/frontend handoff truth, and gives the reviewer a
> compact front-follow-up map.

---

## 1. Scope

Per `docs/reviews/2026-04-19-depth-rebase-001.md`, the real remaining APP-003
closeout work is no longer "build the missing BFF". It is closeout
synchronization for already-reviewed surfaces.

For this sidecar, the active closeout cluster is the Wave 2 operator packet set:

| Packet | Screen | Primary route | Current closeout truth |
|---|---|---|---|
| `PKT-011` | Health Status Board | `GET /api/v1/operator/health-status` | Pantheon route aligned; front replay/publication still needs cleanup |
| `PKT-012` | Alerts Rail | `GET /api/v1/operator/alerts` | Pantheon route aligned; front publication tuple not yet replay-clean |
| `PKT-013` | Operator Home | `GET /api/v1/operator/home` | Pantheon route aligned; front publication and lint cleanup still open |
| `PKT-014` | Paper / Live Drift | `GET /api/v1/operator/paper-live-drift/{runtime_id}` | Pantheon route aligned; front publication replay still open |

These four surfaces are also listed as contract-ready operator pages in
`docs/lovable/PANTHEON_FRONTEND_SA.md` and packetized in
`docs/pantheon-handoffs/OC-002-operator-console-wave2/PACKET_FAMILY.md`.

---

## 2. Implementation Snapshot

### 2.1 Pantheon-owned read surfaces are already live

The current repo snapshot already publishes the four APP-003 closeout routes:

| Packet | Route | Canonical handoff packet |
|---|---|---|
| `PKT-011` | `GET /api/v1/operator/health-status` | `docs/pantheon-handoffs/PKT-011-health-status-board/FRONTEND_CHANGE_SPEC.md` |
| `PKT-012` | `GET /api/v1/operator/alerts` | `docs/pantheon-handoffs/PKT-012-alerts-rail/FRONTEND_CHANGE_SPEC.md` |
| `PKT-013` | `GET /api/v1/operator/home` | `docs/pantheon-handoffs/PKT-013-operator-home/FRONTEND_CHANGE_SPEC.md` |
| `PKT-014` | `GET /api/v1/operator/paper-live-drift/{runtime_id}` | `docs/pantheon-handoffs/PKT-014-paper-live-drift/FRONTEND_CHANGE_SPEC.md` |

The corresponding delivery notes all say the same core thing: Pantheon does not
need a new endpoint or a new page-shaped response in this cycle.

### 2.2 Closeout is blocked by replay/publication truth, not by a missing route

`docs/reviews/2026-04-18-current-state-reconciliation.md` already narrows this
cluster correctly:

- `PKT-011` through `PKT-014` are not stuck on missing backend contract work
- they still need Pantheon/front-end closeout to move from `ui_done_received`
  into a final settled disposition

That is the core APP-003 closeout truth this packet preserves.

---

## 3. Packet-by-Packet Gap Map

### 3.1 Shared pattern

Across all four packets, the dominant remaining gap is:

1. the reviewed frontend artifacts exist in the sibling front checkout working
   tree or in partial form
2. the published request pair and feedback bundle are not yet replay-clean from
   one truthful immutable front commit
3. therefore supervisor-visible closeout cannot honestly collapse the loop yet

### 3.2 Per-packet status

| Packet | Pantheon-side state | Front-owned remaining gap | Reviewer takeaway |
|---|---|---|---|
| `PKT-011` | Route live, tests pass, payload aligned | malformed `source_commit`; governance owner links still unresolved in sibling front router | keep loop open; no Pantheon BFF rework required |
| `PKT-012` | Route live, tests pass, href truth fixed locally | request pair + feedback bundle not present in advertised commit `37a622bca69a95e2aae46aa8c6b0432ad72082a8` | treat as publication replay issue, not route gap |
| `PKT-013` | Route live, tests pass, owner-link href truth resolved | missing canonical `frontend-feedback` request; `ui-done` not published from truthful commit; changed-file ESLint violation in `AppSidebar.tsx` | front must republish and optionally clean lint before final closeout |
| `PKT-014` | Route live, tests pass, owner-link href truth resolved | request pair, bundle, and screen file absent from advertised commit `37a622bca69a95e2aae46aa8c6b0432ad72082a8` | treat as front publication replay issue only |

### 3.3 BFF query-gap classification

For this APP-003 sidecar, the honest BFF query-gap classification is:

| Packet | BFF query gap still open? | Why |
|---|---|---|
| `PKT-011` | No net-new route gap | delivery note says endpoint and contract stay truthful; only front replay + governance owner-link exposure remain |
| `PKT-012` | No | route, tests, and `target_ref.href` contract are already aligned |
| `PKT-013` | No | route and href semantics are already landed in Pantheon |
| `PKT-014` | No | route and owner-link refs are already landed in Pantheon |

The only caveat is `PKT-011`: there is still a frontend routing dependency for
`/governance-review-queue` and `/governance-approval-queue`, but the delivery
note explicitly frames that as a sibling front owner-link resolution problem,
not as a missing Pantheon BFF read surface.

---

## 4. Operator Journey Handoff

### 4.1 Operator Home to detail drill path

This is the intended Wave 2 operator journey across the APP-003 closeout set:

```text
1. Operator lands on PKT-013:
   GET /api/v1/operator/home
2. Operator escalates via backend-owned card target or shortcut:
   - alerts -> PKT-012
   - health -> PKT-011
   - runtime / incidents / governance -> owner screens already referenced by href
3. When a specific runtime drift review is needed:
   GET /api/v1/operator/paper-live-drift/{runtime_id} (PKT-014)
4. Frontend renders backend-owned hrefs and meta.surfaces state verbatim.
5. If the returned front publication tuple is not truthful or replayable, the
   loop remains open even if the local UI looks correct.
```

### 4.2 What the frontend must not do

- Do not reconstruct operator-home cards from lower-level primitives in the browser.
- Do not synthesize alternate target routes when the payload already provides
  `target_ref.href`, `plan_ref.href`, or `recommended_actions[].target_ref.href`.
- Do not hide `meta.surfaces.*` degradation or unavailability behind empty
  states.
- Do not treat a working-tree-only sibling front artifact set as sufficient
  closeout evidence if the published `source_commit` does not contain it.

---

## 5. Frontend Handoff Notes

### 5.1 What is ready today

- Pantheon already owns the page-shaped read model for all four screens.
- Canonical BFF contract, screen spec, example payload, and frontend change spec
  already exist for all four packets.
- The `OC-002` packet family already presents these screens as packetized Wave 2
  operator surfaces.

### 5.2 What still needs to happen in the front lane

| Packet | Front-lane next action |
|---|---|
| `PKT-011` | correct the exact `source_commit`; either expose governance owner-link destinations or publish an explicit follow-up handoff naming that dependency |
| `PKT-012` | republish the request pair and feedback bundle from one immutable commit and repoint both request bodies at that exact commit |
| `PKT-013` | publish the missing `frontend-feedback` request and bundle, republish `ui-done` from the same truthful commit, and resolve the changed-file ESLint violation if lint cleanliness is expected |
| `PKT-014` | republish the request pair, reviewed screen file, and feedback bundle from one truthful immutable commit |

### 5.3 Recommended closeout posture for APP-003 parent owner

Treat APP-003 closeout as a coordination and replayability cleanup lane:

- not a request for new Pantheon read routes
- not a request to reopen canonical packet docs
- not a reason to widen scope into unfinished workbench modules

The parent owner should only absorb fixes that tighten closeout truth for the
existing four packet loops.

---

## 6. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if only this sidecar packet is added |
| Canonical truth untouched | PASS if no L1 docs, BFF contracts, or runtime files changed |
| APP-003 scope is represented honestly | PASS if packet frames the work as closeout synchronization, not missing route implementation |
| PKT-011~014 routes listed as already live | PASS if all four are treated as existing Pantheon surfaces |
| Shared blocker is replay/publication truth | PASS if packet does not misclassify the remaining work as a new BFF contract gap |
| PKT-011 caveat is framed narrowly | PASS if governance owner-link issue is described as frontend routing exposure, not a missing backend read model |
| Parent-owner guidance is bounded | PASS if handoff stays support-only and leaves absorption decisions to the parent owner |

---

## 7. Recommended Reviewer Handoff

Recommended reviewer stance: approve this sidecar if it is sufficient as a
compact APP-003 closeout map.

The key decision for Codex is whether this packet cleanly preserves the repo's
current truth:

- APP-003 closeout is real
- the Wave 2 operator routes already exist
- the remaining work is mostly front publication replay, request-pair truth, and
  a small amount of frontend routing / lint hygiene
- parent closeout should not be expanded back into canonical BFF implementation

If approved, the parent owner may use this packet as a reviewer-ready front/BFF
handoff summary while deciding how to absorb the residual closeout work into the
main APP-003 lane.
