# Backend Dev Publish Plan - 2026-04-29

## Goal

Publish the current backend/runtime progress to the Pantheon dev environment
without treating the dirty dev VM checkout as the source of truth.

Target runtime:

- VM: `pantheon-dev-vm1`
- GCP project: `pantheon-493602`
- Zone: `asia-east1-b`
- Compose project: `pantheon`
- Public BFF: `https://pantheon-dev-bff.35.236.178.81.sslip.io`
- Frontend origin to preserve in CORS:
  `https://pantheon-ai-system-front-dev.lovable.app`

## Current State

Local workspace:

- Branch: `codex/2026-04-21-exec-sync`
- HEAD: `f8c176f8880529163d8006e1290b69796b8c7c2b`
- Subject:
  `SVC-SEARCH-AUTONOMOUS-INDEX-PIPELINE-SIDECAR-BFF-HANDOFF finalize BFF handoff packet`
- Local compose config validation: `docker compose config --quiet` passed.
- Branch is ahead of `origin/codex/2026-04-21-exec-sync` by 34 commits.
- There are substantial local dirty changes: runtime service code, compose
  wiring, tests, plus non-runtime orchestration/status/docs noise.

Dev VM checkout:

- Branch: `codex/2026-04-21-exec-sync`
- HEAD: `591656c9a6dee498753ddd93e7a669a26135a25d`
- Subject:
  `APP-003-RAYTUNE-DEFERRED-PREP-001 Finalize Ray Tune deferred prep lane`
- Dev VM and local histories are deeply diverged:
  `591656c...f8c176f` is `662` left-only and `721` right-only.
- Dev VM compose currently runs the older core stack:
  `operator-bff`, `runtime-manager`, `lineage-read`, governance, telemetry,
  incidents, postmortems, capital, etc.
- Dev VM compose does not currently include the newer local services:
  `consultation-svc`, `source-ingest`, `search-svc`, `training-session-svc`,
  `research-orchestrator-svc`, `reconciliation-drift-svc`, or
  `research-worker-gateway-svc`.
- Dev VM BFF container currently has the correct dev guardrails:
  `PANTHEON_ENV=dev`,
  `PANTHEON_LIVE_BROKER_ENABLED=false`,
  `PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-dev.lovable.app`.

Implication: do not `git pull`, `git reset`, or copy the full local dirty tree
directly over the VM checkout. Build and deploy from a clean publish branch or
clean worktree.

## Include In Backend Dev Publish

Committed local runtime line to carry forward:

- `5b2dd6c` `SVC-BASELINE lock single VM service contract`
- `aba0cd0` `SD-CONSULT-003 adopt consultation service in BFF and runtime`
- `87a8e23` `SVC-RUNTIME-CONTROL package runtime-control as deployable service`
- `ad7895d` `SVC-SURFACES rewire BFF read surfaces to services`
- `5a4ece7` `SVC-COMPOSE assemble single-VM smoke stack`
- `0e24f9e` `SVC-RUNTIME-HARDENING enforce inbound JWT/RBAC/MFA and deployment-plane approval authority`
- `038cb17` `SVC-SOURCE-INGEST-SERVICE add deployable source ingest service`
- `f9803f5` `SVC-SEARCH-SERVICE add deployable search service`
- `bb45011` `SVC-POLICY-LEARNING-BOUNDARY materialize service boundary`
- `e1f3c31` `SVC-HEALTH-OBSERVABILITY-UNIFICATION finalize approved observability work`
- `102ca2c` `SVC-SOURCE-INGEST-AUTONOMOUS-PIPELINE approve autonomous source ingest`
- `e39ff5f` `SVC-SEARCH-AUTONOMOUS-INDEX-PIPELINE finalize durable search pipeline`
- `f8c176f` `SVC-SEARCH-AUTONOMOUS-INDEX-PIPELINE-SIDECAR-BFF-HANDOFF finalize BFF handoff packet`

Dirty runtime files/directories to include:

- `docker-compose.yml`
- `docker-compose.control.yml`
- `scripts/smoke_honest_stack.py`
- `services/capital/main.py`
- `services/consultation/`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/http_smoke_test.py`
- `services/control-plane/bff/test_*`
- `services/control_plane/internal_api.py`
- `services/control_plane/test_internal_api_incident.py`
- `services/deployment/service.py`
- `services/evaluation/main.py`
- `services/evolution/main.py`
- `services/feedback/main.py`
- `services/foundation/__init__.py`
- `services/foundation/dead_letter.py`
- `services/foundation/health.py`
- `services/foundation/tests/test_health.py`
- `services/governance/main.py`
- `services/incidents/main.py`
- `services/knowledge/evidence/bundle_builder.py`
- `services/lineage-read/`
- `services/memory/main.py`
- `services/optimizer-svc/main.py`
- `services/policy-learning/main.py`
- `services/postmortems/main.py`
- `services/promotion/main.py`
- `services/reconciliation-drift/`
- `services/registry/main.py`
- `services/research/Dockerfile`
- `services/research/main.py`
- `services/research/store.py`
- `services/research/tests/`
- `services/research-worker-gateway/`
- `services/runtime-manager/main.py`
- `services/search/`
- `services/source_ingestion/`
- `services/telemetry/main.py`
- `services/training-session/`

Dev-specific guardrails to preserve or add to the publish branch:

- In dev BFF runtime, keep `PANTHEON_ENV=dev`.
- Keep `PANTHEON_LIVE_BROKER_ENABLED=false`.
- Keep BFF CORS limited to
  `https://pantheon-ai-system-front-dev.lovable.app`.
