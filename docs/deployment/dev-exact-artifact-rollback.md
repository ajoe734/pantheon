# Dev exact-artifact rollback design and acceptance boundary

`scripts/dev_release_artifacts.py` is the low-level byte-preserving library.
The driver and workflow integration described below do not constitute hosted
acceptance until an actual deployment and rollback readback complete. Targets are
`operator-bff`, `agora-interaction-worker`, and `loop-run-projector-scheduler`.
Rollback does not restart upgraded business owners, the principal issuer, or
databases. It does not undo migrations, issue credentials, or reopen a terminal
paper provisioning saga. The FE controller separately owns the FE symlink CAS.

`capture_images` resolves one healthy Compose container per service, records
each actual image ID and observed optional OCI/registry metadata, saves exact
IDs to private archives, hashes the bytes and rechecks running IDs. Archives
are outside Docker's image-prune domain. There is no automatic deletion.
Missing metadata stays missing; an image config ID is not a registry digest.

The caller must seal the returned component manifest into the authenticated
outer release admission and supply its separately trusted byte digest to
`validate_images`/`restore_images`. The outer admission must bind dev VM/project,
run/attempt, release candidate, source pair, baseline FE evidence and compatible
Compose configuration. The primitive does not authenticate source admission.

`restore_images` requires dev, a lease-check callback and byte-verified Compose
files. It validates all archives before loading any, reloads missing images
only from those archives, then uses exact image overrides and
`--no-build --pull never --no-deps` for the three fixed services. It does not
prepare principals or invoke the generic BFF deploy. The callback must be
backed by the existing pinned lease watchdog; a callback alone is not a new
authority. Readback returns image evidence only, not whole-release success.

`capture_frontend`/`verify_frontend` are read-only. They compare the qualified
managed symlink target, canonical dist digest and deployment.json byte digest
separately. The canonical dist algorithm follows FE asset-manifest v1, excluding
deployment.json; cross-repo parity can be checked by setting
`PANTHEON_FE_RELEASE_CANDIDATE_HELPER` to the real separate frontend
`scripts/release-candidate.mjs`. FE source admission, secret scanning, atomic CAS
switching and browser probes remain frontend/controller responsibilities.

## System analysis: source equality cannot prove rollback

An old source commit rebuilt today can produce different image bytes. A
successful `/bff/version` response with the prior SHA is therefore insufficient.
Likewise, a current GitHub tip, a passed build, and a historical browser fixture
do not prove which FE symlink or BFF image is serving requests.

| Identity | Meaning | Independent verification |
| --- | --- | --- |
| Source SHA | Exact admitted repository commit | Protected dev and release admission |
| Docker image ID | Actual image configuration/content identity | Private Docker inspect before/after |
| Registry digest | Registry manifest digest, when one exists | Observed metadata; absence stays absent |
| Archive digest | SHA-256 of retained Docker save bytes | Rehash before load; not an image ID |
| FE dist digest | Canonical FE asset manifest, excluding deployment.json | Independent FE helper parity and hosted files |
| FE manifest digest | Exact deployment.json bytes | Hosted file and HTTPS response |
| Actions artifact digest | ZIP bytes transported between jobs | Externally supplied upload-action digest |

## System design and execution sequence

The existing immutable lease controller remains the authority. Its commit and
both source checksums are unchanged; the new transport does not acquire,
transfer or renew a lease. All operations are restricted to the current dev VM
from `vm-dev-staging-prod-management-plan.md`, never a retired project or host.

1. Admit the current exact FE/BFF source pair and observed prior pair. Inside
   the pinned guard, capture the actual three prior images, qualified FE target,
   dist/manifest hashes, prior Compose blob and two allowlisted nonsecret BFF
   config values. Retain images in the VM's private artifact store, outside
   Docker pruning. Upload only the sealed metadata before candidate mutation.
   Capture admits only a live BFF equal to the admitted previous backend with no
   observed-live baseline so a drifted live BFF fails closed before mutation;
   capture, seal-candidate and verify may record an image's literal dev_login_enabled false.
2. Build the candidate without changing running containers. The stable VM
   driver verifies the candidate Compose service/build ownership and each
   built image's exact source revision. It publishes `candidate-images.json`
   and an immutable three-service image-ID override.
