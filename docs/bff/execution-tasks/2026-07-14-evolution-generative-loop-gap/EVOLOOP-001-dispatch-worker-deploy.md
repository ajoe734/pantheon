# EVOLOOP-001 — Evolution Dispatch Worker Deployment

Status: refreshed to current dev; final exact-ref Compose proof waiting on the
acknowledged PINT-010-R2 stable-deploy window

- Owner: Codex
- Reviewer: Claude
- Initial implementation and reviewer remediation: Codex2, reviewed by Codex
- Branch: `task/EVOLOOP-001`
- PR target: `dev`
- Published implementation anchor: `2da815d2923901cc160835946e474232b54657b3`
  (`EVOLOOP-001: anchor dispatch worker activation`)
- Initial proof head: `183cba011d6993029b3e828dc85f13dd166f207c`
- Reviewer-remediation anchor: `8c9308721f93ecfe98f9a49dd199a4b1feee5b94`
  (`EVOLOOP-001: anchor research-only dispatch guard`)
- Successful pre-refresh exact-ref proof: `47a009bffce305cfe1ed4a7f7360ec1b7e413d7e`
- Current refreshed candidate: `633d6f6fbeede181acfc9c6a0832245d384a858a`

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
- Added `services/evolution/hosted_compose_probe.py` and the explicit,
  default-off `run_evolution_dispatch_probe` manual deploy input. This lets the
  existing GitHub WIF identity—not a worker's expiring personal GCP token—run
  the exact-ref ownership/source/API/restart probe and upload its JSON artifact.
  Push-triggered and ordinary manual deployments remain unchanged.

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

The following checks passed after merging current `origin/dev` at
`27735f62f80d0a205a2478798d8266c58892053f` and anchoring the resulting model
compatibility at `633d6f6fbeede181acfc9c6a0832245d384a858a`:

```bash
python3 -m pytest \
  services/evolution/test_dispatch_worker.py \
  services/evolution/test_hosted_dispatch_probe.py \
  services/evolution/test_compose_activation.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider \
  services/evolution -q
docker compose config -q
docker compose config --services | rg -x 'evolution-dispatch-worker'
python3 -c "import pathlib,yaml; yaml.safe_load(pathlib.Path('.github/workflows/nonprod-deploy.yml').read_text())"
python3 -m py_compile \
  services/evolution/dispatch_worker.py \
  services/evolution/hosted_compose_probe.py \
  services/evolution/hosted_dispatch_probe.py
git diff --check
```

Results:

- Focused worker, hosted-probe, and Compose contract:
  `49 passed in 12.50s` (including exact Compose label/source and orphan
  rejection tests).
- Full evolution service suite: `239 passed, 2 warnings in 50.79s`; both
  warnings are existing FastAPI `on_event` deprecations in the incidents
  service.
- Rendered default services include `evolution-dispatch-worker`; it has no
  profile and waits for a healthy `evolution` service.
- Workflow YAML parsing, probe byte-compilation, Compose parsing, and whitespace
  validation passed.
- Before the dev refresh, the remediated image build completed as
  `evoloop-001-evolution-dispatch-worker:latest`, manifest list
  `sha256:253a647708a329f0063cfae285dddcb8a378357050dbbeaf8b58653b7de96d79`.
  The final exact-ref workflow rebuild is still required for the refreshed
  candidate.

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

At that stage, the surviving pre-remediation container could not be described
as Compose-owned or current. The earlier decision
`evoloop-001-probe-b2844ae249` is retained only as historical pre-remediation
evidence.

### Successful exact-ref proof before the final dev refresh

