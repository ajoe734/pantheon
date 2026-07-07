# SRCLIVE-001 - Live Activation Acceptance

Status: ready for fleet execution; blocked from production closeout until live proof exists.

Recommended owner: Antigravity or Codex

Recommended reviewer: Codex2 or Copilot

Do not assign to Claude or Claude2 while their quota is exhausted.

## Goal

Prove the SRCLIVE-001 official-source live path in the actual dev runtime, not only in code, docs, or local tests.

## Evidence Already Published

- Pantheon PR #2517: SRCLIVE-001: anchor official source live path.
- Merge commit: 8da3d35766a041bfbb7b85aa018ee4ef65114cfd.
- Branch CI gates were green.
- Runbook published at docs/05/srclive/tw-activation-runbook.md.

## Missing Production Evidence

The current audit did not find proof that VM-local source-ingest activation was run and accepted. The missing evidence is:

- exact dev VM deploy source SHA;
- source-ingest activation command and exit status;
- official TWSE/TPEx/MOPS source fetch result;
- health/usage snapshot after activation;
- BFF readback for persona-tw-equity;
- archived evidence path and timestamp.

## Required Execution

1. Confirm the current dev VM source SHA and relevant service versions.
2. Run the activation path from the published TW activation runbook.
3. Capture logs and health snapshots.
4. Verify BFF readback for the target persona/dataset.
5. Record evidence paths in this packet or a dated evidence subdirectory.
6. If activation fails, record the concrete failing command, HTTP response or stack trace, and the service responsible.

## Acceptance Criteria

1. Live activation command succeeds in the dev runtime.
2. Health/usage snapshot shows source-ingest activity from official sources.
3. BFF readback returns current official-source-backed data for persona-tw-equity.
4. Evidence is committed through a clean branch, pushed, reviewed, checked, and merged.
5. Final closeout records PR number, merge commit, deploy/run IDs, and evidence paths.
