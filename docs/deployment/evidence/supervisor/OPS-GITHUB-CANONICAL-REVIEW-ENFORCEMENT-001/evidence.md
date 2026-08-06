# OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001

## Outcome

Repository delivery now separates signed-review evaluation from
merge-authoritative check issuance.

`canonical-review-attestation-audit.yml` is a read-only diagnostic workflow.
It rejects any PR base other than `dev` or `master` before checkout, captures
`.base.sha`, and checks out that immutable SHA. It has no `checks: write` or
`statuses: write` permission and makes no check-runs or status API call.

`canonical-review-gate.yml` remains the mainline generic exact-head
review-proof/status workflow for the distinct `Pantheon canonical review gate`
context. It runs as the generic `github-actions` App and cannot satisfy the
dedicated external-App-pinned reviewer-attestation check.

The required `Pantheon canonical reviewer attestation` check must instead be
issued by a dedicated external GitHub App owned outside the shared repository
account. Its private key and installation-token minting must be unavailable to
repository Actions and shared owner runtime. `protection-plan` cannot produce
an activation payload until it receives a successful read-only canary
check-run response from that App.

No GitHub App, live protection, repository setting, reviewer key, merge, or
auto-merge state was created or changed. External App provisioning and live
activation remain Human/Ops operations.

## Current-dev composition

The task branch now composes `origin/dev`
`34e1f494a251f6c2292a6675baa0ed65fdab7bb5` at composition merge
`fc500eb14680ce59145900ef760d1c1aad4d04ce`.

This is the second forward composition. The first,
`9e165a1d7edf751bc3b519e749ccf601eb231c57` over `origin/dev`
`003688bd7402d051986c07f1769285925af24e1b`, cleared the `CONFLICTING`
mergeable state PR #4303 had accumulated while `dev` advanced. The earlier
merge anchor `4583c789ae35d0f16cc8718c73bfad7adfc09505` resolved the generic
`Pantheon canonical review gate` in favor of the current git-native
exact-head review-proof implementation.

The second composition was forced by timing, not by a review finding.
`origin/dev` advanced 20 commits (`003688bd7` -> `34e1f494a`) within two
minutes of Antigravity's exact-head approval of
`e8843e3d706bd30ff4aa45926678ab369903c015`, whose proof tag and required
status were both green. `scripts/git/auto_integrator.py` then returned
`rebase_required`, because that approved head no longer contained `dev` and so
the approval would not have covered what actually landed. The owner refreshed
the branch with a forward merge rather than a rebase, which keeps this
evidence manifest inside the PR diff. The merge reported no conflicts, and
`git log 003688bd7..34e1f494a` restricted to this task's six artifacts is
empty -- no commit in that range touched a file this task owns, so the
refreshed head is behaviourally the same contract Antigravity already
approved.

The only conflict in the first composition was the leading comment block of
`canonical-review-gate.yml`. Both sides were comment-only and both were kept:
dev's `SUP-REVIEW-GATE-DISPATCH-RETRIGGER-20260805` note on why the proof tag
alone cannot satisfy the pinned `github-actions` context, followed by this
task's note that the same generic status is a separate lifecycle check and
cannot stand in for the dedicated external-App-pinned reviewer attestation.
No workflow key, permission, trigger, or step changed in the resolution. It asks GitHub only whether the
governed approval bridge pushed
`refs/tags/pantheon-review/approve/<head-sha>`; it does not attempt to read
the live task board from a GitHub-hosted runner.

The trusted-base attestation audit remains isolated in
`canonical-review-attestation-audit.yml`, so it can remain read-only without
removing the separately governed mainline gate.

The generic helper `canonical_review_gate_ci.py` remains solely on that
distinct exact-head proof/status path. The attestation audit workflow cannot
invoke it and has neither `statuses: write` nor any status/check API call.
The regression contract verifies both sides of this boundary.

## Why GitHub Actions is not an issuer

Read-only metadata for rejected PR #4303 head
`293cb1d4780653c9753ee19d9567f917511b7b70` shows Commit trailers, Runtime
mirror guard, Python packaging provision, and Smoke acceptance all came from
App id `15368`, slug `github-actions`. Repository Actions is enabled with
`allowed_actions=all`, SHA pinning is off, and no repository ruleset was
returned.

Therefore a task-branch workflow/job named
`Pantheon canonical reviewer attestation` could emit the same name and App
identity as the previous design. App id `15368` proves only that GitHub Actions
ran; it does not prove the trusted verifier validated a signed reviewer
envelope.

`issuer-provenance.json` records the read-only check-run ids and the negative
forgery model.

## Trust chain

1. The governed reviewer records the canonical exact PR/head decision.
2. `canonical_review_check.py issue` re-reads canonical task plus immutable
   approval binding and refuses owner/self, stale head, missing event, revoked
   approval, or an unprotected signer key.
3. The protected reviewer key signs repository, task, PR, base, branch, head,
   owner, reviewer, decision, canonical record digest, timestamp, expiry, and
   nonce.
