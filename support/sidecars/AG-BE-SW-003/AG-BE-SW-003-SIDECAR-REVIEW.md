# AG-BE-SW-003 Sidecar Review Packet

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-SW-003-SIDECAR-REVIEW` |
| Helper kind | `review_packet` |
| Parent task | `AG-BE-SW-003` - `agora-strategy-completeness` skill and NBQ scoring |
| Sidecar owner / reviewer | `Codex2` / `Claude2` |
| Prepared by | `Codex2` |
| Date | `2026-06-21` |
| Mutates canonical truth | `false` |
| Status | Ready for sidecar review |

This is a support-only packet. It does not modify L1 canonical truth, core
contract truth, BFF routes, OpenAPI/schema files, runtime binding, registry
behavior, governance behavior, or parent implementation files. Parent owner and
reviewer decide whether the observations below require follow-up.

## Purpose

Summarize review evidence for `AG-BE-SW-003` after the parent implementation
landed on `dev`, so `Claude2` can review the sidecar packet and the parent lane
can decide what to absorb. This packet is intentionally evidence and handoff
material only.

## Sources Read

| Source | Purpose |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Collaboration and sidecar boundary. |
| `.orchestrator/task-briefs/ag_be_sw_003_sidecar_review.md` | Task-scoped owner/reviewer/scope context. |
| `.orchestrator/skills/worker-anchor-commit.md` | Commit and scope discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Finalization and PR flow expectations. |
| `ai-status.json` and `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-SW-003-SIDECAR-REVIEW` | Durable task state; this task is active `in_progress`, owner `Codex2`, reviewer `Claude2`. |
| Parent commit `d091b8a2fc6157a05198ba8f122d195cae8343b2` | Parent implementation under review. |
| Merge commit `bc37f3d5a9d4c6b5bff5110e7f4ee99f58b7beab` | Parent PR #2018 landed on `dev`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/A1_next_best_question_scoring_spec.md` | NBQ scoring, mandatory override, golden case, and Definition of Done reference. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/C1_agora_openclaw_skills_master_spec.md` | Common skill envelope, hard rules, failures, and eval policy. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/strategy-completeness/SPEC.md` | Skill-specific input/output/tools/failure contract. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/next_best_question_gold_cases.json` | Ten golden case source fixture. |
| `task/AG-BE-SW-003-SIDECAR-ACCEPTANCE` at `482a0816` | Sibling pre-implementation acceptance checklist used as a rubric only; not modified or absorbed here. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Parent Implementation Snapshot

Parent implementation commit:
`d091b8a2fc6157a05198ba8f122d195cae8343b2`
(`AG-BE-SW-003: add strategy-completeness skill and NBQ scoring`).

Parent merge to `dev`:
`bc37f3d5a9d4c6b5bff5110e7f4ee99f58b7beab`
(`Merge pull request #2018 from ajoe734/task/AG-BE-SW-003`).

Changed parent files:

| File | Parent change | Review relevance |
|---|---:|---|
| `integrations/openclaw/skills/agora/strategy_completeness/__init__.py` | Added | Exports the new Pantheon-side skill entrypoint. |
| `integrations/openclaw/skills/agora/strategy_completeness/skill.py` | Added | Pydantic input/output models and `run_strategy_completeness()`. |
| `integrations/openclaw/skills/agora/strategy_completeness/test_skill.py` | Added | 26 focused tests covering the 10 NBQ gold cases plus hard-rule and tie-breaker assertions. |
| `services/research/strategy_spec/completeness.py` | Added | Service-layer NBQ scoring, blocking/readiness assessment, provisional and suppression logic. |

Scope confirmation:

- Parent did not modify L1 canonical policy docs.
- Parent did not modify `services/control-plane/specs/agora/strategy_completeness.schema.json`.
- Parent did not modify BFF runtime routes, OpenAPI, registry, governance, runtime binding, capital binding, or broker-order surfaces.
- This sidecar creates only this support artifact under `support/sidecars/AG-BE-SW-003/`.

## Evidence Summary

