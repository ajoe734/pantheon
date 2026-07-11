# OCLAW-PMEM-004 BFF Handoff Follow-up 13

**Sidecar Task ID**: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-13`  
**Parent Task**: `OCLAW-PMEM-004`  
**Parent Owner**: `Claude2`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Antigravity`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: 2026-07-11  
**Mutates Canonical**: `no`

This support-only packet turns the existing composition worksheet into a
reviewer-ready intake and rejection checklist. It does not define canonical
DTO fields, accept dependency work, modify BFF/frontend code, or authorize
frontend dispatch.

## 1. Current Dispatch Recommendation

**Keep frontend dispatch at `defer`.** The parent depends on
`OCLAW-PMEM-002` and `OCLAW-PMEM-003`; route presence and proposed projection
shapes do not establish accepted authority, authorization, join identity,
degradation behavior, or executable fixtures.

The parent may record `ready` only after the intake bundle in section 2 is
complete and the rejection checks in section 3 pass. A condition involving
authority, authorization, field meaning, freshness, completeness, allowed
actions, or fixture fidelity is a blocker rather than a harmless follow-up.

## 2. Parent Intake Bundle

The parent owner should present one reviewable bundle with immutable refs. A
branch name, workspace or credential mount, proposed JSON, or unreviewed diff
does not satisfy an item.

| Intake item | Required contents | Reviewer proof |
|---|---|---|
| Dependency authority | Accepted `OCLAW-PMEM-002` and `OCLAW-PMEM-003` refs; owner of runtime profile, canonical memory, materialization result, and provider evidence | Exact commits/PRs and accepted task artifacts |
| Identity map | Persona ID, authorized memory scope, canonical memory IDs, profile/sync generation, materialization attempt/result ID, provider identity, and opaque reauth session identity | Cross-source join tests, including mismatch rejection |
| BFF projection | Independent runtime, canonical-memory, materialization, auth, live-smoke, dependency-completeness, quota, and reauth children | Implemented BFF ref plus focused contract tests |
| Degradation vocabulary | Bounded statuses/reasons for available-empty, unavailable, unauthorized, timeout, unreachable, malformed, stale, partial, failed, and verifying where applicable | Schema/fixture revision and negative tests |
| Authorization proof | Private persona memory checked before content or identifying metadata is returned; role/MFA enforced for recovery actions | Cross-persona denial and reauth security tests |
| Action contract | Server-advertised retry, materialization recovery, probe, reauth, code submission, cancel, and invoke actions with preconditions | Tests proving the browser need not invent actions |
| Fixture manifest | Sanitized fixtures pinned to the BFF revision with observation times, freshness inputs, expected envelopes/statuses, reasons, and allowed actions | Fixture ref and exact executable commands |
| Frontend task | `ajoe734/execute-plans` task/PR pinned to accepted BFF and fixture refs | Strict-live component/E2E plan; no Pantheon-local frontend tree |

## 3. Reviewer Rejection Checklist

The reviewer should return the bundle to the parent when any answer below is
`yes`:

- Does an empty list stand in for an unreachable, unauthorized, timed-out, or
  malformed Memory Plane response?
- Can a materialization failure hide canonical memory entries or make a
  workspace/cache look authoritative?
- Can auth-ready, configured credentials, a mount, selected model refs, or
  known quota produce provider usability without a fresh passing live smoke?
- Can an incomplete persona dependency inventory appear complete or yield a
  definitive ready result?
- Are unknown, stale, unsupported, or errored quota values replaced with zero,
  unlimited, or another reassuring numeric value?
- Can credential-flow success move directly to usable instead of `verifying`
  until a later fresh probe passes?
- Can the browser invent recovery actions, determine code-entry state, or call
  Memory Plane, OpenClaw adapter, or provider APIs directly?
- Can a cross-persona denial reveal memory content, canonical IDs, counts, or
  identifying metadata before authorization succeeds?
