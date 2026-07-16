# OPS-DEPLOY-WORKFLOW-GUARD-001 post-repair Pantheon proof heartbeat failure

Captured: 2026-07-16T13:58:00Z

After PR #3753 merged the reconciliation-drift JSON fallback repair, the
Pantheon-only proof was rerun against merge commit
`d55a0caf7772ceb15b7914fe74856929f96d0283`.

## Proof run

| item | value |
| --- | --- |
| repo | `ajoe734/pantheon` |
| workflow | `Pantheon Nonprod Deploy` (`269991390`) |
| run | `29504052626` |
| job | `Deploy dev under shared environment lease` (`87640103826`) |
| result | `failure` |
| failed step | `Start identity-bound lease heartbeat` |
| failed at | `2026-07-16T13:53:31Z` |

The run acquired the shared dev environment lease, but the immediate
post-acquire heartbeat verification failed before the deploy step:

```text
environment lease lost: remote dev environment lease immutable field leaseId changed from
'3ea2ac79-f0c9-4d52-a6d6-64545537c936' to
'3e7589ba-4812-4ebe-98f0-cbc831653379'
```

Both shared deploy workflows remained `active` after the failure.

## Repeat pattern

The preceding Pantheon run `29503675321` failed the same step before any deploy
mutation:

```text
environment lease lost: remote dev environment lease immutable field leaseId changed from
'3e7589ba-4812-4ebe-98f0-cbc831653379' to
'5121f427-fb02-4049-b186-7b1029c7c862'
```

The earlier run `29502896049` reached `Deploy dev VM stack under lease` and
verified lease `5121f427-fb02-4049-b186-7b1029c7c862`, so the regression is
specific to the immediate cross-step heartbeat startup path rather than the
deploy wrapper's in-step guard.

## Diagnosis

The acquire step writes a new lease through the GitHub Contents API and the
next step immediately verifies the same branch/path. The failures show the
first verification can read the previous lease document for a short window,
then fail closed as if ownership was lost. The repair adds a bounded retry
around only this initial post-acquire visibility check; the deploy-step wrapper
still fails closed on heartbeat loss and remote lease mismatch.

Next action: merge the bounded initial-verify retry, then rerun only the
Pantheon proof.
