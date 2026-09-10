# Provider authentication and delivery health

Scope: development supervisor/worker authentication, not product BFF auth.

## One decision, one admission authority

`common.claude_auth_ready` is the shared Claude auth decision used by the live
probe and delivery adapter. It returns true for usable auth, false for absent
or rejected auth, and raises `ClaudeAuthRetry` when a temporary failure leaves
auth unproven. The exception carries a safe explanation and an optional retry
deadline, never credentials or the provider response body. This extends the
existing function's error contract; there is no second auth API or legacy
swallow-and-retry implementation.

The observation enters the existing `delivery_health` snapshot. The existing
shared admission predicate controls planning, late launch validation, idle
health refresh and recovery demands.

| Observation | Endpoint | Account |
| --- | --- | --- |
| Successful auth | Healthy until normal expiry | Normal success observation |
| Missing/rejected auth | Unavailable | Unchanged |
| Temporary auth/refresh failure | Retry-after, auth still unproven | Unchanged |
| Authenticated model quota/capacity failure | Existing authenticated-endpoint semantics | Existing capacity retry |

OAuth HTTP 429 is not proof that an expired access token works. It must not be
renamed to a model-capacity failure, which would incorrectly mark the endpoint
healthy. OAuth 5xx, transport errors and malformed responses similarly do not
prove either auth rejection or model capacity exhaustion.

HTTP `Retry-After` supports delta seconds and HTTP dates. Missing, malformed or
past deadlines use the existing configured health retry interval. No new
backoff policy, retry loop, credential store or queue is introduced. A stale
account observation cannot request a probe through an endpoint whose own
cooldown is still in force; a durable account capacity hold remains authoritative.
Existing explicitly authorized health refresh operations retain their semantics.

## Late delivery

If auth needs refreshing only after the queue's initial admission, the Claude
adapter returns the same normalized observation without spawning a process.
The launcher preserves that observation instead of rewriting it as a generic
worker/model failure. The queue stays pending behind the same health gate;
subsequent cycles do not repeatedly launch during the cooldown. A fresh healthy
observation permits the existing intent to proceed. Task ownership, review
authority and execution grants are unchanged.

## Antigravity

The already-integrated probe lifecycle fix keeps native log events separate
from process output: a startup authentication warning may be superseded by a
later explicit successful-auth event. A later failure, contradictory mixed
event, exhausted quota, nonzero exit or empty response still fails closed.
Do not add a second parser or a hand-written healthy override to compensate for
an old runtime. Promote the accepted source and obtain fresh probe evidence.

## Validation and activation

Regression coverage exercises HTTP rejection versus temporary refresh failure,
both forms of Retry-After, credential-write safety, probe/adapter propagation,
endpoint/account separation, cooldown despite stale account evidence and the
actual reserved queue phase without a worker spawn. Existing Antigravity
startup/success/terminal-failure tests remain in place.

After delivery, use the existing immutable-runtime promotion workflow and
verify source/config identity, supervisor and watchdog health, canonical
projection and fresh delivery observations. A healthy supervisor alone is not
proof that provider credentials work, nor that a product task is completed or
deployed. Provider rate limits and genuine login failures may remain after the
tooling correctly classifies them; do not bypass those gates.