3. The remote shell emits one typed candidate receipt. The runner validates the
   eight-field identity, baseline seal, current guard identity, source/image
   map and override bytes. Only after exclusive creation, file fsync and parent
   directory fsync does it send the matching digest ACK. No ACK means no up.
   Candidate start/recreate commands use the admitted image-ID override.
4. Save/upload the candidate receipt even when subsequent deployment fails.
   A successful FE gate/switch requires both artifact uploads, candidate receipt
   validation, BFF deployment and exact public source smoke. Cross-job download
   verifies the exact Actions artifact ID, run/attempt name, expiry, authenticated
   metadata digest and actual ZIP digest before accepting fixed regular files.
5. All three compensation entrances use the stable artifact driver: the VM's
   internal failure path, same-run workflow compensation, and cross-repository
   FE rejection compensation. Same-run local evidence and Actions-download
   evidence have distinct validated provenance; an upload failure must not
   erase a valid runner-local rollback receipt. No source-only success or
   image-rebuild fallback is permitted.
6. Before loading/replacing anything, verify the already-restored prior FE
   target/bytes and require every current scoped container image to be its
   admitted prior or candidate image. Recheck the container snapshot immediately
   before mutating Docker requests. A failed candidate's typed unavailable
   HTTP/connection observation can permit recovery; TLS failures, malformed
   successful responses and known source conflicts cannot. Docker does not
   provide atomic conditional Compose up, so the outer guard remains necessary.
7. Verify exact prior image IDs, exact FE target/dist/manifest, the two nonsecret
   config fields and unchanged protected-owner identities during restore.
   Restore always requires the server-bound viewer readback.
8. Return to the same admitted final sealed candidate bytes (`restore-final` or
   `restore --target final`). Rollback recovery or drill closeout must
   authenticate the retained `candidate-bundle.json` and its immutable archives
   in the private artifact root before switching. The live FE symlink must
   already point to the admitted candidate frontend target and pass dist/manifest
   checks. Strict runtime auth posture, CAS check over admitted prior/candidate
   images, and unchanged protected-owner identities are required before any
   recreate. Docker recreate uses exact image overrides with
   `--no-build --pull never --no-deps`, without rebuilding, pulling, or
   resealing. Readback verifies the exact candidate image IDs, candidate backend
   version, and authenticated viewer readback.
   Require HTTPS source/health, anonymous/invalid-token denial, and server-bound
   authenticated viewer/tenant/session readback. Release/quarantine decisions
   use this complete evidence, never the source SHA alone.

### Supported invocations for final verify and same-final return

Final verification and same-final return (`restore-final`) reuse the existing guarded remote transport and driver without ambient PWD assumptions or rebuilding:

1. **Final verify and candidate seal creation**:
   ```bash
   bash scripts/deploy_nonprod_vm.sh \
     --environment dev \
     --component bff \
     --sha "${CANDIDATE_BACKEND_SHA}" \
     --project-id "${GCP_DEPLOY_PROJECT_ID}" \
     --artifact-verify \
     --artifact-target final \
     --artifact-readback-out "${RELEASE_EVIDENCE_DIR}/candidate/candidate-frontend.json"
   ```
   This passes `--target final` to the remote artifact driver, selects the explicit candidate Compose configuration via `resolve_dev_candidate_compose`, verifies the FE distribution and deployment manifest against their sealed digests, and records the validated candidate frontend seal to `candidate/candidate-frontend.json`.

2. **Same-final return (`restore-final`)**:
   ```bash
   bash scripts/deploy_nonprod_vm.sh \
     --environment dev \
     --component bff \
     --sha "${CANDIDATE_BACKEND_SHA}" \
     --project-id "${GCP_DEPLOY_PROJECT_ID}" \
     --artifact-restore-final \
     --artifact-readback-out "${RELEASE_EVIDENCE_DIR}/candidate-readback.json"
   ```
   Or via the release compensation helper:
   ```bash
   PANTHEON_DEV_ARTIFACT_TARGET=final \
   PANTHEON_DEV_ARTIFACT_OPERATION=restore-final \
     bash scripts/compensate_cross_repo_release.sh
   ```
   This binds `DEPLOY_SHA` and `lease.expectedBackendSha` to the candidate backend SHA, verifies that `candidate-frontend.json` and candidate image receipts exist and match their admitted digests, and performs an exact Compose recreate using `--no-build --pull never --no-deps`.

