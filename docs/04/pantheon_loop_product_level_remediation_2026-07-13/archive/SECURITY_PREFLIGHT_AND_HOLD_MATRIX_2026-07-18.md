# Security Preflight and Hold Matrix — 2026-07-18

Status: security backlog classification; security implementation is deferred

System feature work and security work are intentionally separate. This matrix
does not block ordinary system fixes. It prevents security changes from being
mixed into them or being dispatched accidentally.

## Why this gate exists

The remediation packet contains tasks that touch shared authentication,
credentials, browser sessions, deployment workflows, runtime admission,
worker leases, attestation, OpenClaw repair, or final task-state authority.
Running a few of those tasks in parallel without a security boundary can make
the shared dev system unavailable or silently change who can operate it.

The program therefore marks the security set **deferred**. A fleet may inspect
and prepare a plan, but it may not implement, merge, deploy, or alter any
security-sensitive behavior in this phase.

## Security-sensitive hold set

The following tasks are `security_deferred` and must not be dispatched in this
phase:

### Identity, credentials, browser and privileged routes

`LOOP-PROD-AUTH-001`, `LOOP-PROD-AUTH-BOOT-001`,
`LOOP-PROD-AUTH-OPS-001`, `LOOP-PROD-BROWSER-AUTH-001`, `LOOP-PROD-FE-001`,
`LOOP-PROD-LEASE-001`, `LOOP-PROD-MAI-001`, `LOOP-PROD-MAI-002`,
`LOOP-PROD-MAI-003`, `LOOP-PROD-OODA-001`, `LOOP-PROD-TJ-001`,
`LOOP-PROD-TJ-002`, `LOOP-PROD-AGORA-003`, and `LOOP-PROD-PER-001`.

### Shared runtime, deployment and fleet control plane

`LOOP-PROD-000`, `LOOP-PROD-001`, `LOOP-PROD-002`, `LOOP-PROD-REC-001`,
`LOOP-PROD-DEP-001`, `LOOP-PROD-CAP-001`, `LOOP-PROD-EVO-001`,
`LOOP-PROD-BFF-001`, `LOOP-PROD-WORKER-001`, `LOOP-PROD-FLEET-001`,
`LOOP-PROD-DELIVERY-001`, and `LOOP-PROD-RUNTIME-GATE-SEPARATION-001`.

### Trust roots, release evidence and completion authority

`LOOP-PROD-ATTEST-001`, `LOOP-PROD-FE-EVID-001`, `LOOP-PROD-FE-BUILD-001`,
`LOOP-PROD-SIGNOFF-001`, `LOOP-PROD-CLOSE-001`, and `LOOP-PROD-CLOSE-002`.

Any task whose requested diff changes an artifact under `.orchestrator`, a
shared deploy workflow, a credential/identity provider, a privileged BFF route,
an OpenClaw write path, or `docker-compose.yml` is also security-deferred by
default even if its ID is not in this list. This prevents an apparently
harmless feature task from smuggling in a security change.

## What may proceed while security is deferred

- system feature fixes that preserve the existing auth/permission contract;
- the dedicated system-availability restoration task for the already-implemented
  dev-login transport;
- read-only repository inspection and evidence review; and
- isolated fixture tests and worktree preparation.

Feature work may not change roles, token formats, credential policy, fallback
security posture, privileged routes, deploy gates, or shared runtime locking.
No task may claim product-level completion from this work.

## Release conditions

When security work is later resumed, `LOOP-PROD-SECURITY-PREFLIGHT-001` must
produce and archive:

1. a machine-readable inventory mapping every catalog task to security scope,
   shared-state scope, allowed phase, and explicit block reason;
2. a default-deny dispatcher/test policy for unclassified or security-held
   tasks;
3. a blast-radius and rollback matrix for auth, deploy, runtime, worker, lease,
   browser, OpenClaw, and attestation changes;
4. two independent fleet reviews of the inventory and the exact current
   `origin/dev` source; and
5. a Human/Ops decision identifying which held groups may be released, in what
   order, and with what maintenance window.

Until that future decision, the security set stays deferred. The protected
Ed25519 program completion ceremony remains a separate final
`LOOP-PROD-CLOSE-002` responsibility.
