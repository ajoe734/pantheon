# Loop Product-Level Remediation Gap Addendum

Document status: archived additive planning authority; execution remains
active

Audit date: 2026-07-13

Baseline plan:
`LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`

Execution packet:
`docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/INDEX.md`

## 1. Why This Addendum Exists

The original 36-task packet correctly covered the twelve canonical loops,
the Per-Persona OODA overlay, target-dev product paths, and global evidence
closeout. Execution and adversarial review exposed five classes of control
that were only implicit in those tasks:

1. the fleet process could publish a terminal task outcome after ownership or
   payload identity changed;
2. a deploy or smoke process could mutate a shared environment without one
   protected lease and without proving all candidate processes had exited;
3. candidate-controlled files could self-assert product evidence rather than
   consuming a protected controller attestation;
4. fleet scheduling, credential lifecycle, and frontend build cleanliness had
   no independent product gate;
5. `requires_human_ops_signoff` and the final authority were descriptive
   metadata without a protected transition-time consumer.

These are program-level gaps. They cannot be closed by annotating a passing
unit test, by marking an old task superseded, or by relying on a reviewer to
notice them manually.

## 2. Audited Findings

| Gap | Execution evidence | Product risk | Required control |
| --- | --- | --- | --- |
| Worker terminal CAS | exact-head adversarial review reproduced restart, post-launch supersede, resumed-approval, and auth-pause races | stale or re-owned work can publish success, remain alive, or pause the wrong fleet | exact task/event/owner/run/payload signature admission before and after launch; process-group termination; durable retry validation |
| Environment mutation lease | deploy, OpenClaw, public smoke, and Agora paths used different mutation boundaries | one candidate can outlive its lease or overlap another candidate on the shared dev VM | one controller-issued lease, isolated payload, no runner credentials, and zero-member local/remote cgroup proof |
| Fleet fairness | live supervisor activity showed repeatedly failing review tasks consuming reviewer reservations while older ready review work waited | valid loop work can starve even though the DAG is open and capacity appears available | fixed eligible-clock rules, a two-opportunity/ten-cycle/ten-minute bound, three-failure quarantine, and quota-aware lanes |
| Evidence trust root | frontend release evidence allowed candidate-authored booleans and zero-count assertions | a compromised or defective candidate can declare its own product acceptance | protected controller-generated attestation authenticated by an asymmetric signature or platform-protected keyed identity and bound to exact run, job, SHA, lease, and target; unkeyed checksums remain content digests only |
| Dev auth operations | code can fail closed when GitHub secrets are absent, but provisioning, capability issuance, rotation, and expiry are external | strict auth code may be merged while positive hosted Management AI qualification remains impossible | governed secret lifecycle plus short-lived scoped identity/capability qualification and rotation drill |
| Frontend evidence consumer | the dormant evidence PR did not independently verify protected provenance | UI/release logic can consume replayed, omitted, or self-authored assertions | fail-closed attestation verification with replay, tamper, omission, wrong-SHA, and wrong-lease negatives |
| Frontend product build | exact live/strict build passed but still reported invalid CSS, a circular chunk involving `runActionSafe`, and oversized chunks | release may be functionally green while retaining unstable loading and performance debt forbidden by G5 | warning-free production build, explicit chunk budget, strict browser/performance proof |
| Human/Ops final authority | signoff fields and CLOSE-002 ownership could be completed by an execution fleet without a protected verdict | a worker can self-close the program or replay a stale approval | server-side authorized Human/Ops verdict bound to exact catalog, protected closeout manifest, target, deployment identities, expiry, revocation, and nonce |

## 3. Additive Task Set

Nine tasks are added. Existing IDs are preserved. Two pristine baseline `todo`
records receive a versioned, exact-preimage dependency migration in the same
atomic status update that materializes the additive tasks.

| Task | Repository | Outcome |
| --- | --- | --- |
| `LOOP-PROD-WORKER-001` | `pantheon` | exact-CAS worker outcome, restart, retry, and forced termination integrity |
| `LOOP-PROD-LEASE-001` | `pantheon` | one protected shared-dev mutation lease and payload isolation boundary |
| `LOOP-PROD-FLEET-001` | `pantheon` | fair, quota-aware, starvation-bounded fleet admission |
| `LOOP-PROD-ATTEST-001` | `pantheon` | protected product attestation trust root |
| `LOOP-PROD-AUTH-OPS-001` | `pantheon` | governed dev credential and privileged-capability lifecycle |
| `LOOP-PROD-FE-EVID-001` | `execute-plans` | fail-closed frontend/release attestation consumer |
| `LOOP-PROD-FE-BUILD-001` | `execute-plans` | warning-free, budgeted live/strict product build |
| `LOOP-PROD-SIGNOFF-001` | `pantheon` | protected Human/Ops completion-verdict enforcement |
| `LOOP-PROD-CLOSE-002` | `pantheon` | additive global closeout and sole final program verdict |

