# SVC-POSTGRES-PRODUCTION-OWNERSHIP-WAVE2 Sidecar Review Packet

Task: `SVC-POSTGRES-PRODUCTION-OWNERSHIP-WAVE2-SIDECAR-REVIEW`
Parent task: `SVC-POSTGRES-PRODUCTION-OWNERSHIP-WAVE2`
Prepared by: Codex2
Reviewer: Codex
Date: 2026-04-30
Helper kind: `review_packet`
Mutates canonical truth: no
Closeout status: Codex reviewed and approved this sidecar packet on 2026-04-30.

## Scope

This packet is support material only. It does not approve the parent task,
change L1 canonical truth, or edit runtime, registry, governance, or service
implementation. The parent owner and parent reviewer decide whether this packet
is useful for the parent review.

This sidecar intentionally stayed inside task-scoped context, the parent task
handoff, and commit-scoped evidence. It did not inspect `current-work.md` or the
full `ai-activity-log.jsonl`.

## Parent Review Snapshot

Parent task status at packet preparation:

| Field | Value |
|---|---|
| Parent task | `SVC-POSTGRES-PRODUCTION-OWNERSHIP-WAVE2` |
| Status | `review` |
| Owner | `Codex2` |
| Reviewer | `Claude` |
| Phase | Production Readiness / Data Ownership |
| Parent implementation commit | `1eaf3813b98475feb1886d6d0346e86083c0abc6` |

Parent handoff summary says the parent is ready for review after:

- adding env-gated Postgres owner stores for `research-orchestrator`,
  `research-worker-gateway`, and `policy-learning`
- wiring root compose and production control env examples for wave 2 backend
  selection while retaining dev JSON/JSONL rollback
- documenting the inventory and read contract in the database ownership policy
- preserving completed consultation, source/search, and training-session pilot
  paths as part of the wider wave 2 ownership inventory

## Evidence Summary

| Acceptance area | Evidence observed |
|---|---|
| Remaining JSON/JSONL stores are inventoried | `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` adds a wave 2 inventory covering consultation, source-ingest, search, training-session, policy-learning, research-orchestrator, and research-worker-gateway stores. |
| New Postgres owner stores exist for wave 2 gaps | Commit `1eaf381` adds `PostgresResearchEventStore`, `PostgresWorkerEventStore`, and `PostgresPolicyLearningJobStore` with schema/table bootstrap and JSONB payload persistence. |
| Dev fallback remains default | Root `docker-compose.yml` keeps defaults as `jsonl` for event stores and `json` for policy-learning; store factory tests clear env and assert local file-backed stores remain selected. |
| Staging/prod can select Postgres ownership | `env/prod-control.env.example` sets `CONSULTATION_STORE_BACKEND`, `SOURCE_INGEST_EVIDENCE_BACKEND`, `SEARCH_INDEX_STORE_BACKEND`, `SEARCH_EVIDENCE_BACKEND`, `TRAINING_SESSION_EVENT_STORE_BACKEND`, `POLICY_LEARNING_STORE_BACKEND`, `RESEARCH_ORCHESTRATOR_EVENT_STORE_BACKEND`, and `RESEARCH_WORKER_GATEWAY_EVENT_STORE_BACKEND` to `postgres`. |
| DSN and role split remain configurable | New factories use service-specific `*_DSN` values first and fall back to `DATABASE_URL`, allowing later stricter role separation without changing service code. |
| Read-only sharing boundary is explicit | The wave 2 inventory records owner API or read-role-only access for non-owners, and keeps search evidence reads tied to source-ingest ownership. |
| Rollback path remains env-based | Compose keeps JSON/JSONL volumes and fallback paths; production env comments say local rollback is setting backends to `json` or `jsonl`. |
| Focused tests cover new store selectors | New tests cover JSON/JSONL defaults, env-gated Postgres selection, bootstrap DDL, idempotent event insert behavior, and no fallback file creation in Postgres mode. |

## Parent Commit Files

Commit `1eaf381` changes:

