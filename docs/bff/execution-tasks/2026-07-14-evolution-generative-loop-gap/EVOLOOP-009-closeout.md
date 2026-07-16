# EVOLOOP-009 dev deploy + packet closeout

Status: **blocked pre-closeout — do not mark `done`**

Initial evidence captured: 2026-07-15 13:16-13:25 UTC
Latest recheck: 2026-07-16 01:35 UTC

Owner: Codex2
Reviewer: Claude

## Outcome

All EVOLOOP implementation and follow-up PRs listed below are merged into
`dev`, and the currently hosted APIs contain a real executed mutation chain,
a formal Persona Fleet mutation reference, and an active artifact-v2 binding.
The task is nevertheless **not closeable**. The 2026-07-16 recheck resolved
the original missing secret floor (`DEV_BFF_JWT_SECRET`,
`DEV_BFF_OIDC_CLIENT_ID`, `DEV_BFF_OIDC_CLIENT_SECRET`, and
`DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN` are present) and merged the runtime
dependency fix needed by `policy-learning-svc`. The remaining blockers are:

1. latest `dev` root deploy at merge commit
   `f43e10a3d288ca19aa6651b0d73aa3d44f1289db` could not complete because an
   active local deploy guard cancels all Pantheon nonprod deploy runs except
   allowlisted run `29464403186`;
2. the allowlisted run targets `publish/v2026.07.15.2`
   (`be105af5f1b381f518d767efb9f72813139d5077`), which is not latest `dev`
   and does not contain `services/policy-learning/requirements.txt`
   `jsonschema`;
3. hosted BFF/FE/browser/telemetry gates have not been re-proven against the
   latest `dev` merge commit;
4. the current execute-plans integration gate remains red in the prior
   evidence, and the governed browser session could not render the focused
   Persona Fleet row;
5. the active artifact-v2 runtime readback in prior evidence did not contain
   numeric PnL or drawdown, so packet-level telemetry/threshold acceptance is
   not proven;
6. the UI still lacks accepted hosted evidence visibly tying the active
   binding identity to its artifact id/version.

Per the babysit rule, a green historical workflow or API-only evidence is not
a substitute for the missing deploy and browser readback. No `done` transition
was attempted.

Machine-readable excerpts are archived in
`docs/04/pantheon_evolution_generative_loop_gap_2026-07-14/archive/EVOLOOP-009-live-evidence.json`.
The adjacent `EVOLOOP-009-live-evidence.sha256` manifest covers that JSON and
the hosted-browser failure screenshot.

The 2026-07-16 deploy-guard recheck is archived separately in
`docs/04/pantheon_evolution_generative_loop_gap_2026-07-14/archive/EVOLOOP-009-20260716-deploy-guard-evidence.json`.

## Integrated PR ledger

Every row was re-read from GitHub on 2026-07-15 and reported `MERGED` into
`dev`.

