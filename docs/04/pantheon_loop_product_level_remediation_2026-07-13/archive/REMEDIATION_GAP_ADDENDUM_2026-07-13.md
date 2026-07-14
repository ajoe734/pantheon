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
closeout. Execution and adversarial review exposed eight classes of control
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
6. the planner/controller could implement product artifacts directly, invent a
   Task-ID outside canonical status/archive truth, and then present its own
   trailer or same-session review as independent delivery evidence;
7. BFF auth and execute-plans browser identity changes could merge and deploy
   independently, without a complete read-route matrix, one shared cutover
   lease, hosted browser qualification, or paired rollback;
8. multiple incident repairs could race or merge the same semantic revert,
   leaving duplicate PRs and no unique canonical repair/activation authority.

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
| Dev auth bootstrap and operations | code can fail closed when GitHub secrets are absent, but initial authorization/provisioning, capability issuance, rotation, and expiry are external | strict auth code may be merged or started before the protected identities required by its own hosted acceptance exist | a pre-auth Human/Ops bootstrap gate, followed by governed lifecycle, short-lived scoped identity/capability qualification, and rotation drill |
| Frontend evidence consumer | the dormant evidence PR did not independently verify protected provenance | UI/release logic can consume replayed, omitted, or self-authored assertions | fail-closed attestation verification with replay, tamper, omission, wrong-SHA, and wrong-lease negatives |
| Frontend product build | exact live/strict build passed but still reported invalid CSS, a circular chunk involving `runActionSafe`, and oversized chunks | release may be functionally green while retaining unstable loading and performance debt forbidden by G5 | warning-free production build, explicit chunk budget, strict browser/performance proof |
| Human/Ops final authority | signoff fields and CLOSE-002 ownership could be completed by an execution fleet without a protected verdict | a worker can self-close the program or replay a stale approval | server-side authorized Human/Ops verdict bound to exact catalog, protected closeout manifest, target, deployment identities, expiry, revocation, and nonce |
| Fleet-only delivery provenance | Pantheon PR `#3557` was written and reviewed by the planner identity, had no canonical live/archive task or brief, and carried only self-authored `Codex` ownership/review trailers | planning authority can bypass fleet admission, scope control, independent review, and task accountability | fail-closed task/run/provider/slot/worktree/scope/branch/PR/review/merge/deploy binding; planner cannot edit declared product artifacts; formal distinct-runtime review |
| Coordinated browser auth cutover | PR `#3557` rejected the old privileged bundle token but the exact viewer returned 403 across boot-critical routes; execute-plans PR `#323` changed the FE independently; no hosted cross-repo browser acceptance preceded activation | a valid security tightening can still make the whole console blank, or frontend-first activation can expose a token/permission mismatch | credential-free browser session, complete explicit method/route matrix, separate privileged identities, secret-free origin prerequisite, one paired FE/BFF cutover lease, candidate probes, hosted desktop/mobile proof, and two-sided rollback |
| Duplicate incident repair | Pantheon PRs `#3587` and `#3588` merged the same revert less than one minute apart; the second merge was semantically empty | concurrent repair branches can bypass unique ownership, produce misleading merge evidence, and race rollout/rollback | exact repair lease and CAS over task, intent, prior/candidate heads, branch, PR, cutover, and rollback; duplicate repair is rejected before merge |

## 3. Additive Task Set

Twelve tasks are added. Existing IDs are preserved. Two pristine baseline `todo`
records receive a versioned, exact-preimage dependency migration in the same
atomic status update that materializes the additive tasks.

