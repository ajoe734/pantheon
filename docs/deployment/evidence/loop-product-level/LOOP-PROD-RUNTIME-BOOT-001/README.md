# LOOP-PROD-RUNTIME-BOOT-001 evidence packet

Status: premerge scaffold; not admissible completion evidence

This directory separates evidence that may be prepared before merge from
evidence that can exist only after the exact bootstrap commit is merged,
reviewed, installed in the live supervisor environment, and exercised by the
strict zero-write dispatcher dry-run.

`evidence.premerge.json` is intentionally marked blocked and has no companion
checksum. It is a schema-valid planning scaffold, not `evidence.json`, not a
reviewer verdict, and not authority to materialize the primary task catalog.

## Artifact phases

### Primary bootstrap PR

The primary PR may contain:

- implementation and process/crash/recovery tests;
- `.orchestrator/runtime-task-audit-writer-registry.json`, generated after the
  nine registered writer blobs are frozen;
- an exact-head redacted checks report and checksum;
- `completion.json`, only after the distinct reviewer signs the exact
  completion payload with the protected Ed25519 key;
- this evidence plan and the protocol runbook.

The owner must not fabricate or placeholder-fill `completion.json`. Its
`conclusion: passed`, signature, key/policy identity, revocation check, verdict,
and ledger identity are reviewer/protected-operator facts. If those facts are
not available, omit the final artifact and leave the acceptance gate blocked.

### Post-merge protected install

After the primary PR merges, capture:

- implementation PR number, head SHA, required checks, merge time, and merge
  SHA `M`;
- proof that `M` is an ancestor of `refs/remotes/origin/dev`;
- exact merged bytes and SHA-256 for all nine registered writers, the registry,
  and `completion.json`;
- the live capability manifest and its SHA-256;
- exact supervisor process/checkout identity after restart at `M`;
- the canonical absolute `PANTHEON_RUNTIME_LOCK_VERIFIER_POLICY` path outside
  the repository, with target and parent ownership/mode/symlink checks;
- the active public-key/policy identity, non-revocation result, accepted
  protected ledger entry, and exact `verify_runtime_lock_capability` decision.

Do not copy private key material, unredacted process environments, OAuth/API
tokens, or other secrets into this directory.

### Post-closeout dry-run

The dispatcher requires the bootstrap prerequisite to be exactly `done`.
After independent review, primary merge, and owner closeout, run the canonical
strict `--dry-run` under a documented maintenance window. Preserve:

- exact command, admitted fleet actor, UTC timestamps, PID, checkout HEAD, and
  environment/root identity with secrets redacted;
- lock-order trace and admission decision;
- stdout, stderr, and exit code;
- before and after SHA-256 plus file identity for runtime state, event queue,
  approval queue, task state, active audit, and relevant audit archives; and
- a machine assertion that the before/after sets are identical.

The final evidence-only PR may then add immutable run artifacts,
`evidence.json`, and `evidence.sha256`. It must not modify registered writer
bytes, the writer registry, or `completion.json`. The primary catalog remains
blocked until that evidence is independently accepted and merged.

## Suggested final layout

```text
LOOP-PROD-RUNTIME-BOOT-001/
  README.md
  evidence.premerge.json          # mutable scaffold; never completion proof
  completion.json                 # exact signed schema; primary PR
  checks.json                     # redacted exact-head checks; primary PR
  runs/
    <utc-run-id>/
      command.json
      identity.json
      lock-trace.jsonl
      admission-decision.json
      verifier-decision.json
      before-hashes.json
      after-hashes.json
      dry-run.stdout.txt
      dry-run.stderr.txt
      result.json
  evidence.json                   # final logical-append-only manifest
  evidence.sha256                 # SHA-256 of evidence.json
```

The final manifest must validate against `schemas/product-evidence.schema.json`.
All referenced raw artifacts must be content-addressed in the manifest. Missing
or contradicted proof fails closed.

## Current blocking boundary

The exact version-1 registry still contains only the nine paths declared by
the task contract. Historical `scripts/dispatch_*.py` sinks are now
technically unable to target a canonical Git worktree; isolated fixture use
requires an explicit override outside every worktree and outside the configured
status root. Maintenance bundle/rebuild/queue paths have equivalent canonical
target guards, and the tracked source inventory currently reports zero
unregistered direct writers.

The dispatcher now binds the complete catalog task/dependency graph to one
unique install audit event. Active/archive sources are status-independent,
archive leaves cannot be symlinks, and pending audit recovery survives a valid
terminal archive performed after status commit. Rotated audit history is
scanned without trusting mtimes; missing or duplicate binding proof fails
closed.

That technical boundary is necessary but not completion authority. The owner
exact-head checks and nine writer digests must be refrozen after the current
source/test updates. This packet remains blocked until the distinct `Codex2`
reviewer creates the signed `completion.json`, the primary PR merges, and a
root-controlled operator installs the external verifier policy/ledger. The
post-closeout strict live dry-run and evidence-only follow-up remain separate
post-merge requirements.
