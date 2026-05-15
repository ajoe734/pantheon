# EXEC-RUNTIME-TW04-001 Sidecar Acceptance Packet

Date: `2026-04-21`
Sidecar task: `EXEC-RUNTIME-TW04-001-SIDECAR-ACCEPTANCE`
Parent task: `EXEC-RUNTIME-TW04-001`
Sidecar owner / reviewer: `Claude` / `Codex`
Parent owner / reviewer: `Claude` / `Codex`
Helper kind: `acceptance_packet`
Scope: support-only acceptance checklist, dependency map, and handoff summary; no canonical truth, runtime implementation, or contract docs are modified here

---

## 1. Parent Task Acceptance Criteria

Sourced from `ai-status.json` at the time this sidecar was created:

| # | Criterion |
|---|---|
| AC-1 | The active operator-bff runtime exposes the TW-04 replay route family over live HTTP |
| AC-2 | Browser-facing replay/session links match mounted front routes and evidence targets resolve to deployed owner routes |
| AC-3 | Live runtime truth preserves the published degraded and unavailable replay semantics |

---

## 2. Acceptance Checklist

All three acceptance criteria are now fully met. Evidence is cited per criterion.

### AC-1 — Live TW-04 route family exposed on active runtime

**Status: PASS**

Evidence source: `.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml`
(resolved at `2026-04-21T18:18:08Z` by Codex)

Live HTTP verification on `http://127.0.0.1:18001`:

| Endpoint | Method | Live result |
|---|---|---|
| `/api/v1/trainer/replay` | GET | 200 |
| `/api/v1/trainer/replay/{session_id}` | GET | 200 |
| `/api/v1/trainer/sessions/{session_id}/commit` | POST | mounted; enforces authority guard |
| `/api/v1/trainer/sessions/{session_id}/discard` | POST | mounted; enforces authority guard |

`GET /openapi.json` on port 18001 advertises all four routes.

### AC-2 — Links and evidence targets resolve to deployed owner routes

**Status: PASS**

Evidence sources:
- `.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml`
- `.coordination/requests/TW-04-teaching-replay-bff-gap.yaml` (resolved at `2026-04-21T18:20:00Z` by Codex)

Verified live:
- `links.replay_detail` → `/trainer/replay/{session_id}` (matches mounted front route)
- `links.session_detail` → `/trainer/sessions/{session_id}` (matches mounted front route)
- `event.evidence_ref.url_pattern` → `/operator/paper-live-drift/runtime-042` (deployed owner route; prior stale target `/telemetry/drawdown/:id` is permanently corrected)

The BFF fix is enforced at `services/control-plane/bff/read_store.py` and verified by
`services/control-plane/bff/test_tw04_teaching_replay_contract.py`.

Startup backfill: `ReadSurfaceStore` backfills existing local TW-04 replay snapshots to the corrected route on every restart, so the fix survives future runtime recycles.

### AC-3 — Degraded and unavailable replay semantics preserved

**Status: PASS**

Evidence source: contract test suite `services/control-plane/bff/test_tw04_teaching_replay_contract.py`

- Test count at acceptance close: **34 passed** (up from 32 at initial review)
- Degraded and unavailable replay semantics are covered within the 34-test suite
- Authenticated POST commit/discard on an already-committed replay returns
  `PRECONDITION_NOT_MET` on `allowedActions.canCommit` / `allowedActions.canDiscard`,
  proving the authority guard semantics are live

---

## 3. Dependency Map

```
EXEC-REBASE-TW04-001 (done)
  └─ TW-04 BFF contract bundle, handoff materials, and frontend SA published
  └─ Unlocked EXEC-RUNTIME-TW04-001

EXEC-RUNTIME-TW04-001 (done — finalized commit b0975f5)
  ├─ Depends on: EXEC-REBASE-TW04-001
  ├─ Produces: live operator-bff serving TW-04 route family with corrected evidence targets
  ├─ Review artifact: .coordination/reviews/TW-04-teaching-replay-review.md
  └─ Sidecar support:
      ├─ EXEC-RUNTIME-TW04-001-SIDECAR-REVIEW (done — finalized commit 9f319d9)
      └─ EXEC-RUNTIME-TW04-001-SIDECAR-ACCEPTANCE (this packet — reviewer closeout packet)
```

### Upstream dependencies resolved before close

| Dependency | Status | Notes |
|---|---|---|
| `EXEC-REBASE-TW04-001` | done | TW-04 BFF contract bundle complete, frontend lane unblocked |
| TW-04 runtime needs-runtime | resolved `2026-04-21T18:18:08Z` | All four routes live on port 18001 |
| TW-04 BFF gap | resolved `2026-04-21T18:20:00Z` | Evidence route topology corrected and verified on live runtime |

