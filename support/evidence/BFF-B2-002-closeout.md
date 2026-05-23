# BFF-B2-002 Closeout Evidence

Task: BFF-B2-002 — Evolution + Operations facade (B2.2 13 endpoints)
Owner: Claude2
Reviewer: Codex2
Phase: Sprint BFF-2 / EPIC-BFF-GAP-CORE

## Delivery

PR #443 merged to `dev` on 2026-05-23T09:00:42Z.
PR title: `BFF-B2-002: anchor evolution+jobs+ops facade`
Implementation commit: `a4a49931 BFF-B2-002: anchor evolution+jobs+ops facade`

## Endpoints Delivered (B2.2 — 13 primary + 2 dedicated sub-routes)

| Route | Method | Handler |
|---|---|---|
| `/bff/evolution-programs` | GET | `bff_list_evolution_programs` |
| `/bff/evolution-programs/{program_id}` | GET | `bff_get_evolution_program` |
| `/bff/evolution-programs/{program_id}/runs` | GET | `bff_list_evolution_program_runs` |
| `/bff/evolution-programs/{program_id}/candidates` | GET | `bff_list_evolution_program_candidates` |
| `/bff/jobs` | GET | `bff_list_jobs` |
| `/bff/jobs/{job_id}` | GET | `bff_get_job` |
| `/bff/alerts` | GET | `bff_list_alerts` |
| `/bff/incidents` | GET | `bff_list_incidents` |
| `/bff/audit` | GET | `bff_list_audit` |
| `/bff/artifacts` | GET | `bff_list_artifacts` |
| `/bff/runtimes` | GET | `bff_list_runtimes` |
| `/bff/runtimes/{runtime_id}` | GET | `bff_get_runtime` |
| `/bff/v5/loop-runs` | GET | `bff_list_loop_runs` (dedicated handler) |
| `/bff/v5/loop-runs/{loop_run_id}` | GET | `bff_get_loop_run` (dedicated handler) |
| `/bff/v5/sentinel/findings/{finding_id}` | GET | `bff_get_sentinel_finding` (dedicated handler) |

## Files Changed

- `services/control-plane/bff/main.py` — added 4 dedicated handlers
  (`bff_list_loop_runs`, `bff_get_loop_run`, `bff_get_sentinel_finding`,
  `bff_list_artifacts`); removed catch-all dead-entry stubs for B2.2 routes
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md`
  — updated §B2.2 with final endpoint surface
- `services/control-plane/bff/tests/test_bff_b2_002_evolution_jobs_ops.py`
  — 31 integration tests covering all 13 primary B2.2 endpoints plus 401 probes

## Reviewer Approval

Codex2 approved: "PR #443 is merged to dev; dedicated handlers for loop-runs,
sentinel finding detail, and artifacts are registered before catch-alls; removed
catch-all decorators match §B2.2; focused local validation passed with 99 tests
plus route/auth probes."

## Verification

Commands run during owner closeout (2026-05-23):

```
python3 -m pytest tests/test_bff_b2_002_evolution_jobs_ops.py -q
# Result: 31 passed

python3 -m pytest tests/test_bff_b2_002_evolution_jobs_ops.py tests/test_bff_b2_list_detail_facade.py -q
# Result: 70 passed (B2.1 + B2.2, no regressions)
```

PR #443 state: MERGED to `dev` (confirmed via `gh pr view 443`).