- Do not copy staging-live broker settings into dev.
- Keep Lovable dev pointed at
  `https://pantheon-dev-bff.35.236.178.81.sslip.io`.

## Exclude From This Publish

Do not include these in the backend dev runtime publish unless a separate
commit explicitly scopes them:

- `.orchestrator/`
- `ai-status.json`
- `ai-task-archive/`
- `current-work.md`
- `docs-site/`
- `docs/reviews/`
- `support/sidecars/`
- `tools/storage-state.json`
- Dev VM APP-003 deferred prep dirty work for FinRL/RLlib/Ray Tune/W&B.
- EP5/IBKR live-broker evidence and staging-live docs unless publishing the
  staging-live lane.
- Lovable prompt/status docs, except when needed as operator documentation.

## Publish Strategy

1. Snapshot the dev VM before touching runtime files.

   ```bash
   TS=$(date -u +%Y%m%dT%H%M%SZ)
   mkdir -p "/home/lupin/dev-prepublish-${TS}"
   cd /home/lupin/code/pantheon
   git status --short > "/home/lupin/dev-prepublish-${TS}/git-status.txt"
   git diff > "/home/lupin/dev-prepublish-${TS}/git-diff.patch"
   git ls-files --others --exclude-standard > "/home/lupin/dev-prepublish-${TS}/untracked.txt"
   docker compose ps > "/home/lupin/dev-prepublish-${TS}/compose-ps.txt"
   ```

2. Build a clean local publish branch.

   Recommended branch name:

   ```bash
   backend-dev-publish-20260429
   ```

   Stage only the include paths above. Avoid `git add .`.

3. Run local validation before pushing/deploying.

   ```bash
   docker compose config --quiet
   pytest \
     services/consultation \
     services/source_ingestion \
     services/search \
     services/training-session \
     services/research/tests \
     services/reconciliation-drift \
     services/research-worker-gateway \
     services/foundation/tests \
     services/control-plane/bff/test_read_store_service_clients.py \
     services/control-plane/bff/test_training_session_service_client.py
   ```

4. Push the publish branch.

   ```bash
   git push origin backend-dev-publish-20260429
   ```

5. On the dev VM, deploy from a clean worktree instead of the dirty runtime
   checkout.

   Suggested path:

   ```bash
   /home/lupin/code/pantheon-backend-dev-publish-20260429
   ```

6. Rebuild and start the dev stack from that clean checkout.

   ```bash
   PANTHEON_ENV=dev \
   PANTHEON_LIVE_BROKER_ENABLED=false \
   PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-dev.lovable.app \
   docker compose -p pantheon up -d --build
   ```

7. Smoke test service readiness on the dev VM.

   ```bash
   curl -fsS http://127.0.0.1:18001/health
   curl -fsS http://127.0.0.1:18001/readyz
   curl -fsS http://127.0.0.1:18081/readyz
   curl -fsS http://127.0.0.1:18094/readyz
   curl -fsS http://127.0.0.1:18096/readyz
   curl -fsS http://127.0.0.1:18097/readyz
   curl -fsS http://127.0.0.1:18098/readyz
   curl -fsS http://127.0.0.1:18099/readyz
   curl -fsS http://127.0.0.1:18101/readyz
   curl -fsS http://127.0.0.1:18102/readyz
   curl -fsS http://127.0.0.1:18103/readyz
   ```

8. Smoke test public BFF and Lovable CORS.

   ```bash
   curl -fsS https://pantheon-dev-bff.35.236.178.81.sslip.io/health
   curl -fsS -X OPTIONS https://pantheon-dev-bff.35.236.178.81.sslip.io/health \
     -H 'Origin: https://pantheon-ai-system-front-dev.lovable.app' \
     -H 'Access-Control-Request-Method: GET' \
     -i
   ```

## Rollback

If the publish breaks dev:

1. Do not reset the dirty original checkout.
2. Use the prepublish snapshot to identify the previous compose state.
3. Restart the original runtime checkout only if needed:

   ```bash
   cd /home/lupin/code/pantheon
   PANTHEON_ENV=dev \
   PANTHEON_LIVE_BROKER_ENABLED=false \
   PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-dev.lovable.app \
   docker compose -p pantheon up -d --build
   ```

## Open Risks

- The local branch and dev VM branch are deeply diverged, so a normal merge may
  pull in unrelated APP-003 or orchestration work.
- Local `docker-compose.yml` currently has newer runtime service wiring but must
  be checked against the dev VM's BFF env guardrails before final commit.
- New service ports must be allowed only as needed. Public browser access should
  continue to flow through the HTTPS BFF, not directly to every service.
- Dev-only live broker disablement is safety-critical. Any deployment command
  must explicitly pass `PANTHEON_LIVE_BROKER_ENABLED=false`.