| Task | Repository | Outcome |
| --- | --- | --- |
| `LOOP-PROD-DELIVERY-001` | `pantheon` | fleet-only product implementation provenance, canonical task admission, and formal distinct-runtime review |
| `LOOP-PROD-AUTH-BOOT-001` | `pantheon` | authorized protected credential bootstrap before strict hosted auth qualification |
| `LOOP-PROD-WORKER-001` | `pantheon` | exact-CAS worker outcome, restart, retry, and forced termination integrity |
| `LOOP-PROD-LEASE-001` | `pantheon` | one protected shared-dev mutation lease and payload isolation boundary |
| `LOOP-PROD-BROWSER-AUTH-001` | `pantheon` | coordinated credential-free FE/BFF browser auth cutover, complete route matrix, hosted proof, and paired rollback |
| `LOOP-PROD-FLEET-001` | `pantheon` | fair, quota-aware, starvation-bounded fleet admission |
| `LOOP-PROD-ATTEST-001` | `pantheon` | protected product attestation trust root |
| `LOOP-PROD-AUTH-OPS-001` | `pantheon` | governed dev credential and privileged-capability lifecycle |
| `LOOP-PROD-FE-EVID-001` | `execute-plans` | fail-closed frontend/release attestation consumer |
| `LOOP-PROD-FE-BUILD-001` | `execute-plans` | warning-free, budgeted live/strict product build |
| `LOOP-PROD-SIGNOFF-001` | `pantheon` | protected Human/Ops completion-verdict enforcement |
| `LOOP-PROD-CLOSE-002` | `pantheon` | additive global closeout and sole final program verdict |

## 4. Dependency And Migration Rules

- The dispatcher preserves every field of existing active task records except
  two declared dependency arrays. Under the shared task-state and runtime
  admission locks it requires exact old
  dependency preimages, `todo` status, the baseline catalog digest, and no
  branch/run/attempt/worker/queue/approval admission before appending
  `ATTEST-001` to `AGORA-002`, and both `WORKER-001` and
  `BROWSER-AUTH-001` to `MAI-001`.
- `AUTH-001` is already non-pristine (`in_progress` during audit and `review`
  at the 2026-07-14T02:00:09Z live dry-run); its live dependency array is never
  rewritten. `BROWSER-AUTH-001` depends on both `AUTH-BOOT-001` and
  `AUTH-001`, and is the sole authority that can accept or activate the
  coordinated browser cutover. An `AUTH-001` merge/deploy alone is input only.
- Both dependency patches and all twelve new IDs are committed in one atomic
  status write. Any missing, changed, started, claimed, or differently-bound
  preimage aborts the whole migration with no write. The migration records old
  and new dependency hashes, catalog digests, and audit events; rerun is an
  exact zero-write no-op.
- Every canonical writer uses the stable `.orchestrator/task-state.lock`; the
  dispatcher acquires runtime admission serialization before that lock and
  refuses any target that is queued, running, approval-suspended, execution
  admitted, or backed by missing, empty, malformed, or unreadable live runtime
  state. Event producers use the same runtime lock, so no enqueue can cross the
  check-to-commit interval.
- A live or archived additive ID is accepted only when its program, catalog,
  immutable task contract, dispatcher provenance, and completion role match
  exactly. Duplicate live IDs and foreign final-authority collisions abort the
  whole transaction.
- Activity events use a status-committed transactional outbox with stable event
  IDs. A crash before append or before outbox acknowledgement is recovered on
  rerun without losing or duplicating the append-only audit record.
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
- `LOOP-PROD-AUTH-BOOT-001` must complete before coordinated strict hosted
  auth activation is accepted; it records only
  protected redacted metadata and cannot be completed by a fleet-authored
  secret, stub identity, or self-issued approval. Because the existing
  `AUTH-001` record was already running and cannot be mutated, this ordering is
  enforced at the coordinated `BROWSER-AUTH-001` activation gate rather than
  by rewriting the non-pristine live task. Later lifecycle and rotation work
  remains in `LOOP-PROD-AUTH-OPS-001`.
- `DELIVERY-001` must complete before every additive implementation path. The
  planner/controller may author plans, packets, dispatcher changes, monitoring,
  and review verdicts only; it may not edit any declared product artifact.
- The dispatcher encodes the exact twelve canonical L1 IDs and the OODA
  composite overlay, rejects foreign or omitted loop IDs, requires inventory
  and final closeout to cover the exact union, and requires each loop to retain
  at least one non-close `product-level` execution task.

