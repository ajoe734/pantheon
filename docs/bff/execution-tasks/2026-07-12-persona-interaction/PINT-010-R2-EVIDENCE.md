# PINT-010-R2 hosted integration evidence

Date: 2026-07-16 UTC
Owner: Codex2
Reviewer: Codex
Status: accepted. The canonical Persona eligibility and OpenClaw adapter
repairs, exact #381 candidate, clean 6/0 hosted write proof, independently
restored same-pair read-only frontend, and fresh final strict BFF restore are
complete. Superseded failures remain recorded as diagnostic history.

## 2026-07-16 closure record

This section supersedes the completion posture of the historical 2026-07-14
record below. The older record remains useful provenance, but its exact pair
and permissive-stub posture are not acceptance evidence for the current
candidate.

### Current delivery lineage

#### Pantheon

| PR | Head SHA | Merge SHA | Evidence role |
| --- | --- | --- | --- |
| [#3751](https://github.com/ajoe734/pantheon/pull/3751) | `e5fb548caf67f6859ffc91cfe15685393b837843` | `33afc82e54469a70a77f7dc1df2c8178d3f339d2` | Canonical tenant ownership and capability snapshots; deterministic, write-authorized, paper-only Servant ensure; fail-closed Persona eligibility |
| [#3752](https://github.com/ajoe734/pantheon/pull/3752) | `2733a30209fc0afda029514d969768d831efe40f` | `9637455aa55638d518bde8f31fb3827ba0ec8471` | Strict hosted-auth posture verifier syntax repair and executable contract coverage |
| [#3754](https://github.com/ajoe734/pantheon/pull/3754) | `b9ba936dabf9cc15e5888fe12459a43a5304a4e0` | `ddf4d0d5d33a848b3c86e3be2f6713e2ad9c0524` | Bounded exact-predecessor retry for initial lease visibility after successful CAS |
| [#3757](https://github.com/ajoe734/pantheon/pull/3757) | `d963586c9803744a5745104e9b490c2aeae651cc` | `87c2f7e50bc66b23e16436aa32775fcf2fedd8bb` | Trust-pin the protected lease controller and checksums; scope bounded retry to immediate verification |
| [#3761](https://github.com/ajoe734/pantheon/pull/3761) | `f5ec1aac9e10baa7d7c7d83f905e5df0a9cd4096` | `9967ce47fb826f782f3b84be1f08e6aefef88091` | Clear `PYTHONINSPECT` at every sanitized deploy boundary so successful remote commands cannot enter Python inspect mode |
| [#3765](https://github.com/ajoe734/pantheon/pull/3765) | `c05603df65b3a4e2010a793c550be235b17bd367` | `aa68f7508fcb58d403a1f845fa1d6a8f5a3fe748` | Route Servant reconciliation through the authenticated OpenClaw adapter with exact canonical admission, durable idempotency, and no execution authority |

All six PRs are merged into `dev`. Their visible branch checks concluded
successfully. PR #3751 recorded `48 passed` for its focused Agora regression
and `273 passed, 4 skipped, 1 pre-existing failure` for the broad
Agora/read-store/provisioning regression. The sole broad failure reproduced on
clean baseline `5a02825edd229aebfdd567e26233dac88697fe62`.

The deployment-reliability chain recorded these focused validations:

- #3752: 24 deployment-contract tests passed.
- #3754: 26 tests and 13 subtests passed; retry remains exact-predecessor,
  expired-predecessor, opt-in, and bounded to 30 seconds.
- #3757: 50 focused tests and 13 subtests passed; controller SHA256 is
  `52276793f99162fc7ca307a1370addd8d99478208ebf7beb67eab23b97b83048`
  and wrapper SHA256 is
  `f3995a2baedc2ff47178a0de8ad1952096df4de508d5a47c8e0042a151ab7ea8`.
- #3761: 24 deployment-contract tests passed, workflow YAML parsed, both
  deploy shell scripts passed `bash -n`, and `git diff --check` passed.
- #3765: 167 tests and 18 subtests passed, including built-adapter OpenClaw CLI
  create/replay/collision/UID ownership proof, running-gateway visibility, and
  dev/split-staging topology validation.

#### execute-plans

| PR | Head SHA | Merge SHA | Evidence role |
| --- | --- | --- | --- |
| [#379](https://github.com/ajoe734/execute-plans/pull/379) | `eb48ebe2e69e335516c5ac1841b84795878f21f5` | `1816ece7c77813b5b5c6098155776ad14a6991da` | Deterministic ensured-Persona selection, stable tenant/operator idempotency, exact preflight checks, viewer-negative ensure, and retry-free 6/0 hosted gate contract |
| [#380](https://github.com/ajoe734/execute-plans/pull/380) | `29e0b11ca6eb1351e2713c3764db188666624e84` | `88d3d0acf1a2a3db6810c2d2b51c09cafe456b09` | Emit only allowlisted HTTP status, error code, and failed-precondition evidence for non-2xx Servant proof responses |
| [#381](https://github.com/ajoe734/execute-plans/pull/381) | `cd1162686c7eb8c72ed2d33b28b114510b077105` | `b13635514618ad46e3855772e9ed1a0fa1fce3d4` | Gate Persona interaction submission on Workshop readiness and keep the composer disabled until the surface can emit the governed POST |

All three PRs are merged. PR #379's integration gate run `29502685625` attempt
3 and required branch checks succeeded. PR #380 ensures that a failed Servant
preflight cannot print a raw response, arbitrary message, Authorization value,
or token material. PR #381 repairs the readiness race exposed by the #380
candidate's rejected 4/6 diagnostic attempt.

### Canonical Persona authority properties

The merged BFF behavior does not infer Persona eligibility from lifecycle or
deployment stage. Admission requires exact tenant truth, supported lifecycle,
environment ceiling, matching Persona/snapshot identity, and an explicit
owner-backed `persona_opinion` capability snapshot. Servant ensure is
write-authorized and cannot be mutated by a viewer. It remains deterministic,
paper-only, and fail-closed until OpenClaw synchronization and exact snapshot
admission succeed.

The Persona and Servant surfaces retain `execution_authority=none`. No order,
broker, live-capital, capital-binding, runtime-binding, or memory authority is
granted by this repair.

### Successful strict BFF baseline

Pantheon Nonprod Deploy run
[`29507925706`](https://github.com/ajoe734/pantheon/actions/runs/29507925706),
attempt 1, job
[`87653469276`](https://github.com/ajoe734/pantheon/actions/runs/29507925706/job/87653469276),
completed successfully from `14:44:09Z` through `14:47:14Z`. It deployed only
the dev BFF from exact merged SHA
`9967ce47fb826f782f3b84be1f08e6aefef88091`, with `allow_dirty=false` and
`dev_auth_profile=strict`.

The successful job established:

- trusted lease controller
  `ddf4d0d5d33a848b3c86e3be2f6713e2ad9c0524` and the pinned controller and
  wrapper digests above;
- exact deployment-complete payload SHA
  `9967ce47fb826f782f3b84be1f08e6aefef88091`;
- hosted posture `auth_stub=false`, `auth_mode=strict`;
- successful public BFF smoke and exact `/bff/version` proof;
- successful Agora Postgres restart persistence for workshop
  `deploy-restart-29507925706-1` and proposal
  `proposal-deploy-restart-29507925706-1`, including completed-outbox replay
  and pending-outbox recovery;
- six expected transient HTTP 502 responses during the intentional BFF
  restart, followed by readiness recovery and successful verification; and
- clean lease release at `14:47:08Z`, lease ID
  `cdf8e16b-92a9-4cef-b08d-f1c2b2996d02`.

This is a verified strict baseline, not the final closeout restore. The bounded
write-proof window may temporarily require a different dev auth profile; a
fresh deployment of the final exact BFF lineage back to strict, with exact
hosted proof, must be recorded after that window.

### Superseded #380 candidate and initial read-only deployment

execute-plans push gate
[`29517392064`](https://github.com/ajoe734/execute-plans/actions/runs/29517392064),
attempt 1, completed successfully for exact frontend SHA
`88d3d0acf1a2a3db6810c2d2b51c09cafe456b09` and exact BFF SHA
`aa68f7508fcb58d403a1f845fa1d6a8f5a3fe748`. Its pair ID is
`f58b10450aec28b638916d57b3291c5a9c7a954f85fc89a527d61313a76a0555`.

This pair is superseded diagnostic history. It is not the accepted candidate
for the next write proof.

| Artifact | ID | GitHub artifact digest |
| --- | ---: | --- |
| `pantheon-fe-release-candidate-attempt-1` | `8383559811` | `sha256:617debcc60aa05a20d90e208251a7040cc3d3ad002dd1162e54f7488af8e12c4` |
| `pantheon-integration-evidence-attempt-1` | `8383560278` | `sha256:21877e6dd23ec79361f3d86850508f94459c90c65585d29779a7eb97d45790bf` |
| `pantheon-release-identity-attempt-1` | `8383560618` | `sha256:4110f380d7f5d910c3e36cda939dff1030d151bc68d447985c0a719eebc61a1f` |

The candidate binds read-only digest
`2797b8765556b9f6899ccaa5c88cda28f9f1ff87a09f50da3ff3db56e27815ee`
and write-proof digest
`7194d70e42e1c956db3a623e2736818a3856def998c331bc9d140f650891b356`.

Automatic workflow-run deploy
[`29518247457`](https://github.com/ajoe734/execute-plans/actions/runs/29518247457),
attempt 1, accepted that exact pair in read-only mode. Artifact
`pantheon-dev-fe-deploy-evidence-attempt-1` ID `8383846424`, digest
`sha256:1cb9c32f2f18e48067c9c65f0a36f247c2416cc90e295831ab706f49c2c8714d`,
records live/strict transport, real writes false, stub writes false, embedded
bearer false, candidate/post-switch probes passed, and
`rollbackRequired=false`.

Its post-switch hosted scan fetched 65 bundle assets with zero fetch failures
and scanned 66 HTML/JS/CSS sources with zero sensitive findings. The browser
made five intended BFF requests, all GET/HEAD, with zero Authorization headers,
zero write requests, zero old-host hits, and successful desktop/mobile hosted
UX profiles. These preliminary counts remain supporting history; the structured
scan file now records the fresh #381 candidate below.

### Superseded #380 bounded write-proof attempt and safe recovery

Pantheon run
[`29518975266`](https://github.com/ajoe734/pantheon/actions/runs/29518975266),
attempt 1, successfully deployed exact BFF
`aa68f7508fcb58d403a1f845fa1d6a8f5a3fe748` with the explicitly bounded
`permissive-stub` profile (`auth_stub=true`, `auth_mode=permissive`). Public
exact-version and Agora restart-persistence smokes passed, and lease
`4d2ef39b-bc23-4122-8d2b-441085d5b4a8` was cleanly released.

The fresh attempt-1 parent
[`29519185869`](https://github.com/ajoe734/execute-plans/actions/runs/29519185869)
used correlation ID `e06ae26b-d52b-4e6c-aa13-1601d92d0b60`. It deployed the
exact write-proof profile and dispatched independently authorized child
[`29520132313`](https://github.com/ajoe734/execute-plans/actions/runs/29520132313)
plus watchdog
[`29519934947`](https://github.com/ajoe734/execute-plans/actions/runs/29519934947).

This attempt is classified as failed and is not final proof. Child artifact
`pantheon-authorized-write-proof-attempt-1` ID `8384570109`, digest
`sha256:c72cc9baabf5d819c888fb0b3ae3b6a21744bde347b736cc91889c3a1e3009a5`,
shows four expected Playwright cases passed and two interaction cases timed out
waiting for the `/bff/agora/interactions` POST, one on desktop and one on
mobile. There were zero skips, zero retries/flaky cases, and two unexpected
timeouts; this does not satisfy the required 6/0 gate.

The failure is narrower than backend admission or governance:

- Servant ensure passed for exact Persona
  `agora-servant-b996f46bc40c4764690a`, class `agora_servant`, owner scope
  `user_private`, memory scope `private_user`, registry-backed, and
  `executionAuthority=none`.
- The governed proposal probe passed: unauthenticated create 401
  `AUTH_REQUIRED`, viewer create/modify 403, operator create 201, modify 200,
  validate 200, revisions `1,2,3`, audit `create,modify,validate`, replay at
  revision 3, no downstream execution, and no recorded token.
- Before/after manifests remained the exact same pair and write-proof digest,
  with embedded bearer false.

The watchdog completed all three jobs successfully and restored the exact pair
to read-only before any successor mutable action. The parent restore-confirm
job also passed. The restored browser probe had zero failures, zero write
requests, zero Authorization headers, zero sensitive findings, and explicitly
reported the paired read-only release restored.

Pantheon safety-restore run
[`29521081353`](https://github.com/ajoe734/pantheon/actions/runs/29521081353),
attempt 1, job `87698032121`, then completed successfully from `17:45:20Z` to
`17:48:57Z` for exact BFF SHA
`aa68f7508fcb58d403a1f845fa1d6a8f5a3fe748`. Three `/bff/version` reads
returned the exact known SHA with build time `2026-07-16T17:47:09Z`,
`auth_stub=false`, and `auth_mode=strict`; three `/healthz` reads returned
`live=true`, `ready=true`, with all dependencies healthy. This is a safety
restore after the failed proof, not the final strict restore required after a
future successful 6/0 proof.

Both deploy workflows were active during recovery: Pantheon Nonprod Deploy ID
`269991390` and Pantheon Dev FE Deploy ID `292028803`. Pantheon run
`29469158508` was observed as an old GitHub-queued platform ghost on
`task/EVOCHAIN-011`: it had zero jobs, had not updated since `03:31:02Z`, and
held no lease. Both normal cancel and force cancel returned GitHub HTTP 500.
It remains recorded transparently as historical, non-executing diagnostic
state rather than being hidden as a clean queue.

### Fresh accepted #381 candidate

execute-plans PR #381 supersedes the rejected #380 proof pair. Push gate
[`29522604349`](https://github.com/ajoe734/execute-plans/actions/runs/29522604349),
attempt 1, job `87703016409`, completed successfully for exact frontend SHA
`b13635514618ad46e3855772e9ed1a0fa1fce3d4` and exact BFF SHA
`aa68f7508fcb58d403a1f845fa1d6a8f5a3fe748`. Branch CI run `29522610098`
also succeeded.

The accepted pair ID is
`433a21fe127a3bde0020b9444869f565e99ed4b36b7ade2c3ea89aa5185a8d36`,
with read-only digest
`61dfc0986f57cb696b0a6aa7b21af6d3eb8aa25fe8b5b63f7f3ad556afd47360`
and write-proof digest
`db21014d4c69404aeb23987012cff1d54dd1eaa5f33d10700a9a2cae6d53e660`.

| Artifact | ID | GitHub artifact digest |
| --- | ---: | --- |
| `pantheon-fe-release-candidate-attempt-1` | `8385576520` | `sha256:2bae2da1b7f8acdb91d54f41786ba64ba6c747b56da8c3d62d3bd221b90e9b36` |
| `pantheon-integration-evidence-attempt-1` | `8385577029` | `sha256:3f0cd967d8f4260d472fa0490ef962d525a69b5fb6516cde8f1865ef1d03b96c` |
| `pantheon-release-identity-attempt-1` | `8385577374` | `sha256:15d8f777a3b70ba7cdfa394408a5f151596493775d1613c106f68de96dc27fff` |

Automatic read-only deploy
[`29523324214`](https://github.com/ajoe734/execute-plans/actions/runs/29523324214),
attempt 1, job `87705406096`, accepted the exact pair at
`2026-07-16T18:28:45Z`. Its
`pantheon-dev-fe-deploy-evidence-attempt-1` artifact ID is `8385901699`,
digest
`sha256:d471f1232982174c15ad54458fd08d14b9e8d20278c33e80c95b96cfcdd78b02`.
Three independent manifest reads were exact and safe: read-only profile,
live/strict transport, real writes false, stub writes false, and embedded bearer
false.

A fresh independent same-origin scan captured at
`2026-07-16T18:31:20.105Z` and completed at
`2026-07-16T18:31:23.272Z`. It classified 811 HTTP 200 responses: 48 HTML
responses (the root plus 47 SPA fallbacks), 763 JavaScript responses totaling
27,433,340 bytes, zero other responses, and zero failures. Both forbidden
viewer and operator literal counts were zero, and pre/post manifest identity
remained stable. These are the authoritative counts in
`PINT-010-R2-BUNDLE-SCAN-2026-07-16.json`.

### Accepted #381 write proof and final restoration

The first permissive-profile dispatch
[`29525959905`](https://github.com/ajoe734/pantheon/actions/runs/29525959905)
was rejected before deployment. Its lease acquire/read-after-write path received
GitHub API HTTP 400 while reading an expired predecessor owner. No deploy step
ran, the BFF remained unchanged, and no write-proof window opened. This is an
infrastructure coordination failure, not application or proof evidence.

Replacement Pantheon run
[`29526445708`](https://github.com/ajoe734/pantheon/actions/runs/29526445708),
attempt 1, job `87715949316`, completed successfully for exact BFF SHA
`aa68f7508fcb58d403a1f845fa1d6a8f5a3fe748`. Three `/bff/version` reads and
three `/healthz` reads confirmed the exact known SHA, `auth_stub=true`,
`auth_mode=permissive`, live/ready true, and all dependencies healthy.

Fresh parent run
[`29526662869`](https://github.com/ajoe734/execute-plans/actions/runs/29526662869),
attempt 1, used correlation ID
`5baa7d49-394a-43a5-8ea4-ade765313277`. All three jobs succeeded:

- deploy exact gated candidate: job `87716670643`;
- coordinate bounded authenticated Persona proof: job `87718210667`;
- confirm independent same-pair read-only restore: job `87720081823`.

Its immutable artifacts were:

| Artifact | ID | GitHub artifact digest |
| --- | ---: | --- |
| `pantheon-dev-fe-deploy-evidence-attempt-1` | `8387096770` | `sha256:7d6e3fbc78d3e902b9fb6d38e2c23b6aa16b697ef9702c5fe7d9e4965db655b5` |
| `pantheon-proof-binding-attempt-1` | `8387165728` | `sha256:0536a99c7c1f171cb82f6914d052fc6ec0dc59208ab1766a5de50af30b8da962` |
| `pantheon-proof-child-claim-attempt-1` | `8387231666` | `sha256:a1373dc2c64d2ed93be463ad39b0080a43ad2714aa586d94ece49ba6981c0ba9` |

Independent watchdog
[`29527293543`](https://github.com/ajoe734/execute-plans/actions/runs/29527293543),
attempt 1, passed exact-parent authentication (`87718776813`), bounded proof
watching (`87718876632`), and exact read-only restore (`87720078613`).

Independently authorized child
[`29527452120`](https://github.com/ajoe734/execute-plans/actions/runs/29527452120),
attempt 1, passed authorization job `87719310624` and proof job `87719347860`.
Artifact `pantheon-authorized-write-proof-attempt-1` ID `8387313123`, digest
`sha256:3999572b0c5ff0d4e9682427320ce0bb61ce12256fb383a007bdb59c17489fad`,
contains the accepted proof.

The focused Playwright result was exactly six expected cases in
`71935.323ms`: three desktop and three mobile, with zero skipped, unexpected,
or flaky cases and no retry. Servant preflight resolved exact Persona
`agora-servant-b996f46bc40c4764690a`, class `agora_servant`, owner scope
`user_private`, memory scope `private_user`, registry-backed, and
`executionAuthority=none`.

The governed proposal proof returned unauthenticated 401 `AUTH_REQUIRED`,
viewer create/modify 403, operator create 201, modify 200, and validate 200.
Canonical readback contained revisions `1,2,3`, audit actions
`create,modify,validate`, final and replay revision 3,
`downstreamExecutionAttempted=false`, and `tokensRecorded=false`.

Before and after proof manifests remained exact pair
`433a21fe127a3bde0020b9444869f565e99ed4b36b7ade2c3ea89aa5185a8d36`
with write-proof digest
`db21014d4c69404aeb23987012cff1d54dd1eaa5f33d10700a9a2cae6d53e660`;
the accepted write-proof manifest time was `2026-07-16T19:18:01Z`.

The watchdog restored that same pair with read-only digest
`61dfc0986f57cb696b0a6aa7b21af6d3eb8aa25fe8b5b63f7f3ad556afd47360`.
Three independent manifest reads matched, the accepted restore timestamp was
`2026-07-16T19:25:04.844Z`, `safeRestore` passed, and
`rollbackRequired=false`.

Final Pantheon strict run
[`29527966606`](https://github.com/ajoe734/pantheon/actions/runs/29527966606),
attempt 1, job `87721031182`, completed successfully from `19:26:09Z` through
`19:28:45Z`. Three `/bff/version` reads returned exact known SHA
`aa68f7508fcb58d403a1f845fa1d6a8f5a3fe748`, build time
`2026-07-16T19:27:37Z`, `auth_stub=false`, and `auth_mode=strict`. Three
`/healthz` reads returned live/ready true with every dependency healthy.

At final verification both deploy workflows were active and no executing,
lease-holding, or conflicting Pantheon or execute-plans deployment remained.
The historical GitHub-queued run `29469158508` was still nonterminal but had
zero jobs and no lease, as recorded above. The final runtime evidence is
complete and accepted; no live-capital, broker, order, capital-binding,
runtime-binding, or memory authority was granted or exercised.

## Historical 2026-07-14 record

## Delivery lineage

### execute-plans

| PR | Merge SHA | Evidence role |
| --- | --- | --- |
| #275 | `ff195d8166a5be5bb928b86dfb103afc706bdf9c` | Original PINT-006 source on `main` |
| #288 | `ada61156d15977bc188d7e73d4f1e04e556c2b37` | Contract-aware reconciliation onto `dev` |
| #332 | `68770f1e59f503d85126248195b0e4e173ec5b47` | Governed Persona interaction journey |
| #333 | `622b8620db1ebdecc00216ae76cceb7faf3e8827` | Authenticated proposal audit readback |
| #334 | `aa30a578be092b803d3270f427cee89883171475` | Positive `/health` deploy gate |
| #336 | `8cf10d809893c1765be1fdbcb3cc0f28d656916c` | Cross-origin hosted E2E fixture fidelity |
| #337 | `936f252e09fa3bb887c88e733e24b6941cac644e` | Tokenless bundle, viewer-negative UI, opt-in live proposal gate |
| #338 | `60461cb65038c43e427e192e0c857c4772f03ced` | Schema-valid unauthenticated proposal probe |
| #340 | `761c3013ac1c70da1dd6b20f5b0931f3eab6607d` | Canonical Pack-D `AUTH_REQUIRED` assertion and evidence field |
| #342 | `544efc8929b5a723289ea19b48240aabef1fd77d` | Latest-lens fixture compatibility and mobile Trading Room access |
| #343 | `6bea2d28c84d823993398e34ecbdd2d9a46bdf81` | Unrelated hosted fallback-card assertion |
| #344 | `cbc6877630e0af087cd4d119da6024d816e4e495` | Narrow responsive parity and viewport regressions |
| #345 | `b6a5bc9311941cf7333c5f738526868715533101` | Hosted drawer gate hardening |

PR #275 is the main-side source and is not an ancestor of the deployed `dev`
history. PR #288 is the reviewed reconciliation commit that is an ancestor of
the hosted frontend. PRs #338, #340, #342, #344, and #345 have green required
trailer, generated-file, and smoke checks. Final hosted SHA `b6a5bc93...` is a
merged descendant of #342. PR #343 changes only the unrelated
`e2e/evochain009.spec.ts` probe; #344 and #345 add later responsive coverage
while retaining the PINT journey.

### Pantheon

| PR | Merge SHA | Evidence role |
| --- | --- | --- |
| #3480 | `ca36f1209e401c7ed1953003c60295dd56b54c9f` | PINT-006 handoff evidence |
| #3589 | `4223ae4ef98c6d1b9ecc0e0286376240b2ec3bee` | Canonical approval evidence binding |
| #3594 | `39709fa4f3f418ed71f4badfccd905a9d8fc6203` | Viewer capability boundary |
| #3601 | `cef3701f0ceb84a063288f15c987d50effb2a459` | Governed proposed-action path |
| #3603 | `863112506186f5cd8ba211bebf5ee7a6fca6435b` | Auth-neutral deploy persistence smoke |
| #3604 | `7823838eb62b8635d55c5491ed456c1a09214996` | Durable revisions, idempotency, and outbox |
| #3605 | `8de9ed3b09ae1002edc74256f33b9bec1fe3b717` | Write-authority enforcement and restart-smoke repair |
| #3607 | `1d711ea7b88433c1c7450df2667ac0a761599b6e` | Residual acceptance and closeout gates |
| #3482 | `07594aaccb07ffa5b36ca80a8f99eb54b37601c1` | Pack-D errors, regression coverage, evidence, and restart recovery |
| #3628 | `9d393816acfe322a12ba1b295218f829db36ac28` | Final merged `dev` deployment identity; #3628 PR delta was task docs only |

PR #3482 merged with all required checks green. Its merge is an ancestor of
every accepted BFF deployment in this record.

## Authority and real hosted BFF proof

The final focused backend composition passed locally:

```text
pytest -q \
  services/control-plane/bff/tests/test_agora_write_authority.py \
  services/control-plane/bff/tests/test_agora_governed_proposals.py \
  services/control-plane/bff/tests/test_agora_persona_interactions.py \
  services/control-plane/bff/tests/test_agora_strategy_workshop.py \
  scripts/test_agora_workshop_restart_persistence_smoke.py

109 passed, 1 skipped, 136 warnings in 170.22s
```

The skipped case requires the separately configured external
`TEST_DATABASE_URL`. The deploy restart evidence below verifies that the dev
workshop and governance stores use Postgres. The focused restart helper also
passed independently: `5 passed`.

The task-scoped live BFF step in final cross-repository run `29316624607`
checked exact merged BFF SHA
`9d393816acfe322a12ba1b295218f829db36ac28`, created paper-only proposal
`prop_70da230441f1490eb737f553bebd6c90`, and recorded no bearer values:

| Check | Result |
| --- | --- |
| Unauthenticated proposal create | HTTP 401, Pack-D `AUTH_REQUIRED` |
| Viewer proposal create | HTTP 403, Pack-D `FORBIDDEN` |
| Viewer proposal modify | HTTP 403, Pack-D `FORBIDDEN` |
| Operator proposal create | HTTP 201, revision 1 |
| Operator modify | HTTP 200, revision 2 |
| Operator paper validation | HTTP 200, revision 3 |
| Authenticated readback | revisions `1, 2, 3`; audit `create, modify, validate` |
| Same-key replay | same proposal, still revision 3 |
| Execution authority | `none`; no downstream execution attempted |

Regression coverage additionally rejects viewer writes when a strict JWT grants
`agora.workshop.v1`, self-approval, cross-tenant and wrong-target approvals,
wrong proposal revision/content/validation digests, expired, superseded, or
revoked decisions, stale ETags, and same-key/different-payload collisions.
The strict capability-bearing viewer checks cover interaction, proposed-action,
proposal-create, and proposal-modify writes; the rejected modify leaves revision
1 unchanged. The shared admin mutation role has positive workshop-create and
proposal-create regressions. Persona and Servant output remains advice or a
governed proposal; it has no order, broker, capital-binding, runtime-binding,
or memory authority.

## Durability and restart evidence

Pantheon deploy run
[`29306830685`](https://github.com/ajoe734/pantheon/actions/runs/29306830685),
job `87001761085`, successfully deployed exact BFF SHA
`7c2e3673b26a277bdba4e57d009f7088efce34d0`. It verified:

- `/bff/version` returned HTTP 200 with that exact 40-character SHA and
  `source_commit_known=true`;
- `/healthz` returned HTTP 200 with `live=true`, `ready=true`, and healthy
  dependencies;
- OpenClaw and public/CORS smoke passed;
- workshop `deploy-restart-29306830685-1` and proposal
  `proposal-deploy-restart-29306830685-1` retained revisions `1,2,3` and audit
  `create,modify,validate` across a fresh BFF process;
- a completed outbox item replayed without rerunning its side effect;
- a pending recovery item left after a modeled partial failure was reclaimed
  and completed exactly once after restart;
- latest-revision idempotent replay did not append a revision or rerun work.

Four transient readiness HTTP 502 responses occurred during the intentional
restart and recovered. The run completed successfully.

Run
[`29308875940`](https://github.com/ajoe734/pantheon/actions/runs/29308875940)
then repeated the same deploy and recovery gate for exact descendant SHA
`c30bf618249f9f43604edd058b4e2ca34c892e07`. Workshop
`deploy-restart-29308875940-1` and proposal
`proposal-deploy-restart-29308875940-1` seeded
`completed_outbox=completed` and `recovery_outbox=pending`; after restart the
result was `completed_outbox=replayed` and `recovery_outbox=recovered`. Three
transient 502 responses recovered, and all workflow steps passed.

The final accepted restore is Pantheon run
[`29315706536`](https://github.com/ajoe734/pantheon/actions/runs/29315706536),
job `87030684634`. Exact input ref, checkout, resolved SHA, deploy `--sha`,
managed `dev-bff` worktree, BFF source verification, and deployment-complete
marker all agreed on merged `dev` SHA
`9d393816acfe322a12ba1b295218f829db36ac28`. Public BFF smoke passed;
`/bff/version` returned that exact SHA with `source_commit_known=true`, and
`/healthz` returned HTTP 200 with live/ready runtime-manager, governance, and
deployment dependencies.

Its extended restart gate used workshop `deploy-restart-29315706536-1` and
proposal `proposal-deploy-restart-29315706536-1`. It seeded
`completed_outbox=completed` and `recovery_outbox=pending`; after a fresh BFF
process, the result was `completed_outbox=replayed` and
`recovery_outbox=recovered`. Four expected transient HTTP 502 responses
recovered, and the job completed successfully. The OpenClaw step was correctly
skipped for `component=bff`; the immediately preceding full task deployment
`29314870187` passed its OpenClaw live smoke.

## Frontend deployment and hosted browser proof

execute-plans deploy run
[`29316287074`](https://github.com/ajoe734/execute-plans/actions/runs/29316287074),
job `87030975673`, artifact `pantheon-dev-fe-deploy-evidence` ID `8304151693`
with digest
`sha256:33ca223a7e513389b3df51da92d3feb0dc81f3a0fbb76d334fc380ee1e6fa29f`,
successfully checked out, built, installed, and served immutable frontend SHA
`b6a5bc9311941cf7333c5f738526868715533101`. Live `deployment.json` at
`20260714T075837Z` paired it with exact BFF SHA
`9d393816acfe322a12ba1b295218f829db36ac28` and recorded:

```text
VITE_BFF_MODE: live
VITE_BFF_FALLBACK: strict
VITE_BFF_REAL_WRITES: false
VITE_BFF_ALLOW_DEV_STUB_WRITES: false
VITE_BFF_EMBEDDED_BEARER_TOKEN: false
```

The deploy artifact records five intended browser requests and five responses,
zero failures, zero old-host hits, `/health` HTTP 200, and accepted tokenless
`/bff/me` HTTP 401. It also records no non-production Persona rows and no armed
or standby seed fallback. A fresh Content-Type-aware recursive read-only scan
verified 762 actual hosted JavaScript responses with zero HTTP failures and
zero literal `pantheon-dev-browser:viewer` or
`pantheon-dev-browser:operator` matches. It excluded 674 `.js`-looking strings
that the SPA host resolved to `text/html` fallback rather than miscounting
those HTTP 200 responses as JavaScript chunks. The structured result is
recorded in `PINT-010-R2-BUNDLE-SCAN.json` beside this evidence file.

The accepted deployment order is explicit: the BFF restore job completed at
`07:58:02Z`; the FE deploy recorded its exact pair at `07:58:37Z`; and the
final integration job began at `08:02:47Z`.

Against that exact hosted pair, the focused Persona suite passed both viewports
in final integration run `29316624607`:

```text
Chromium desktop: 5 passed
Pixel 5 / mobile Chromium: 5 passed
Total: 10 passed
```

The cases cover a named-Persona ask, red-team consultation with visible
disagreement, proposal modify and paper validation, viewer mutation denial
without revision change, audit readback with ETag, Trading Room context and
proposal linkage, Persona Trade Journal reflection, and a negative assertion
that no order, broker, capital, runtime, binding, or memory authority was
emitted.

The browser journey loads the real hosted asset bundle, but its deterministic
Persona mutations use a credentialed cross-origin fixture. It proves hosted UI
wiring and authority-negative requests, not durable hosted mutation by itself.
The real BFF operator/viewer and durable-readback proof is the governed-proposal
step from the same final run and exact-pair checkpoint; it is composed with,
but not conflated with, the fixture-backed browser proof.

## Final cross-repository integration gate

execute-plans run
[`29316624607`](https://github.com/ajoe734/execute-plans/actions/runs/29316624607),
job `87032047017`, was dispatched from exact frontend source
`b6a5bc9311941cf7333c5f738526868715533101` with both expected BFF SHA and
Pantheon contract ref fixed to
`9d393816acfe322a12ba1b295218f829db36ac28`. Its two opt-in hard steps passed:

| Task gate | Result |
| --- | --- |
| PINT hosted governed-proposal smoke | success, `08:13:33Z`-`08:14:24Z` |
| PINT hosted desktop/mobile E2E | success, `08:21:31Z`-`08:22:21Z`; 5 desktop + 5 mobile; zero retry/flaky |

At both task-step completion checkpoints, hosted `deployment.json` remained FE
`b6a5bc93...` plus BFF `9d393816...`, with live/strict and all three
write/stub/token flags false; live `/bff/version` independently returned exact
`9d393816...`. The proposal result was HTTP 401 `AUTH_REQUIRED` without auth,
HTTP 403 for viewer create and modify, and HTTP 201/200/200 for operator
create/modify/paper-validation. Its readback contained revisions `1,2,3`,
audit actions `create,modify,validate`, idempotent replay at revision 3,
`executionAuthority=none`, `downstreamExecutionAttempted=false`, and
`tokensRecorded=false`. The focused browser log is a clean `10 passed (48.2s)`
with no retry or flaky case.

Artifact `pantheon-integration-evidence` ID `8305250932`, digest
`sha256:401d7713c3be8b619fa1744fa1d0d6459854b4506c12ff349e79f5aed327b335`,
contains the structured proposal result, focused browser log, and terminal
aggregate. The workflow concluded `failure` only because `Aggregate release
gate` preserved four non-PINT failures: two lint errors in
`e2e/evochain009.spec.ts`, F01 startup/session coverage (6 of 8 runnable
passed), F13 Agora aggregate coverage (22 runnable passed and 8 expected
skipped among 32 matching cases), and overlay-focus handling. The broad
Playwright tail reported 169 expected, 42 skipped, 14 unexpected, and 1 flaky;
those results do not weaken either earlier opt-in hard-step success. Management
hosted acceptance passed.

After both PINT steps completed, automatic FE runs `29318299639` and
`29318474120` advanced the shared host to merged descendants `cb139ca8...` and
`16a8e330...` during the unrelated full-Playwright tail. The diff from
`b6a5bc93...` is limited to a narrow-responsive E2E spec and Human Inbox/BFF
management client files; it does not touch the PINT journey or Trading Room
production paths. The current descendant manifest still pairs exact merged
BFF `9d393816...`. This record uses the two timestamped PINT checkpoints, not
the later repository-wide tail, as immutable-pair task proof.

## Integration runs and known failures

- execute-plans deploys for #332 (`29301525217`) and #333 (`29302049975`)
  failed because a protected-route auth challenge was used as the health gate.
  PR #334 changed the required signal to positive `/health`; run `29302393447`
  passed.
- The deploy after unrelated PR #335 (`29302487605`) saw transient
  `/bff/version` unavailability. The #336 and #337 deploys subsequently passed.
- Pantheon runs `29302102989` and `29302498354` failed when `grep -q` under
  `pipefail` yielded SIGPIPE exit 141. PR #3605 made the probes consume complete
  input; `29303223159` and all later accepted restart runs passed.
- Integration runs `29302889967` and `29302946272` had the PINT cases green
  (4/4 desktop and 4/4 mobile), but unrelated aggregate failures, a stale
  generic bearer, and stale BFF SHA mean they are not exact-pair proof.
- Pantheon run `29305416126` was canceled while queued and ran no steps. Run
  `29305788872` succeeded for a task-containing branch target but was not the
  accepted final `dev` lineage.
- Integration run `29305376271` failed its unrelated aggregate with the PINT
  opt-in path skipped.
- Integration run `29308070725`, artifact ID `8301022546`, reached the live
  proposal API but expected Pack-D `UNAUTHENTICATED`; the API correctly returned
  `AUTH_REQUIRED`. PR #340 corrected the assertion.
- Delayed Pantheon run `29306967263` then deployed explicit SHA
  `183cba011d6993029b3e828dc85f13dd166f207c`, superseding the earlier live
  `7c2e...` state.
- Replacement integration run `29308481272`, artifact ID `8301203792`, failed
  its exact-SHA precondition: expected `7c2e...`, observed `183cba...`. It made
  no proposal mutation request, and desktop/mobile PINT E2E was skipped. This
  was environment drift, not a recurrence of the Pack-D assertion bug.
- Auto FE run `29308447944` was canceled, and later successful run
  `29308549121` superseded that hosted manifest.
- Integration run `29311323207`, job `87015494638`, artifact ID `8303065628`,
  passed its real proposal gate against BFF `4a27eb31...`. An automatic FE
  deploy then changed the live bundle from declared `544efc89...` to
  descendant `6bea2d28...` before the browser step. The browser step finished
  `9 passed, 1 flaky` after one successful mobile retry, while five unrelated
  aggregate checks failed or were incomplete. Because no one immutable FE
  identity spans both task steps, this run is supporting evidence only.
- Replacement run `29312299695`, artifact ID `8302695077`, was intentionally
  canceled after its exact guard observed manifest BFF `4a27eb31...` but live
  BFF `2c336434...`. Both PINT steps were skipped; that means not executed, not
  zero passing cases.
- Integration run `29313952469`, artifact ID `8304212000`, established a clean
  intermediate exact-pair window: real proposal proof passed and its focused
  browser result was `10 passed` with no retry. Unrelated run `29313232772`
  switched live BFF to unmerged `c49edeb3...` only after both PINT steps, then
  failed its own dirty-worktree probe and skipped public/restart smokes. It was
  not accepted as final.
- Follow-up task-branch deploy `29314870187` repaired that probe, but current
  merged-lineage acceptance was restored by `component=bff` run `29315706536`.
  FE run `29316287074` and final integration run `29316624607` then established
  the current merged pair and both task-specific hosted gates. The latter run
  ended with only the unrelated aggregate failure detailed above; its two PINT
  steps and immutable artifact remained successful.

## Explicit non-claims

- No live-capital, order, broker, capital-binding, runtime-binding, or memory
  mutation was attempted or authorized.
- No hosted smoke approved a proposal. Exact binding and distinct-approver
  behavior are regression/contract proof, not a claim of a production-like dev
  approver credential.
- The dev BFF reports permissive stub auth. Strict-live frontend transport with
  no embedded bearer is not a claim that dev uses production OIDC/JWT posture.
- Fixture-backed hosted UI evidence and real hosted BFF mutation evidence are
  composed but never conflated.
- A green task-specific PINT path does not erase unrelated repository-wide
  integration failures.

## Remaining gates before review handoff

1. Commit and merge this final task-scoped evidence through a Pantheon PR, then
   hand off to Claude.
2. Only distinct reviewer approval and `review_approved` permit the owner to
   run the closeout skill and mark the task `done`.
