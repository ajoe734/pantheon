# TW-01 Teaching Dialog — UI Decisions

## pending-BFF gate

Both TeachingDialogList and TeachingDialogDetail check `BFF_PENDING = true` and render
a placeholder banner instead of the live UI. This prevents production deployment
before the routes are confirmed live by Pantheon.

## context_refs[] input

Optional context_refs[] are accepted as one `type:id` entry per line in a textarea.
Entries are parsed client-side and included in the POST body only when non-empty.
This matches the optional field defined in CreateTrainerSessionBody.

## Message composer gating

`allowedActions.canSendMessage` is the sole authority for enabling the composer.
Session `status` is never used to infer write authority.

## Transcript ordering

Events are rendered in the order returned by `events[]` from the BFF.
No client-side sort or insertion is performed.

## Post-send merge

After a successful POST /message, only the backend-echoed `event` from the response
is appended. The detail route is not re-fetched unless the operator explicitly refreshes.