## 4. Dependency And Migration Rules

- The dispatcher preserves every field of existing active task records except
  two declared dependency arrays. Under the status lock it requires exact old
  dependency preimages, `todo` status, the baseline catalog digest, and no
  branch/run/attempt/worker admission before appending `ATTEST-001` to
  `AGORA-002` and `WORKER-001` to `MAI-001`.
- Both dependency patches and all nine new IDs are committed in one atomic
  status write. Any missing, changed, started, claimed, or differently-bound
  preimage aborts the whole migration with no write. The migration records old
  and new dependency hashes, catalog digests, and audit events; rerun is an
  exact zero-write no-op.
- `LOOP-PROD-CLOSE-001` remains a baseline checkpoint because it may already
  exist in live state. It is not sufficient to declare the program complete.
- `LOOP-PROD-CLOSE-002` depends on the baseline checkpoint and every additive
  control, including `LOOP-PROD-SIGNOFF-001`. Only its protected guarded
  `done` transition is final program closure.
- The protected attestation task consumes the worker and environment lease
  controls; an attestation from an unprotected candidate lane is invalid.
- Frontend evidence and build finalization run after the feature-bearing
  execute-plans tasks whose broad `src` scopes they qualify.
- Dev auth operations requires Human/Ops action for secret provisioning and
  capability policy. A missing authorization is an explicit blocker, never a
  reason to create, reveal, or weaken credentials.

## 5. Current Implementation Convergence

The following active changes are implementation inputs, not completion proof:

| Change | Additive or baseline task | Admission rule |
| --- | --- | --- |
| Pantheon worker outcome guard PR `#3554` | `LOOP-PROD-WORKER-001` | refresh declared artifacts from the final exact diff, then merge only after exact-head adversarial review closes restart, child-process, resumed-approval, provider-pause, malformed-retry, task-lock, dispatch-policy, wakeup, and watch-event findings |
| Pantheon environment lease workflow PR `#3558` | `LOOP-PROD-LEASE-001` | rebase after strict-auth workflow changes, then prove all mutation lanes and zero-member cleanup |
| Pantheon strict dev auth PR `#3572` | `LOOP-PROD-AUTH-001` and `LOOP-PROD-AUTH-OPS-001` | no secret in argv/log/environment, no cross-environment forwarding, direct deploy fails before cloud access when unprovisioned |
| execute-plans credential-boundary PR `#311` | `LOOP-PROD-FE-001` | exact-head independent review, clean environment tests, live/strict/safe-write build, and hosted qualification |
| execute-plans dormant evidence PR `#310` | `LOOP-PROD-FE-EVID-001` | do not merge its self-authored assertion model; replace it with protected attestation verification |

No PR listed here is evidence of `done` merely because it is open, locally
green, or merged. Each task still needs the checksummed evidence and reviewer
closeout required by the baseline plan.

## 6. Product-Level Exit Delta

In addition to the thirteen baseline exit criteria, final closure now requires:

1. no stale, re-owned, superseded, or malformed worker attempt can publish a
   terminal success, mutate retry/quota state, or leave a process/payload alive;
2. every shared-dev mutation is bound to one exact lease and produces
   controller-authored zero-member cleanup evidence;
3. ready review work satisfies the fixed two-opportunity, ten-cycle, and
   ten-minute eligible-age bounds, while three same-signature failures trigger
   a thirty-minute quarantine that persists across restart;
4. all accepted product assertions verify a protected attestation bound to
   the exact FE SHA, BFF SHA, run/job, target, and lease;
5. governed dev credentials and privileged capabilities are provisioned,
   scoped, rotated, expired, and negatively tested without entering source,
   logs, browser bundles, or evidence archives;
6. the final execute-plans live/strict build has no invalid CSS, circular
   chunk, unexpected chunk-load error, or unexplained bundle-budget breach;
7. `LOOP-PROD-SIGNOFF-001` installs a protected server-side guard that rejects
   missing, forged, replayed, stale, revoked, rejected, wrongly-bound, or
   unauthorized Human/Ops decisions and marks `CLOSE-001` checkpoint-only;
8. `LOOP-PROD-CLOSE-002` receives independent Human/Ops acceptance with zero
   unresolved blocking product risk through that protected guard.

Until all eight deltas are proved, the 45-task program remains active.
