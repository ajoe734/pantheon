# Development tooling cleanup — 2026-09-11

This is development-tooling and delivery-infrastructure maintenance, not a
product deployment, a Loops 1–12 acceptance, or permission to resume stopped work.
The initial custom MFA retirement is PR #5788 (`81cdf18a42aa081b9ccb09b63a95ce989e8a1d42`).

## Implemented simplification

| Area | Change | Minimum responsibility retained |
| --- | --- | --- |
| Tool authorization | Delete the custom MFA issuer, execution grants and grant-only checks | Canonical task scope, owner/lease, explicit Human/Ops holds |
| Worker processes | One executable-position parser shared by supervisor/watchdog | Do not mistake provider prompt text for a worker or kill unrelated processes |
| Startup/status | Put post-binding sandbox/startup errors through existing terminal handling; reuse durable writer | Failed receipt survives reload |
| Sandbox | Delete the growing task-state sibling filename registry | Dedicated writable task-state data directory; outer config, keys, source and other worktrees stay read-only |
| Runtime update | Ordinary promotion never moves journal/runtime/approval data; migration requires `--migrate-storage` | One drain/admission fence, exact source, health readback and qualified prior-runtime rollback |
| Local packet | Share wire canonicalization and label CLI source `local_development_tooling` | Preserve historical signed bytes, provenance, replay identity and holds |
| Retired VM paths | Remove obsolete migration/cutover executables and target/account fallbacks | Explicit current environment inputs and pinned SSH transport |
| Diagnostics | Report safe stage/class/source location and actual process exit | No raw secret-bearing stderr or fabricated success |
| Acceptance | Remove swallowed required failures and repeated verifier invocations | Existing checks determine exit status |

The task-state directory is **data only**. Never place configuration, signing
keys, interpreters or executable source inside it. The outer runtime mount stays
read-only; a symlink from data to outer configuration does not confer write access.

Ordinary runtime updates refuse data path changes before stopping the incumbent.
The existing storage migration/rollback implementation remains available only
for an explicitly requested migration. It is not invoked on ordinary replacement,
including ordinary replacement failure and rollback.

## Existing delivery mechanisms retained after tracing consumers

There is already one retained-image restore implementation:
`dev_release_artifact_driver.py` calls `dev_release_artifacts.restore_images`.
VM failure and external workflow compensation enter that same driver; a successful
restore does not turn the failed release green. Do not introduce another restore
service or replace exact artifacts with a rebuild from a source SHA.

The environment lease and remote watchdog have different jobs: serializing shared
environment changes versus propagating cancellation/disconnection to VM children.
Both are retained. They are not human-MFA authorities.

Direct operator-authorized tooling delivery uses normal validation and protected
dev checks, with a mechanical `delivery:tooling` PR only when required by GitHub.
No reviewer attestation, operator-accept proof, or review-proof tag is manufactured.
Product auth changes are separate from this tooling exception.

Packet verification keeps the original wire object when computing canonical bytes;
it must not reparse old payloads into a model that adds new defaults. The historical
`operator_authorization_required` field remains inert provenance, not a live grant.

## Delivery and activation evidence

Source commits, required GitHub checks, merge identity and live runtime identity
are separate evidence. See the final cleanup handoff for exact merged and activated
versions. Source changes alone do not prove activation, and tooling activation does
not prove product availability. No task journal, queue, credentials or deployment
artifacts are deleted by this cleanup; removed tracked source is recoverable from Git.
