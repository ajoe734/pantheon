# Sidecar Review Packet: STRAT-V2-001

Sidecar task: STRAT-V2-001-SIDECAR-REVIEW
Parent task: STRAT-V2-001 - Strategy spec distillation production smoke (real research note)
Helper kind: review_packet
Owner: Codex
Reviewer: Codex2
Generated: 2026-05-18
Status: Review approved; owner closeout record added

## Scope

This packet is support-only. It summarizes the parent task's review posture,
evidence bundle, and lifecycle handoff for the assigned reviewer. It does not
modify canonical truth, L1 policy, StrategySpec contracts, registry behavior,
governance behavior, runtime code, or the parent implementation.

Primary parent artifacts:

- `services/research/strategy_spec/production_distillation.py`
- `services/research/strategy_spec/test_production_distillation.py`
- `support/evidence/STRAT-V2-001/research_note_tw_momentum_quality.md`
- `support/evidence/STRAT-V2-001/sample_run.json`
- `support/evidence/STRAT-V2-001/closeout.md`
- `support/reviews/STRAT-V2-001-review-codex2.md`
- `support/reviews/STRAT-V2-001-review-claude.md`

Task-scoped context read:

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/strat_v2_001_sidecar_review.md`
- `.orchestrator/skills/worker-anchor-commit.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json`
- `docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/planning-session.json`

## Current Lifecycle Snapshot

Central status, read with `AI_NAME=Codex ./scripts/ai-status.sh show`, shows:

| Task | Status | Owner | Reviewer | Notes |
|---|---|---|---|---|
| `STRAT-V2-001` | `review_approved` | Codex2 | Codex | Parent is awaiting owner finalization. Review notes record a Codex approval after scoped verification and merged PR gate. |
| `STRAT-V2-001-SIDECAR-REVIEW` | `in_progress` | Codex | Codex2 | This sidecar packet is the only task-owned repo artifact. |

The local worktree `ai-status.json` snapshot is stale for lifecycle purposes;
status reads and updates for this task used the configured central status root
through `scripts/ai-status.sh`.

## Parent Evidence Summary

The production distillation surface converts a real internal research note into
a schema-valid StrategySpec payload and a registry-admission payload without
performing a registry write. The parent implementation keeps registry mutation
outside the distiller and records `metadata.registry_write_performed=false`.

Evidence highlights:

| Evidence item | Verified posture |
|---|---|
| Real source note | `support/evidence/STRAT-V2-001/research_note_tw_momentum_quality.md` contains source id `src-note-tw-momentum-quality-001`, hypothesis, universe, frequency, risk caps, data requirements, evaluation metrics, and one Qlib code ref. |
| StrategySpec payload | `sample_run.json` contains strategy id `strat-tw-momentum-quality-production-seed-10425304`, market scope `universe:twse-tpex-top200`, `frequency=1d`, and TWSE/TPEx equity scope. |
| Risk caps | StrategySpec metadata contains `max_position_pct=0.03`, `max_gross_exposure_pct=0.35`, and `max_single_sector_pct=0.25`. |
| Evidence binding | StrategySpec has two evidence refs: an evidence bundle and an evidence item for `src-note-tw-momentum-quality-001#strategy-distillation`. |
| Code binding | StrategySpec has one code ref to `services/research/qlib/adapter/qlib_adapter.py`, `QlibAdapter`, lines 80-140. |
| Registry payload | `registry_payload.artifact_state=draft`, checksum is present, `version=1.0.0`, and storage is inline under registry metadata. |
| Candidate request | `candidate_advance_request.target_state=candidate`; no paper, canary, live, broker, capital, or runtime action is authorized by this packet. |

## Acceptance Mapping

| Parent acceptance criterion | Evidence posture |
|---|---|
| `production_distillation.py` exposes `distill(source_record_id)` returning a StrategySpec dict ready for registry write | Satisfied by module-level `distill()` and `ProductionStrategySpecDistiller.distill()` returning a validated payload. Registry admission payload is available through `distill_registry_payload()`. |
| Distillation extracts hypothesis, universe, frequency, and risk caps from markdown structured sections | Satisfied by parser coverage and sample evidence. The TW note maps to the expected hypothesis, `market_scope`, `frequency=1d`, and risk caps. |
| `evidence_refs` and `code_refs` bind via STRAT-004 helper | Satisfied by `distill_result()` delegating through `StrategySpecConversionService.convert_source_material()` with constructed source/evidence objects. |
| Test feeds 2 fixture research notes and asserts both produce valid StrategySpecs | Satisfied by `test_distills_two_fixture_research_notes_to_valid_strategy_specs`. It validates TW momentum and US ETF volatility reversal fixtures. |
| Malformed sources reject with explicit `ValidationError` | Satisfied by `test_malformed_research_note_rejects_with_validation_error`, which checks a missing `risk_caps` section. |
| `pytest -q` exits 0 | Satisfied for the scoped strategy_spec package: `25 passed`. Full repository pytest is not the verified shape for this parent because unrelated optional dependencies have previously failed collection. |

## Review Records

Two review records already support the parent task:

- `support/reviews/STRAT-V2-001-review-codex2.md` found no scoped code issues and recorded the stale-status lifecycle correction problem that interrupted the first review pass.
- `support/reviews/STRAT-V2-001-review-claude.md` approved all parent acceptance criteria, confirmed the same scoped code posture, and treated the Codex2 review as supporting evidence.

