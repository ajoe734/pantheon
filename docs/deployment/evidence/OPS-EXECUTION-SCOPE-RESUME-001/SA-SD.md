# Step 5 explicit resume: bounded source prerequisite and execution gaps

Date: 2026-09-08 UTC. Operator instruction: 不用，step5能夠做做一做.
This supersedes the previous manual Step 5 pause. It does not revoke accounts,
prove MFA, grant hosted execution, complete dependencies, or authorize production.

## SA: observed state and responsibility

Canonical S5-PAIR/LOOPS/PROVENANCE/JOURNEYS/ROLLBACK/REPORT tasks already exist;
do not duplicate them or change their work class to evade authorization.
The planning hold and PAIR next-action notes now record explicit resume.
All six remain todo with no execution grant. PAIR still depends on incomplete
DEV502-STOP-001 and DEV-DELIVERY-001. Actual 502 traceback/RCA is not established.

OPS-EXECUTION-MFA-ISSUER-001 is archived done, PR 5661, merged source
c99204a9a93fa87015dfb8ebe0839478a0482c08. Its issuer service and request client
unconditionally reject Step 5; this is an obsolete operator-pause assumption,
not a missing GCP project permission. Preserve the genuine authentication gate.

Read-only probes this turn observed protected dev tips:
- Pantheon ff9ae2d03a6ba013c0ff19782c54d8c23652c79e
- execute-plans fda58bb05052c90e0e18310666ad174b5ab3ff51
These are observations, not frozen deployment candidates; resolve again at build.

Public FE deployment.json reports a3bf4060f803d1f8b44f6611e89347d59cd6ae0f;
BFF /bff/version reports 4d7f440c29d8f9057641b680f31e4ecd012f7558 and
image_digest=unknown. Thus no current exact digest-complete pair is proven.
FE declares live/strict and real/stub writes false. BFF reports strict auth,
dev login enabled, MFA not required. Product login and issuer MFA are separate.
Last three nonprod runs returned failure; newest observed run 34081262894.
An accepted flag in an old FE manifest is not fresh end-to-end acceptance.

## SD: minimal source-only resume support

Create OPS-EXECUTION-SCOPE-RESUME-001, implemented by Antigravity, reviewed by
Codex, depending on the archived issuer task. Do not modify live trust or deploy.
Use a clean current-dev worktree; do not overlap active AGORA-CHAIN-001,
JOURNAL-CONSUMER-ISOLATION-CORRECTIVE-001 or OPS-WORKER-MOUNTS-001.

Replace the unconditional lexical S5 prohibition with explicit trusted issuer
configuration of exact allowed task IDs. Default must remain TRACE-only and
fail closed. Provide a documented explicit-resume example listing only the six
existing S5 task IDs and pantheon-dev, not a wildcard or allow-all switch.
Do not enable that example on a live issuer in this source task.
The CLI must be capable of requesting an exact S5 ID and must not invent a
local authorization authority. Issuer-side configured exact scope and canonical
task policy/generation/spec binding remain authoritative. Preserve repository,
environment, artifacts/resources, actor, challenge, expiry, replay, MFA,
revocation, and operator UID checks. Missing, malformed, non-list, empty or
wildcard scope must not silently widen authority. Unknown tasks remain denied.

Keep genuine human second-factor verification and separate signer assumptions.
Do not treat ADC, project Owner login, this document, task notes, dev-login
tokens or synthetic test claims as a live grant. No new BFF routes or canonical
TaskStore writer. No credential revocation, rotation, IAM grants or live config
changes in this task. Do not replace an unchanged 502 blocker with a success.

Tests: default denies all S5; explicit exact allowlist accepts only configured
S5 request with valid canonical binding; unlisted S5 and unknown IDs denied;
wrong environment/repository, stale generation/spec/policy, forged claims,
missing MFA, expired/replayed challenge and malformed config denied. Preserve
existing TRACE regression tests and cryptographic verification tests. Exercise
the actual request CLI and issuer boundary using isolated fixtures. Label every
fixture and transport-mocked result simulation, never hosted acceptance.

Deliver updated service/client/config documentation, focused CI-wired tests,
committed SA/SD and evidence under the task evidence path. Use scoped commits,
independent exact-head Codex review, required checks, PR merge to dev and archive.
Source merge is only source readiness, not runtime promotion or hosted success.

## Remaining Step 5 execution sequence (existing tasks)

1. Complete actual TRACE -> FIX -> OBSERVE -> STOP and delivery prerequisites;
   provision genuinely authorized issuer and establish task-scoped grants.
   User declined account revocation; do not silently make it a task action.
2. Resolve current protected FE/BFF tips; build once, capture release_id,
   FE digest, BFF immutable image digest, compatibility hash and prior pair.
   Lease, safe writes, strict auth, HTTPS/CORS and readiness gate BEFORE switch.
3. New stimulus drives Loops 1-12. Each needs trigger, terminal output,
   next-consumer receipt, owner worker identity and durable reload readback.
4. Check Loop 5 simulation/real provenance, Loop 8 executable RuntimeBinding,
   Loop 9 real paper lifecycle, authenticated Management and Agora journeys,
   OpenClaw answer -> paper-only action -> terminal receipt -> reload.
5. Exercise bounded candidate failure and restore exact prior artifacts/config
   without rebuild; read served identity after restore, record lease/CAS receipt.
6. Final report distinguishes pass/fail/not-run/blocked with fresh evidence.

This document records observed gaps and a source implementation task; it is
not the final S5 acceptance report or proof of deployed source.
