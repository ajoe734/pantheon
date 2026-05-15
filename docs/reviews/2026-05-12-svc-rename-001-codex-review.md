# SVC-RENAME-001 Codex Review

Reviewer: Codex
Date: 2026-05-12
Disposition: approved

## Scope Reviewed

- `.orchestrator/task-briefs/svc_rename_001.md`
- `docs/architecture/services-namespace-migration-map-2026-05-10.md`
- `docs/reviews/2026-05-10-svc-rename-001-codex2-review.md`
- `docs/reviews/2026-05-10-svc-rename-001-claude2-review.md`
- Current `services/` directory layout and targeted import/path references

## Verification Commands

```bash
find services -maxdepth 2 -type d | sort
rg -n "services\\.control_plane|services/control_plane|control-plane/feedback|feedback\\.store|registry-core|services/learning/trl|services\\.learning\\.trl|services/research/trl|services\\.research\\.trl|source_ingestion|source-ingest" services scripts tests docker-compose*.yml docs/architecture/services-namespace-migration-map-2026-05-10.md -g '!**/__pycache__/**'
git merge-base --is-ancestor e2a9d80c HEAD && git show --stat --oneline --name-status e2a9d80c
git diff --check HEAD -- docs/architecture/services-namespace-migration-map-2026-05-10.md docs/reviews/2026-05-10-svc-rename-001-codex2-review.md docs/reviews/2026-05-10-svc-rename-001-claude2-review.md
find services/control_plane services/control-plane/internal -maxdepth 3 -type f | sort
git log --oneline --name-status -n 20 -- services/control_plane services/control-plane/internal services/runtime-manager/internal_api_routes.py tests/run_internal_api_smoke.py
```

## Review Notes

- The migration map satisfies the task acceptance for a plan-only artifact: it inventories the ambiguous service directory pairs, classifies true collisions vs role-separated service/library splits, records import/path references, defines file move and import rewrite rules, covers docker-compose risks, and proposes a staged roll-forward plan.
- The previous blocking findings are resolved in the artifact. Pair E now documents the telemetry `feedback_adapter.py` bare `feedback.store` import and the required `services.trader_feedback` shim; Pair J correctly states there is no current `services/research/trl`; Pair A defines an executable `services.control_plane.internal.*` shim pattern rather than assuming `services/control-plane` is Python-importable.
- The task-scoped delivery commit `e2a9d80c` is an ancestor of `HEAD` and contains only the migration map plus two review records. No application code change is part of the reviewed SVC-RENAME-001 delivery.
- Current `HEAD` already contains later Pair A migration files in `services/control-plane/internal/` and `services/control_plane/internal/` from a subsequent commit. That does not block this plan-only review; it only means the map is now partly consumed by follow-up implementation work.

## Acceptance Check

| Criterion | Result |
|---|---|
| Inventory of duplicate-looking dirs with role classification | Pass |
| Grep/import summary for paths to be moved | Pass |
| Migration map with file destinations and import rewrites | Pass |
| Risk table covering compose refs and downstream consumers | Pass |
| Roll-forward plan designed to preserve tests via shims/phasing | Pass |
| No code changes in this task | Pass for task-scoped commit `e2a9d80c` |

## Disposition

Approved. Move `SVC-RENAME-001` to `review_approved` and return it to owner `Codex2` for closeout finalization.
