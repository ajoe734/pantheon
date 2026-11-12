# Memory writeback wiring evidence

Task: `MEMORY-WRITEBACK-WIRING-20261112`. Owner: Codex. Reviewer: Claude.

## Source delivery

The `learning-feedback-bridge` compose worker polls the Evolution and Postmortem
owner APIs. Executed decisions require a terminal result and outcome; postmortems
require published findings. The worker uses the configured Memory service bearer
and records source/attribution/delivery failures in its report and logs. Source
records remain in the owner APIs and are replayed on the next poll.

Research starts its durable outbox drainer with the service. Delivery uses
`http://memory:8086/api/memory/writebacks/learn-feedback`, the configured service
credential, and the owning demand/member persona. Conflicting or missing persona
attribution is refused and recorded rather than replaced with a legacy persona.
Failed deliveries retain their reason and retry count; exhausted records remain
as dead letters and can be retried through the existing outbox route.

The Memory HTTP whitelist admits `persona-svc` for `pool_outcome` and
`harness_experience`, and `consultation-svc` for `consultation_closed`. These are
the Memory production admission endpoints for subsequent persona/panel writers;
their business trigger implementations remain owned by those downstream tasks.
The endpoint verifies the caller's writer role and tenant. Replays repair a
partially persisted persona/institutional pair and refuse tenant or persona
reassignment of an existing source event.

The existing strict JWT and Governance retrieval policy are retained from
`BFF-PERSONA-MEMORY-WIRING-20261112`. Tests exercise actual Memory, Governance,
Research completion/writeback endpoints and feedback polling, substituting
outbound HTTP only. Test events and test JWTs stay in tests.

## Runtime configuration

Both workers need an existing, valid Memory service credential provided as
`PANTHEON_MEMORY_AUTH_TOKEN`, or as `PANTHEON_MEMORY_AUTH_TOKEN_FILE` at a path
visible inside the container. A token file supplied by a deployment override must
also be mounted by that override. Resolution is repeated on delivery so a mounted
file can rotate. The token must satisfy the existing Memory verifier, issuer,
audience, expiry and tenant checks and carry the appropriate writer role (the
bridge needs both `incident-svc` and `evolution-svc`, or an existing shared
`memory-writer` principal). No issuer, credential or authorization grant is created
by this change. Missing credentials leave deliveries failed and retryable.

The bridge also consumes the existing `EVOLUTION_AUTH_TOKEN` and
`EVOLUTION_DEFAULT_TENANT_ID`. Its compose command skips OpenClaw synchronization;
canonical memory materialization remains owned by the persona-agent work.

## Hosted acceptance remains outstanding

On 2026-11-12, read-only `https://app.dev.mvl-cap.tw/deployment.json` returned an
accepted pair with FE `acf3efaeb9141e1abd80375c9c2a357328c02c79` and BFF source
`bee62bc2978618539984aff9db136b064adc14a0`. This predates this task's source
delivery and does not establish writeback acceptance.

This worker environment has neither `PANTHEON_MEMORY_AUTH_TOKEN[_FILE]` nor the
dev SSH transport settings. The configured Nonprod Deploy workflow is active;
that fact grants no additional hosted execution authority. No VM deployment,
credential issuance, synthetic hosted event or hosted PASS is recorded here.

After independent source review and canonical integration, use the existing
authorized exact-pair dev delivery flow and existing service credential. Hosted
acceptance must record the deployed SHA/image, worker running identity, naturally
occurring executed evolution and published postmortem event IDs, their single
persona/institutional receipts, demand/member-attributed gate finding delivery,
retry evidence, and operator/committee retrieval with cross-persona denial.
Record that evidence through `milestone hosted done` before terminal closeout.

Local validation results are in `evidence.json`; they prove source behavior only.

## Review admission recovery

PR #7262 was pushed, but canonical handoff rejected head
`0d75e27563cabb7b5d93cacb1f529cc8bbaccff0`: required Memory schema/store/writeback
files, Alpha writer files and the Postmortem regression test are absent from the
task artifact contract. The eight exact paths are listed in `evidence.json`.
Human/Ops admitted all eight existing paths through the governed
`artifact-contract` command and reopened the same task on 2026-11-12. The live
governed `show` confirms Codex ownership, Claude review, the original six acceptance
criteria and hosted `external_wait`. The historical rejection remains in
`evidence.json`; it is no longer a contract blocker.

The fetched `origin/dev` remains `8f0445559a19474d7e7da29ed3f175c0800df558`, already
an ancestor of this branch. Gate PR #7257 is still open at
`3b9273c7b7e08f6c47b76d56388eeb86becad9b2`; its separate mechanism input/support
changes have not entered `dev`. No other task's commits or trailers were copied.
There is currently no merge conflict. If Gate lands first, refresh from current
`dev`, preserve both owners' Alpha worker/test changes, rerun composed regressions
and freeze a new review head through the existing workflow.

The next owner handoff binds this committed manifest to the exact new PR head.
Independent Claude approval and native integration remain required. Source
functional validation is recorded separately from the outstanding hosted proof;
neither an open PR nor local tests establish hosted acceptance or terminal `done`.
