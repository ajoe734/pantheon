# EVOLOOP-001 — Evolution Dispatch Worker Deployment

Status: hosted dev dispatch/restart proof accepted; PR and reviewer closeout pending

- Owner: Codex2
- Reviewer: Codex
- Branch: `task/EVOLOOP-001`
- PR target: `dev`
- Published implementation anchor: `2da815d2923901cc160835946e474232b54657b3`
  (`EVOLOOP-001: anchor dispatch worker activation`)
- Published proof head: `183cba011d6993029b3e828dc85f13dd166f207c`

## Scope

This task makes `evolution-dispatch-worker` a default root-compose service for
dev. The worker polls the evolution service's approved-decision endpoint, then
calls its boundary and gated execute routes for each candidate. It owns its
interval, timeout, actor, one-shot test control, and health-file settings.

This task does not change the daily sweep cadence, any supervisor cadence,
threshold or approval policy, the downstream research consumer, or runtime
bindings. An `executed` decision in this slice means the governed dispatch was
accepted with `execution_result.status=submitted`; it does not claim that a
research job or artifact has completed. Research completion belongs to
`EVOLOOP-004`, and target-plane terminal readback belongs to
`LOOP-PROD-EVO-001`.

## Implementation

- Added the unprofiled `evolution-dispatch-worker` root-compose service, using
  the existing evolution Dockerfile/runtime and
  `python -m services.evolution.dispatch_worker` command.
- Preserved the existing worker's immediate first tick and added no change to
  the daily sweep scheduler or its interval.
- Added dedicated environment controls:
  `EVOLUTION_DISPATCH_ACTOR_ID`, `EVOLUTION_DISPATCH_INTERVAL_SECONDS`,
  `EVOLUTION_DISPATCH_MAX_TICKS`, `EVOLUTION_DISPATCH_TIMEOUT_SECONDS`, and
  `EVOLUTION_DISPATCH_HEALTH_FILE`.
- Added a container health command that requires a recent successful poll.
  Missing, malformed, degraded, starting, or stale health state fails closed.
- Tightened the worker so a boundary-read failure records a diagnostic and
  skips `/execute`; a partially reachable API can no longer bypass the
  boundary preflight.
- Strengthened automated coverage for entrypoint-driven auto-execution,
  durable-store restart idempotence, dispatch metadata, boundary failure,
  total API outage, health freshness, and the Compose contract.

## Dispatch Metadata Contract

The durable evidence for a successful dispatch is:

- `decision_state=executed`;
- `execution_result.status=submitted`;
- `execution_result.plane` matching the boundary (the proof uses `research`);
- `execution_result.execution_ref_id=dispatch-<decision_id>`;
- non-null execution, cooldown, and observation timestamps; and
- exactly one `executed` review-chain step attributed to the worker actor.

The test reloads `decisions.json` into a new `EvolutionDecisionStore` before a
second poll. The second worker instance finds no approved decision, makes no
second execute request, and preserves the original execution reference and
single executed review step.

## Local Verification

The following checks passed on `task/EVOLOOP-001` at tested head
`183cba011d6993029b3e828dc85f13dd166f207c`:

```bash
python3 -m pytest \
  services/evolution/test_dispatch_worker.py \
  services/evolution/test_compose_activation.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider \
  services/evolution -q
docker compose config -q
docker compose config --services | rg -x 'evolution-dispatch-worker'
docker compose build evolution-dispatch-worker
git diff --check
```

Results:

- Focused worker and Compose contract: `22 passed in 10.80s`.
- Post-rebase full evolution service suite: `192 passed, 2 warnings in 48.86s`;
  both
  warnings are existing FastAPI `on_event` deprecations in the incidents
  service.
- Rendered default services include `evolution-dispatch-worker`; it has no
  profile and waits for a healthy `evolution` service.
- Image build completed as
  `evoloop-001-evolution-dispatch-worker:latest`.
- Compose parsing and whitespace validation passed.

The successful entrypoint test creates, reviews, and approves a unique
low-risk retrain decision through the real FastAPI routes. It never calls the
execute route directly. A one-tick `dispatch_worker.main()` run performs the
boundary lookup and execute call, then the test reads the durable store and
asserts `executed` plus `dispatch-<decision_id>` metadata.

## Fail-closed Container Evidence

The built image was also run for one tick against an unreachable evolution API:

```bash
docker run --rm \
  -e EVOLUTION_API_URL=http://127.0.0.1:1 \
  -e EVOLUTION_DISPATCH_MAX_TICKS=1 \
  -e EVOLUTION_DISPATCH_TIMEOUT_SECONDS=0.2 \
  -e EVOLUTION_DISPATCH_HEALTH_FILE=/tmp/evolution-dispatch-health.json \
  evoloop-001-evolution-dispatch-worker:latest \
  python -m services.evolution.dispatch_worker
```

Minimal output at `2026-07-14T04:13:49Z`:

```json
{
  "tick": 1,
  "health": {
    "status": "degraded",
    "ticks": 1,
    "total_dispatched": 0,
    "total_errors": 1,
    "last_failure_reason": "<urlopen error [Errno 111] Connection refused>"
  },
  "result": {
    "decisions_found": 0,
    "dispatched": 0,
    "dispatch_items": [],
    "errors": ["<urlopen error [Errno 111] Connection refused>"]
  }
}
```

The one-shot tick reported zero successful dispatches. Separately, the
instrumented total-outage test asserts that the worker's POST helper is never
called, proving that an unreachable API does not cause an execute attempt or
state mutation. The temporary validation container was stopped and removed
after the check.