### FE Owner restore-sealed and Helper Reuse

1. **FE Owner Delegation**:
   When restoring artifacts (`restore` or `restore-final`), `deploy_nonprod_vm.sh` invokes the frontend owner script (`scripts/deploy-dev-vm.sh`) with:
   - `PANTHEON_DEPLOY_ACTION=restore-sealed`
   - `PANTHEON_DEPLOY_PROFILE=operator-live`
   - `PANTHEON_DEPLOY_LEASE_DELEGATED=true`
   - `PANTHEON_DEPLOY_LEASE_OWNER=pantheon-dev-deploy`
   - `PANTHEON_DEPLOY_SEALED_FROM_JSON` and `PANTHEON_DEPLOY_SEALED_TO_JSON` (carrying the reversed seals)
   This restores the hosted FE symlink via the authoritative frontend deploy owner under the active delegated lease before the BFF containers are started. If the live symlink already matches the target release, the switch is safely identified as redundant and skipped without error.

2. **Deduplicated Release Verification & Opt-in Roundtrip Entry**:
   The workflow step `final_artifact_verify` reuses `scripts/compensate_cross_repo_release.sh` with `PANTHEON_DEV_ARTIFACT_OPERATION=verify` and `PANTHEON_DEV_ARTIFACT_TARGET=final`. This deduplicates the lease lifecycle (acquire, heartbeat, verify, release) and error handling, ensuring identical lease ownership semantics between compensation rollback and candidate admission verification.
   Additionally, an optional workflow dispatch input `run_exact_artifact_roundtrip` (boolean, default false) executes a same-job baseline restore followed by a same-final return using unchanged original seals under the active lease. When enabled, actual roundtrip leg outcomes feed directly into release retirement and compensation enforcement.

3. **Credential Preflight Isolation**:
   `deploy_nonprod_vm.sh` enforces full actor credential checks (`VIEWER`, `APPROVER`, `RISK_OWNER`, `OPERATOR_A`, `OPERATOR_B`) and human-provisioned OpenClaw tokens for normal VM deployment routines. During `--artifact-verify`, deploy-time runtime credential preflights are bypassed because verification probes already-deployed containers without recreating services. Conversely, during `--artifact-restore` and `--artifact-restore-final`, full runtime credential checks are retained because restoration recreates container services via docker-compose; missing credentials fail closed before any FE or container mutation is attempted.

4. **FE Controller Version Binding**:
   `compensate_cross_repo_release.sh` reads the run ID retained in `release-controller-accepted.json` and resolves that run's immutable `head_sha` through the existing `GitHubClient.get_run`; that is the admitted FE controller commit (not the FE candidate SHA or the BE controller SHA). It is exported as `PANTHEON_DEV_FE_CONTROLLER_SHA` and forwarded to the VM. FE `restore-sealed` exits before the FE controller's own expected-HEAD and tracked-clean checks, so `deploy_nonprod_vm.sh` applies the same semantics before executing the script: the script must be the checkout's `scripts/deploy-dev-vm.sh`, the checkout HEAD must equal the admitted commit, and the script plus `scripts/atomic-symlink-cas.py` and `scripts/atomic-release-manifest.py` must be tracked and unmodified. `restore-final` fails closed when no admitted identity is supplied. No controller service, lock, or copy of FE code is added.

5. **Candidate Compose Context and Retirement**:
   The final verify and both roundtrip legs receive `PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPOSE_FILE` as `<DEV_DEPLOY_WORKTREE_ROOT>/dev-<root|bff>/docker-compose.yml` (the layout of `prepare_deploy_worktree`; `component=bff` selects `dev-bff`) plus `PANTHEON_DEV_ARTIFACT_CANDIDATE_COMPONENT`. The direct deploy remote resolver keeps its existing `$HOME/pantheon-ci-deploy/managed-deploy-worktrees` default, but compensation and the workflow's local prepare do not: final compensation requires an explicitly configured `DEV_DEPLOY_WORKTREE_ROOT` containing the candidate Compose for the actual component and the sealed digest, and a missing or unconfigured path is rejected (fail closed) rather than falling back to the VM `$HOME`. The receipt compose digest is still verified; there is no alias or directory search. Release-case retirement is `accepted` only for a successful frontend release, final verify and (when opted in) both roundtrip legs; otherwise it runs only after a successful compensation, as `compensated`. A failed roundtrip leg with failed compensation never retires.