| Area | Result | Evidence |
|---|---|---|
| A1 scoring formula | Present | `QuestionCandidate.final_score()` implements the five base weights and four penalty slots with clamp to `[0, 100]`. |
| Policy version | Present | `QUESTION_SCORING_POLICY_VERSION = "QuestionScoringPolicy.v1"` and `CompletenessInput.question_scoring_policy_version` literal. |
| NBQ threshold | Present | `NBQ_SCORE_THRESHOLD = 55.0`; regular questions below threshold return no primary question. |
| Mandatory queue | Present, partial by field taxonomy | PIT/data cutoff, risk/max loss, exit/invalidation, risk constraints, and selected conflicting fields bypass normal scoring. |
| Suppression | Present, static | Tool-derivable and low-level fields are suppressed; recently asked fields suppress by exact field name. |
| Provisional assumptions | Present, static | `hedge_ratio` and `hedge_definition` can return `tool_estimate_pending` instead of asking the trader. |
| Readiness gates | Present | `assess_blocking_items()` maps research, validation, and trading-room blockers; `assess_readiness()` derives gate booleans. |
| Golden cases | Present in tests | 10 A1 golden cases are named in `test_skill.py`; focused pytest passes 26 tests. |
| Runtime/capital safety | Present by absence | Skill output test asserts no `runtime_binding`, `live_enable`, or `broker_order` fields. Parent code writes no runtime/capital/broker surface. |
| Existing schema boundary | Preserved | Existing `strategy_completeness.schema.json` remains unchanged; parent implementation uses its own Pydantic output shape. |

## Verification Run

Commands run from this task worktree after aligning the sidecar branch to
`origin/dev`:

```bash
python3 -m json.tool docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/next_best_question_gold_cases.json
python3 -m pytest integrations/openclaw/skills/agora/strategy_completeness/test_skill.py -q
python3 -m pytest services/research/strategy_spec/ -q
git diff --name-status d091b8a2^ d091b8a2
git diff --stat d091b8a2^ d091b8a2
```

Observed results:

| Command | Result |
|---|---|
| `python3 -m json.tool .../next_best_question_gold_cases.json` | PASS; JSON parses. |
| `python3 -m pytest integrations/openclaw/skills/agora/strategy_completeness/test_skill.py -q` | PASS; `26 passed in 21.95s`. |
| `python3 -m pytest services/research/strategy_spec/ -q` | PASS; `25 passed in 35.28s`. |
| `git diff --name-status d091b8a2^ d091b8a2` | PASS for review inventory; exactly 4 added files. |
| `git diff --stat d091b8a2^ d091b8a2` | PASS for review inventory; 1293 insertions, no deletions. |

## Reviewer Attention Items

These are not sidecar code changes. They are the main AG-BE-SW-003 review
questions for the parent owner/reviewer.

| Item | Observation | Suggested disposition |
|---|---|---|
| A1 penalty completeness | The implementation has penalty fields in `QuestionCandidate`, but `already_answered`, `cognitive_burden`, and `premature_optimization` are not dynamically computed. Low-level suppression happens before scoring. | Decide whether static suppression plus open-state filtering satisfies v1, or open a follow-up for full penalty computation. |
| A1 eligibility depth | Non-duplicate handling is exact field-name suppression via `recent_questions`; it does not implement semantic similarity `> 0.85`. Scope-safety and decision-relevance are represented by pre-scoped field taxonomy, not by a runtime eligibility evaluator. | Treat as v1 bounded implementation if caller controls the taxonomy; otherwise require follow-up before claiming full A1 eligibility coverage. |
| Mandatory override breadth | Field-based mandatory rules cover PIT/risk/exit and selected conflicts, but do not explicitly model user claims about insider/manipulation language or unauthorized private ContextBundle content. | Parent should decide whether those cases belong in this skill, a context-bundle validator, or policy/governance follow-up. |
| Score-component traceability | Output includes target fields, score, mandatory flag, and `why_now`; it does not expose individual score components. | If A1 Definition of Done is interpreted literally, expose score component diagnostics in a follow-up. |
| C1 common envelope | `CompletenessInput`/`CompletenessOutput` implement the skill-specific payload, but not the full C1 invocation/result envelope (`skill_call_id`, `trace_id`, `output_schema`, `evidence_refs`, `tool_invocations`, `audit`, checksums). | Accept only if the surrounding OpenClaw adapter wraps the envelope; otherwise this is a parent-scope gap. |
| Allowed tool invocation record | Current v1 function accepts a supplied `state_map`; it does not call or record `strategy_spec.read`, `research.capabilities`, `question_policy.read`, etc. | Accept as pure scoring core, or require an adapter/loader follow-up before runtime invocation claims. |
| Schema/BFF shape alignment | Existing `strategy_completeness.schema.json` still models a persisted assessment with `dimensions`; the new Pydantic output uses `state_map`, `blocking_items`, and `next_best_question`. | Parent owner should decide whether BFF readback translates between these shapes or whether a separate schema version is needed. |
| Failure/eval coverage | Tests cover `CAPABILITY_DENIED` and policy token presence, but not the full C1 failure-code set, privacy/scope failure, tool timeout/degraded case, or evidence completeness check. | Non-blocking for scoring-core review; blocking if this task is expected to close full C1 runtime integration. |