| Scope | PR | Merge SHA |
|---|---:|---|
| Packet/spec/dispatcher | [#3606](https://github.com/ajoe734/pantheon/pull/3606) | `ed848d146c656ef6712b3a52ebd0a6ad71a837ea` |
| Connector/convergence addendum | [#3609](https://github.com/ajoe734/pantheon/pull/3609) | `93fae4ee07259f159bc03ec1edc2881eae9df667` |
| EVOLOOP-001 implementation | [#3618](https://github.com/ajoe734/pantheon/pull/3618) | `9902229220335ed91b20c6bc82b5e1df1fc7f8d8` |
| EVOLOOP-001 closeout | [#3643](https://github.com/ajoe734/pantheon/pull/3643) | `c90ca73c6c6a8696e5339101406b73a4c148551f` |
| EVOLOOP-002 implementation | [#3622](https://github.com/ajoe734/pantheon/pull/3622) | `9f292de3a627b72441a12b478ef307119fa2c9ba` |
| EVOLOOP-002 closeout | [#3628](https://github.com/ajoe734/pantheon/pull/3628) | `9d393816acfe322a12ba1b295218f829db36ac28` |
| EVOLOOP-003 | [#3623](https://github.com/ajoe734/pantheon/pull/3623) | `2708d731fa2a36c186ada68c2fef9f37e877d90b` |
| EVOLOOP-004 implementation | [#3649](https://github.com/ajoe734/pantheon/pull/3649) | `44e156db71246be96dc26dd61c60ad4324e8a62d` |
| EVOLOOP-004 closeout | [#3651](https://github.com/ajoe734/pantheon/pull/3651) | `f7d79ac02af7881793ab7e6dfe8e3e0a86f1a106` |
| EVOLOOP-005 implementation | [#3641](https://github.com/ajoe734/pantheon/pull/3641) | `ecf7c1573914a8982ff2917e247986781cc33f06` |
| EVOLOOP-005 reviewer evidence | [#3645](https://github.com/ajoe734/pantheon/pull/3645) | `9d209bb4941fc3af6213b0329d9a1c282ce573a5` |
| EVOLOOP-006 implementation | [#3629](https://github.com/ajoe734/pantheon/pull/3629) | `1e9882f2a7ff08be51a0f93a2c647b818137fd2b` |
| EVOLOOP-006 live evidence | [#3633](https://github.com/ajoe734/pantheon/pull/3633) | `ff20e93d52fc66bf1dceffb85021c73c877d335a` |
| EVOLOOP-006 closeout | [#3660](https://github.com/ajoe734/pantheon/pull/3660) | `2fce4d2c3db74773a02076748a3656f6987393af` |
| EVOLOOP-007 implementation | [#3662](https://github.com/ajoe734/pantheon/pull/3662) | `f9bdac3fb02cb355374e1e7a45b6fbb545d440f7` |
| EVOLOOP-007 review remediation | [#3684](https://github.com/ajoe734/pantheon/pull/3684) | `2ecc5117c2ecf1d65b7173aed5d4fd36a5565394` |
| EVOLOOP-008 verifier | [#3669](https://github.com/ajoe734/pantheon/pull/3669) | `5705a697fcacc4a6bd6a44843057c0a8f61c12d9` |
| EVOLOOP-008 hosted-flow implementation | [#3670](https://github.com/ajoe734/pantheon/pull/3670) | `6d5bc6184de17adc56224fbff33c9eab29dfec7f` |
| EVOLOOP-008 review remediation | [#3686](https://github.com/ajoe734/pantheon/pull/3686) | `c6bb48fcb8627ca76577e96aedd9afbb00d349d6` |
| EVOLOOP-010 | [#3647](https://github.com/ajoe734/pantheon/pull/3647) | `503d8da96aa581ec0b2cd253c4fea3e90309819d` |
| EVOLOOP-011 | [#3663](https://github.com/ajoe734/pantheon/pull/3663) | `0a244c48651055af7889eeae5ddabbba316be326` |
| EVOLOOP-009 dependency/dispatch recheck | [#3728](https://github.com/ajoe734/pantheon/pull/3728) | `f43e10a3d288ca19aa6651b0d73aa3d44f1289db` |

## Deployment readback

| Check | Result |
|---|---|
| Pantheon target used for this attempt | `4c96fe9edc93954afe6be0427b2cfe5f7d2491c5`; Branch CI run [29417978057](https://github.com/ajoe734/pantheon/actions/runs/29417978057) succeeded and the SHA contains every final EVOLOOP merge above. |
| Root deploy attempt | [29418966102](https://github.com/ajoe734/pantheon/actions/runs/29418966102), `component=root`, failed before SSH because strict adapter service auth requires a human-provisioned `DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN`. No dirty or bypass flag was used. |
| Workflow control | `Pantheon Nonprod Deploy` was already `disabled_manually`; it was enabled only long enough to queue the single fixed-SHA run and immediately restored to `disabled_manually`. |
| Hosted BFF identity | `a10f752b3ea4420f271535e255f2d4e7d3d498b2`; `auth_stub=true`, `auth_mode=permissive`, `mfa_required=false`. The successful run [29416809292](https://github.com/ajoe734/pantheon/actions/runs/29416809292) was BFF-only and did not run the evolution dispatch probe. |
| Hosted FE identity | `b352faa087e6e1bd6087c619d6e9d99a35fbca41`; manifest points to BFF `a10f752...`. Safe write defaults are false, but the manifest has no accepted-candidate/gate identity. |
| Current execute-plans `dev` | `b8167c47a7f33fa7daf5a42f19f623e006520e8b`; integration run [29416149529](https://github.com/ajoe734/execute-plans/actions/runs/29416149529) failed Gate 4 browser responses, Gate 5 F13/F16, Gate 6 focus, and Gate 7 release decision. No candidate was accepted for deployment. |
| 2026-07-16 strict secret floor | GitHub environment `dev` contains `DEV_BFF_JWT_SECRET`, `DEV_BFF_OIDC_CLIENT_ID`, `DEV_BFF_OIDC_CLIENT_SECRET`, and `DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN`; strict deploy runs pass the auth/secret floor. |
| 2026-07-16 dependency fix | Strict deploy run [29462890670](https://github.com/ajoe734/pantheon/actions/runs/29462890670) passed the secret floor but failed in VM deploy because `policy-learning-svc` missed `jsonschema`. PR [#3727](https://github.com/ajoe734/pantheon/pull/3727) added the missing dependency on `dev`; PR [#3728](https://github.com/ajoe734/pantheon/pull/3728) merged bounded alignment and this task handoff. |
| 2026-07-16 latest-dev deploy attempt | Runs [29464312537](https://github.com/ajoe734/pantheon/actions/runs/29464312537) and [29464359888](https://github.com/ajoe734/pantheon/actions/runs/29464359888), targeting `55b2202bb...` and then `f43e10a3d...`, were canceled before deploy. A local deploy guard process was observed canceling every Pantheon deploy run except allowlisted run `29464403186`, while restoring workflow `269991390` to `disabled_manually`. |
| 2026-07-16 allowlisted deploy | [29464403186](https://github.com/ajoe734/pantheon/actions/runs/29464403186) succeeded but targets `publish/v2026.07.15.2` / `be105af5...`, not latest `dev`; `f43e10a3d...` is not an ancestor of that publish SHA, that publish SHA lacks `jsonschema` in `services/policy-learning/requirements.txt`, and the evolution dispatch, canonical lifecycle, and OpenClaw assistant probes were skipped. It cannot satisfy EVOLOOP-009 closeout. |

The most recent successful root deployment (`29390952944`, SHA
`1fef00eb7f23da05fd964087db85426863331540`) predates the final EVOLOOP-007,
EVOLOOP-008 and EVOLOOP-010 merges, so it cannot satisfy this task.

## Hosted API evidence that does pass

All reads below used the hosted BFF over HTTPS between 13:16 and 13:23 UTC.
The token was used only for GET requests.

### Executed decision and linked formal entry

The filtered Evolution Journal returned both a `mutation_review` and an
`evolution_decision` for:

`evo-pm-candidate_artifact-paper-artifact-persona-20260528-597cbad2-v2-v2-incident_cluster-inc-threshold`

Both entries are `executed` and target
`paper-artifact-persona-20260528-597cbad2-v2-v2@1.2.0`. The formal review
contains:

- incident `inc-threshold-e179c81b1c30`;
- postmortem `pm-inc-threshold-e179c81b1c30`;
- approval `apv-evoloop008-1784119062`;
- reviewed by `reviewer-e2e-008`, approved by `approver-e2e-008`, executed by
  `evolution-dispatch-worker` at `2026-07-15T12:37:58Z`;
- execution result `submitted` to plane `research` with dispatch ref
  `dispatch-evo-pm-candidate_artifact-paper-artifact-persona-20260528-597cbad2-v2-v2-incident_cluster-inc-threshold`.

### Persona Fleet formal mutation link

`persona-20260528-597cbad2` (`US-Macro-Hedger`) reports:

- `last_mutation_kind=formal_mutation`;
- `mutation_confidence=formal`;
- the same non-null mutation/evolution entry id;
- href `/management/evolution-journal?persona=persona-20260528-597cbad2&mutation_review=...`;
- no mutation diagnostics.

### Active promoted binding

The runtime readback returns HTTP 200 for:

- runtime `rt-9566ba8a`;
- binding `rb-63a0108368eb4bee8e547122ca9f3c02`;
- artifact `paper-artifact-persona-20260528-597cbad2-v2-v2@1.2.0`;
- plan `plan-redeploy-evoloop008-1784078455`;
- state `active`;
- deployment saga `deployment-saga-plan-redeploy-evoloop008-1784078455` and
  outbox event `deployment-saga-plan-redeploy-evoloop008-1784078455-evt-0001`.

This proves the service read model has the v2 identity. It does not prove the
hosted UI visibly associates that identity with the binding.

## Failed acceptance evidence

### Telemetry/threshold closure

`GET /api/v1/telemetry/rt-9566ba8a/summary` returns HTTP 200 with current
heartbeat/binding lineage but no `pnl`, `pnl_at`, `drawdown`, or `drawdown_at`.
The Persona Fleet performance summary for the same persona remains `pnl=0.0`,
`max_drawdown=null`. A second artifact-v2 runtime (`rt-8fc8939f`) exposes a
drawdown snapshot but still no PnL, and artifact performance returns 404.

Therefore the packet-level claims of moving mark-to-market PnL, paired
drawdown, and governed `rolling_pnl_floor` activation are not accepted here.

### Hosted browser/session and artifact visibility

Focused live test:

```sh
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
PANTHEON_PERSONA_FLEET_AUDIT_ID=persona-20260528-597cbad2 \
./node_modules/.bin/playwright test \
  e2e/25-persona-fleet-live-linked-pages.spec.ts \
  --project=chromium --reporter=line
```

The direct API preflight succeeded, but the browser session rendered
`STRICT TYPED ERROR strict: Failed to fetch · seed fallback blocked`, zero
formal rows, and could not find the focused persona. The failure screenshot is
archived as `EVOLOOP-009-persona-fleet-browser-failure.png`.

The current Runtimes UI exposes runtime/binding/persona fields but not the
artifact id/version; the Deployment UI exposes artifact/version without a
clear active runtime-binding association. The required visible promoted
binding proof is therefore absent.

### Mutation detail degradation

The journal list and evolution-decision detail are available, but
`GET /api/v1/operator/mutation-review/{decision_id}` returns HTTP 503 with
`MUTATION_REVIEW_UNAVAILABLE`. This is a separate degraded detail surface and
must not be hidden by the list-level success.

## Blocker and residual-risk register

| ID | Blocker / risk | Owner | Expiry / recheck |
|---|---|---|---|
| `EVOLOOP-009-B1` | Original secret floor resolved on 2026-07-16: required dev secrets are present and strict deploy runs pass the auth/secret floor. Keep `DEV_OPENCLAW_ADAPTER_SERVICE_AUTH_REQUIRED` strict. | Human/Ops | resolved; recheck on next deploy |
| `EVOLOOP-009-B2` | Clear or retarget the active deploy guard so a single latest `dev` strict deploy can run at `f43e10a3d...` or newer with evolution and canonical probes enabled. Do not accept the current allowlisted `publish/v2026.07.15.2` run as EVOLOOP-009 evidence. | Human/Ops + deploy owner | 2026-07-16 12:00 UTC |
| `EVOLOOP-009-B3` | Hosted BFF strict-auth/MFA posture must be re-proven after the latest `dev` deploy; synthetic source tokens must stop reading privileged routes. | Human/Ops + Codex | 2026-07-16 12:00 UTC |
| `EVOLOOP-009-B4` | Repair and rerun execute-plans Gate 4/5/6/7; only deploy an immutable candidate from the successful exact-SHA gate. | Gate owners: Gemini, Codex, Codex2 | 2026-07-17 12:00 UTC |
| `EVOLOOP-009-B5` | Make the hosted browser governed session load Persona Fleet and Evolution Journal, and visibly tie active binding id to artifact id/version. | execute-plans owner (Codex) | 2026-07-17 12:00 UTC |
| `EVOLOOP-009-B6` | Produce hosted numeric, moving PnL + drawdown with field timestamps and complete governed `rolling_pnl_floor` activation, or explicitly reopen EVOLOOP-002/005. | Antigravity | 2026-07-17 12:00 UTC |
| `EVOLOOP-009-B7` | Restore mutation-review detail availability and recheck the exact decision id. | BFF owner (Codex) | 2026-07-17 12:00 UTC |

## Resume gate

The reviewer should not approve EVOLOOP-009 until all of the following are
simultaneously true:

1. backend root deploy succeeds at an exact current `dev` SHA under strict
   auth and every loop service/probe is green;
2. hosted `/bff/version` and FE `/deployment.json` identify the accepted
   backend/frontend commits and release gate;
3. the exact execute-plans integration gate is green and its candidate is the
   one served by the hosted symlink;
4. governed browser evidence shows the executed journal chain, formal Persona
   Fleet link, and active binding artifact v2 identity;
5. live curl proves paired PnL/drawdown telemetry and the governed threshold
   state; and
6. residual items retain an owner and expiry.

Only then should the normal `review -> review_approved -> done` closeout flow
resume.
