# AG-BE-RS-001 BFF and Frontend Handoff Follow-up 3

| Field | Value |
|---|---|
| Task ID | `AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-RS-001` - ResearchPlan facade/router |
| Parent owner / reviewer | `Claude` / `Claude2` |
| Prepared by | `Codex` |
| Reviewer | `Claude` |
| Date | 2026-06-21 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This is a support artifact only. It does not modify L1 canonical truth, OpenAPI,
JSON schemas, BFF runtime, research services, registry/governance code, or the
`execute-plans` frontend. It follows the earlier packets:

- `support/sidecars/AG-BE-RS-001/AG-BE-RS-001-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/AG-BE-RS-001/AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`

## Purpose

Follow-up 2 was written while parent PR #2087 was still open. This follow-up
records the post-merge handoff state:

- parent PR #2087 is merged into `dev`;
- parent task `AG-BE-RS-001` is archived `done`;
- the BFF plan-first facade is now a valid `dev` baseline for downstream work;
- AG-BE-RS-002 and AG-FE-RS-001 still need to respect the limits of the merged
  facade instead of treating thin queued runs as complete research results.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates work; support packets do not override canonical truth. |
| `.orchestrator/task-briefs/ag_be_rs_001_sidecar_bff_handoff_followup_3.md` | This sidecar is support-only: prepare BFF query gap, operator journey, and frontend handoff materials without canonical changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes need explicit scope and narrow commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo changes require task commit, PR, merge, and owner closeout before `done`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | Active sidecar is `in_progress`, owner `Codex`, reviewer `Claude`, helper parent `AG-BE-RS-001`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001` | Parent task is archived `done`; PR #2087 merged into `dev`; delivery HEAD is `794218a7`; merge target SHA is `8b32f4fe`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002` | Unified run/progress/result projection remains `todo` and depends on `AG-BE-RS-001`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001` | Frontend research cards remain `todo` and depend on `AG-BE-RS-002`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Previous follow-up is archived `done` and merged; it flagged PR #2087 review-time attention items. |
| `gh pr view 2087 --json ...` and `gh pr checks 2087` | PR #2087 is `MERGED`; visible Branch CI and Orchestrator Sync checks pass. |
| `git fetch origin dev` / `git merge --ff-only origin/dev` | This sidecar branch was fast-forwarded to `origin/dev` after PR #2087 merged. |
| `services/control-plane/bff/agora/research/router.py` | Merged dev now contains the research BFF route handlers and stage-routing policy. |
| `services/control-plane/bff/agora/research/store.py` | Merged dev now contains `MemoryResearchPlanStore`; only the in-memory backend is implemented. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 still lists the ten plan-first research plan/run routes. |
| `services/control-plane/specs/agora/v4/research_plan_execution.schema.json` | Plan lifecycle, stage types, routing backend enum, fallback policy enum, and no-order proof remain closed. |
| `services/control-plane/specs/agora/v4/research_run_projection.schema.json` | Run projection defines queued/running/result fields, progress, backend mode, refs, failures, data cutoff, and no-order proof. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/02_research_facade_run_projection.md` | Plan-first rule, approval gate, typed stages, fallback posture, run projection requirements, and no-order rules. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Post-merge Snapshot

| Surface | Current state after PR #2087 merge | Handoff meaning |
|---|---|---|
| Parent task state | `AG-BE-RS-001` is archived `done` at `2026-06-21T14:42:41Z`. | Downstream tasks may treat the parent as complete in L0 state. |
| Parent PR | #2087 merged into `dev` with merge commit `8b32f4fe518a4afc4ba991511df990fce0e01a5b`. | The BFF facade is now in the `dev` baseline. |
| Parent delivery HEAD | Task branch head `794218a7b70eba5380e12239f319a45518e8cda2` is an ancestor of `origin/dev`. | Parent closeout recorded a merged task branch. |
| Visible checks | Commit trailers, Runtime mirror guard, Smoke acceptance, and Forward to orchestrator passed. | Good CI health for handoff; this sidecar did not rerun parent smoke tests. |
| BFF route coverage | `research/router.py` now implements the ten v1.3 routes for plans and runs. | The largest BFF query gap from the first packet is closed. |
| Store backend | `research/store.py` provides a thread-safe in-memory store only. | Useful for dev/smoke facade; not durable research truth or a production projection store. |
| Review notes | Claude2 approved route/stage coverage, no live/canary and no-order gates, ETag, and Idempotency-Key. | Parent review accepted the core facade shape. |
| Review follow-ups | Parent archive notes mention committed tests and tenant filtering for future Postgres backend as follow-up. | Do not treat those as solved by this sidecar. |

## Downstream Consumption Boundary

### AG-BE-RS-002

