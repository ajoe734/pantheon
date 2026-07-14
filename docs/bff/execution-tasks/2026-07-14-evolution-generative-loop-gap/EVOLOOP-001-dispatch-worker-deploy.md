# EVOLOOP-001 — Evolution Dispatch Worker Deployment

Status: reviewer remediation implemented; fresh exact-ref Compose proof pending

- Owner: Codex2
- Reviewer: Codex
- Branch: `task/EVOLOOP-001`
- PR target: `dev`
- Published implementation anchor: `2da815d2923901cc160835946e474232b54657b3`
  (`EVOLOOP-001: anchor dispatch worker activation`)
- Published proof head: `183cba011d6993029b3e828dc85f13dd166f207c`
- Reviewer-remediation anchor: `8c9308721f1799f820fb1f54a87c50c31a0dd82c`
  (`EVOLOOP-001: anchor research-only dispatch guard`)

## Scope

This task makes `evolution-dispatch-worker` a default root-compose service for
dev. The worker polls the evolution service's approved-decision endpoint, then
calls its boundary and gated execute routes only for the canonical research
action family. It owns its interval, timeout, actor, one-shot test control, and
health-file settings.

The unattended allowlist is `observe`, `revalidate`, `retrain`,
`require_more_data`, and `flag_for_review`. Governance, deployment, and runtime
actions are emitted as structured `skipped_unsupported` diagnostics and remain
`approved` for their authoritative owner. In particular, an active-live freeze
is never auto-consumed: proposal metadata is an evidence snapshot, not
dispatch-time Runtime Manager truth, and it does not say whether the approved
follow-through is `freeze_stage` or rollback.

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
- Restricted default-on dispatch to research actions. A supported boundary
  must be a complete object with `boundary_key=research_<action>`,
  `execution_plane=research`, and `followthrough=[]` before mutation is
  attempted.
- Removed caller-supplied runtime/freeze claims from the worker's execute
  request. The request now carries only its actor role and actor id; execute
  request model defaults are sufficient for a research action.
- Rejects empty, non-object, structurally incomplete, or semantically
  mismatched boundary and execute 2xx payloads. A successful execute response
  must match the decision/action, confirm `executed` and `submitted`, carry the
  research plane, exact `dispatch-<decision_id>` ref, and non-empty execution,
  cooldown, and observation timestamps before the worker counts it.
- Resets the health file to `status=starting,ticks=0` before the first network
  poll on every boot, so a retained writable-layer file cannot advertise a
  prior container's healthy state.
- Strengthened automated coverage for entrypoint-driven auto-execution,
  durable-store restart idempotence, dispatch metadata, boundary failure,
  malformed 2xx responses, active-live-freeze non-consumption, total API
  outage, boot health reset/freshness, and the Compose contract.
- Added `services/evolution/hosted_dispatch_probe.py`, a reproducible two-phase
  hosted probe with a sanitized request ledger. Its initial phase performs only
  create/review/approve mutations; its verify phase is read-only and checks the
  same exact ref and single execution step after a Compose restart.

## Dispatch Metadata Contract

The durable evidence for a successful research dispatch is:

- `decision_state=executed`;
- `execution_result.status=submitted`;
- `execution_result.plane` matching the boundary (the proof uses `research`);
- `execution_result.execution_ref_id=dispatch-<decision_id>`;
- non-null execution, cooldown, and observation timestamps; and
- exactly one `executed` review-chain step attributed to the worker actor.

The worker counts no non-research decision as dispatched. Its structured skip
record includes `decision_id`, `action_type`, `target_stage`, any reported
runtime binding id for diagnosis only, and the explicit-owner reason. The
decision retains `decision_state=approved`, a null `execution_result`, and no
`executed` review step.

The test reloads `decisions.json` into a new `EvolutionDecisionStore` before a
second poll. The second worker instance finds no approved decision, makes no
second execute request, and preserves the original execution reference and
single executed review step.

## Local Verification

The following checks passed on the reviewer-remediation worktree based on
anchor `8c9308721f1799f820fb1f54a87c50c31a0dd82c`:

```bash
python3 -m pytest \
  services/evolution/test_dispatch_worker.py \
  services/evolution/test_hosted_dispatch_probe.py \
  services/evolution/test_compose_activation.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider \
  services/evolution -q
docker compose config -q
docker compose config --services | rg -x 'evolution-dispatch-worker'
docker compose build evolution-dispatch-worker
git diff --check
```

Results:

- Focused worker, hosted-probe, and Compose contract:
  `43 passed in 10.45s`.
- Post-refresh full evolution service suite: `218 passed, 2 warnings in 44.77s`;
  both
  warnings are existing FastAPI `on_event` deprecations in the incidents
  service.
- Rendered default services include `evolution-dispatch-worker`; it has no
  profile and waits for a healthy `evolution` service.
- Image build completed as
  `evoloop-001-evolution-dispatch-worker:latest`, manifest list
  `sha256:253a647708a329f0063cfae285dddcb8a378357050dbbeaf8b58653b7de96d79`.
- Compose parsing and whitespace validation passed.

The successful entrypoint test creates, reviews, and approves a unique
low-risk retrain decision through the real FastAPI routes. It never calls the
execute route directly. A one-tick `dispatch_worker.main()` run performs the
boundary lookup and execute call, then the test reads the durable store and
asserts `executed` plus `dispatch-<decision_id>` metadata.

## Fail-closed Container Evidence

The remediated image was also run for one tick against an unreachable evolution API:

