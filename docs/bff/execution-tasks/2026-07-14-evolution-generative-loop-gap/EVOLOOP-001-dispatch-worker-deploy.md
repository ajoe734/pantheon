# EVOLOOP-001 — Evolution Dispatch Worker Deployment

Status: implementation validated locally; hosted dev proof pending PR delivery

- Owner: Codex2
- Reviewer: Codex
- Branch: `task/EVOLOOP-001`
- PR target: `dev`
- Implementation anchor: `EVOLOOP-001: anchor dispatch worker activation`

## Scope

This task makes `evolution-dispatch-worker` a default root-compose service for
dev. The worker polls approved `EvolutionDecision` records and calls only the
evolution service's boundary and gated execute routes. It owns its interval,
timeout, actor, one-shot test control, and health-file settings.

This task does not change the daily sweep cadence, any supervisor cadence,
threshold or approval policy, the downstream research consumer, or runtime
bindings. An `executed` decision in this slice means the governed dispatch was
accepted with `execution_result.status=submitted`; it does not claim that a
research job or artifact has completed. Research completion belongs to
`EVOLOOP-004`, and target-plane terminal readback belongs to
`LOOP-PROD-EVO-001`.

## Implementation

- Added the unprofiled `evolution-dispatch-worker` root-compose service, using
  the existing evolution image and
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

The following checks passed on `task/EVOLOOP-001`:

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
- Full evolution service suite: `192 passed, 2 warnings in 81.52s`; both
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

No execute request or state mutation occurred. The temporary validation
container was stopped and removed after the check.

## Hosted Dev Proof

Pending PR-visible task-ref or merged-`dev` root deployment. The hosted proof
must record all of the following before closeout:

1. the exact deployed commit and deployment run;
2. `evolution-dispatch-worker` running and healthy in the root Compose project;
3. a unique decision created, reviewed, and approved only through APIs;
4. automatic transition to `executed` after a worker tick, without a direct
   operator execute request;
5. the dispatch metadata fields listed above; and
6. a second interval or worker restart with no duplicate execute step or
   changed execution reference.

Authentication and other secret values must not be copied into this record.

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
| Default dev Compose service with own interval and healthcheck | Rendered Compose contract, service-list check, image build | Passed locally; hosted pending |
| Approved decision auto-transitions to executed with metadata | Entrypoint integration test using real API routes and durable store | Passed locally; hosted pending |
| Restart does not double-dispatch | Store-reload test preserves one execute step and execution reference | Passed locally; hosted pending |
| Evolution API failure logs diagnostics and dispatches nothing | Boundary-failure test, main-loop outage test, built-image one-shot output | Passed |
| Existing cadences remain unchanged | Diff contains only the new worker cadence | Passed |

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
- Hosted dev deployment and automatic dispatch proof: pending.