Run
[29313232772](https://github.com/ajoe734/pantheon/actions/runs/29313232772)
deployed exact SHA `c49edeb3d9a96b89d21a7c9345b0133860031e8c`, but its hosted-probe step failed
closed before mutation because the git-porcelain parser stripped the leading
dot from the first allowed runtime task-brief path. It uploaded no artifact.
Commit `47a009bffce305cfe1ed4a7f7360ec1b7e413d7e` fixed that parser and added the
regression test.

The superseding exact-ref
[run 29314870187](https://github.com/ajoe734/pantheon/actions/runs/29314870187)
completed successfully at `47a009bffce305cfe1ed4a7f7360ec1b7e413d7e`.
Artifact `8303995676`, named `evoloop-001-hosted-29314870187-1`, has archive
digest
`sha256:63a7b32a47e1f6cff9c18015e68fd756ce40e7222e4553bee4ef4fdf52cba890`;
the extracted normalized JSON is 24,135 bytes with digest
`sha256:9c2e8a022f6455aa321a5b093334fc4b9b278067cecc26c3d8a92c382a6a24d4`.
It records `assertion_failures=[]` and proves:

- requested ref, resolved SHA, checkout SHA, and BFF source SHA before and after
  the probe all matched `47a009bffce305cfe1ed4a7f7360ec1b7e413d7e`;
- Compose project/service/config labels and rendered config hash matched, and
  host/container `dispatch_worker.py` source hashes were identical;
- the probe made zero direct execute calls, while research decision
  `evoloop-001-29314870187-1-research` auto-transitioned to
  `executed/submitted/research` with exact dispatch ref and one worker step;
- active-live freeze `evoloop-001-29314870187-1-freeze-live` retained its
  runtime-binding snapshot and remained `approved` with no execution result or
  executed step for the full observation window; and
- restarting only the Compose worker produced fresh tick `1`, recovered to
  healthy, did not redispatch research, and continued to report the freeze skip.

This proves the remediated task runtime at `47a009bff`, but it is not the final
PR-head acceptance. `dev` subsequently changed root Compose and the Evolution
API models/main. Those changes were merged into this branch before candidate
`633d6f6fb`, so the combined runtime must receive one final exact-ref probe.
A later BFF-only deployment also changed the currently served BFF identity; it
does not invalidate the captured artifact, but it prevents describing the
present mixed deployment as the exact-ref proof state.

### Fresh acceptance gate

Pending for the refreshed candidate after the PINT-010-R2 stable-deploy hold is
explicitly released. The accepted probe must:

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

The governed exact-ref invocation is:

```bash
gh workflow run nonprod-deploy.yml \
  --ref task/EVOLOOP-001 \
  -f environment=dev \
  -f component=root \
  -f ref=<exact-task-sha> \
  -f allow_dirty=false \
  -f allow_example_env=false \
  -f run_evolution_dispatch_probe=true
```

The probe input defaults to `false` and is accepted only for a manual
`dev/root` run. The workflow executes the probe from
`/home/lupin/pantheon-ci-deploy/dev-root` after deployment and uploads a
30-day task artifact through `actions/upload-artifact`.

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
| Default dev Compose service with own interval and healthcheck | Rendered no-profile Compose contract passes locally; run 29314870187 proved exact Compose ownership at 47a | Final refreshed-head proof pending |
| Approved research decision auto-transitions to executed with metadata | Entrypoint integration test plus run 29314870187 exact dispatch evidence | Passed at 47a; final refreshed-head proof pending |
| Active-live freeze is not silently consumed | Local daily-sweep-shaped regression plus run 29314870187 no-execute observation | Passed at 47a; final refreshed-head proof pending |
| Restart does not double-dispatch and resets health first | Store-reload/boot-health tests plus run 29314870187 fresh tick and zero redispatch | Passed at 47a; final refreshed-head proof pending |
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
- Reviewer decision: pending Claude review after final hosted evidence.
- Merge commit: pending.
- Hosted dev deployment and automatic dispatch proof: run `29314870187` passed
  at pre-refresh SHA `47a009bff`; final refreshed candidate proof is waiting on
  the acknowledged execute-plans PR #328 stable-deploy hold.
