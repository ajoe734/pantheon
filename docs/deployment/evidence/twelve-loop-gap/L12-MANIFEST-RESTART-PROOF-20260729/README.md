# L12 manifest isolated restart proof

Task `L12-MANIFEST-RESTART-PROOF-20260729`, owner `Codex2`, reviewer
`Claude2`. This packet closes the narrow daemon-side restart gap left by
`L12-MANIFEST-001` without crashing any container in the shared `pantheon`
Compose project.

## Result

Pass at `2026-07-29T02:50:30Z` on Docker Engine `29.1.3`.

- The isolated worker used the same image digest as the shared
  `policy-learning-shadow-eval-scheduler`:
  `sha256:95a8fd94d9b15e5e4d389cb41f96afe7015af8c697253b1743c415a779006f2c`.
- The rendered fixture carried the production manifest's actual worker
  command, `restart: unless-stopped`, and `stop_grace_period: 30s`.
- The worker ran in project `l12-manifest-restart-proof-20260729` on an
  internal-only network with no host port binding and no volume.
- After twelve stable seconds, the runner validated the worker's host PID and
  full Docker cgroup identity, then sent `SIGKILL` to that PID. It did not call
  `docker stop`, `docker kill`, or Compose stop.
- Docker restarted the same container ID automatically. Its host PID changed
  from `4186562` to `4188784`, `RestartCount` changed from `0` to `1`, and the
  worker emitted a second `startup_recovery` record.
- No `docker start` or `docker compose up` command ran after the signal.
- The shared worker's bounded inspect snapshot was byte-equivalent before and
  after: same container ID, PID, start/finish timestamps, state, image,
  networks, and `RestartCount=0`.
- Cleanup left zero containers and zero networks under the isolated project.

The normalized run is in `proof-run.json`; `evidence.json` maps it to each
task acceptance criterion.

## Why the signal comes from the host PID namespace

The prior parent packet correctly rejected `docker kill` as a restart-policy
probe because Docker records that API call as explicit operator intent. It
suggested `docker exec <container> kill -9 1` instead. A preliminary isolated
run showed that this second command also does not produce the intended crash:
Linux protects the PID-namespace init process from signals sent by another
process in the same namespace, so the worker remained at `RestartCount=0`.

The accepted runner therefore signals the already validated container PID 1
from the host PID namespace. From Docker's perspective this is an unsolicited
process exit, not a container stop request. The operator causes the failure,
but the daemon alone performs the recovery; the incremented `RestartCount`,
unchanged container ID, changed PID, and absence of a post-signal start/up
command distinguish that recovery from an operator restart.

## Isolation and safety

`run-proof.sh` fails closed when:

- the requested Compose project is named `pantheon`;
- the isolated project already contains any container;
- the worker is not running with `RestartCount=0`, `unless-stopped`, and the
  expected 30-second stop timeout;
- the selected host PID is not bound to the full isolated container ID in its
  Docker cgroup;
- `RestartCount` does not increment within 30 seconds;
- the restarted PID does not change; or
- cleanup leaves a container or network carrying the isolated project label.

The API stub returns a fail-closed error for every product POST, so the real
worker code exercises startup and recovery without performing a product write.
Neither service exposes a host port or mounts a volume.

## Reproduce

From the Pantheon repository root on the dev VM:

```bash
bash -n \
  docs/deployment/evidence/twelve-loop-gap/L12-MANIFEST-RESTART-PROOF-20260729/run-proof.sh

docker compose \
  --project-name l12-manifest-restart-proof-20260729 \
  --file docs/deployment/evidence/twelve-loop-gap/L12-MANIFEST-RESTART-PROOF-20260729/fixture.compose.yml \
  config --quiet

docs/deployment/evidence/twelve-loop-gap/L12-MANIFEST-RESTART-PROOF-20260729/run-proof.sh
```

The runner needs passwordless `sudo -n kill` only for the one host PID that it
first binds to the isolated container's full cgroup identity.

## Parent integration

The `L12-MANIFEST-001` owner can integrate this packet without copying raw
values:

1. cite this task's merged `evidence.json` and `proof-run.json`;
2. expire or close residual risk
   `auto_restart_policy_not_proven_end_to_end`;
3. update AC2's scope note to distinguish the isolated daemon trigger proof
   from the existing shared-stack runtime readback; and
4. preserve the statement that the shared live worker itself was not crashed.

This child task does not edit the parent evidence because `Claude2` owns
`L12-MANIFEST-001` and its independent verdict remains with that task's
reviewer.