`AG-BE-RS-002` can now build on the merged plan/run facade, but it should not
reimplement or fork the ten route names already delivered by `AG-BE-RS-001`.
Recommended boundary:

- keep `ResearchPlanExecution` create/list/detail/approve/cancel and thin run
  dispatch semantics aligned with `research/router.py`;
- add real `ResearchRunProjection` depth: progress, metrics, findings,
  warnings, blocking reasons, artifact refs, evidence refs, lineage refs,
  failure, data cutoff, backend version/activation state, and timestamps;
- preserve the no-order proof values already used by the facade;
- treat `MemoryResearchPlanStore` as a dev/smoke backend unless the task
  explicitly adds durable storage;
- add tenant-aware list behavior when a durable or shared store is introduced.

The merged dispatch route creates a queued run from the first pending/ready
stage. That is dispatch intent, not proof of real worker execution or research
completion. AG-BE-RS-002 remains the owner for completed run/result semantics.

### AG-FE-RS-001

`AG-FE-RS-001` should still wait for AG-BE-RS-002 before rendering full
progress/result cards. If it consumes the merged parent facade for plan cards
before AG-BE-RS-002 lands:

- call through the shared BFF client only;
- bind plan cards to schema-approved `ResearchPlanExecution` data;
- read the plan concurrency token from `meta.etag` unless a later BFF change
  adds an HTTP `ETag` header;
- send `Idempotency-Key` for write actions and `If-Match` for approve/cancel
  and dispatch;
- show queued runs as queue intent only;
- display backend mode and research-only/no-order labels;
- do not show promotion, canary, live, capital, or order controls from research
  responses;
- do not add local fixture fallback or direct research-service fetches.

## Remaining Risk Notes For Reviewers

These notes do not reopen `AG-BE-RS-001`; they are downstream integration
guardrails.

| Risk / gap | Current posture | Suggested owner |
|---|---|---|
| Thin run projection | Merged facade returns queued/cancelled run objects from memory, not produced research results. | `AG-BE-RS-002` |
| Durable storage | `AGORA_RESEARCH_PLAN_STORE_BACKEND` reserves future backend selection, but memory is the only implemented store. | Follow-up backend/storage task or `AG-BE-RS-002` if scoped |
| Tenant filtering | Parent review notes list tenant filtering for future Postgres backend as follow-up. | Storage follow-up |
| Full governance preconditions | Parent review approved no live/canary and no-order proof; deeper data/compute/budget gates remain design concerns for future expansion. | Governance/backend follow-up |
| Frontend result readiness | Result cards need metrics, findings, refs, and data cutoff from real projection. | `AG-BE-RS-002` then `AG-FE-RS-001` |

## Reviewer Handoff

Claude review should verify:

| Check | Expected result |
|---|---|
| Scope | Only this follow-up support artifact and normal task-owned status metadata are in scope. |
| Canonical truth | No canonical docs, schemas, OpenAPI, BFF runtime, research services, registry/governance, or frontend files changed by this sidecar. |
| Factual alignment | Parent `AG-BE-RS-001` is archived `done`; PR #2087 merged into `dev` at `8b32f4fe`; AG-BE-RS-002 and AG-FE-RS-001 remain `todo`. |
| Handoff usefulness | Packet clearly distinguishes merged plan facade readiness from remaining run/progress/result and frontend card work. |

Recommended reviewer approval command:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-BE-RS-001/AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md \
  REVIEW_NOTES_ZH="Support-only follow-up approved: it records that AG-BE-RS-001 PR #2087 merged into dev, parent task is archived done, and downstream AG-BE-RS-002 / AG-FE-RS-001 may consume the merged plan facade while preserving the thin-run, durable-store, no-order, and frontend gating boundaries." \
  ./scripts/ai-status.sh approve AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Support-only AG-BE-RS-001 post-merge BFF/frontend follow-up packet approved for downstream handoff."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Describe the factual correction, downstream boundary issue, or missing post-merge handoff detail needed before approval."
```

## Validation Run

Commands run from this sidecar worktree:

```bash
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-002
AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-RS-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
gh pr view 2087 --json number,title,state,isDraft,mergeable,mergeStateStatus,headRefName,baseRefName,headRefOid,mergeCommit,url,statusCheckRollup,reviewDecision,autoMergeRequest
gh pr checks 2087
git fetch origin dev
git merge --ff-only origin/dev
sed -n '1,220p' services/control-plane/bff/agora/research/router.py
sed -n '1,180p' services/control-plane/bff/agora/research/store.py
sed -n '380,565p' services/control-plane/openapi/agora_v1_3.openapi.yaml
sed -n '1,260p' services/control-plane/specs/agora/v4/research_plan_execution.schema.json
sed -n '1,320p' services/control-plane/specs/agora/v4/research_run_projection.schema.json
sed -n '1,180p' docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/02_research_facade_run_projection.md
```
