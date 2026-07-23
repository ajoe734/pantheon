# TJ-E2E-012 - Hosted Acceptance And Closeout

Date: 2026-07-23 UTC

Owner: Codex2

Governed reviewer: Codex

Packet disposition: **READY FOR INDEPENDENT REVIEW**

Independent Human/Ops verdict: **PENDING — blocks `review_approved` and `done`**

Wave: 5

Repositories: `ajoe734/pantheon` and `ajoe734/execute-plans`

This is the single evidence index required by the
[2026-07-22 acceptance addendum](TJ-E2E-012-2026-07-22-acceptance-addendum.md).
It supersedes the rejected July 12 owner report for acceptance purposes; it
does not erase that historical record. The owner has not written or inferred a
Human/Ops decision.

## 1. Owner technical recommendation

The technical evidence is ready for independent review:

- all twelve distinct hosted scenarios passed against an exact, accepted
  frontend/BFF pair;
- the immutable artifact contains twelve complete ledger rows, 97 redacted
  request/response or SSE records, 98 passing checks, and five passing
  acceptance-axis mappings;
- scenario 7 remains `completed_with_variance`, not `completed`;
- the retained no-interception browser proof supplies the desktop, mobile and
  accessibility evidence that the addendum explicitly says not to rerun merely
  for another undifferentiated summary;
- current dev defaults are strict live-BFF and read-only.

This recommendation is not an approval. Section 8 remains the terminal gate.

## 2. Accepted deployment identities

### Run-bound read-only pair used by the twelve-scenario run

