# OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001

## Outcome

Repository delivery adds a cryptographically signed exact-head reviewer
attestation and a trusted-base GitHub workflow that publishes an Actions-app
check on the exact PR head. The shared GitHub user can still write ordinary
commit statuses and comments, but cannot create a valid reviewer signature or
an Actions-app-owned check.

No live GitHub protection or repository setting was changed. Activation remains
a Human/Ops operation after a fresh baseline and canary.

## Trust chain

1. The governed reviewer records the canonical exact PR/head approval.
2. `canonical_review_check.py issue` re-reads canonical task plus immutable
   approval binding and refuses owner/self, stale head, missing event, revoked
   approval, or an unprotected signer key.
3. The protected reviewer key signs repository, task, PR, base, branch, head,
   owner, reviewer, decision, canonical record digest, timestamp, expiry, and
   nonce.
4. The reviewer public key is selected from
   `.github/canonical-review-keys.json` in the protected PR base branch. The
   shipped registry is empty and fails closed until Human/Ops lands a
   separately reviewed bootstrap/rotation PR.
5. The signed envelope may be transported by the shared GitHub credential in a
   PR comment; transport identity grants no authority.
6. `canonical-review-gate.yml` runs from the protected base branch, verifies
   every current comment, and creates `Pantheon canonical reviewer attestation`
   on the exact PR head through the GitHub Actions token.
7. PR/comment changes are evaluated immediately; a 15-minute schedule
   re-evaluates every open dev/master PR so expiry, key revocation, or removed
   evidence cannot leave a stale successful check indefinitely.
8. Authorized branch protection pins that context to Actions app id `15368`.
   A same-name user status does not satisfy it.

## Baseline truth

The dependency task recorded the original dev hole on 2026-07-27:
`required_approving_review_count=0`, stale/last-push review enforcement off,
and `enforce_admins=false`.

During this task's read-only capture, live state changed externally:

- `master`: required approvals `0`, admins unenforced, three Actions-app CI
  checks.
- `dev`: admin enforcement enabled; the scoped review endpoint returned one
  required approval, while the adjacent full protection response omitted the
  review block; existing canonical status and temporary root-freeze contexts
  remained.
- repository: `allow_auto_merge=true`.

`baseline.json` preserves both the historical hole and current drift. No stale
payload is safe to apply.

## Merge entry points

GitHub documents that required checks must pass before a protected-branch PR
can merge, that a required check can be restricted to one GitHub App source,
and that branch restrictions must explicitly include administrators. GitHub
also requires repository-level enablement before PR auto-merge can be created.

The authorized target configuration therefore requires all three:

- exact check context pinned to GitHub Actions app id `15368`;
- `enforce_admins=true`;
- repository `allow_auto_merge=false`.

`verify-protection` fails unless all three are read back. This closes web UI,
`gh pr merge`, REST merge, GraphQL merge, auto-merge finalization, auto-merge
creation, and administrator bypass as listed in
`merge-entrypoint-matrix.json`.

Official references:

- <https://docs.github.com/en/pull-requests/reference/status-checks>
- <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches>
- <https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/incorporating-changes-from-a-pull-request/automatically-merging-a-pull-request>
- <https://docs.github.com/en/rest/branches/branch-protection>

## Activation boundary

`activation-plan.json` is deliberately not executable as a stale snapshot.
Human/Ops must:

1. merge the trusted workflow to the target branch;
2. land the protected public-key registry through a separately reviewed PR and
   provision repo-external reviewer signer keys;
3. create and validate a signed canary check from Actions app id `15368`;
4. capture fresh full and scoped GitHub protection/repository/ruleset state;
5. generate exact activation plus rollback payloads;
6. authorize and apply the scoped operations;
7. run `verify-protection` and negative canary merge probes;
8. roll back immediately on any wrong app id, lost check, false pass, bypass,
   or workflow outage.

Dev and master are separate rollouts. Master must not be activated until the
workflow has reached master.

## Review focus

Reviewer should independently verify:

- candidate code never supplies the workflow/checker used by
  `pull_request_target` or `issue_comment`;
- public key lookup binds `key_id -> reviewer`;
- owner equals reviewer fails;
- exact repository/PR/head/branch/base comparisons precede success;
- the latest valid signed rejection overrides an older approval;
- expired/future/ambiguous/forged/stale envelopes fail;
- required-check readback requires app id `15368`;
- no command in this task mutated GitHub protection or auto-merge.
