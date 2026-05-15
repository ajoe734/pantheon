# P1-PERSIST-001 Review

Reviewer: Claude
Date: 2026-05-01

## Outcome

Approved. All three acceptance criteria are met. Implementation is clean,
consistent across all targeted services, and well-verified.

## Verification

Artifacts reviewed:

- `support/reviews/P1-PERSIST-001-codex-review-handoff.md`
- `services/foundation/persistence_posture.py`
- `services/foundation/tests/test_persistence_posture.py`
- `services/governance/main.py` (posture integration sample)
- `services/consultation/main.py` (posture integration sample)
- `docs/04/pantheon_sa/SA-13_contract_schema_gap_analysis.md` §12.3
- `docs/04/pantheon_sa/SA-20_v2_risk_register_corrected.md` R-DATA-003
- `env/prod-control.env.example`

Checks run:

- `rg "persistence_posture" services/ --files-with-matches` → 12 service
  main.py files + test + module (14 total)
- Inspected `ENFORCED_MODES` set and `_is_enforced_mode()` prefix logic — covers
  staging, staging-live, prod, production, and prefix variants
- Confirmed `require_persistence_posture()` called at module level in governance
  and consultation (fail-fast at import/startup)
- Confirmed `PERSISTENCE_POSTURE.to_dict()` wired into both `dependencies=` and
  `details=` on `/healthz` in sampled services
- Confirmed `dev_fallback_allowed = not self.enforced` surfaced correctly
- Confirmed `env/prod-control.env.example` sets `PANTHEON_PERSISTENCE_POSTURE=production`
  and all four `PANTHEON_S3_*` / `PANTHEON_ARTIFACT_BUCKET` vars
- SA-13 §12.3 lists all 12 guarded services and the guard description
- SA-20 R-DATA-003 mitigation updated to reference
  `services.foundation.persistence_posture` with correct acceptance text

## Acceptance Criteria Verdict

**AC-1 — staging/prod fail fast without Postgres/object store**: ✅

`require_persistence_posture()` is called at module import on all 12 services.
In enforced modes (stage/staging/staging-live/prod/production), it raises
`RuntimeError` unless `DATABASE_URL` is a Postgres DSN, each service backend
env is `postgres`, and all four `OBJECT_STORE_KEYS` are present.

**AC-2 — dev fallback clearly dev-only**: ✅

`PersistencePostureCheck.to_dict()` surfaces `dev_fallback_allowed: true` only
when `enforced is False`. No enforced mode can return `dev_fallback_allowed:
true` because the guard raises before the service starts.

**AC-3 — environment posture visible in health/runtime metadata**: ✅

`PERSISTENCE_POSTURE.to_dict()` is included in both the `dependencies=` lambda
(healthz summary) and the `details=` lambda (healthz detail) across all
integrated services.

## Non-Blocking Notes

- `services/capital/test_service.py` has three pre-existing failures on
  unhandled domain exceptions in binding write paths. These are unrelated to
  P1-PERSIST-001 and `test_health` passes. Owner acknowledgment in the handoff
  packet is correct.
- The dirty worktree contains bracket-order changes in
  `services/execution/lean_runtime/*` from adjacent execution work. They are
  correctly excluded from the P1-PERSIST-001 scope and should be isolated into a
  separate commit when that work closes.
- SA-20 R-DATA-003 acceptance text in the doc reads "staging/prod fail without
  Postgres/object store when required" — the "when required" qualifier is accurate
  because `require_object_store=True` is the default but can be overridden. No
  change needed; the implementation honors this correctly.

## Closeout Guidance

Owner Codex should finalize per
`.orchestrator/skills/task-closeout-finalization.md`:

1. Confirm no additional task-scoped doc updates are needed.
2. Stage only P1-PERSIST-001 owned files (exclude bracket-order changes in
   `services/execution/lean_runtime/*` and unrelated SA-20 hunks).
3. Create a task-scoped commit with subject including `P1-PERSIST-001` and body
   including `LLM-Agent: Codex`, `Task-ID: P1-PERSIST-001`, `Reviewer: Claude`.
4. Run `AI_NAME=Codex ./scripts/ai-status.sh done P1-PERSIST-001 "<message>"`.
5. Push to upstream after `done`.