| Area | Files |
|---|---|
| Research orchestrator event ownership | `services/research/store.py`, `services/research/main.py`, `services/research/tests/test_research_postgres_event_store.py`, `services/research/Dockerfile` |
| Research worker gateway event ownership | `services/research-worker-gateway/store.py`, `services/research-worker-gateway/main.py`, `services/research-worker-gateway/tests/test_research_worker_gateway_postgres_event_store.py`, `services/research-worker-gateway/Dockerfile` |
| Policy-learning job ownership | `services/policy-learning/store.py`, `services/policy-learning/main.py`, `services/policy-learning/tests/test_policy_learning_postgres_store.py`, `services/policy-learning/requirements.txt` |
| Compose/env ownership selection | `docker-compose.yml`, `env/prod-control.env.example` |
| Ownership inventory | `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` |

## Verification Run For This Packet

Commands run from `/home/edna/code/pantheon` on 2026-04-30:

```bash
git show --stat --oneline --decorate --no-renames 1eaf381
rg -n 'RESEARCH_ORCHESTRATOR_EVENT_STORE_BACKEND|RESEARCH_WORKER_GATEWAY_EVENT_STORE_BACKEND|POLICY_LEARNING_STORE_BACKEND|TRAINING_SESSION_EVENT_STORE_BACKEND|SOURCE_INGEST_EVIDENCE_BACKEND|SEARCH_INDEX_STORE_BACKEND|CONSULTATION_STORE_BACKEND' docker-compose.yml env/prod-control.env.example services/research services/research-worker-gateway services/policy-learning services/training-session services/source_ingestion services/search services/consultation
python3 -m pytest services/research/tests/test_research_postgres_event_store.py services/research-worker-gateway/tests/test_research_worker_gateway_postgres_event_store.py services/policy-learning/tests/test_policy_learning_postgres_store.py
git diff --check -- support/sidecars/SVC-POSTGRES-PRODUCTION-OWNERSHIP-WAVE2 ai-status.json current-work.md
```

Results:

- `git show` confirmed parent implementation commit `1eaf381` and its task
  metadata.
- `rg` confirmed backend env selectors across service code, compose, and
  production env example.
- Focused tests passed: 6 passed.
- `git diff --check` passed for this sidecar scope and generated state files.

## Reviewer Checklist

Recommended Codex review focus for this sidecar:

| Check | Expected answer |
|---|---|
| Did this sidecar avoid canonical/runtime implementation edits? | Yes. It only adds this support packet plus status handoff updates. |
| Does the packet clearly distinguish parent review from sidecar review? | Yes. Parent review remains with `Claude`; sidecar review is assigned to `Codex`. |
| Is evidence tied to concrete parent files and commit metadata? | Yes. Evidence is tied to commit `1eaf381`, changed file groups, env selectors, and tests. |
| Are non-owner read boundaries described as support evidence, not new policy? | Yes. The packet references the parent commit's inventory and does not alter L1 truth. |
| Are verification commands bounded and reproducible? | Yes. The packet uses commit inspection, env selector search, focused pytest, and diff whitespace checks. |

## Non-Claims

This packet does not claim:

| Non-claim | Correct disposition |
|---|---|
| The parent task was approved by this packet. | Parent approval and closeout remain owned by the parent task's assigned reviewer/owner flow. |
| A live staging/prod database migration has been applied. | The evidence covers code, env, compose, and focused tests, not an actual deployed database. |
| Control-plane wave 3 stores are migrated. | Those belong to `SVC-CONTROL-PLANE-POSTGRES-OWNERSHIP-WAVE3`. |
| Service-specific Postgres roles or grants are fully materialized. | DSN override hooks exist; concrete role/grant matrix remains a later database administration slice. |
| This sidecar changed canonical policy. | It did not edit canonical files; it summarizes the parent commit's existing policy/doc evidence. |

## Handoff

To: `Codex`
From: `Codex2`
Requested review outcome: approve this sidecar if it is an accurate,
support-only review packet for `SVC-POSTGRES-PRODUCTION-OWNERSHIP-WAVE2`.

Recommended reviewer disposition:

1. Approve if this packet accurately summarizes the parent review evidence and
   stays support-only.
2. Request changes only for wording, missing evidence, or mismatches in this
   support packet.
3. Do not treat this packet as approval of the parent task; parent review stays
   with Claude unless reassigned in `ai-status.json`.

## Owner Closeout Addendum

Codex approved this sidecar on 2026-04-30 as an accurate support-only review
packet. The approval confirms that this artifact stayed bounded to review
evidence and handoff material, with no L1 canonical truth or runtime,
registry, governance implementation changes.

Closeout verification re-ran the focused wave 2 Postgres store tests and the
support artifact whitespace check from `/home/edna/code/pantheon`.
