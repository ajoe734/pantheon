# Pantheon Agora — Open Design Gaps Round 2 (for SD team)

**Date:** 2026-06-21
**Purpose:** Consolidated inventory of the design sections the remaining downstream Agora tasks cite but that do **not** exist in any artifact on `pantheon@dev`. Hand this to the SD team to close in one comprehensive pass instead of patching task-by-task.
**Status:** Execution input. None of these are ops-fixable; they are missing product/contract design.

---

## Method

Scanned the 20 remaining (non-done, non-sidecar) Agora tasks in `ai-status.json`, extracted every `§` citation in their briefs/blockers, and cross-checked each against what is actually present on `pantheon@dev`:

- `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md`
- `.../design-closure/` (A1–A4, B1–B3, C1–C4)
- `.../contract-closure/` (servant/workshop/dashboard v1.1)
- `.../sw001-deep-closure/` (workshop private content / refs / db / lifecycle)
- canonical `services/control-plane/specs/agora/` + `openapi/`

## Root cause

`SD_2026-06-20.md` is a thin stub: it only contains **§0–8** (Purpose, Context, Naming, Schema Bundle, Capability Manifest, Route Catalog, Schema Versioning, Type Gen, Security) plus the **§17** route anchor and **§22.1** manifest. The closure packs since added: dashboard/widget (≈§9), front-end IA page composition (≈§10/§11 shell), and workshop persistence. **Everything else the tasks cite (§7 research detail, §11.x card specs, §12 trading room, §6.4/§7.4 versioning, §24.3 E2E flow, §26 isolation) was never written.** Each downstream worker correctly STOPs when it cannot find its cited section.

---

## Gap groups

### A. Strategy versioning / patch / readiness  (HIGHEST — blocks the workshop spine)

- **Cited:** §6.4, §7.4 (VersionPatchProposal), §11.3 (readiness gates)
- **Exists:** nothing. `VersionPatchProposal` appears in **no** artifact on dev. `VersionCreateRequest.patch` is only `{type: object, additionalProperties: true}`.
- **Missing / needs spec:**
  1. `VersionPatchProposal` envelope (who proposes, source event, target Registry version).
  2. Patch grammar — concrete schema for "JSON path from/to" (recommend RFC 6902 JSON Patch: op/path/from/value, or RFC 7386 merge-patch — SD team to pick).
  3. `version_compare` semantics (multi-version diff + predicted effect) for VersionCompareCard.
  4. Readiness model — the 3 gates (Preliminary / Full validation / Trading-room readiness): state machine, required items, what flips each gate.
- **Blocks:** `AG-BE-SW-002` (BLOCKED now), `AG-FE-SW-003`, `AG-BE-RS-004`, `AG-FE-RS-001`.

### B. Research facade + run projection

- **Cited:** §7.1, §7.2 (ResearchPlan create/approve/stage routing), §7.3 (ResearchRunSummary projection), §11.2 (research cards)
- **Exists:** schemas `research_plan.schema.json` / `research_run_summary.schema.json` and the C1 `research-planning` skill SPEC; **but no §7.x BFF facade contract** (plan draft/approve, stage→tool routing, run/progress/result projection, SSE progress shape). `ResearchRunSummary` projection contract: none.
- **Missing / needs spec:** §7.1/§7.2 ResearchPlan BFF behavior + stage→tool routing rules; §7.3 ResearchRunSummary projection fields + SSE `progress` event shape; the "no-order-route" governance restatement (§7.4/§9).
- **Blocks:** `AG-BE-RS-001`, `AG-BE-RS-002`, and the FE research cards (`AG-FE-RS-001`).

### C. Workshop SSE aggregate event schema

- **Cited:** §8.3, §17.2 (`/bff/agora/workshops/{id}/stream`)
- **Exists:** the route exists (v1.1) but only as a **generic SSE text stream**.
- **Missing / needs spec:** typed aggregate event schema for the stream — message ack, completeness update, research progress, version event — with first-ack latency target (<2s) and long-task semantics.
- **Blocks:** `AG-BE-SW-004` (BLOCKED now).

### D. Trading Room aggregate + governed intent

- **Cited:** §12, §12.1, §12.2, §12.3, §12.4, §13.1, §21
- **Exists:** front-end **IA / page composition** in `contract-closure/05` (TradingDeskShell, queues, drawers) and `trading_event.schema.json` / `trading_intent.schema.json`. **But no §12 BE aggregate contract**, no §12.x event-queue field semantics, no §13.1, no §21 handoff/governance detail beyond the schema.
- **Missing / needs spec:** §12 trading-room aggregate + entry/add/reduce/exit/review event-queue contract (field semantics: confidence/probability/EV/rationale/riskNotes/evidenceRefs/invalidation); §12.4/§21 governed TradingIntent handoff (canary/live = request-only, never order); §12.2/§12.3 candidate review → decision card flow.
- **Blocks:** `AG-BE-TR-001`, `AG-BE-TR-002`, `AG-FE-TR-001`, `AG-FE-TR-002`.