- Can one failed or unavailable child erase valid runtime, canonical-memory,
  materialization, or capacity evidence from another child?
- Are the accepted dependency, BFF, fixture, or frontend refs mutable,
  missing, or inconsistent with the tested revision?

## 4. Minimum Executable Scenarios

The intake bundle should exercise these scenarios as cross-surface behavior,
not merely isolated endpoint success:

1. Valid runtime plus authorized, available-empty canonical memory.
2. Valid runtime plus Memory Plane timeout or unreachable result.
3. Canonical entries available plus failed OpenClaw materialization.
4. Cross-persona private-memory denial without content or identity leakage.
5. Auth ready plus missing, stale, and failed live-smoke variants.
6. Auth observation plus incomplete persona dependency inventory.
7. Failed smoke plus known quota, and unknown/stale quota without numeric
   substitution.
8. Reauth awaiting code, credential success, probe pending, then a fresh probe
   result that alone determines restored usability.
9. Mixed Codex, Claude, and OpenClaw evidence states without pool-wide
   flattening.
10. Stale or mismatched join identities rejected rather than silently merged.

For each scenario, record the BFF commit, fixture revision, exact test command,
expected HTTP status/envelope, bounded reasons, observation/freshness inputs,
and advertised actions.

## 5. Operator Journey Handoff

The frontend should receive a journey contract only after section 2 is
accepted:

1. Render runtime route, canonical memory, materialization, provider health,
   capacity, and recovery state as independently inspectable sections.
2. Display source, observation age, freshness, and completeness beside claims
   that can become stale or partial.
3. Distinguish genuine empty memory from unavailable, unauthorized, timeout,
   unreachable, and malformed results.
4. Keep canonical entries visible while materialization is failed or retried;
   label workspace/cache material as derived evidence.
5. Render BFF-computed provider usability and only BFF-advertised actions.
6. Show code entry only for the active opaque session state that advertises
   it. After reauth success, retain the prior bounded reason while showing
   `verifying` until a fresh probe completes.

The bounded dispatch instruction is:

```text
Implement in ajoe734/execute-plans against Pantheon BFF revision <ref> and
fixture revision <ref>. Use BFF routes only. Render each BFF-owned child with
its source, bounded reason, observation time, freshness, completeness, and
advertised actions. Use BFF-computed provider usability. Cover every accepted
fixture in component or E2E tests and validate strict live-BFF mode before
hosted smoke.
```

## 6. Parent Decision Record

The parent owner should record:

```text
Decision: absorb | absorb-with-conditions | defer
Accepted OCLAW-PMEM-002 ref: <immutable ref>
Accepted OCLAW-PMEM-003 ref: <immutable ref>
BFF implementation ref: <immutable ref>
Fixture manifest ref: <immutable ref>
Focused verification: <exact commands>
execute-plans task/PR: <pinned ref>
Rejected checklist items: <none or list>
Residual conditions: <none or conditions that keep dispatch deferred>
```

Parent owner `Claude2` owns absorption, canonical names, implementation, and
frontend dispatch. Reviewer `Antigravity` reviews only this sidecar artifact's
scope discipline, accuracy, and usefulness. Approval does not accept the
dependencies, promote this packet to canonical truth, or prove frontend
readiness or deployment.

## 7. Sidecar Closeout Record

- Reviewed artifact commit: `1ee29e49a`
- Review note: `OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF-FOLLOWUP-13-REVIEW.md`
- Reviewer outcome: approved by `Antigravity` on 2026-07-11
- Delivered boundary: support-only intake, rejection, executable-scenario, and
  operator-journey handoff material
- Explicitly not delivered: canonical contract changes, BFF/frontend runtime
  implementation, dependency acceptance, frontend dispatch, or deployment
- Focused closeout verification: `git diff --check`; sidecar metadata and
  required-section assertions
- Composition owner: parent owner `Claude2` decides whether and how to absorb
  this packet into `OCLAW-PMEM-004`