## Acceptance Rubric Mapping

| Rubric area | Current parent state |
|---|---|
| Create `agora-strategy-completeness` skill package | Met. New package exists under `integrations/openclaw/skills/agora/strategy_completeness/`. |
| Implement A1 scoring engine | Mostly met for static scoring, threshold, mandatory, suppression, provisional, and readiness gates. See attention items for dynamic penalties and semantic eligibility. |
| Maintain 10 golden cases | Met for focused unit tests; `26 passed`. The JSON fixture exists in design closure docs, not copied into the skill package. |
| Preserve support/runtime boundaries | Met. No L1, schema, BFF, runtime binding, capital binding, broker-order, registry, or governance edits in the parent diff. |
| Match skill-specific SPEC input/output | Mostly met for payload fields and readiness/NBQ output; full C1 envelope is not implemented in this module. |
| Regression guard | Met for focused skill tests and existing `services/research/strategy_spec/` suite. |

## Suggested Reviewer Gates

`Claude2` should be able to review this sidecar with these gates:

1. Confirm this packet is support-only and only creates
   `support/sidecars/AG-BE-SW-003/AG-BE-SW-003-SIDECAR-REVIEW.md`.
2. Confirm parent implementation surface is accurately summarized as the four
   added files in `d091b8a2`.
3. Confirm local verification results are recorded without overstating runtime
   integration.
4. Confirm the attention items correctly separate scoring-core evidence from
   full A1/C1 runtime/envelope claims.
5. Approve this sidecar if the packet is an accurate review surface, even if
   the parent owner chooses separate follow-up work for the listed gaps.

## Handoff

Recommended handoff message:

```text
AG-BE-SW-003-SIDECAR-REVIEW is ready for Claude2 review at
support/sidecars/AG-BE-SW-003/AG-BE-SW-003-SIDECAR-REVIEW.md.
Evidence: parent commit d091b8a2 added the strategy_completeness package and
scoring core; focused tests pass 26/26 and strategy_spec regression passes 25/25.
The packet also calls out parent-review attention items around dynamic penalties,
semantic eligibility, full C1 envelope/tool records, schema/BFF shape alignment,
and broader failure/eval coverage.
```

Suggested reviewer action if approved:

```bash
AI_NAME=Claude2 REVIEW_FILE=support/sidecars/AG-BE-SW-003/AG-BE-SW-003-SIDECAR-REVIEW.md \
  REVIEW_NOTES_ZH="Review packet accurately summarizes AG-BE-SW-003 parent evidence and separates scoring-core validation from full A1/C1 runtime/envelope follow-up questions." \
  ./scripts/ai-status.sh approve AG-BE-SW-003-SIDECAR-REVIEW \
  "Review packet approved; parent owner can decide whether listed A1/C1 follow-ups are needed."
```

Suggested reviewer action if correction is needed:

```bash
AI_NAME=Claude2 ./scripts/ai-status.sh reopen AG-BE-SW-003-SIDECAR-REVIEW \
  "Describe the packet correction needed."
```

*Prepared by Codex2 for the `AG-BE-SW-003-SIDECAR-REVIEW` support slice.*