6. **Source-Level CLI Integration Test Coverage (AC5)**:
   `test_full_cli_restore_final_executes_original_bff_chain` runs `deploy_nonprod_vm.sh --artifact-restore-final` from a hostile working directory through the original guarded sender, original bootstrap and remote watchdog (real pulses, guard FD and exit status), the deploy payload, the ORIGINAL driver and library bytes taken from the admitted controller commit, and the external `dev_artifact_compensation_evidence.py readback` validator, which consumes the driver-generated readback. Baseline manifest, candidate receipt and final FE seal are produced by the original driver in-process beforehand. Synthetic boundaries (not hosted evidence): a PATH `ssh` runs the remote command locally and substitutes the fixed `/home/...` artifact-store constants in the transported payload; a PATH `python3` shim patches only the driver/validator path and hostname constants and swaps Docker/HTTP for the driver test models; the FE deploy command is an explicit mock that records its env and switches the live link (the real FE controller/CAS is covered by the independent FE tests and the parent hosted roundtrip). Negative cases reject a tampered final FE seal after the driver readback and an unadmitted FE controller commit before any FE or driver action. Hosted environment execution and parent business data acceptance remain separate boundaries.


The eight identity fields are `candidate_id`, `run_id`, `attempt`,
`controller_sha`, `candidate_backend_sha`, `candidate_frontend_sha`,
`previous_backend_sha`, and `previous_frontend_sha`. Controller Python bytes
are matched against the exact committed blobs before installation. The prior
Compose checkout is a new retained worktree; an owner-mounted source checkout
is never switched for rollback.

## Whole-stack compensation (baseline restore)

The sealed three-image bundle (`pantheon.dev-bff-image-bundle.v1`: operator-bff,
agora-interaction-worker, loop-run-projector-scheduler, with archives) is
unchanged. Compensating only those three left the other ~50 containers on the
rejected candidate while `deployment.json` still showed the old pair accepted.

* **Baseline.** The outer manifest is now `pantheon.dev-release-artifact-baseline.v2`,
  which adds exactly one field, `stack_baseline` (`pantheon.dev-stack-image-baseline.v1`):
  for every running Compose service (and every one-shot that exited 0, e.g.
  `minio-init`, `source-ingest-controller-migrate`) the image id, whether it has a
  healthcheck, and the recorded `PANTHEON_TENANT_ID` / `GIT_SHA` from its
  `Config.Env` (only those two names are read; the filter runs in Docker's
  formatter). v1 manifests are still accepted for `verify` and the roundtrip but
  cannot be used for whole-stack scope.
* **No new archives.** Other services add no tar (hosted: 54 images, 7.8 GB
  with shared layers, disk 111/193 GB, release-artifacts 3.1 GB; see
  DEV-RELEASE-ARTIFACT-RETENTION-20261103). Capture tags each recorded image
  `pantheon-protect/<service>:<id12>` (idempotent; the same tag the deploy prune
  step writes) and fails by service name if an image is not inspectable.
* **Prune safety.** `deploy_nonprod_vm.sh` tags every compose container image
  with `pantheon-protect/*` before builder/dangling prune; stale protect tags are
  removed only for images no container uses, i.e. on a later deploy. The opt-in
  `PANTHEON_DEV_DOCKER_PRUNE=true` branch now excludes the Compose project
  (`--filter label!=com.docker.compose.project=pantheon`) from container/system
  prune, so exited one-shot containers keep their images in use. All pruning
  precedes candidate mutation.
* **Scope.** `--scope auto|images|stack` (`PANTHEON_DEV_ARTIFACT_SCOPE`). `auto` is
  whole-stack for a baseline `restore` of a v2 baseline: the deploy job compensation
  (`rollback_dev_bff_on_failure` and the `deploy_compensation` step) and
  `compensate_cross_repo_release.sh`. `restore-final`, `verify`, and the roundtrip
  baseline restore (workflow sets `images`) keep the three-image scope.
