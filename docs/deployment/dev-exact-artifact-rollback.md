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
   Require HTTPS source/health, anonymous/invalid-token denial, and server-bound
   authenticated viewer/tenant/session readback. Release/quarantine decisions
   use this complete evidence, never the source SHA alone.

The eight identity fields are `candidate_id`, `run_id`, `attempt`,
`controller_sha`, `candidate_backend_sha`, `candidate_frontend_sha`,
`previous_backend_sha`, and `previous_frontend_sha`. Controller Python bytes
are matched against the exact committed blobs before installation. The prior
Compose checkout is a new retained worktree; an owner-mounted source checkout
is never switched for rollback.

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
