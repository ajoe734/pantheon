# Persona interaction daily strict-operator delivery plan

Date: 2026-07-17
Status: execution approved
Supersedes: the claim that `PINT-010-R2` bounded write proof delivered a daily
Persona product. Its historical proof remains valid evidence for the bounded
CRUD and authority checks it ran, but not for daily availability or real
Persona reasoning.

## Outcome

An authenticated Pantheon operator can start from Persona Detail, Strategy
Workshop, Trading Room, Trade Journal, or Human Inbox and ask, challenge,
compare, propose, or reflect with one or more selected Personas. Each selected
Persona produces an independently attributed answer through the governed
OpenClaw provider path. The operator can inspect disagreement and evidence,
modify a candidate measure, accept it for governed review, reject it, or defer
it. Requests, replies, decisions, revisions, and audit provenance survive
reloads and BFF restarts.

This is an advisory and governed-proposal product. Personas and proposers have
`execution_authority=none`. No flow may place an order, call a broker, bind
capital or runtime, promote a lifecycle, mutate policy, or write Persona memory.

## Corrected baseline

The 2026-07-17 audit found that the current product is not daily-delivered:

- `services/control-plane/bff/agora/interaction/router.py` generates canned
  results in `simulate_interaction_debate_and_synthesis`; it does not call a
  selected Persona or OpenClaw provider.
- the deployed frontend is the `read-only` profile with real writes disabled;
  the only write-capable release is a bounded permissive-stub proof that is
  automatically restored to read-only;
- the browser's Supabase session is not bridged to a Pantheon strict BFF
  session, while hosted proof injects a test bearer and a runtime write
  override;
- proposal persistence and revision state exist, but candidate content is
  derived from the human topic rather than a Persona structured measure;
- proposal validation accepts browser-supplied result data, and daily
  `accept-for-review` is not distinguished from formal approval;
- parts of the UI and hosted test retain simulator toggles, fixed context, or
  direct-API fallback instead of a complete product flow.

The strict BFF role and capability gates already support authenticated operator
writes and viewer denial. They remain in force; this plan focuses new work on
real product capability and an authentic session path.

## Product interaction model

### Entry and context

Every entry preserves a typed context snapshot: tenant, source route, focused
object, strategy and version when present, decision or journal reference,
position/risk snapshot references, evidence cutoff, selected Personas, initial
mode, and return route. Fixed demo version/cutoff values are forbidden in the
live path.

Persona Detail exposes Talk, Challenge, Compare, Propose candidate measure,
and Reflect on thesis versus outcome directly. Other source pages open or
resume the same canonical Workshop rather than creating an unrelated chat.

### Interaction lifecycle

An interaction has `queued`, `running`, `completed`, `degraded`, or `failed`
state. It contains the immutable human request, participant snapshots,
per-Persona provider invocations, independent opinions, synthesis, evidence
and freshness, missing/degraded participants, candidate proposal links, and
audit provenance.

Each opinion identifies Persona id/version, provider/agent identity, request
and response correlation, conclusion, rationale, confidence, uncertainty,
risks, invalidation conditions, evidence references, and zero or more
structured recommended measures. Synthesis cannot overwrite individual
opinions. Provider failure is shown honestly and must never produce a forged
memo.

### Candidate decisions

Recommended measures can become governed candidates. A daily operator may:

- modify a candidate, creating a durable revision;
- accept it for governed review or adoption consideration;
- reject it with rationale;
- defer or cancel it where the existing proposal lifecycle permits.

`accept-for-review` is not formal approval and has no execution side effect.
Formal validation and approval retain distinct actor, exact digest/revision,
authoritative receipt, expiry, tenant, capability, and self-approval controls.

## Strict operator delivery profile

The persistent dev profile is `operator-live`:

```text
VITE_BFF_MODE=live
VITE_BFF_FALLBACK=strict
VITE_BFF_REAL_WRITES=true
VITE_BFF_ALLOW_DEV_STUB_WRITES=false
VITE_BFF_EMBEDDED_BEARER_TOKEN=false
BFF auth_mode=strict
BFF auth_stub=false
```

It is a separate immutable release artifact and digest from `read-only` and
bounded `write-proof`. It requires an exact healthy strict BFF and an
authenticated cookie or short-lived bearer session. It does not arm the proof
watchdog and is not automatically restored to read-only. Read-only remains an
explicit rollback profile.

The browser must never contain an OIDC client secret or privileged default
token. The product login path uses a BFF-verifiable existing OIDC/Supabase JWT
or a server-side exchange that issues an HttpOnly/short-lived BFF session.
Roles and capabilities always come from `/bff/me`.

## Hosted definition of done

Completion requires the exact merged BFF and frontend commits deployed on the
Pantheon dev hosts and the final manifest still showing `operator-live`, real
writes true, stub writes false, no embedded bearer, BFF strict, and BFF stub
false.

Authenticated desktop and mobile product journeys must cover:

1. Persona Detail ask and challenge;
2. two-Persona independent disagreement and synthesis;
3. thesis-versus-outcome reflection from a journal context;
4. a Persona-generated candidate measure;
5. modify, accept-for-review, and reject decisions in the UI;
6. authoritative validation and a distinct reviewer decision where applicable;
7. refresh, relogin, SSE reconnect, idempotent replay, and BFF restart readback;
8. viewer UI denial plus direct 403, unauthenticated 401, self-approval denial,
   and provider-outage degraded behavior with no fake opinion;
9. zero order, broker, capital, runtime-binding, lifecycle-promotion, policy,
   or memory side effects.

Tests may obtain short-lived strict credentials through an authorized
server-side CI step, but may not enable writes through browser storage, use a
permissive stub, fall back to direct API for the claimed UI flow, or restore the
final product to read-only.

## Execution and ownership

Execution is split into `PINT-011` through `PINT-018` in
`docs/bff/execution-tasks/2026-07-17-persona-daily-strict-operator/INDEX.md`.
Each repository change uses a clean worktree, focused and adjacent validation,
scoped commit trailers, push, PR, visible checks, merge, and exact-SHA evidence.
Frontend source remains exclusively in `ajoe734/execute-plans`.

