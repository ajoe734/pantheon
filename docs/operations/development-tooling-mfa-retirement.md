# Development tooling MFA retirement

Task: `OPS-DEV-MFA-RETIRE-001`. Date: 2026-09-11.

The operator authorized removing the custom development-tooling MFA requirement
and simplifying ordinary development. This change removes the implementation;
it does not issue credentials, manufacture acceptance, or restart stopped tasks.

## Removed

- `.orchestrator/execution_grant_issuer/` and its deployment unit, configuration,
  dependencies, browser client, tests and installation runbook.
- `.orchestrator/execution_authorization.py`, its dedicated tests, and the
  request/submit/revoke CLI commands.
- Automatic MFA-pending task records, the extra dispatch verdict, one-shot grant
  reservation, runner grant heartbeat, and MFA-specific runtime promotion probe.
- Issuer-specific CI detection, dependency installation and acceptance entries.

There is no replacement issuer, approval service, queue, or unconditional
verification stub. The source packet signature is still the existing local
transport contract; it is not a substitute human-MFA assertion.

## Minimal remaining behavior

Canonical tasks retain their scope, dependencies, owner and generation. The
supervisor and runner retain the existing process/worker lease, exact runtime
identity, read-only review workspace and bounded cancellation. New ordinary dev
tasks, including `security` and `hosted`, need no MFA service. New `live` tasks
retain an explicit Human/Ops hold; this change does not authorize production,
capital operations, credential rotation, or product writes.

Existing `waiting_for` holds remain effective, even when originally created for
the issuer. Reopening as a worker does not clear an operator hold. An explicit
Human/Ops reopen retains its existing ability to release one. Do not bulk-edit
tasks or treat a source upgrade as permission to resume S5 or deployment work.
Historical authorization fields remain inert provenance; no record migration or
deletion is required. The removed commands must not be reintroduced to interpret
those fields as current authority.

The bridge's legacy `operator_authorization_required` classification and optional
operator assertion fields are retained in packet provenance so already-signed,
queued or failed packets keep their exact replay/readback identity. They are not
consulted by dispatch, worker entry or promotion. Do not treat that historical
field name as a current instruction to obtain MFA or rewrite stored packets.

Product JWT/login/tenant/role checks, GitHub/GCP authentication, HTTPS/CORS and
exact-artifact rollback are separate and unchanged. In particular, this change
does not fix or endorse the product dev configuration's static MFA claims.

## Activation and validation

Use the existing source delivery and runtime promotion commands. Check active
workers before promotion; do not interrupt unrelated work or clear task holds.
Once the merged command source is active, ordinary task materialization and
worker entry must work without issuer configuration. Existing stop and stale
worker negative cases must still reject execution. Promotion still verifies
source identity, sandbox capability, drain and health, without the retired MFA
probe. Repository merge and live activation are separate facts to report.

Focused regression coverage is in the existing dispatch-admission/policy,
supervisor, worker-runner, task-status, bridge and runtime-promotion suites.
Tests of the deleted MFA protocol are removed; tests of canonical ownership,
holds, scope validation, replay-safe task transport and cancellation remain.

Removal is recoverable through Git history. Do not keep a second executable
copy in a `legacy` directory. This batch deletes no task data, secrets, deployed
artifacts or installed services.