* **Restore** extends `restore_images` (no second path). It first checks every
  recorded image is held and names missing services before any mutation; then runs
  the unchanged three-service `up --force-recreate --wait --wait-timeout 120`; stops
  candidate-only services (`docker stop`); starts a stopped service already on its
  recorded image; recreates only services whose image differs, via the same
  compose override (`image`, `pull_policy: never`) plus the recorded
  `PANTHEON_TENANT_ID`/`GIT_SHA` so Compose defaults cannot reset them. Services
  already on their recorded image are not recreated. The three sealed services are
  always recreated (their configuration is restored from the seal). One-shot
  containers are recreated without `--wait` and pass when `exited 0`.
* **Readback and budget.** Every recorded service must run the recorded image id
  (wrong image, missing container or a still-running candidate-only service fail
  immediately); services with a healthcheck must be `healthy` and one-shots
  `exited 0`, polled every 5 s for at most 900 s (`STACK_READBACK_SECONDS`), then
  the environment of recreated services is compared with the baseline. The
  compensation deadline default is 1500 s inside the 60 minute step.
* **Evidence.** The readback gains `stack` (`scope`, per service
  `baseline_image_id` / `observed_image_id`, `candidate_only_stopped`), validated
  against the sealed baseline. On failure the driver prints
  `PANTHEON_ARTIFACT_STACK_MIXED_V1 {"kind","services"}` (validated service names
  only); the deploy shell turns it into an `::error::` line and a job-summary
  section: "the stack is mixed" for mismatches or "compensation NOT proven" for
  missing images. The run fails; database migrations are not rolled back.

## Compensation drill

**Run:** dispatch `nonprod-deploy` for dev/root with `compensation_drill=true`,
strict auth, a requested pair different from the served pair, and none of
`isolated_provider_qualification`, `hosted_all12_chain`,
`run_exact_artifact_roundtrip` or `bootstrap_empty_host`; any of those (or a
non strict profile) is rejected before mutation, and a requested pair equal to
the served pair is refused before the lease because restoring identical images
proves nothing. Record the result as a drill against item 4 of
HOSTED-DEPLOY-WINDOW-ATTRIBUTION-ACCEPTANCE-20261106.

**Flow:** candidate deploy and seal, then the step "Intentional compensation
drill failure after candidate stack is up" fails (no VM access), then the one
existing compensation step and per-service readback run unchanged. The job
summary and the `pantheon-dev-compensation-drill-<run>-<attempt>` marker state
that the run was a drill; no second compensation path exists.

**Proves:** after a failure that follows a changed stack, compensation restores
the whole recorded stack to the hosted baseline. **Does not prove:** recovery
from an unplanned failure point, or database migration rollback. The run name
is `Dev compensation drill ...`, so `auto_deploy_dev_pair` never counts it as a
failed attempt of the pair.

## Remote cancellation and limitations

The runner and SSH sender stay in the pinned guard's process group. A remote
watchdog owns the child group and separate inherited pulse/receipt-ACK pipes.
It pauses the entire remote group after three seconds of runner silence and
terminates it on EOF, invalid frames, cancellation or deadline. Fresh pulses
precede continuation. Docker CLI children must not create a new session that
would escape that group. This is a bounded loss-detection window, not instant
propagation. A Docker daemon request already accepted cannot be recalled;
after cancellation no subsequent guarded Docker request is permitted.

No candidate receipt means verification of an unchanged baseline only, never
an observed-current-image admission or invented restore authority. A damaged
or missing retained archive fails closed. There is no automatic archive prune.
FE retention beyond the immediate current/previous release window requires a
separate retention pin; this change does not claim long-lived FE retention.

## Acceptance evidence still required

Tests use isolated synthetic Docker fixtures, not hosted images or lifecycle
receipts. No exact historical artifact can be recreated by rebuilding its
source SHA. Unit tests cover byte integrity, cancellation, durable-before-ACK,
partial prior/candidate image CAS, unavailable-candidate recovery, strict auth
and failure rejection. They do not prove a live rollback drill or candidate
restore. Acceptance must include the actual run ID, release ID, artifact IDs
and digests, admitted source pair, served identities, prior/candidate images,
complete restore readback and safe return to the accepted candidate.

The broader product goal additionally requires one fresh stimulus through
Loops 1–12 with trigger/terminal/next-consumer/worker/reload evidence, Loop 5
provenance, executable Loop 8 RuntimeBinding, Loop 9 paper lifecycle, and the
authenticated Management, Agora and Management-AI journeys. None is inferred
from supervisor health, library tests, source proof or this deployment design.
