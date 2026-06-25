# V1 — Deployment integrity & drift detectability (direction E)

- Date: 2026-06-14
- Branch: task/verify-v1-deploy-drift

## Plan
Verify whether the dev deployment can be reliably checked against the repo. This
session repeatedly hit STALE deployed images (the lean-runtime worker image was 8
days behind binding-scoped-key commits; telemetry/operator-bff were stale) caught
only by hand. Determine the drift-detection capability and close the gap.

## Findings
1. **Images carry NO git-SHA / OCI revision label** (only compose metadata) ->
   there is no reliable way to tell which commit a running service was built from.
   This is the root cause of silent drift.
2. A date-granularity heuristic (image build date vs commits touching the service
   path) over-reports same-day rebuilds; with full build timestamps it is accurate.
   Current accurate drift census (after this session's telemetry/bff/worker rebuilds):
   only `broker` is drifted (1 commit after its 06-12 image); core loop services ok.
3. **paper-fleet-reconciler is NOT running** (workers unmanaged; gated behind compose
   profile `paper-fleet`) — the durability gap that let the 2026-06-12 DNS outage
   freeze 15 workers for ~2 days unnoticed. (escalated to a later round)

## Fix (this round, shipped via PR)
- `scripts/audit_deploy_drift.sh` — interim drift detector (image build timestamp vs
  service-path commits) + `--precise` mode that uses the new revision label; also flags
  the missing reconciler.
- Bake `org.opencontainers.image.revision=$GIT_SHA` into the loop-critical Dockerfiles
  (telemetry, control-plane/bff, runtime-manager [service + execution], lean_runtime,
  broker) via an `ARG GIT_SHA`; pass `GIT_SHA` as a compose build arg. Verified the
  mechanism: a build with `--build-arg GIT_SHA=<sha>` stamps the label (throwaway build).
  Once images are rebuilt with `GIT_SHA=$(git rev-parse HEAD)`, `--precise` gives exact
  drift (`git log <label_sha>..origin/dev -- <path>`).

## Follow-ups (later rounds)
- Roll the SHA-label pattern to all remaining service Dockerfiles.
- Wire the deploy build to pass `GIT_SHA=$(git rev-parse HEAD)`.
- Start/manage paper-fleet-reconciler (durability).
- Wire contract/service suites into the CI merge gate (rot prevention).
