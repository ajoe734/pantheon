# AG-BE-SW-003 Sidecar: Acceptance Packet and Dependency Map

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-003-SIDECAR-ACCEPTANCE` |
| Helper kind | `acceptance_packet` |
| Parent task | `AG-BE-SW-003` — agora-strategy-completeness skill (NBQ scoring engine) |
| Parent owner / reviewer | TBD (auto-assigned) / Claude |
| Prepared by | Claude2 |
| Reviewer | Claude |
| Date | 2026-06-21 |
| Mutates canonical truth | false |
| Status | In-progress sidecar — support artifact only |

## Purpose

This packet gives the AG-BE-SW-003 owner a ready-to-verify acceptance
checklist, a precise dependency map, an as-found gap analysis, and the
target file list needed to implement the `agora-strategy-completeness`
OpenClaw skill.

This sidecar does not modify any L1 canonical policy, existing schema
files, BFF routes, workshop store, or OpenClaw adapter. All decisions
remain with the parent task owner.

---

## 1. Scope of AG-BE-SW-003

AG-BE-SW-003 implements the **agora-strategy-completeness** OpenClaw skill
as described in:

- `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/A1_next_best_question_scoring_spec.md` (Design Frozen v1.0)
- `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/strategy-completeness/SPEC.md`
- `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/C1_agora_openclaw_skills_master_spec.md`

The skill evaluates a StrategySpec draft and outputs:

1. A `StrategyCompletenessMap` — per-field state (confirmed / missing / weak / conflicting / inferred_needs_confirmation / not_applicable).
2. A list of `blockingItems` — fields that block the research, validation, or trading-room gate.
3. `researchReadiness`, `validationReadiness`, `tradingRoomReadiness` booleans.
4. The **Next-Best-Question (NBQ)** using the A1 five-factor scoring formula plus four penalties.
5. `suppressedQuestions` — candidates that were filtered by eligibility gates.

---

## 2. Dependency Map

### Hard dependencies (must be true before implementation)

| Dependency | Location | Current state |
|---|---|---|
| AG-BE-SW-001 workshop session routes | `services/control-plane/bff/agora/strategy_workshop/` | ✅ Done — session CRUD, event append, completeness snapshot read all implemented |
| Strategy completeness BFF route | `GET /bff/agora/workshops/{id}/completeness` in `router.py` | ✅ Exists — reads `store.get_latest_completeness_snapshot(workshop_id)` |
| Workshop store `strategy_completeness_snapshot` table | `services/control-plane/bff/agora/strategy_workshop/store.py` | ✅ Exists — `create_completeness_snapshot`, `get_latest_completeness_snapshot` implemented for both Memory and Postgres backends |
| `strategy_completeness.schema.json` frozen schema | `services/control-plane/specs/agora/strategy_completeness.schema.json` | ✅ Exists — `spec_version`, `overall_grade`, `dimensions`, `blockers`, `research_ready`, `assessed_at` |
| A1 NBQ design spec (Design Frozen v1.0) | `docs/04/.../design-closure/A1_next_best_question_scoring_spec.md` | ✅ Frozen |
| C1 OpenClaw skills master spec (Design Frozen v1.0) | `docs/04/.../design-closure/C1_agora_openclaw_skills_master_spec.md` | ✅ Frozen |
| Skill SPEC.md for strategy-completeness | `docs/04/.../design-closure/skills/agora/strategy-completeness/SPEC.md` | ✅ Frozen |
| Expert-consult skill pattern reference | `integrations/openclaw/skills/agora/expert_consult/` | ✅ Exists — use as the canonical directory/file pattern |
| `QuestionScoringPolicy.v1` as the policy version token | Defined in A1 §8 | ✅ Defined in spec |
| `StrategyCompletenessMap` field list | Derived from A1 §2 candidate sources | ✅ Derivable from A1 |

### Soft dependencies (must coordinate with)

| Dependency | Impact | Action |
|---|---|---|
| AG-BE-RS-003 (expert-consult route) | AG-BE-SW-003 invokes expert consult tool via `expert_consult` skill | No interface change needed — skill calls are within OpenClaw layer |
| `services/control-plane/specs/agora/capability_manifest.json` | `agora.workshop.v1` already lists `strategy_completeness.schema.json`; no manifest update required for this task | Verify after implementation that schema refs still validate |
| Research capability snapshot | Skill input requires `researchCapabilitySnapshotRef` per SPEC.md | Must exist at runtime but is supplied by caller; not owned by AG-BE-SW-003 |

---

## 3. As-Found Gap Analysis

| Gap | Evidence | Impact |
|---|---|---|
| No `strategy_completeness` skill module exists | `integrations/openclaw/skills/agora/` has only `expert_consult/` | Blocking — entire NBQ engine is absent |
| No `next_best_question_gold_cases.json` fixture | A1 §9 requires minimum 10 golden cases; none found in repo | Blocking — acceptance cannot be verified without fixtures |
| No `QuestionScoringPolicy` dataclass or scoring engine | A1 §8 requires offline-updateable policy version with factor weights | Blocking — scoring formula is not implemented |
| `StrategyCompletenessMap` field taxonomy not codified | A1 §2 lists candidate sources (missing/weak/conflicting/inferred_needs_confirmation); no Python enum or model found | Blocking — skill cannot produce state_map without field taxonomy |
| `strategy_completeness_snapshot.state_map_json` column type | Store saves state_map as a JSON blob; the top-level shape must match SPEC.md `CompletenessOutput.stateMap` record, not the current `strategy_completeness.schema.json` `dimensions` array | Design decision for owner — the schema shape differs between the storage model and the skill output shape |
| `GET /bff/agora/workshops/{id}/completeness` returns raw store row | Router returns the raw store snapshot dict; it does not validate against `CompletenessOutput` or translate to C1 result envelope | Low risk for this task; note for owner to decide whether response translation is in scope |

---

## 4. Acceptance Checklist

Items derived from A1 spec §11 (Definition of Done), SPEC.md, and C1 §6.

### 4.1 Scoring engine (A1)

- [ ] Five base factors implemented with stated weights (0.30 / 0.25 / 0.20 / 0.10 / 0.15)
- [ ] Four penalty types implemented (already_answered, low_level_question, cognitive_burden, premature_optimization)
- [ ] `final_score = clamp(100 × (base_score - penalty), 0, 100)`
- [ ] Eligibility gate enforced: Unanswered, Non-derivable, Decision-relevant, Scope-safe, Non-duplicate, User-level-appropriate
- [ ] Candidates that fail eligibility are added to `suppressedQuestions` with reason, NOT scored
- [ ] Mandatory override queue implemented (six trigger conditions, priority order: compliance/privacy > PIT/data-leakage > risk/leverage > exit/invalidation > execution/cost)
- [ ] Score threshold: `final_score < 55` → no question asked; provisional assumptions stated instead
- [ ] Tie-breaker order: mandatory override → downstream_blocking_weight → risk_impact → recent_user_focus → answerable_by_option
- [ ] At most one primary question per invocation
- [ ] At most two optional clarifications per invocation; both must share the same decision bundle
- [ ] Policy version is `QuestionScoringPolicy.v1` and is stored — not derived at runtime
- [ ] Same input + policy version + persona context → deterministic ranking
- [ ] Field importance table from A1 §4.1 is encoded (exit/invalidation = 1.0, down to display/dashboard_preference = 0.30)

### 4.2 Skill interface (C1 + SPEC.md)

- [ ] Skill accepts `CompletenessInput`: `workshopId`, `strategySpecRef`, `strategyVersionId`, `workshopEventRefs`, `researchCapabilitySnapshotRef`, `questionScoringPolicyVersion`
- [ ] Skill returns `CompletenessOutput`: `stateMap`, `blockingItems`, `provisionalAssumptions`, `researchReadiness`, `validationReadiness`, `tradingRoomReadiness`, `nextBestQuestion`, `suppressedQuestions`
- [ ] Skill wraps output in C1 result envelope: `status`, `output_schema`, `result`, `result_ref`, `evidence_refs`, `warnings`, `blocking_reasons`, `tool_invocations`, `memory_candidates`, `audit`
- [ ] `audit.skill_version`, `audit.input_checksum`, `audit.output_checksum` are populated
- [ ] Skill never writes RuntimeBinding, capital binding, broker order, or live enable
- [ ] Skill does not cross user scope
- [ ] Skill does not send raw private prompt to central persona without explicit authorization
- [ ] All citations carry evidence refs; uncertain inferences are flagged `uncertain` not stated as fact
- [ ] Error codes from C1 §5 are returned on failure (INPUT_SCHEMA_INVALID, REGISTRY_VERSION_MISMATCH, etc.)

### 4.3 Allowed tools (SPEC.md)

- [ ] Skill only invokes allowed tools: `strategy_spec.read`, `strategy_spec.validate`, `research.capabilities`, `source_catalog.capabilities`, `question_policy.read`, `persona_memory.read_private`
- [ ] Tool invocations are recorded in `result.tool_invocations`

### 4.4 Golden cases (A1 §9 — minimum requirement)

All ten golden cases from A1 §9 must have corresponding fixtures and pass:

| Case | Required outcome |
|---|---|
| GC-01: Winner-branch — complete description | Must ask about identity-mapping evidence role, NOT data format |
| GC-02: Sector laggard — clear universe, missing exit | Must ask about invalidation conditions |
| GC-03: Technical breakout — complete entry/stop, missing position sizing | Must ask about per-position and gross exposure |
| GC-04: Pair trade — pair selected, no spread/hedge ratio | Must ask about hedge definition or offer tool inference |
| GC-05: Event trade — event date and data availability unclear | Must trigger mandatory PIT question before model choice |
| GC-06: Options — payoff defined, max loss undefined | Must ask risk budget before anything else |
| GC-07: Fully described by user in one message | Must NOT re-ask field-by-field; must build ResearchPlan directly |
| GC-08: Liquidity queryable by tool | Must suppress question; mark as `reason: derivable_by_tool` |
| GC-09: Provisional defaults available | Must state provisional values, proceed to initial research (score < 55 path) |
| GC-10: User corrects question as too low-level | Subsequent similar questions must receive elevated `low_level_question_penalty` |

- [ ] Fixture file `next_best_question_gold_cases.json` exists in the skill directory or `tests/fixtures/agora/`
- [ ] All 10 golden cases produce deterministic, spec-compliant outputs
- [ ] At least 1 privacy/scope failure case
- [ ] At least 1 tool failure / degraded case

### 4.5 Regression requirements (C1 §6)

- [ ] Prompt/skill changes trigger offline eval → regression suite → shadow sessions → reviewed skill version → shared skill update
- [ ] Evidence completeness check runs on every output (no empty evidence_refs when result has citations)
- [ ] Schema validation runs on every invocation output before returning to caller

---

## 5. Target File List

The implementer should create or update the following files.

### New files (create)

```
integrations/openclaw/skills/agora/strategy_completeness/
  __init__.py
  skill.py          — CompletenessInput/Output models; scoring engine; skill entrypoint
  SPEC.md           — copy from design-closure/skills/agora/strategy-completeness/SPEC.md
  test_skill.py     — unit tests for scoring engine + golden cases