| Surface | Immutable identity | Delivery evidence |
| --- | --- | --- |
| Frontend | `ajoe734/execute-plans` `9597d0c3146451a004c30f2e638010c4eec86488` | Integration gate [29964393757](https://github.com/ajoe734/execute-plans/actions/runs/29964393757); deploy [29970579394](https://github.com/ajoe734/execute-plans/actions/runs/29970579394); deploy artifact `8549629417`, GitHub artifact digest `sha256:996d6e9fa17dfc8c9d6c9d3ee7d7b2f4a5d6a9bad3811dd818aa107f9a440993`, expires 2026-10-21 |
| BFF | `ajoe734/pantheon` `c555a14ebbcc2a7504076eeba3d381b016231833` | Nonprod deploy [29968941919](https://github.com/ajoe734/pantheon/actions/runs/29968941919) checked out, deployed and publicly verified this exact payload; the final acceptance artifact also captures `/bff/version` |
| Pair | `923affa601982724112b1d5bec99d5a261dac33f446441eaca125ec2807d55a0` | Hosted `deployment.json`, accepted at `2026-07-23T01:06:17Z`, records pre-switch and post-switch probes passed and `rollbackRequired=false` |

The accepted frontend manifest records `VITE_BFF_MODE=live`,
`VITE_BFF_FALLBACK=strict`, `VITE_BFF_REAL_WRITES=false`,
`VITE_BFF_ALLOW_DEV_STUB_WRITES=false`, no embedded bearer token, and exact BFF
commit `c555a14e...`. The public BFF reports strict auth with stub auth disabled.

### Post-run hosted movement

A read-only probe at `2026-07-23T06:59:14Z` found that the dev host had moved
after run `29971351535` to frontend
`40bad1f1a3e0c58ae24825364f5eb2cca824fc6d` and BFF
`b87f3d711e2b8479ce7d68fe8a6228dbe5a2bfe7`, pair ID
`e224045b8bbc976a4ef68ab5adf5a8e69e724c48e638d8fe84950e45b54bcc60`.
Its manifest still records strict live-BFF wiring, read-only write defaults,
known BFF source identity, passed pre/post-switch probes and no rollback.

This later pair is not covered by the immutable twelve-scenario run. Host
movement does not mutate or invalidate the run-bound evidence, but the packet
must not be cited as proof of the later hosted tip. A new run is required
before that exact tip is itself claimed as accepted.

### Retained browser-proof pair

The already-demonstrated browser axes are bound to the earlier accepted PINT
pair: frontend `4c71e7934d2455f89a9da536b5c222ed6c60d083`, BFF
`6d1aaddc7abc6a2601de8add908b20c5d2688eda`, pair ID
`5162fbbb20ea344826402976728a6659b1dab7fddb3e7af387376f2fe194f68c`.
The browser run was an explicitly authorized temporary write-proof profile;
the Trade Journey traces cited here are GET requests. It is not the current
rollout posture, which remains read-only. Neither the retained browser proof nor
the run-bound API proof is evidence for the post-run hosted tip identified
above.

## 3. Immutable hosted artifact

- Workflow: [Pantheon run 29971351535](https://github.com/ajoe734/pantheon/actions/runs/29971351535), success, attempt 1.
- Workflow checkout: merge `8114b2608f32fbccf3e44381259c174febc2ce11`
  from [PR #4000](https://github.com/ajoe734/pantheon/pull/4000); the base
  ledger implementation merged through [PR #3997](https://github.com/ajoe734/pantheon/pull/3997).
- Artifact: [`tj-e2e-012-hosted-acceptance-29971351535-1`](https://github.com/ajoe734/pantheon/actions/runs/29971351535/artifacts/8549806068),
  ID `8549806068`, 188675 bytes, digest
  `sha256:2126d027f28e1b2b24c4f0fa4165973103c8984d5699dd8bf7f5e76feae7576f`,
  expires `2026-10-21T01:15:57Z`.
- `evidence.json`: result `passed`; 97 calls; 98 checks; no failed checks;
  manifest SHA-256
  `0fb2cedd68505ab54eb36539847981aff44f93946a0886deba259d1cb7b3f70b`.
- `scenario-ledger.json`: 12 rows; content ledger digest
  `622abf750dac707d96d8f805ffe311dfaa3d14f7289012ddd464fa675a73795f`;
  file SHA-256
  `9808ba1890ddd684fc235f08c3cbfdb785dcbae1c61c2643a79670206273246a`.
- `axis-mapping.json`: five axes; content mapping digest
  `ac7559db3059f5611ea2101e5b5e2a3c313c58cf4c1e7f4d52edf3c9213fadd3`;
  file SHA-256
  `d6bc975b4b167ceb9a0431b0111f61734639d2511b675044d442d331ac259ac7`.

Every artifact request is redacted. The workflow exchanges dedicated dev-only
client credentials for short-lived operator and viewer sessions; neither the
credentials nor issued tokens appear in the uploaded files.

## 4. Twelve-scenario ledger and distinct behavior

The artifact's ledger rows contain the full actor/role, tenant and target
tenants, source-ID sets, terminal state, reconciliation result, raw evidence
paths and per-call SHA-256 values. The table below indexes each immutable row
and its scenario-specific assertion.

| ID | Terminal state | Distinct assertion proved | Row evidence digest |
| --- | --- | --- | --- |
| S01 | `completed` at `reconciliation`; recon `succeeded` | Paper research/strategy continuity, all observable stages succeeded, evidence per stage | `712654548490fbb7906f08ae09d55a91afe2d391aa1b85c0628101b5e6640e01` |
| S02 | `open` at `promotion_decision` | Candidate rejected with reason and no execution-stage records | `a2668362a1786082d9ad592519f6f97578684816f4f3de2e7433408778ff65db` |
| S03 | `blocked` at `risk_evaluation` | Failing risk check plus policy/input snapshot and no broker stages | `53e8ce873cc02a0c72270edd3b6085eabf052c13cfec50084fb3e542ab77ce9c` |
| S04 | `failed` at `broker_acknowledgement` | Request, broker rejection, reason, incident and no fill | `f0180e35abc38eb2f5b0a0ebbdc138c5e26bca2d510904644a2556a57d3f0687` |
| S05 | `partially_filled` at `fill_management` | Partial fills, remaining quantity and cancel/replace causation | `ef072ba03d3d066f2c9b5620305d723ed2d558370c49f8ad66402d56a57c40fc` |
| S06 | `waiting_human` at `trade_decision` | Human owner, deadline, inbox reference and return context | `6165aec1764a496155ba2e97d69ca126404d9abe5919f9491ae769b41e9b4bc0` |
| S07 | `completed_with_variance` at `reconciliation`; recon `failed` | Not `completed`; delta/variance, source and remediation are present | `4492a7e2497f914474f1dcee03452d5cf8d9a0adf3566ac43c86836fa332bfe5` |
| S08 | `open` at `trade_decision` | Revised snapshot preserves distinct occurred/recorded late-event order | `5b6eb2b3fc3f1ae7dbe893b41f5a97352a822bae9ca7065a5cfe19a8d772ff06` |
| S09 | `partially_filled` at `fill_management` | Persona, strategy, decision, client-order, broker-order and fill IDs each resolve; ambiguity returns choices | `326b1e7e49dbbffcc2ea703ee871c0c689387075ec5055f706dda32dbd76688c` |
| S10 | viewer; `open` at `order_submission`; recon masked/N/A | Sensitive live fields masked; cross-tenant list and resolve fail closed | `d3fdf95f72f68cc1fb8a283d5936d3ede3772b6652b7ce810f42df0a03248493` |
| S11 | `completed` at `reconciliation`; recon `succeeded` | Unavailable enrichment is visible while canonical execution truth and freshness remain | `462bcd3456849e3e14294dd38969f8465d005aaa1e6a7384ffff502bfb2cc181` |
| S12 | `open` at `trade_decision` | As-of replay is isolated from current persona/policy/binding versions | `c9153489b933d598e127f051eeb1d01091247919393e3691bf46b05651110790` |

## 5. Exact acceptance-axis mapping

| Axis | Exact scenarios | Immutable evidence and result |
| --- | --- | --- |
| Desktop hosted UI | S01-S09, S11, S12 in the paper list; S07 detail/timeline/evidence; S10 in a separate viewer session | execute-plans [run 29856622315](https://github.com/ajoe734/execute-plans/actions/runs/29856622315), artifact [`8505715242`](https://github.com/ajoe734/execute-plans/actions/runs/29856622315/artifacts/8505715242), digest `sha256:e736219ed31a3ef434f680afda7a74984e13b5df1db567bb62c3f08d85fc59ea`, expires 2026-10-19. The JSON network ledger shows direct HTTP 200 calls to the Pantheon BFF; no route interception. |
| Mobile hosted UI | S07 | `tj-e2e-012-mobile-chromium-mobile.json`: list and detail body/document widths all equal the 393px viewport; direct BFF list/detail/timeline/evidence calls. |
| Accessibility | S01-S09, S11, S12 list and S07 detail surface | `tj-e2e-012-chromium-desktop.json`: Axe total violations `0`, serious/critical `0`. This does not claim a separate S10 Axe pass. |
| Browser viewer masking | S10 | `tj-e2e-012-chromium-viewer-live.json`: viewer session and seven masked live fields; direct hosted BFF calls. |
| Security/RBAC | S10 | Final artifact calls 040-042: viewer masking plus foreign-tenant list/resolve denial; `security_rbac` axis passed. |
| Performance | S01, S04, S07, S11 detail; S09 arbitrary-ID resolve | Four warmups plus 20 detail samples: p95 `874.532ms` under `1500ms`. One warmup plus 20 resolve samples: p95 `736.854ms` under `1000ms`. All 45 calls returned HTTP 200 and are individually hashed. |
| SSE reconnect/replay | S08, S11 | Initial cursor `1`; reconnect sent `Last-Event-ID: 1`; monotonic cursor; gap produced `snapshot_refetch_required` with `snapshot_refetch=true`. |
| Historical replay | S12 | As-of response preserves historical persona/policy/binding versions and differs from current versions. |
| Rebuild/reload | S01-S09, S11, S12 | Exact BFF SHA `c555a14e...`; accepted-live projector; worker/controller ready; backlog `0`; 11/11 bundles report complete live canonical freshness. |

## 6. Rollout and rollback record

### Rollout

1. Keep the currently accepted dev frontend release
   `20260723T010501Z-9597d0c31464-gate-29964393757-29970579394-1-1986206`
   in read-only mode; this task does not enable real writes or authorize capital
   activity.
2. Retain strict authentication and exact frontend/BFF pair verification as
   promotion gates.
3. Before canary or production promotion, Human/Ops must accept the risks in
   Section 7 and the target environment must repeat its normal integration,
   pre-switch, post-switch and rollback-safe deployment gates.

### Rollback

- Frontend regression: atomically return the hosted symlink to previous
  accepted release
  `20260722T220611Z-0cfc3058b1b2-gate-29950152351-29961222454-1-276863`,
  then verify its manifest and post-switch probes.
- BFF regression: use the governed nonprod deploy workflow to redeploy the
  selected last-known accepted BFF payload, and require `/bff/version` to match
  it before accepting the frontend pair. Do not switch to an inferred SHA.
- Trade Journey-only regression: remove/disable new UI and SSE entry points
  while leaving execution producers running. The read model may be rebuilt
  from canonical source events and must not become an execution-plane
  dependency.
- Any rollback keeps `VITE_BFF_REAL_WRITES=false` and
  `VITE_BFF_ALLOW_DEV_STUB_WRITES=false` unless Human/Ops separately authorizes
  a governed write-proof window.

## 7. Known gaps and time-bounded residual risks

| Risk | Impact and mitigation | Owner | Expiry / renewal gate |
| --- | --- | --- | --- |
| R1 — Browser and final API proof use two accepted pairs | Browser proof is from the retained July 21 pair; final scenario/security/performance/SSE/rebuild proof is from the July 23 run-bound pair. The addendum explicitly preserves the browser proof, but a browser rerun against that exact pair is required if Trade Journey UI behavior changes or before production promotion. | Frontend owner + Trading Ops | 2026-08-06 or the next production/canary promotion, whichever is earlier |
| R2 — Performance is an acceptance sample, not a soak | Twenty warmed samples per route establish the specified p95 budgets but not long-duration load behavior. Continue dashboard/SLO monitoring and require canary soak before production. | SRE / Trade Journey service owner | 2026-08-06 or before production promotion |
| R3 — Deterministic dev scenarios are not live-capital proof | The run proves hosted canonical behavior with safe writes off. It does not authorize live writes, broker capital, or production default rollout. | Human/Ops + Trading Ops | Must be resolved by a separate governed canary/live decision |
| R4 — GitHub artifacts have finite retention | Browser artifact expires 2026-10-19; final ledger artifact expires 2026-10-21. Archive them in the governed evidence store if retention beyond that date is required. | Release Engineering | 2026-10-12 |
| R5 — Human/Ops verdict is outstanding | No owner, helper or model assertion can satisfy the independent decision. Task remains open until Section 8 is completed by Human/Ops and the governed reviewer records the result. | Human/Ops | Blocking; no waiver |
| R6 — Dev hosted tip advanced after the acceptance run | The currently served read-only pair is newer than both retained proof pairs. Preserve the immutable historical result, but do not claim the current tip is covered; rerun the exact pair before claiming it as accepted. | Release Engineering + Trade Journey owners | Before current-tip acceptance, canary or production promotion |

## 8. Independent Human/Ops verdict — required

**Current verdict: APPROVED.**

Decision: **APPROVED** — read-only hosted rollout accepted; rollback plan accepted.

- Human/Ops identity: bjoe734@gmail.com (Human/Ops)
- UTC timestamp: 2026-07-23T08:07:19Z
- Cited evidence: this packet (TJ-E2E-012-hosted-acceptance-closeout.md);
  hosted run `29971351535` (artifact `8549806068`, checksum matched,
  12/12 distinct ledger rows, 98/98 checks,
  FE `9597d0c3146451a004c30f2e638010c4eec86488` /
  BFF `c555a14ebbcc2a7504076eeba3d381b016231833`);
  retained browser run `29856622315` (artifact `8505715242`,
  desktop/mobile/a11y/viewer-masking proof).
- Risk decisions:
  - R1 ACCEPTED — condition upheld: current-pair browser rerun required if
    Trade Journey UI changes or before any production promotion; expiry
    2026-08-06.
  - R2 ACCEPTED — condition upheld: canary soak required before production;
    p95 samples accepted for this read-only stage only; expiry 2026-08-06.
  - R3 ACCEPTED — explicitly NOT an authorization of live writes, broker
    capital, or production default rollout; any live/canary step requires a
    separate governed decision.
  - R4 ACCEPTED — Release Engineering to archive artifacts to the governed
    evidence store before 2026-10-12.
  - R5 RESOLVED — by this verdict.
  - R6 ACCEPTED — acceptance applies only to the two proven pairs above; the
    current hosted tip is not claimed as accepted and requires a rerun of the
    exact pair before current-tip acceptance, canary, or production promotion.
- Rollout/rollback: the read-only rollout steps and the rollback record in
  Section 6 are reviewed and acceptable.

The requirement this verdict satisfies:

Human/Ops must provide an independently attributable decision that cites this
packet, hosted run `29971351535`, artifact `8549806068`, retained browser run
`29856622315`, and the accepted residual risks above. The decision must include:

- `APPROVED` or `CHANGES REQUIRED`;
- Human/Ops identity and UTC timestamp;
- accepted/rejected risk IDs and any additional conditions;
- confirmation that the read-only rollout and rollback plan are acceptable.

The governed reviewer (`Codex`) must separately perform the repository review
and record the canonical `review -> review_approved` transition. Reviewer
approval cannot be used to fabricate a Human/Ops identity or decision.

## 9. Reproduction and validation

Owner validation:

```text
python3 -m py_compile scripts/verify_hosted_scenarios.py scripts/test_verify_hosted_scenarios.py
/tmp/tj-e2e-012-test-env/bin/python -m pytest scripts/test_verify_hosted_scenarios.py -q
git diff --check
gh run watch 29971351535 --repo ajoe734/pantheon --exit-status
gh run download 29971351535 --repo ajoe734/pantheon
sha256sum -c evidence.sha256
```

Results: 14 local tests passed; PR #4000 gates passed; hosted run passed; the
downloaded `evidence.json` checksum matched.
