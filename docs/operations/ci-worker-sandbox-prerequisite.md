# SA/SD — existing worker sandbox prerequisites in Branch CI

Task: OPS-CI-WORKER-SANDBOX-PREREQUISITE-001
Repository: ajoe734/pantheon; delivery base: current dev.

This is narrow CI prerequisite work supporting the existing approved
OPS-REVIEW-HANDOFF-RECOVERY-CONTRACT-001 acceptance. Authority is the operator's
ongoing supervisor/auto-worker completion request and governed-contract /
prerequisite workflow, not new approval of a runtime architecture change.
No direct chatbox implementation or security/hosted execution grant is issued.

## SA: observed cause and boundaries

Exact classifier head97c8d4ca880609f3dfe69c8aa85cbba99ece7bea failed Branch CI
34079580708, Smoke101612323383:858tests/272.445s/1failure/exit1 at03:31:29UTC.
New child lifecycle diagnostics show worker_runner.py:509 refuses launch
because bubblewrap is absent. This is a missing prerequisite for an existing
mandatory read-only command-runtime boundary, not slow child completion.

Current accepted runner requires bubblewrap. Branch CI does not install it.
Installing the existing dependency belongs to the CI environment, not a new
worker implementation, state API, compatibility layer or second scheduler.
The classifier owner has no grant for Branch CI and is still active; its
artifact contract cannot lawfully be edited mid-flight. This prerequisite
owns only the missing CI preparation. It does not duplicate classifier work.

The separately reported live atomic-publication / individual mount race is
UNAPPROVED and outside this task. Missing bwrap does not establish that race
as a CI cause. No runtime source/mount/lock/auth changes are granted here.

## SD: one existing workflow, real sandbox

1. Rebase current dev in a clean task worktree. Modify the existing
   `.github/workflows/branch-ci.yml` Smoke acceptance job, not a new workflow.
   Provision the distribution's existing bubblewrap package before protected
   worker tests execute. Preserve all current component gates and commands.
2. Fail closed on a missing or unusable dependency. Reuse the existing real
   capability probe shape in test_worker_runner_heartbeat._probe_bwrap:
   `bwrap --die-with-parent --unshare-pid --ro-bind / / --dev-bind /dev /dev
   --proc /proc -- /bin/true`. A binary's presence alone is not capability.
   Do not satisfy this check with PANTHEON_SANDBOX_BINARY pointing to a stub.
3. Exercise existing real sandbox tests from test_worker_runner_heartbeat.
   Show the protected command source rejects writes, legitimate isolated task
   work remains writable, and the corresponding required real cases actually
   run rather than skip. Reuse existing tests; no parallel sandbox framework.
4. If installation exposes a different host restriction, report its actual
   error and use the supported CI package/profile configuration. Do not
   disable host-wide AppArmor/user-namespace restrictions, use privileged
   containers, remove isolation, or change live host security under this task.
   Any such additional design/authority requirement returns for discussion.
5. Preserve classifier97c8d4 diagnostic evidence and coordinate its owner to
   retire the newly introduced transparent fake_bwrap acceptance shortcut.
   This task MUST NOT modify the classifier owner's tests/source. After this
   prerequisite merges, that owner rebases current dev and reruns the original
   genuine queue/TaskStore/runner case in GitHub CI without bypass/skip.

## Exact artifacts

- .github/workflows/branch-ci.yml
- docs/operations/ci-worker-sandbox-prerequisite.md
- docs/deployment/evidence/OPS-CI-WORKER-SANDBOX-PREREQUISITE-001/evidence.json

Copy this plan byte-identically to the declared operations document. Record
its SHA256 and the accompanying RECHECK.md source digest in evidence.json.
No changes to the original immutable classifier SA/SD or runtime sources.

## Acceptance and delivery

- Confirm missing dependency against the exact failing job and current
  workflow before implementation; distinguish earlier successful stubbed
  tests from real namespace enforcement.
- Validate workflow syntax and unchanged independent tooling/product gates.
  Record actual commands, terminal counts, skips, exit codes, package version,
  real capability result, source base/head, manifest blob and CI job URLs.
- The real capability probe and required sandbox write-denial cases must pass
  on the GitHub runner. No required real-case skip, fake bwrap, weakened
  permissions, fabricated evidence or local-only claim is accepted.
- Use normal supervisor owner/reviewer workflow: scoped commit/trailers,
  branch push, genuine exact-head review, required checks, current-dev merge.
  Keep this task's source/CI acceptance distinct from the classifier's own
  later genuine queue test, runtime promotion, product readiness and12loops.
- Deliver the exact merged prerequisite SHA and CI proof to the existing
  classifier and SYS-SIMPLIFY-IMPLEMENTATION-CLOSURE-001 owners; no second
  global closure task, new cron, service, source mirror or deployment.
- A signed CI packet is not a hosted/operator-live authorization. Do not
  alter live processes, active leases, canonical JSON, credentials, production
  resources, development VM deployments or unrelated task scope.

## Dependency and ownership handling

This prerequisite does not depend on the unfinished classifier (that would
deadlock its prerequisite). It has no overlapping Pantheon source artifacts
with that task. The FE task's same-named branch-ci.yml is in execute-plans,
not this repository. Recheck pending packets, active contracts and archives
before intake. Use existing supervisor serialization/capacity; do not raise
capacity or interrupt an owner to force this task to run.