tests/fixtures/agora/
  next_best_question_gold_cases.json   — 10 golden cases + 1 privacy + 1 degraded
```

### Existing files to verify (not change unless required)

```
services/control-plane/bff/agora/strategy_workshop/router.py
  — GET /bff/agora/workshops/{id}/completeness returns store snapshot; may need
    a response-translation step to match CompletenessOutput shape

services/control-plane/specs/agora/strategy_completeness.schema.json
  — Schema dimensions array shape differs from SPEC.md stateMap record;
    owner must decide which governs the BFF response envelope.

services/control-plane/specs/agora/capability_manifest.json
  — agora.workshop.v1 already includes strategy_completeness.schema.json;
    no change needed unless the skill output schema is registered separately.

integrations/openclaw/skills/agora/__init__.py
  — Add export for strategy_completeness module after implementation.
```

---

## 6. Interface Contract Summary

### Skill input (from SPEC.md)

```json
{
  "workshopId": "ws-...",
  "strategySpecRef": "strat-reg-...",
  "strategyVersionId": "v-...",
  "workshopEventRefs": ["event-..."],
  "researchCapabilitySnapshotRef": "cap-snap-...",
  "questionScoringPolicyVersion": "QuestionScoringPolicy.v1"
}
```

### Skill output (from SPEC.md)

```json
{
  "stateMap": {
    "<field_name>": "confirmed|inferred_needs_confirmation|missing|weak|conflicting|not_applicable"
  },
  "blockingItems": [
    {"field": "exit_condition", "reason": "...", "gate": "research|validation|trading_room"}
  ],
  "provisionalAssumptions": [],
  "researchReadiness": true,
  "validationReadiness": false,
  "tradingRoomReadiness": false,
  "nextBestQuestion": {
    "policy_version": "QuestionScoringPolicy.v1",
    "primary_question": {
      "question_id": "q_...",
      "text": "...",
      "target_fields": ["..."],
      "score": 87.5,
      "mandatory": false,
      "why_now": "...",
      "answer_mode": "single_choice|multi_choice|free_text|confirm_provisional",
      "options": []
    },
    "optional_clarifications": [],
    "provisional_assumptions": [],
    "suppressed_questions": [{"question_id": "...", "reason": "derivable_by_tool"}]
  },
  "suppressedQuestions": []
}
```

### C1 result envelope

The above is nested under `result` inside:

```json
{
  "status": "completed|needs_user|blocked|failed",
  "output_schema": "agora.strategy_completeness.v1",
  "result": { "...above..." },
  "result_ref": null,
  "evidence_refs": [],
  "warnings": [],
  "blocking_reasons": [],
  "tool_invocations": [],
  "memory_candidates": [],
  "audit": {
    "trace_id": "...",
    "skill_version": "1.0.0",
    "input_checksum": "...",
    "output_checksum": "..."
  }
}
```

---

## 7. Open Questions for Parent Task Owner

These are not blockers on starting implementation, but the owner should
resolve them before final closeout.

| # | Question | Impact |
|---|---|---|
| OQ-1 | Which schema governs the `GET /bff/agora/workshops/{id}/completeness` response: the existing `strategy_completeness.schema.json` (dimensions array) or the SPEC.md `CompletenessOutput` (stateMap record)? | Determines whether the BFF route needs a translation layer or whether the storage schema is updated |
| OQ-2 | Does AG-BE-SW-003 scope the BFF translator (skill result → workshop completeness snapshot write-back) or is that a follow-on task? | The existing `store.create_completeness_snapshot` columns (`state_map_json`, `blocking_items_json`, `next_question_json`) map to SPEC.md fields; the translator would populate those columns from skill output |
| OQ-3 | Should `next_best_question_gold_cases.json` live in `integrations/openclaw/skills/agora/strategy_completeness/` or `tests/fixtures/agora/`? | Affects test import path and CI matrix scope |
| OQ-4 | GC-10 requires memory of user corrections across skill invocations. Is cross-invocation correction memory in scope for v1 or deferred to the `agora-personalization` skill? | A1 §8 says corrections become learned input for offline replay, not live weight adjustment; v1 only needs to receive an elevated penalty as a skill input, not persist it |

---

## 8. Verification Commands

Run these after implementation to validate before handoff to reviewer:

```bash
# Run strategy-completeness skill unit tests
python3 -m pytest integrations/openclaw/skills/agora/strategy_completeness/test_skill.py -v

# Run golden case fixture validation
python3 -m pytest integrations/openclaw/skills/agora/strategy_completeness/test_skill.py -v -k golden

# Confirm no leakage into workshop router tests
python3 -m pytest services/control-plane/bff/tests/test_agora_strategy_workshop.py -v

# Verify schema bundle still validates after any schema changes
python3 scripts/agora_schema_bundle.py --verify
```

---

## 9. Handoff Note

This packet is a **sidecar support artifact**. It does not modify L1
canonical truth, schema files, BFF routes, workshop store, registry, or
governance models.

After the parent task owner implements AG-BE-SW-003, this packet should
be marked absorbed or archived. The reviewer for this sidecar (Claude) may
choose to treat the checklist as the AG-BE-SW-003 review rubric.

---

*Prepared by Claude2 · 2026-06-21 · task AG-BE-SW-003-SIDECAR-ACCEPTANCE*
