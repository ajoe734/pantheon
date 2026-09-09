# Dev exact-artifact primitive (not wired to deployment)

The new `scripts/dev_release_artifacts.py` is a library only. It does not create
a deployment lane or claim release compensation. Its three fixed targets are
`operator-bff`, `agora-interaction-worker`, and `loop-run-projector-scheduler`.
No workflow, existing deployment command, owner, issuer, auth or database code
is changed by this first slice.

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

Next slice, subject to Root review: capture and seal inside the guarded lease
before switch, authenticate transport across jobs, wire all three compensation
entrances to one restore path, remove source-only skip/success predicates,
preserve upgraded owner/issuer identities, and keep quarantine until complete
artifact plus public readback. FE long-lived rollback target pinning is also
needed if retention beyond its current/previous release window is required.

Tests use isolated synthetic Docker fixtures, not hosted images or lifecycle
receipts. No exact historical artifact can be recreated by rebuilding its
source SHA. A live rollback drill and candidate restore remain unperformed.
