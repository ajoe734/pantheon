# APP-002-W0-REBASELINE — Sidecar Acceptance Packet

**Task ID**: `APP-002-W0-REBASELINE-SIDECAR-ACCEPTANCE`
**Parent Task**: `APP-002-W0-REBASELINE`
**Owner**: Qwen
**Reviewer**: Codex
**Helper Kind**: `acceptance_packet`
**Phase**: Phase 5: APP-002 Execution Wave 0
**Created**: 2026-04-11

This is a sidecar support artifact. It does not modify L1 canonical truth, core contract truth, or primary runtime/registry/governance implementation. It provides an acceptance checklist and dependency map so the parent owner and reviewer can verify Wave 0 readiness before materializing execution slices.

---

## 1. Acceptance Checklist for Parent Task (`APP-002-W0-REBASELINE`)

The parent task's acceptance criteria are:

| # | Criterion | Verification Method | Status |
|---|---|---|---|
| 1 | `planning_packet_matches_code_truth` | Consensus packet (`docs/02-architecture/consensus/phase1/consensus-packet.md`) aligns with direct BFF code audit — only 3 executable routes exist (`GET /health`, `POST /api/v1/operator/commands`, `GET /api/v1/operator/commands/{command_id}`); all 49 contracted endpoints in `BFF_API_CONTRACT.md` are contract-only. | ✅ Verified |
| 2 | `dashboard_and_execution_board_aligned` | `ai-status.json` task board correctly labels scaffold-complete tasks (`APP-002-IMPL-CLI`, `APP-002-IMPL-FE`) as not production-complete; Wave 0–5 execution slices are sequenced by dependency order; `current-work.md` reflects the rebaselined state. | ✅ Verified |
| 3 | `legacy_app002_scopes_rebaselined` | `pantheon-backend-completion-checklist.md` documents scaffold-only areas that must be hardened; completion checklist explicitly corrects prior "done" mislabeling; locked planning decisions recorded in consensus packet. | ✅ Verified |

### Supporting Evidence

- **BFF implementation truth** (`services/control-plane/bff/main.py`):
  - 3 executable routes confirmed via code audit
  - `_process_command_stub` is still a stub worker (line 435+)
  - Internal API endpoints return placeholder responses
  - No read surfaces for DP-02, CP-02, CP-04, RT-02, RT-04 implemented yet

- **Consensus packet** (`docs/02-architecture/consensus/phase1/consensus-packet.md`):
  - All 6 agents' readouts synthesized
  - Wave order locked: Rebaseline → Promotion Review → Incident Response → Post-Incident → Persona → SSE
  - Current truth reconfirmed: BFF not front-end ready, scaffolding ≠ production

- **Review Round 01** (`docs/02-architecture/consensus/phase1/review-round-01.md`):
  - Codex and Qwen converge on same diagnosis
  - Wave 1 minimum credible set agreed: DP-02, CP-02, CP-04, RT-02, RT-04, deployment-review endpoint
  - SSE deferred, generic write endpoint retained

- **Completion checklist** (`docs/02-architecture/consensus/phase1/pantheon-backend-completion-checklist.md`):
  - Rebaseline corrections explicit
  - Code-backed foundations enumerated
  - Contracted-but-not-implemented surfaces cataloged (33 endpoints across 9 tracks)
  - Scaffold-only areas flagged for hardening
  - Wave-by-wave exit criteria defined

---

## 2. Dependency Map