## Hosted Dev Proof

Accepted at `2026-07-14T05:25:09Z` while official workflow run
[29306967263](https://github.com/ajoe734/pantheon/actions/runs/29306967263)
held the dev-root deployment lease. The run completed successfully at
`2026-07-14T05:25:54Z`; its requested ref, workflow-resolved SHA, prepared
`/home/lupin/pantheon-ci-deploy/dev-root` checkout, and BFF source SHA were all
`183cba011d6993029b3e828dc85f13dd166f207c`. The root deployment step completed
at `2026-07-14T05:24:46Z`, and its OpenClaw, public BFF, and Agora restart
persistence smokes also passed.

The `pantheon` root Compose project showed
`pantheon-evolution-dispatch-worker-1` running with Docker health `healthy`.
A task-scoped probe then issued exactly these mutating requests:

```text
POST /api/evolution/proposals
POST /api/evolution/proposals/evoloop-001-probe-b2844ae249/review
POST /api/evolution/proposals/evoloop-001-probe-b2844ae249/approve
```

The probe instrumented every request it made and asserted that all later API
requests were GETs. It never invoked `POST .../execute`; the hosted worker made
the gated execute request on its next poll. Minimal secret-free proof:

```json
{
  "approved_at": "2026-07-14T05:24:30Z",
  "observed_at": "2026-07-14T05:25:09Z",
  "decision_id": "evoloop-001-probe-b2844ae249",
  "decision_state": "executed",
  "execution_result": {
    "status": "submitted",
    "plane": "research",
    "execution_ref_id": "dispatch-evoloop-001-probe-b2844ae249",
    "executed_at": "2026-07-14T05:24:48Z"
  },
  "cooldown_ends_at": "2026-07-17T05:24:48Z",
  "observation_window_ends_at": "2026-07-21T05:24:48Z",
  "executed_step_count": 1,
  "executed_step_actor": "evolution-dispatch-worker",
  "dispatch_log_count": 1,
  "health_before_restart": "healthy",
  "health_after_restart": "healthy",
  "restart_tick_seen": true,
  "direct_execute_calls_by_probe": 0
}
```

The probe restarted only `evolution-dispatch-worker`. Docker retained container
ID `07ab1e53ac16609674f7a7f9ea431d74b4a96a05207936fdbeea72b21326fa4d`, as
expected for a Compose restart, and the worker emitted a fresh `tick: 1` with
healthy state. Post-restart readback still contained one executed review step
and the same execution reference, proving no duplicate dispatch.

Earlier run
[29305788872](https://github.com/ajoe734/pantheon/actions/runs/29305788872)
also completed for the same task ref, but a later task-ref deployment replaced
the hosted checkout before this probe could run. It is recorded only as
superseded deployment history; run `29306967263` is the accepted hosted truth.

## Intentional Disable And Re-enable

Temporarily stop only this worker without changing any cadence:

```bash
docker compose -p pantheon -f docker-compose.yml stop evolution-dispatch-worker
```

For a broad Compose reconciliation that must keep it disabled, explicitly
scale it to zero for that invocation:

```bash
docker compose -p pantheon -f docker-compose.yml up -d \
  --scale evolution-dispatch-worker=0
```

An ordinary later root `docker compose up -d` intentionally restores this
default-on service. Re-enable it immediately with:

```bash
docker compose -p pantheon -f docker-compose.yml up -d evolution-dispatch-worker
```

Do not use an interval of zero as a disable mechanism; the worker rejects
values below one second.

## Acceptance Matrix

| Criterion | Evidence | State |
|---|---|---|
| Default dev Compose service with own interval and healthcheck | Rendered no-profile Compose contract, exact-ref run `29306967263`, hosted service running/healthy | Passed |
| Approved decision auto-transitions to executed with metadata | Entrypoint integration test plus hosted decision `evoloop-001-probe-b2844ae249`, worker actor, submitted/research/ref/timestamps | Passed |
| Restart does not double-dispatch | Store-reload test plus hosted worker restart, fresh tick, one executed step, unchanged ref | Passed |
| Evolution API failure logs diagnostics and dispatches nothing | Boundary-failure test, main-loop outage test, built-image one-shot output | Passed |
| Existing cadences remain unchanged | Compose diff leaves existing scheduler blocks and interval variables unchanged; worker uses its own namespaced interval | Passed |

## Residual Risks

- The JSON store supplies durable restart deduplication after a completed
  execute, but it is not an exactly-once outbox. A process crash inside the
  execute/store persistence window is outside this thin slice. Owner:
  Evolution service. Review by `LOOP-PROD-EVO-001`.
- This worker records a governed submitted dispatch; it does not confirm
  target-plane completion. Owner: research/evolution integration. Review by
  `EVOLOOP-004` and `LOOP-PROD-EVO-001`.
- Docker marks a degraded worker unhealthy but does not restart a still-running
  unhealthy process solely because of health status. The poll loop continues
  retrying on its own interval; process exits remain covered by
  `restart: unless-stopped`. Owner: Evolution service operations. Reassess at
  `EVOLOOP-009` closeout.

## Review And Delivery

- PR: pending.
- Reviewer decision: pending Codex review.
- Merge commit: pending.
- Hosted dev deployment and automatic dispatch proof: accepted via successful
  run `29306967263` and decision `evoloop-001-probe-b2844ae249`.