4. The dedicated external verifier fetches the live PR, accepts only
   `dev|master`, captures the exact base SHA, loads the checker/key registry
   from that SHA, and evaluates all current signed envelopes.
5. The verifier publishes the exact-head check only through its dedicated
   external App installation token. Repository workflows and the shared owner
   runtime never receive that credential.
6. Pull-request/comment changes and expiry/key-revocation deadlines trigger
   re-evaluation so a removed or expired approval cannot leave a stale success.
7. Human/Ops captures a successful signed canary from GitHub and gives the raw
   check-run JSON to `protection-plan`.
8. Plan generation rejects App id `15368`, slug `github-actions`, an App owned
   by `ajoe734`, a wrong check name, a non-successful run, or malformed
   App/head/check-run metadata.
9. Authorized branch protection pins the exact check name to the verified
   dedicated App id retained in the machine plan.

The attestation audit workflow remains useful diagnostics, but its Actions
check is never merge authority. The mainline generic lifecycle status remains
separate and likewise cannot satisfy the external App-pinned attestation.

## Trusted-base workflow correction

All workflow event paths now converge on the same pre-checkout guard:

- fetch PR JSON;
- require `.base.ref` to be exactly `dev` or `master`;
- require `.base.repo.full_name` to match the current repository;
- require full `.base.sha` and `.head.sha` identities;
- only then fetch comments;
- checkout `${{ steps.snapshot.outputs.base_sha }}` with persisted credentials
  disabled.

The explicit attacker fixture uses base `task/ATTACKER-001` and verifies the
allowlist guard appears before both comment retrieval and
`actions/checkout`.

## Protection and entry points

GitHub documents that protected branches may require a check from a specific
GitHub App, and that check-run writes are available only to GitHub Apps. The
security property comes from pinning a separately controlled App, not merely
from using the Checks API.

An authorized target configuration requires all three controls:

- the exact context pinned to the verified dedicated external App id;
- `enforce_admins=true`;
- repository `allow_auto_merge=false`.

`verify-protection` derives the issuer only from the retained schema-v2 machine
plan. It also requires unchanged baseline `strict` and the full pre-existing
`context/app_id` multiset. A same-name success from `github-actions` App id
`15368`, an unpinned status, any lost existing check, strict drift, admin
bypass, or enabled auto-merge fails readback.

This configuration contract covers web UI direct merge, `gh pr merge`, REST
merge, GraphQL merge, auto-merge creation/finalization, and administrator
bypass as listed in `merge-entrypoint-matrix.json`. Live negative merge probes
remain withheld.

Official references:

- <https://docs.github.com/en/rest/branches/branch-protection>
- <https://docs.github.com/en/rest/guides/using-the-rest-api-to-interact-with-checks>
- <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches>
- <https://docs.github.com/en/pull-requests/reference/status-checks>
- <https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/automatically-merging-a-pull-request>

## Activation boundary

`activation-plan.json` is non-executable and has
`status=blocked_pending_dedicated_external_github_app`. Human/Ops must:

1. provision an externally owned dedicated GitHub App and verifier with
   minimum read/check-write permissions;
2. keep App private keys and installation-token minting outside repository
   Actions, the Pantheon repository/status root, and shared owner runtime;
3. land the protected reviewer public-key registry and provision reviewer
   signer keys;
4. prove exact base allowlisting/SHA pinning plus event and expiry
   re-evaluation in the external verifier;
5. obtain a successful signed canary and capture its raw GitHub check-run JSON;
6. capture fresh full/scoped protection, repository, and ruleset baselines;
7. generate and retain the schema-v2 machine plan with
   `--issuer-check-run-json`;
8. authorize and apply the ordered operations;
9. run plan-bound readback and negative merge/forgery probes;
10. roll back on any wrong App identity, lost check, false pass, bypass, or
    verifier outage.

Dev and master are separate rollouts. The committed snapshot is evidence, not
an activation payload.

## Review focus

Reviewer should independently verify:

- attestation-audit workflow permissions are read-only and it cannot write
  checks or statuses;
- every workflow event path rejects non-`dev|master` bases before checkout;
- checkout uses the captured base SHA, not a mutable branch ref;
- App id `15368` and slug `github-actions` cannot generate a protection plan;
- an App owned by `ajoe734` cannot generate a protection plan;
- same-name App id `15368` success fails plan-bound readback;
- schema-v2 plan binds App id/slug/external owner plus canary
  check-run/head/payload digest;
- baseline strict and every pre-existing `context/app_id` pair are preserved;
- no command in this task mutated GitHub protection, App installation, or
  auto-merge.
- the trusted-base attestation audit cannot invoke
  `canonical_review_gate_ci.py` or post any generic commit status, while the
  distinct mainline git-native exact-head proof gate remains present;
- the fresh PR head must receive a fresh exact-head approval from the current
  reviewer `Antigravity`: its generic gate fails closed until the governed
  approval transaction has pushed the corresponding exact-head review-proof
  tag. The task row reassigned owner to `Claude` and reviewer to `Antigravity`
  after the earlier `Codex2` rejections, which stand as history against the
  superseded heads `6e427581e`, `293cb1d47`, and `8b45c9834`.