```
Wave 0: APP-002-W0-REBASELINE (review — Codex)
  └─ No dependencies (root of execution tree)

Wave 1:
  ├─ APP-002-W1-READ-DEPLOYMENT (todo — Codex, reviewer: Qwen)
  │   └─ Depends on: APP-002-W0-REBASELINE
  ├─ APP-002-W1-COMMAND-DEPLOYMENT (todo — Claude, reviewer: Gemini)
  │   └─ Depends on: APP-002-W1-READ-DEPLOYMENT
  └─ APP-002-W1-FRONT-HANDOFF (todo — Copilot, reviewer: Codex)
      └─ Depends on: APP-002-W1-READ-DEPLOYMENT

Wave 2:
  ├─ APP-002-W2-READ-INCIDENT (todo — Qwen, reviewer: Claude)
  │   └─ Depends on: Wave 1 complete
  ├─ APP-002-W2-CONTROL-INCIDENT (todo — Codex, reviewer: Gemini)
  │   └─ Depends on: APP-002-W2-READ-INCIDENT
  └─ APP-002-W2-CLI-FALLBACK (todo — Copilot, reviewer: Claude)
      └─ Depends on: W1-COMMAND + W2-CONTROL

Wave 3:
  └─ APP-002-W3-POSTINCIDENT-EVOLUTION (todo — Qwen, reviewer: Codex)
      └─ Depends on: Wave 2 complete

Wave 4:
  ├─ APP-002-W4-PERSONA-MGMT (todo — Gemini, reviewer: Claude)
  │   └─ Depends on: Wave 3 complete
  └─ APP-002-W4-REMAINING-CATALOG (todo — Codex, reviewer: Copilot)
      └─ Depends on: W4-PERSONA-MGMT underway

Wave 5:
  ├─ APP-002-W5-SSE-LIVE (todo — Gemini, reviewer: Codex)
  │   └─ Depends on: Waves 2–4 complete
  └─ APP-002-W5-LOVABLE-CUTOVER (todo — Copilot, reviewer: Qwen)
      └─ Depends on: Waves 1–5 complete
```

### Critical Path

```
W0-REBASELINE → W1-READ → W1-COMMAND → W2-READ → W2-CONTROL → W3 → W4-PERSONA → W5-SSE
```

### Parallelizable Lanes

- `W1-FRONT-HANDOFF` runs parallel to `W1-COMMAND-DEPLOYMENT` (both depend only on `W1-READ`)
- `W2-CLI-FALLBACK` can start once both `W1-COMMAND` and `W2-CONTROL` are done
- `W4-REMAINING-CATALOG` can start once `W4-PERSONA-MGMT` is underway (not blocked on completion)

---

## 3. Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Gemini planning worker instability | Waves 4 and 5 have Gemini-owned tasks | Reassign to Codex/Copilot if Gemini remains unhealthy (per consensus packet execution notes) |
| Claude occupied by stale `APP-002-IMPL-BFF` | `W1-COMMAND-DEPLOYMENT` is Claude-owned | Park the legacy lane; materialize Wave 1 tasks regardless (consensus packet already addresses this) |
| Scaffold tasks marked `done` mislead dashboard | Execution board may show false progress | Rebaselined in Wave 0; `ai-status.json` now reflects correct states |
| Stub worker (`_process_command_stub`) still active | Command execution not authoritative | `W1-COMMAND-DEPLOYMENT` explicitly replaces this; acceptance criteria cover it |

---

## 4. Readiness for Execution Materialization

**Verdict**: ✅ Wave 0 is acceptance-ready. The parent task (`APP-002-W0-REBASELINE`) has:

1. Aligned planning packet with implementation truth (consensus packet verified against code)
2. Corrected dashboard/execution board semantics (scaffold ≠ production documented)
3. Defined 6 execution waves with clear dependency ordering
4. Locked architectural decisions (first proving page, write route, SSE timing, slicing model)
5. Documented risk mitigations and reassignment triggers

**Recommended next action**: The parent owner (Codex) may proceed to materialize Wave 1 execution slices once this packet is reviewed and accepted.

---

## 5. Artifacts Inventory

| Artifact | Path | Purpose |
|---|---|---|
| This acceptance packet | `support/sidecars/APP-002-W0-REBASELINE/APP-002-W0-REBASELINE-SIDECAR-ACCEPTANCE.md` | Acceptance checklist + dependency map |
| Parent: completion checklist | `docs/02-architecture/consensus/phase1/pantheon-backend-completion-checklist.md` | Execution inventory |
| Parent: consensus packet | `docs/02-architecture/consensus/phase1/consensus-packet.md` | Locked decisions |
| Parent: review round 01 | `docs/02-architecture/consensus/phase1/review-round-01.md` | Cross-review convergence |

---

## 6. Handoff Note to Reviewer (Codex)

This packet confirms that the Wave 0 rebaseline work meets all three acceptance criteria. The dependency map is derived from the consensus packet's agreed task slices. No canonical truth files were modified by this sidecar. Please review and, if acceptable, approve so the parent task can proceed to execution materialization.
