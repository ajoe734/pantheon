# Development review authority

## Decision

During development, `approvals.review_authority` is
`canonical_taskstore`. The canonical TaskStore is the sole authority for a
review decision. A valid decision requires all of the following in the same
TaskStore transaction:

- an assigned reviewer distinct from the task owner;
- a frozen delivery binding for the exact PR head;
- a durable decision intent protected by its task digest and nonce; and
- evidence whose digest, actor, decision, PR identity, and intent nonce all
  match that intent.

`approve` and reviewer `reopen` do not call GitHub's review or status APIs.
They can therefore proceed when multiple configured workers share one GitHub
transport account.

## GitHub boundary

GitHub remains a delivery transport and evidence source: PR handoff freezes
the actual PR/manifest identity, CI checks are evaluated before integration,
and the integrator still performs the merge. Its review/status context is not
a required branch check and is ignored by the integrator in this mode.

The explicit `Human/Ops` operator-accept path remains separate. It is not an
independent reviewer implementation: it is an exceptional human authority
with its own exact-head admission rules, and does not replace reviewer
approval for ordinary work.

## Operational effect

Pending reviewer intents can be retried using the same task id, message,
actor, and nonce. The retry finalizes locally without a GitHub reviewer token.
Any changed owner, reviewer, task digest, PR number, head SHA, branch, base,
or evidence digest fails closed and requires a new review decision.