```bash
docker run --rm \
  -e EVOLUTION_API_URL=http://127.0.0.1:1 \
  -e EVOLUTION_DISPATCH_MAX_TICKS=1 \
  -e EVOLUTION_DISPATCH_TIMEOUT_SECONDS=0.2 \
  -e EVOLUTION_DISPATCH_HEALTH_FILE=/tmp/evolution-dispatch-health.json \
  evoloop-001-evolution-dispatch-worker:latest \
  python -m services.evolution.dispatch_worker
```

Minimal output at `2026-07-14T06:24:25Z`:

```json
{
  "tick": 1,
  "health": {
    "status": "degraded",
    "ticks": 1,
    "total_dispatched": 0,
    "total_errors": 1,
    "last_failure_reason": "<urlopen error [Errno 111] Connection refused>",
    "total_skipped": 0
  },
  "result": {
    "decisions_found": 0,
    "dispatched": 0,
    "dispatch_items": [],
    "skip_items": [],
    "skipped_unsupported": 0,
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

### Superseded evidence — not acceptance

Workflow run
[29306967263](https://github.com/ajoe734/pantheon/actions/runs/29306967263)
did deploy the earlier task implementation and supported a research auto-dispatch
probe. It is no longer acceptance evidence for two independent reasons:

1. it predates the research-only, malformed-payload, and boot-health reviewer
   remediation; and
2. later root deployments replaced the hosted checkout. Runs
   [29308875940](https://github.com/ajoe734/pantheon/actions/runs/29308875940)
   and
   [29309421576](https://github.com/ajoe734/pantheon/actions/runs/29309421576)
   each reported `pantheon-evolution-dispatch-worker-1` as an orphan because
   their deployed refs did not contain this task's Compose service.

Therefore the currently running old container must not be described as
Compose-owned or current. The earlier decision
`evoloop-001-probe-b2844ae249` is retained only as historical pre-remediation
evidence.

### Fresh acceptance gate

Pending after publication of the remediated task ref. The accepted probe must:

- dispatch `nonprod-deploy.yml` for `environment=dev`, `component=root`, and
  an exact task commit;
- prove the workflow-resolved SHA, remote checkout SHA, and container source
  match that exact commit;
- prove Docker Compose labels identify project `pantheon`, service
  `evolution-dispatch-worker`, and the active root Compose config rather than
  an orphan;
- create/review/approve one unique research decision without calling its
  execute route, then observe the worker's exact dispatch ref and one executed
  review step;
- create/review/approve one daily-sweep-shaped active-live freeze with a
  runtime binding snapshot and prove it remains `approved`, has no execution
  result, and receives no worker execute POST;
- restart only the Compose service, observe health reset/recovery and a fresh
  tick, then prove neither decision was double-dispatched; and
- capture the command sequence and secret-free normalized output here before
  requesting re-review.

The API portion is reproducible with the task-owned probe:

```bash
python3 -m services.evolution.hosted_dispatch_probe \
  --api-url http://127.0.0.1:18093 \
  --output /tmp/evoloop-001-hosted-initial.json \
  initial \
  --prefix evoloop-001-<unique-suffix> \
  --freeze-observation-seconds 65

docker compose -p pantheon -f docker-compose.yml \
  restart evolution-dispatch-worker

python3 -m services.evolution.hosted_dispatch_probe \
  --api-url http://127.0.0.1:18093 \
  --output /tmp/evoloop-001-hosted-restart.json \
  verify \
  --input /tmp/evoloop-001-hosted-initial.json
```

The Compose ownership/source checks run immediately before and after those
commands; their normalized output and the two probe JSON files are archived
with the final hosted evidence.

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
| Default dev Compose service with own interval and healthcheck | Rendered no-profile Compose contract passes locally; old hosted service is orphaned | Pending fresh hosted proof |
| Approved research decision auto-transitions to executed with metadata | Entrypoint integration test covers worker actor, submitted/research/exact ref/timestamps | Local passed; hosted pending |
| Active-live freeze is not silently consumed | Daily-sweep-shaped regression proves structured skip, no POST, still approved, no execution result/step | Local passed; hosted pending |
| Restart does not double-dispatch and resets health first | Store-reload and boot-health tests pass | Local passed; hosted pending |
| Evolution API failure logs diagnostics and dispatches nothing | Boundary-failure test, main-loop outage test, built-image one-shot output | Passed |
| Existing cadences remain unchanged | Compose diff leaves existing scheduler blocks and interval variables unchanged; worker uses its own namespaced interval | Passed |

## Residual Risks

- The JSON store supplies durable restart deduplication after a completed
  execute, but it is not an exactly-once outbox. A process crash inside the
  execute/store persistence window is outside this thin slice. Owner:
  Evolution service. Review by `LOOP-PROD-EVO-001`.
- This worker records a governed submitted dispatch; it does not confirm
  target-plane completion or create the real research work item by itself.
  Owner: research/evolution integration. Review by `EVOLOOP-004` and
  `LOOP-PROD-EVO-001`.
- Non-research decisions intentionally remain `approved`. The worker cannot
  select a freeze-stage versus rollback path from evidence metadata, and the
  current generic execute route does not persist or deliver its in-memory
  companion commands. Owner: Evolution/Governance/Runtime integration. This
  restriction must remain until an authoritative runtime read plus approved
  follow-through contract and downstream acceptance ref exist.
- Docker marks a degraded worker unhealthy but does not restart a still-running
  unhealthy process solely because of health status. The poll loop continues
  retrying on its own interval; process exits remain covered by
  `restart: unless-stopped`. Owner: Evolution service operations. Reassess at
  `EVOLOOP-009` closeout.

## Review And Delivery

- PR: [#3618](https://github.com/ajoe734/pantheon/pull/3618), open.
- Reviewer decision: pending Codex review.
- Merge commit: pending.
- Hosted dev deployment and automatic dispatch proof: pending fresh exact-ref
  Compose deployment; run `29306967263` is explicitly superseded.
