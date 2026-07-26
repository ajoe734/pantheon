# PPL-ALLOC-009 hosted closeout truth record — 2026-07-26

Status: reviewer approved; owner finalization publication pending

Task ID: `PPL-ALLOC-009`

Owner: Codex

Required final reviewer: Claude2

Real/live capital authority: disabled and out of scope

Machine-readable evidence index:
[`PPL-ALLOC-009-B1-B3-EVIDENCE-INDEX-2026-07-26.json`](./PPL-ALLOC-009-B1-B3-EVIDENCE-INDEX-2026-07-26.json)

## Final truth correction

The first version of this record, merged in
[Pantheon PR #4124](https://github.com/ajoe734/pantheon/pull/4124),
incorrectly marked B3 and B5 as passed. The second version, merged in
[Pantheon PR #4128](https://github.com/ajoe734/pantheon/pull/4128), correctly
recorded the then-current failed browser proof and left B3/B5 open.

Subsequent repairs were merged, deployed, and exercised. The final
non-diagnostic hosted acceptance
[run 30194836870](https://github.com/ajoe734/pantheon/actions/runs/30194836870)
completed successfully at `2026-07-26T08:36:22.092Z`. Its single active test
was the same-chain test named `correlates governed B1 and proves the same
identity on desktop and 393px mobile`; the read-only diagnostic test was
skipped. This run supersedes the earlier negative B1/B3 result.

Claude2 independently reviewed the final evidence and recorded B5 `ACCEPT` at
`2026-07-26T10:48:28Z`. The owner may use this record as `done` evidence only
after this finalization update merges into `dev`.

## Accepted exact pair

The successful run verified the accepted hosted pair before executing the
chain:

| Component | Exact identity |
| --- | --- |
| Frontend repository | `ajoe734/execute-plans` |
| Frontend commit | `6a8d2d9b4f725056735eefd7165ef47b52cda53d` |
| BFF repository | `ajoe734/pantheon` |
| BFF runtime commit | `be956c07aca889043ef301389412b6744452f20b` |
| Pair ID | `c05fc6b0abea92ceb1805cde8c2f3f4d7bcfab12fb77ac45be0a4241ea5874cf` |
| FE gate | [30192097967](https://github.com/ajoe734/execute-plans/actions/runs/30192097967) |
| Hosted acceptance | [30194836870](https://github.com/ajoe734/pantheon/actions/runs/30194836870) |
| Sanitized evidence artifact | [8629787902](https://github.com/ajoe734/pantheon/actions/runs/30194836870/artifacts/8629787902) |
| Artifact JSON SHA-256 | `8f13eb7c9632ae61c3e020db00cee54396f26b2a90f76485f2a9a487fa0c21df` |
| Artifact ZIP SHA-256 | `2968a40f3ae17434c1f57661005bb306117d9d5d4123645dfaa08efdf934bd3d` |

The accepted frontend manifest reported `deploymentState=accepted`,
`profile=read-only`, `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and all
real-write, dev-stub-write, and embedded-bearer flags `false`. The BFF reported
strict auth, MFA required, and the exact runtime commit above.

## B1 — correlated governed paper-only allocation chain

Gate result: **passed**.

The evidence contains one linked chain:

| Boundary | Authoritative identity / result |
| --- | --- |
| Tenant | `tenant-dev` |
| Persona | `persona-34ac77f34d030185079d` |
| Runtime | `rt-bc3d8bbd` |
| Runtime binding | `rb-b174499b69484ee7b207f6e437605cd9` |
| Current paper session | `prmon-rb-b174499b69484ee7b207f6e437605cd9-621fd2b0` |
| Telemetry eligibility event | `b07c14a3-c3e5-5004-85e1-8ec11879dbc6` |
| Ranking snapshot | `ranking-quarterly-2026-q3-507a8d36f7269e406f429d3f` |
| Promotion review | `pm12-2026-q3-persona-34ac77f34d030185079d-promote_to_canary_candidate--snapshot-221d3a44f5a0ac9bb6141685209e888b` |
| Paper allocation evaluation | `paper-allocation-evaluation-84beb9ef6a8067bcb85a1845` |
| Paper ledger | `paper-ledger-75b390797fd9861f92c6` |
| Capital binding | `pcb-persona-paper-75b390797fd9861f92c6` |
| Rebalance | `rb-20260725-3be1a73f7b06` |
| Approval decision | `approval-30095677466` |
| Apply command | `cmd-a39c04a4a90743d7a88056275f3f6b0e` |

The chain was not produced by fixtures or a direct store edit:

1. operator authentication returned `200`;
2. Persona/paper bundle creation returned `201`;
3. provisioning reconciliation and Persona readback returned `200`;
4. the guarded eligibility producer returned `202` and Telemetry owner
   accepted and read back the exact event;
5. canonical quarterly ranking and recommendation reads returned `200`;
6. promotion submission and the human promotion decision each returned `202`;
7. allocation evaluation returned `200`;
8. the existing idempotent rebalance proposal was recovered after the expected
   `409`, then read back from the owner;
9. a distinct approver created approval `approval-30095677466`;
10. the operator obtained a confirmation token and applied the rebalance;
11. command receipt readback returned `200`;
12. Capital owner readback returned `200` with
    `authoritativeCapitalReadback=true`.

The resumed owner record was `applied`, used
`persona-paper-allocation-simulation-v1`, and retained the same ranking,
allocation-evaluation, rebalance, approval, and apply identities. The
eligibility producer recorded telemetry owner status `accepted`,
`reconciliation=accepted`, and a matching readback event ID.

Identity separation was verified:

- approval actor: `pantheon-dev-approver`, class `approver`, MFA true;
- apply actor: `pantheon-dev-operator-a`, class `operator_a`, MFA true;
- `distinctApprovalAndApply=true`.

## B3 — exact-chain authenticated desktop and mobile proof

Gate result: **passed**.

Both browser sessions used GCP Identity Platform email/password sign-in;
`syntheticSession=false`.

| Viewport | Routes exercised | Result |
| --- | --- | --- |
| Desktop `1440x900` | Rankings, Governance recommendations, Capital | all expected BFF requests `200`; no console errors, page errors, request failures, serious/critical accessibility violations, or horizontal overflow |
| Mobile `393x852` | Rankings, Governance recommendations, Capital | all expected BFF requests `200`; no console errors, page errors, request failures, serious/critical accessibility violations, or horizontal overflow |

The browser routes were bound to the exact Persona, quarter, pool, and
rebalance IDs from B1. The artifact records 23 API request/response checkpoints
plus route-level network correlation evidence for both viewports.

## B5 — independent IA reviewer decision

Gate result: **passed**.

Claude2 accepts that the canonical Rankings, Governance-Decisions, and
Performance centers supersede the original PPL-ALLOC-006/007 primary-workbench
IA contract. No bounded UI task is reopened.

The reviewer verified the accepted frontend commit
`6a8d2d9b4f725056735eefd7165ef47b52cda53d`:

- `/management/promotion-allocation` mounts
  `ManagementCanonicalRedirect`, not a competing workbench;
- the route manifest maps `paper-candidates` to quarterly Rankings,
  `real-ranking` to rolling Rankings, capital and emergency/containment tabs
  to Governance-Decisions Capital, and formula policy to
  Governance-Decisions Policy;
- the retired promotion-allocation route is absent from sidebar navigation, so
  only the canonical workflow is primary.

The accepted supersession contract is
`docs/04/pantheon_management_performance_ranking_ia_gap_2026-07-11/archive/ROUTE_MIGRATION_MATRIX.md`.

## Safe restore and current posture

Immediately after acceptance, proof-off deployment
[run 30194930965](https://github.com/ajoe734/pantheon/actions/runs/30194930965)
completed successfully. It verified inside the deployed BFF container:

```text
PANTHEON_PPL_ALLOC_009_DEV_PROOF_ENABLED=false
```

The later deployment
[run 30195508721](https://github.com/ajoe734/pantheon/actions/runs/30195508721)
again completed successfully with the proof flag `false`, strict auth, exact
BFF version proof, lifecycle readiness, restart persistence, and the same
accepted FE/BFF pair. No rollback was required.

The final acceptance evidence asserts:

- `paperOnly=true`;
- `authoritativeCapitalReadback=true`;
- `canaryExecutionEnabled=false`;
- `liveCapitalSideEffects=false`;
- `realWritesEnabled=false`.

## Delivery inventory

### Pantheon

| PR | Merge commit | Purpose |
| --- | --- | --- |
| [#4056](https://github.com/ajoe734/pantheon/pull/4056) | `763c98b1761f6d56b379b3a07e48d5e18d3e8d16` | governed paper allocation path |
| [#4061](https://github.com/ajoe734/pantheon/pull/4061) | `99ece708c8e48c6889b224047a9f70a1179af7eb` | governed hosted acceptance workflow |
| [#4063](https://github.com/ajoe734/pantheon/pull/4063) | `33ee1d4b906a27ffc30a8c26dbfbb0bbfc4f0db0` | guarded paper eligibility producer |
| [#4079](https://github.com/ajoe734/pantheon/pull/4079) | `789f5e0865b4a87294def470f06079ae83baf07f` | retry-safe telemetry proof |
| [#4127](https://github.com/ajoe734/pantheon/pull/4127) | `72afd991b9133dc7e73c775978dc854e6d3877ce` | real browser identity inputs |
| [#4128](https://github.com/ajoe734/pantheon/pull/4128) | `5e5a3ff072c325173040704c495ea77aa2a36b4e` | prior truth correction |
| [#4130](https://github.com/ajoe734/pantheon/pull/4130) | `dab595770df46db98c7dd692b058704da81b5102` | strict lineage resume pair |
| [#4131](https://github.com/ajoe734/pantheon/pull/4131) | `8bdb85b6441536bd095426abb5c4226a1bd7828b` | applied-owner resume pair |
| [#4133](https://github.com/ajoe734/pantheon/pull/4133) | `f9851d0f4702776e77e5d6560150664ebd639116` | accessibility pair |
| [#4134](https://github.com/ajoe734/pantheon/pull/4134) | `d8c1a81756e5ad904d173c2ffeea83373d4f2d97` | browser navigation pair |
| [#4136](https://github.com/ajoe734/pantheon/pull/4136) | `ea25a615fe89e400f071c48a79f987a2b1b09a72` | isolated final browser pair |

### execute-plans

| PR | Merge commit | Purpose |
| --- | --- | --- |
| [#528](https://github.com/ajoe734/execute-plans/pull/528) | `9defed5029c4422bb5a0a4b4c79e2ad637eb4bca` | exact-pair acceptance harness |
| [#531](https://github.com/ajoe734/execute-plans/pull/531) | `7492ad7fd0b430df40dd7fe7b6b0d187d8742350` | immutable action repair |
| [#533](https://github.com/ajoe734/execute-plans/pull/533) | `6d74db4d1ffb327224080642dd9cdae5c5f9a017` | genuine eligibility wait |
| [#535](https://github.com/ajoe734/execute-plans/pull/535) | `ecb78bd9b647bbb4ffcbe83069aea38b642d0bb0` | canonical run identity |
| [#537](https://github.com/ajoe734/execute-plans/pull/537) | `861b02a5254c57f84bb31a4dee6d6532e37303fb` | retry-safe proof identity |
| [#544](https://github.com/ajoe734/execute-plans/pull/544) | `3bf97323f7c72bd47256c7a60618dd7f837cd592` | real GCP browser identity |
| [#547](https://github.com/ajoe734/execute-plans/pull/547) | `694ecdf28f90773f8c127b6038c8475b9d68a00b` | strict rebalance lineage resume |
| [#548](https://github.com/ajoe734/execute-plans/pull/548) | `69888404584d31caacf394b1e4c7dc99bb26191f` | approved/applied owner resume |
| [#550](https://github.com/ajoe734/execute-plans/pull/550) | `acd2d6610d01845cd361db3f36266c006ea47ce6` | ranking accessibility |
| [#551](https://github.com/ajoe734/execute-plans/pull/551) | `59844bab22006bcc16f5c18ef0543d7657b562a4` | login/navigation settling |
| [#552](https://github.com/ajoe734/execute-plans/pull/552) | `6a8d2d9b4f725056735eefd7165ef47b52cda53d` | isolated final browser proof |

## Gate disposition

| Gate | Current status | Remaining condition |
| --- | --- | --- |
| B1 | Passed | none |
| B2 | Passed | credentials remain masked; no reprovisioning required |
| B3 | Passed | none |
| B4 | Passed | merged delivery inventory recorded above |
| B5 | Passed | Claude2 accepted canonical IA supersession at `2026-07-26T10:48:28Z`; no UI task reopened |

## Residual risks

- The accepted frontend route manifest maps `emergency-actions` to
  `governance-decisions?tab=capital`, but the governing route matrix Primary
  Pages table does not list that mapping. Owner: MGMT-PERF-IA lane. Expiry:
  none assigned. Blocking: no; this is a documentation follow-up in the owning
  IA lane.
- GitHub retains the full sanitized artifact for 90 days. This repository
  preserves its content digest, exact linked IDs, request/response index,
  accepted-pair identity, safety posture, and browser verdicts so later
  artifact expiry cannot turn B1/B3 into an unsupported assertion. Owner:
  Codex. Expiry: artifact retention at 90 days. Blocking: no.
- The proof producer remains disabled by default and in the current hosted
  container. Any future use requires another explicit strict dev/root
  deployment with the bounded flag enabled. Owner: Human/Ops for any future
  authorization. Expiry: none. Blocking: no.
- The applied allocation is paper-ledger state only. It is not evidence of,
  and grants no authority for, canary/live/real-capital execution.
  Owner: Human/Ops. Expiry: none. Blocking: no.

## Completion checklist

- [x] PR and merge-SHA inventory
- [x] accepted deployment manifest identity
- [x] B1 linked evidence index
- [x] B3 desktop and 393px evidence index
- [x] proof-off restore and current safe posture
- [x] residual risks with ownership and blocking status
- [x] independent Claude2 B5 IA decision
- [x] reviewer approval transition
- [x] owner finalization artifact prepared and verified
- [ ] canonical `done` after this finalization PR merges