### E. Workshop conversation card field specs

- **Cited:** §11.1, §11.2, §11.3
- **Exists:** `contract-closure/05` lists the **component tree** (StrategyUnderstandingCard, MissingDefinitionCard, ResearchPlanCard, …) but not the **field-level data contract** per card.
- **Missing / needs spec:** per-card data shape for UserStrategyDescription / ServantReconstruction / CompletenessUpdate / NextQuestion / ResearchPlanProposal / ResearchProgress / ConsultResult / VersionCompare cards — what each binds from which BFF projection.
- **Blocks:** `AG-FE-SW-001`, `AG-FE-SW-002`, `AG-FE-SW-003`, `AG-FE-RS-001`.

### F. End-to-end flow + cross-repo isolation acceptance

- **Cited:** §24.3 (winner-branch E2E steps 1–11), §26 (isolation)
- **Exists:** nothing concrete; §24.3 and §26 are not present in the SD.
- **Missing / needs spec:** §24.3 the canonical winner-branch end-to-end step list (description → StrategySpec draft → research → completeness → version → trading-room → dashboard → governed intent), with assertions; §26 cross-repo + cross-user + Agora-vs-management isolation acceptance matrix.
- **Blocks:** `AG-E2E-SW-001`, `AG-E2E-TR-001`, `AG-TEST-ID-001`.

---

## Already covered (NOT gaps — for reference)

- §9 Dashboard/Widget → `design-closure/A3` + `contract-closure` dashboard v1.1 (DONE; `AG-BE-DB-001`/`AG-FE-DB-001` shipped).
- §5.x Servant + §6/§7 workshop persistence/lifecycle/private-content → `contract-closure/03` + `sw001-deep-closure` (DONE; `AG-BE-SW-001` review_approved).
- Servant `session_type` (§5.3/§17.1) → patched as `AG-XR-OPENAPI-003` (in flight; unblocks `AG-BE-ID-003`).
- §10/§11 shell IA (page composition, three tabs, redirects) → `contract-closure/05` (front-end shell only; card field specs still missing — see group E).

## Non-design issue (do NOT route to SD team)

- `AG-FE-DB-002` is **not** a design gap. It STOPs because `AG-FE-DB-001`'s front-end artifacts (`src/agora/widgets/*`, WidgetSpecV2/DashboardRecipe renderers) are not present in the `execute-plans@dev` repo — a cross-repo mirror/sync issue. Resolve by confirming the FE-DB-001 outputs actually landed in `ajoe734/execute-plans`, then retry.

---

## Remaining-task → gap map

| Task | Gap group | Status |
|---|---|---|
| AG-BE-SW-002 | A | BLOCKED |
| AG-FE-SW-003 | A, E | gated |
| AG-BE-RS-004 | A, B | gated |
| AG-FE-RS-001 | A, B, E | gated |
| AG-BE-RS-001 | B | gated |
| AG-BE-RS-002 | B | gated |
| AG-BE-SW-004 | C | BLOCKED |
| AG-BE-TR-001 | D | gated |
| AG-BE-TR-002 | D | gated |
| AG-FE-TR-001 | D, E | gated |
| AG-FE-TR-002 | D, E | gated |
| AG-FE-SW-001 | E | gated |
| AG-FE-SW-002 | E | gated |
| AG-FE-ID-001 | (none — gated on AG-BE-ID-003 only) | gated |
| AG-E2E-SW-001 | F | gated |
| AG-E2E-TR-001 | F | gated |
| AG-TEST-ID-001 | F | gated |
| AG-FE-DB-002 | non-design (cross-repo sync) | retrying |
| AG-BE-ID-003 | covered by AG-XR-OPENAPI-003 | gated |
| AG-BE-CP-001 | covered (A2 recipe + candidate_pool schema) | gated on RS-002 |

## Suggested deliverable

One additive **v1.3 design pack** closing groups A–F (mirroring the prior packs' style: prose contract + additive schemas under `specs/agora/v3` or new files, no edits to frozen v1/v1.1/v1.2 bundles). Recommended priority: **A → C → B → D → E → F** (A unblocks the workshop spine that most FE/RS/TR work funnels through).
