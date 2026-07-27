# L12-DEP-001 dispatcher evidence

Status: independent `Codex` review approved; accepted by `Codex2` for the
per-task PR into `dev`.

This packet records the reconciled code, contract, and deterministic behavioral
proof for the authenticated, tenant-partitioned Deployment saga dispatcher. It
does not activate a Compose manifest, authorize live capital, bypass an
ApprovalDecision, or move RuntimeBinding write ownership out of Runtime
Manager.

The single closeout manifest for this task is
[`closeout/evidence.json`](closeout/evidence.json), with its companion digest in
[`closeout/evidence.sha256`](closeout/evidence.sha256). It is the path recorded
as the archived `review_file` for `L12-DEP-001`.

## Replay-source layout

`scripts/loop_done_guardrail.py --evidence-root` discovers replay sources by
globbing for files named `evidence.json`, so this packet must expose exactly one
of them. It previously carried two: `closeout/evidence.json` and a top-level
`evidence.json`.

The top-level file was the pre-PR dispatcher receipt that `Codex` reviewed. It
was never a closeout manifest — its `overall_admission` was
`review_approved_for_task_pr` — so it failed every replay of `L12-DEP-001` and
was superseded in full by `closeout/evidence.json`.
`OPS-L12-DEP-EVIDENCE-REPLAY-001` removed it, together with its companion
`evidence.sha256`, so that the global evidence-root replay resolves `L12-DEP-001`
to one accepted source and zero failing ones.

Nothing was rewritten to achieve that: `closeout/evidence.json` and
`closeout/evidence.sha256` are byte-for-byte unchanged, and the deleted files
were removed rather than edited. The removed content stays auditable in merged
`dev` history at commit `22e9e319ef340b2822d7382ad49890ca09207110`, and the
closeout manifest still records its digest as
`integrity.source_artifact_sha256_by_epoch.reviewed_dispatcher_receipt`
(`6405c222a4ba405a11c9b1a09de9c2b006f831c94ad8495b4d0402b8a146f263`).

Because `closeout/evidence.json` is immutable, it still cites the removed paths
in `integrity.checksum_coverage`, `scope.evidence_changed_files`, and the
`record_log` sequence-1 `owner_evidence_ready` reference. Resolve those against
merged history, not the working tree.

## Delivered boundary

- Every Deployment and promotion API mutation requires a bearer-authenticated
  service/operator/governance role and an explicit tenant.
- DeploymentPlan, DeploymentSaga, projection, outbox, and inbox reads are
  tenant-partitioned; the authenticated actor replaces caller-authored actor
  fields.
- Pending outbox events are delivered through process-safe exclusive leases.
  Claim, acknowledgement, delivery release, expiry recovery, retry, DLQ, replay,
  and compensation state are observable.
- The dispatcher sends its service token and tenant on every Deployment API
  call and propagates tenant plus foundation correlation into RuntimeBinding
  request metadata.
- A clean idle poll restores degraded health and records the recovery.

## Crash and concurrency proof

`test_l12_dep_001_dispatcher.py` proves two consumers racing on one outbox event
receive exactly one active claim. It also advances a deterministic clock past
lease expiry, rejects the stale claim token, and allows one recovered owner.

The crash test creates and reads back one RuntimeBinding, simulates process loss
after the side effect and saga binding state but before outbox acknowledgement,
then reclaims the expired event. Replay follows the recorded binding id,
performs authoritative GET readback, and leaves the Runtime Manager `deploy`
call count at one.

The wider dispatcher suite proves retry/DLQ/replay, compensation sequencing,
kill-switch priority, response-loss handling, and terminal RuntimeBinding plus
DEP-003 projection readback before receipt.

## Validation

```text
/home/lupin/pantheon/.venv/bin/pytest -q \
  services/deployment/test_l12_dep_001_dispatcher.py \
  services/deployment/test_service.py \
  services/deployment/test_outbox_consumer_worker.py \
  services/promotion/test_service.py
98 passed, 1 warning in 20.91s

/home/lupin/pantheon/.venv/bin/pytest -q \
  services/deployment services/promotion
166 passed, 1 warning in 39.10s
```

The warning is the repository's existing Starlette `TestClient` deprecation
warning.

Independent reviewer `Codex` approved implementation commit
`d9bc76c2d9580d7da9130247f428bd7668d8ab57` after reproducing the 166-test
suite and the changed-module compilation, checksum, diff, and merge-tree
checks. During owner closeout, `Codex2` re-ran the complete suite with the same
`166 passed, 1 warning` result, plus changed-module `py_compile`, this packet's
checksum, and `git diff --check`.

## Composition boundary

`L12-MANIFEST-001` owns Compose/environment activation of
`PANTHEON_DEPLOYMENT_SERVICE_TOKEN`,
`PANTHEON_DEPLOYMENT_TENANT_ID`, and the service auth mode. Until that wiring is
accepted, the dispatcher fails closed rather than sending anonymous or
cross-tenant mutations.