The parent central status has since moved to `review_approved`; Codex2 is now
the parent owner and Codex is the parent reviewer. This sidecar does not change
that owner/reviewer assignment.

## Verification Performed

Successful verification run for this sidecar:

```bash
python3 -m pytest services/research/strategy_spec -q
python3 -m services.research.strategy_spec.production_distillation src-note-tw-momentum-quality-001 --source-dir support/evidence/STRAT-V2-001 --sample-run --output /tmp/STRAT-V2-001-SIDECAR-REVIEW-sample_run.generated.json
diff -u support/evidence/STRAT-V2-001/sample_run.json /tmp/STRAT-V2-001-SIDECAR-REVIEW-sample_run.generated.json
python3 -m json.tool support/evidence/STRAT-V2-001/sample_run.json
git diff --check -- services/research/strategy_spec/production_distillation.py services/research/strategy_spec/test_production_distillation.py support/evidence/STRAT-V2-001/sample_run.json support/evidence/STRAT-V2-001/research_note_tw_momentum_quality.md support/reviews/STRAT-V2-001-review-codex2.md support/reviews/STRAT-V2-001-review-claude.md support/evidence/STRAT-V2-001/closeout.md
```

Results:

- StrategySpec package tests: `25 passed in 5.49s`.
- Sample run regeneration: exit 0.
- Sample run diff: exit 0, no diff.
- Sample JSON formatting check: exit 0.
- Whitespace diff check on parent artifacts and review records: exit 0.

## Owner Closeout Record

Owner finalization rechecked the approved sidecar state on 2026-05-18.

Closeout inputs:

- `AI_NAME=Codex ./scripts/ai-status.sh show STRAT-V2-001-SIDECAR-REVIEW`
  showed this sidecar as `review_approved`, owned by Codex and reviewed by
  Codex2.
- Codex2 approval notes record that this packet stayed support-only, PR #150
  merged, Branch CI Gate passed, and Orchestrator Sync passed.
- `AI_NAME=Codex ./scripts/ai-status.sh show STRAT-V2-001` showed the parent
  task archived as `done`; the parent lifecycle snapshot above is therefore a
  historical review snapshot, not a reopened implementation state.
- `gh pr view 150 --json number,state,mergedAt,mergeCommit,headRefName,baseRefName,statusCheckRollup,title,url`
  confirmed PR #150 was merged into `dev` with merge commit
  `62621ec112261abf7c756d3fa0e7671f2a2ce875` and successful required checks.

Closeout verification rerun:

```bash
python3 -m pytest services/research/strategy_spec -q
python3 -m services.research.strategy_spec.production_distillation src-note-tw-momentum-quality-001 --source-dir support/evidence/STRAT-V2-001 --sample-run --output /tmp/STRAT-V2-001-SIDECAR-REVIEW-sample_run.generated.json
diff -u support/evidence/STRAT-V2-001/sample_run.json /tmp/STRAT-V2-001-SIDECAR-REVIEW-sample_run.generated.json
python3 -m json.tool support/evidence/STRAT-V2-001/sample_run.json
git diff --check -- support/sidecars/STRAT-V2-001/STRAT-V2-001-SIDECAR-REVIEW.md support/evidence/STRAT-V2-001/sample_run.json support/evidence/STRAT-V2-001/research_note_tw_momentum_quality.md services/research/strategy_spec/production_distillation.py services/research/strategy_spec/test_production_distillation.py
```

Results:

- StrategySpec package tests: `25 passed in 5.75s`.
- Sample run regeneration: exit 0.
- Sample run diff: exit 0, no diff.
- Sample JSON formatting check: exit 0.
- Whitespace diff check: exit 0.

Closeout scope remains unchanged: this task owns only the sidecar review
packet and does not modify L1 canonical truth, StrategySpec contracts,
registry behavior, governance behavior, runtime behavior, or parent
implementation artifacts.

## Reviewer Attention Items

1. Parent central lifecycle is already `review_approved`, so Codex2 should use
   this sidecar as finalization support rather than as a request to reopen the
   parent implementation.
2. The parent `ai-status.json` snapshot in this worktree is stale. Use
   `AI_NAME=<actor> ./scripts/ai-status.sh show STRAT-V2-001` for the central
   status root before finalizing parent or sidecar state.
3. `sample_run.json` proves deterministic production distillation and
   registry-admission payload construction. It does not prove paper trading,
   canary execution, live execution, broker connectivity, capital binding, or
   registry write execution.
4. The review file named in the current parent status is
   `support/reviews/STRAT-V2-001-review-claude.md`, while the reviewer field is
   now Codex after lifecycle correction. This sidecar treats that as historical
   review evidence, not as a blocker to the support packet.

## Handoff Recommendation

Hand this sidecar packet to Codex2 for sidecar review. Suggested use:

- Treat parent evidence and scoped tests as sufficient for the production
  distillation acceptance criteria.
- Preserve the boundary that the distiller emits StrategySpec and registry
  payloads only; registry mutation and promotion remain outside this sidecar.
- If Codex2 finalizes the parent, cite this packet only as supporting evidence
  and keep the actual parent closeout on the existing task lifecycle.
- Do not absorb this packet into L1 canonical truth unless the parent owner
  separately decides a documentation promotion is needed.