The canonical live dry-run at 2026-07-14T02:00:09Z proposed exactly two
dependency migrations (`AGORA-002`, `MAI-001`), twelve additive creates,
thirty-three preserved live records, and three archived skips. It preserved
`AUTH-001:review` and `REC-001:review` without mutation and performed no write.

## 5. Current Implementation Convergence

The following active changes are implementation inputs, not completion proof:

| Change | Additive or baseline task | Admission rule |
| --- | --- | --- |
| Pantheon worker outcome guard PR `#3554` | `LOOP-PROD-WORKER-001` | refresh declared artifacts from the final exact diff, then merge only after exact-head adversarial review closes restart, child-process, resumed-approval, provider-pause, malformed-retry, task-lock, dispatch-policy, wakeup, and watch-event findings |
| Pantheon environment lease workflow PR `#3558` | `LOOP-PROD-LEASE-001` | rebase after strict-auth workflow changes, then prove all mutation lanes and zero-member cleanup |
| Pantheon strict dev auth PR `#3572` | `LOOP-PROD-AUTH-BOOT-001`, `LOOP-PROD-AUTH-001`, and `LOOP-PROD-AUTH-OPS-001` | code and validator contribution only; no secret in argv/log/environment, no cross-environment forwarding, direct deploy fails before cloud access when unprovisioned, and external provisioning remains blocked until authorized Human/Ops proof exists |
| execute-plans credential-boundary PR `#311` | `LOOP-PROD-FE-001` | exact-head independent review, clean environment tests, live/strict/safe-write build, and hosted qualification |
| execute-plans dormant evidence PR `#310` | `LOOP-PROD-FE-EVID-001` | do not merge its self-authored assertion model; replace it with protected attestation verification |
| Pantheon PR `#3557` | `LOOP-PROD-DELIVERY-001` and `LOOP-PROD-BROWSER-AUTH-001` | incident fixture only: the planner implemented it without a canonical task and presented the same Codex identity as owner/reviewer; its valid public-token threat model does not excuse missing viewer-route authorization, FE coordination, hosted browser proof, or independent review |
| Pantheon reverts `#3587` and `#3588` | `LOOP-PROD-DELIVERY-001` | incident fixtures only: both reverted the same change, the second merge was semantically empty, and neither duplicate merge proves unique repair admission or product completion |
| execute-plans PR `#323` | `LOOP-PROD-BROWSER-AUTH-001` | incident fixture only: a frontend token change cannot activate before exact BFF policy/read routes and the paired cutover lease are ready; fleet must audit the exact deployed head and may rewrite or discard it |

No PR listed here is evidence of `done` merely because it is open, locally
green, or merged. Each task still needs the checksummed evidence and reviewer
closeout required by the baseline plan.

At 2026-07-14 01:38 UTC, the hosted FE deployment manifest reported commit
`ce4d6e038ba177e0d487d6cd28840e691587f5f9` with
`VITE_BFF_REAL_WRITES=true` and `VITE_BFF_ALLOW_DEV_STUB_WRITES=true`, while
the BFF reported source commit `ebceb59b47bf6d3e7f76110f4d5ffa67979043fc` and
returned `403 AUTH_PUBLIC_BROWSER_ENVIRONMENT_FORBIDDEN` for the exact viewer
on `/bff/me`, personas, dashboard summary, Management AI, and incidents. The
revert deploy was still pending. This timestamped observation is incident
evidence, not an assertion about later live state; `BROWSER-AUTH-001` must
re-read both deployed identities and flags immediately before any cutover.

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
9. every product change is implemented by a supervisor-admitted fleet worker,
   not the planner, and is bound to one canonical task/run/worktree/scope/PR,
   a formal review by a distinct admitted runtime identity, one semantic repair
   lease, and exact merge/deploy provenance;
10. the browser contains no reusable credential, every boot-critical viewer
    route and privileged negative is proven, FE/BFF activation uses one paired
    lease and exact candidate identities, safe-write flags are false, and
    BFF-first, FE-first, duplicate, stale, partial, and rollback incident
    matrices pass on hosted desktop and mobile.

Until all ten deltas are proved, the 48-task program remains active.
