# Review — SVC-HEALTH-OBSERVABILITY-UNIFICATION-SIDECAR-BFF-HANDOFF

**Reviewer**: Claude
**Owner**: Codex
**Parent task**: SVC-HEALTH-OBSERVABILITY-UNIFICATION
**Helper kind**: bff_handoff_packet
**Artifact**: `support/sidecars/SVC-HEALTH-OBSERVABILITY-UNIFICATION/SVC-HEALTH-OBSERVABILITY-UNIFICATION-SIDECAR-BFF-HANDOFF.md`
**Date**: 2026-04-28
**Disposition**: approved

## Reviewer focus checks

1. **Support-only, no canonical mutation** — confirmed. `git status --short
   support/sidecars/SVC-HEALTH-OBSERVABILITY-UNIFICATION/` shows only the new
   untracked directory containing this single packet markdown. The packet
   header, §1, and §2 explicitly disclaim canonical/L1, runtime, registry,
   governance, BFF implementation, frontend code, and compose changes.

2. **Current implementation snapshot is accurate** — verified each row of §2:
   - BFF exposes `GET /health` only — confirmed at
     `services/control-plane/bff/main.py:5875`. Greps for
     `/healthz|/livez|/readyz` on that file return no matches, so the
     "does not expose `/healthz`, `/livez`, or `/readyz` today" claim holds.
   - `GET /api/v1/operator/health-status` exists at
     `services/control-plane/bff/main.py:7505`.
   - PKT-011 contract groups (`runtime`, `telemetry`, `incident`,
     `governance`, `kill_switch`) match `docs/bff/PKT-011-health-status-board.md`.
   - Compose healthchecks are genuinely mixed: `/__health__` (runtime-manager,
     telemetry, incidents, postmortems, lineage-read, evaluation, feedback,
     memory, registry, optimizer-svc, promotion, evolution-trigger,
     reconciliation), `/health` (governance, capital, deployment, evolution,
     persona, router, consultation-svc, source-ingest, search-svc,
     training-session-svc, policy-learning, research-orchestrator,
     reconciliation-drift, operator-bff), `/healthz` (NATS), and
     infrastructure-native (`/minio/health/live`).
   - `scripts/smoke_honest_stack.py` mirrors the same split for app services
     (lines 114–138). Minor drafting note: the smoke script does not itself
     hit NATS `/healthz` — that route is only used by compose. This is not
     load-bearing for the parent owner.
   - Newly activated service `/health` routes confirmed:
     `services/consultation/main.py:92`, `services/source_ingestion/main.py:152`,
     `services/search/main.py:194`, `services/training-session/main.py:111`.
   - Pending parent dependencies match `ai-status.json`: SVC-RESEARCH-WORKER-
     GATEWAY and SVC-RECONCILIATION-DRIFT-SERVICE remain `todo`,
     SVC-RESEARCH-ORCHESTRATOR-SERVICE is `review_approved`.

3. **PKT-011 domain health vs raw service readiness split** — confirmed.
   §2's parent-owner implication, §3's gap matrix, and §4's operator journey
   all keep the operator-facing PKT-011 board distinct from a future
   service-readiness inventory. The packet correctly forbids treating
   service liveness as proof that runtime/telemetry/governance/kill_switch
   domain truth is healthy (§4.2 row 5).

4. **BFF remains the only browser-facing health owner** — confirmed in §4.2
   and §5. Frontend constraints in §5 explicitly forbid browser polling of
   service `/health|/healthz|/livez|/readyz` and forbid client-side
   synthesis of service health from unrelated routes. PKT-011 / PKT-013 /
   degraded-control-guidance materials are referenced as existing contract
   docs without proposing a new Lovable task.

5. **Parent acceptance coverage** — §3, §6, and §7 map every parent-task
   acceptance bullet (readiness alias trio, legacy compatibility, compose
   healthchecks, smoke standardization, baseline observability with
   dependency status, healthy/degraded/dependency-failure tests). §6 lists
   the right BFF compatibility verification commands and includes
   `docker compose config --quiet` and PKT-011 contract test as the
   parent-side verification shape.

## Notes for the parent owner (Gemini on SVC-HEALTH-OBSERVABILITY-UNIFICATION)

- Absorption is optional. The most directly reusable parts are the §3 BFF
  query gap matrix (especially the additive vs new-route decision for
  service readiness exposure) and §7's suggested implementation order
  (alias services first, then update compose/smoke, then choose BFF
  exposure model, then add tests).
- §5's hard rule that absent / pending / unconfigured services must render
  as degraded/unavailable rather than healthy-empty is the correct
  operator-safety stance and should carry into the parent slice.
- The smoke-script `/healthz` attribution noted above is the only minor
  drafting inaccuracy and does not change any reviewer disposition.

## Outcome

Approve as support material. Returning the task to the owner (Codex) for
formal closeout.