### Downstream gates unlocked

| Gate | Condition |
|---|---|
| `EXEC-FRONT-TW04-001` (frontend lane) | Unblocked by EXEC-REBASE-TW04-001; runtime freshness now confirmed |
| Subsequent runtime refresh cycles | Startup backfill ensures evidence-route correction survives recycles |

---

## 4. Evidence Artifact Registry

| Artifact | Type | Status | Key finding |
|---|---|---|---|
| `.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml` | coordination request | completed `2026-04-21T18:18:08Z` | All 4 TW-04 routes live; evidence target corrected on active runtime |
| `.coordination/requests/TW-04-teaching-replay-bff-gap.yaml` | coordination request | resolved `2026-04-21T18:20:00Z` | Route topology gap closed; no missing or divergent fields remain |
| `.coordination/reviews/TW-04-teaching-replay-review.md` | review record | approved | No blocking findings; EXEC-RUNTIME-TW04-001 approved for `review_approved` |
| `services/control-plane/bff/read_store.py` | implementation | committed | `_TW04_DRAWDOWN_EVIDENCE_ROUTE = "/operator/paper-live-drift/runtime-042"` |
| `services/control-plane/bff/test_tw04_teaching_replay_contract.py` | contract tests | 34 passed | Covers live routes, link topology, evidence targets, degraded/unavailable semantics |
| `support/sidecars/EXEC-RUNTIME-TW04-001/EXEC-RUNTIME-TW04-001-SIDECAR-REVIEW.md` | sidecar review packet | approved (commit 9f319d9) | Captured review surface; preserved runtime-vs-workspace distinction |

---

## 5. Residual Risks (non-blocking)

The following items were noted as residual risks in the review but treated as non-blocking for this task:

1. **Seeded surface freshness** — `meta.surfaces.trainer_replay = stale` on the live seeded dataset at port 18001. Acceptance bar for this task is route exposure, link topology, and evidence-target reachability, not service-backed freshness.
2. **Example-payload identity drift** — live list proof used `persona-alpha`; canonical example payload illustrates `p-breakout-trainer`. No impact on runtime acceptance; clean-up deferred to a later packet.
3. **Browser QA** — no deployed browser session was exercised against the refreshed runtime in this pass; browser QA is a separate follow-up concern.

---

## 6. Parent Task Lifecycle Summary

| Event | Timestamp / Commit | Notes |
|---|---|---|
| Parent `EXEC-RUNTIME-TW04-001` created | prior to 2026-04-21 | Runtime refresh scope for TW-04 route family |
| Sidecar review packet created | commit `9f319d9` | `EXEC-RUNTIME-TW04-001-SIDECAR-REVIEW` finalized |
| Review approved by Codex | `.coordination/reviews/TW-04-teaching-replay-review.md` | All acceptance criteria confirmed met |
| Parent finalized as `done` | commit `b0975f5` | `EXEC-RUNTIME-TW04-001 finalize approved TW-04 runtime refresh closeout` |
| This acceptance packet created | `2026-04-21` | Sidecar post-facto acceptance documentation |

---

## 7. Acceptance Verdict

**All three acceptance criteria: PASS**

The parent task `EXEC-RUNTIME-TW04-001` has been reviewed, approved, and finalized. This packet confirms that:

- The acceptance checklist is fully satisfied by live HTTP evidence
- All coordination artifacts are resolved and consistent
- The dependency chain is closed without open blockers
- Residual risks are bounded, non-blocking, and documented

This sidecar acceptance packet is submitted as the reviewer/owner closeout support artifact.

---

## 8. Sidecar Acceptance Self-Check

- Support artifact created only: yes
- Canonical truth modified: no
- Canonical contract documents modified: no
- Runtime or BFF implementation modified: no
- Reviewer handoff ready: yes

---

## 9. Owner Finalization Record

Date: `2026-04-21`
Finalized by: `Claude` (owner)
Review approved by: `Codex` (reviewer)

Owner final checks confirmed:
- All three acceptance criteria verified PASS against live HTTP evidence and 34-pass contract suite
- Acceptance packet is consistent with resolved coordination artifacts (needs-runtime, bff-gap)
- No canonical truth, runtime implementation, or contract documents were modified
- Dependency chain is closed with no open blockers
- Residual risks are bounded, non-blocking, and documented in Section 5

Sidecar task `EXEC-RUNTIME-TW04-001-SIDECAR-ACCEPTANCE` is formally closed as `done`.
