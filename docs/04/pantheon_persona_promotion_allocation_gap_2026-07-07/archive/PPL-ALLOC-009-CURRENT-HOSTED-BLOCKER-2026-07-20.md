# PPL-ALLOC-009 Current Hosted Blocker Recheck - 2026-07-20

Status: **blocked; not ready for `review` or `done`**

Evidence captured through `2026-07-20T10:32:30Z`. The sanitized,
machine-readable companion is
`PPL-ALLOC-009-CURRENT-HOSTED-BLOCKER-2026-07-20.json`.

## Decision

The current Pantheon-owned frontend is an accepted, rollback-safe read-only
release and the hosted BFF is healthy, strict, and source-identifiable. Those
facts do not close this task.

The deployed BFF SHA predates both PPL-ALLOC-009 follow-up deliveries that add
distinct governed proof identities and atomic stage promotion. A strict deploy
of a ref containing both changes failed closed before SSH or GCP mutation
because five dedicated dev-login client secrets and the Management AI control
passphrase hash are not provisioned. The current frontend release gate therefore
skipped authenticated smoke, live-write probing, the PINT write proof, and the
PINT desktop/mobile journey. No current hosted evidence joins one governed
Persona through canonical quarterly ranking, Runtime/Telemetry, target weights,
proposal, distinct approval/apply, receipt, and authoritative Capital readback.

This recheck did not create credentials, mint proof identities, enable frontend
real writes, promote a Persona, approve capital, or issue a capital-affecting
command.

## Delivery Delta Since The 2026-07-15 Rescue

| Slice | PR | Task commit | Merge commit | Result |
| --- | --- | --- | --- | --- |
| Distinct governed dev identities and strict deploy floor | Pantheon [#3896](https://github.com/ajoe734/pantheon/pull/3896) | `f18f7ad224b9787e30ad594eb372bf9023dca0f4` | `a5093897b7bbe6877f8eede538dc7e18c501aea4` | Merged to `dev`; required CI checks passed. |
| Atomic paper-to-canary / canary-to-live promotion authority | Pantheon [#3897](https://github.com/ajoe734/pantheon/pull/3897) | `3b9336b0dc2929128b17b924921d63e0e5ed7911` | `41c725cbb348b270f5515a80bf38086e1ecce5f7` | Merged to `dev`; required CI checks passed. |

Both task commits are ancestors of failed deployment target
`d13c19025e7267c38b9e7b98e4f1b159501ddf2f` and current Pantheon `origin/dev`
`ce31dfcbca508fbaa02dcc6d0a5cc3e69c18e578`.

The child-task ledger and independently valid create-paper, human-decision,
governed Capital apply, and emergency-containment subchain evidence remain as
recorded in the 2026-07-14 and 2026-07-15 rechecks. They still cannot be joined
across different identities and snapshots to claim the required full path.

## Current Hosted Identity

| Surface | Current observation | Verdict |
| --- | --- | --- |
| Frontend manifest | Execute Plans `dceaaa50638a4ed69ca585e04348737d87ca78e3`, accepted at `2026-07-20T08:25:07Z`; deploy run [29727357250](https://github.com/ajoe734/execute-plans/actions/runs/29727357250) | Pass for the active read-only artifact. |
| Frontend build posture | `VITE_BFF_MODE=live`, strict fallback, real writes false, dev-stub writes false, embedded bearer false | Safe default; not write-proof evidence. |
| Frontend release profile | `deploymentProfile=read-only`; candidate/post-switch probes passed; rollback not required | Pass for read-only delivery only. |
| Frontend gate | Run [29726612084](https://github.com/ajoe734/execute-plans/actions/runs/29726612084) succeeded at the workflow level, but its release summary is `WARN` | Authenticated/write/PINT gaps remain. |
| BFF identity | `/bff/version` reports `93c50da6d67560f7035025879af08dfc3197fb76`, `source_commit_known=true` | Exact runtime identity is known and agrees with the FE manifest. |
| BFF posture | `auth_stub=false`, `auth_mode=strict`, `dev_login_enabled=true`, `mfa_required=false`, `assistant_kernel_enabled=true` | Strict anonymous boundary passes, but this older SHA lacks both 2026-07-20 PPL deliveries. |
| BFF health | `/healthz` and `/readyz` returned 200; Runtime Manager, Governance, and Deployment dependencies were healthy | Service readiness pass. |
| Anonymous protected routes | `/bff/me`, `/bff/auth/readiness`, `/bff/assistant/mode`, and canonical quarterly ranking returned governed 401 envelopes | Fail-closed pass; not authenticated acceptance. |

The deployed BFF SHA is not a descendant of either `f18f7ad22...` or
`3b9336b0d...`. Its `strict` label must not be used to claim that the new
distinct-actor or atomic-promotion contracts are hosted.

## Failed Strict Deploy And Credential Boundary

Pantheon run [29733822822](https://github.com/ajoe734/pantheon/actions/runs/29733822822)
targeted `d13c19025e7267c38b9e7b98e4f1b159501ddf2f` with the `strict` profile. It
failed in `Enforce dev auth deployment floor` before any remote deployment.

The `dev` GitHub environment currently contains these base secret names:

- `DEV_BFF_JWT_SECRET`
- `DEV_BFF_OIDC_CLIENT_ID`
- `DEV_BFF_OIDC_CLIENT_SECRET`
- `DEV_OPENCLAW_ADAPTER_SERVICE_TOKEN`

The failed run showed these required values absent:

- `DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET`
- `DEV_BFF_DEV_LOGIN_APPROVER_CLIENT_SECRET`
- `DEV_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET`
- `DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_SECRET`
- `DEV_BFF_DEV_LOGIN_OPERATOR_B_CLIENT_SECRET`
- `DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH`

The five client IDs have stable non-secret dev defaults in the workflow, but a
client ID without its dedicated secret is not a governed identity pair. The
worker is not authorized to invent, recover, print, rotate, or substitute any
of these values.

## Current Frontend Proof Status

Gate `29726612084` built read-only, operator-live, and write-proof candidate
artifacts, but only the read-only candidate became the hosted release.

- `Authorize parent-bound hosted write proof`: skipped.
- `Run authorized one-time Persona write proof`: skipped.
- authenticated BFF smoke: skipped because no short-lived auth path was
  available to the gate;
- live dry-run write probe: skipped;
- PINT source-pair capture: skipped;
- PINT hosted desktop/mobile E2E: skipped;
- hosted production acceptance logged that a short-lived `BFF_AUTH_TOKEN` was
  required;
- generic fixture-backed mobile Playwright passed its runnable cases, but its
  live BFF write cases were skipped and it is not the correlated PPL journey.

The active FE/BFF identity pair and read-only route probes are useful wiring
evidence. They do not satisfy the task's authenticated live-write, PINT,
desktop, or mobile acceptance.

## Acceptance Matrix

| Gap-spec acceptance | Current result |
| --- | --- |
| Hosted create produces an isolated `paper_running` bundle | Historical subchain evidence remains valid; current-SHA authenticated recheck blocked. |
| Fleet and Capital show distinct binding identities | Prior source/hosted evidence remains valid; current authenticated joined proof blocked. |
| Paper recommendation reaches governed human decision | Historical subchain evidence remains valid; not joined to the current promotion authority. |
| Canonical real ranking produces target weights and rebalance | **Blocked:** no one current hosted governed identity joins the full chain. |
| Human approval precedes any real increase | Local contract and independent historical apply evidence pass; new distinct-actor contract is not deployed. |
| Emergency containment cannot promote/increase | Local and historical independent containment evidence pass; current correlated hosted proof remains blocked. |
| Legacy/diagnostic IA is accepted as superseded | **Blocked:** the current reviewer must explicitly accept or reopen the canonical-center IA supersession. |
| Pantheon/Execute Plans deliveries are merged and dev-published | FE read-only publish passes; BFF publish containing PRs #3896/#3897 failed closed. |
| Final closeout records evidence and residual risks | This artifact updates the blocker evidence; final closeout is not yet allowed. |

The deployed Execute Plans `package-lock.json` still has SHA-256
`2d796e302699e72c9b0e731f4bab23b07eefbc007cfbe795dcfc43cd1ff778ad`,
matching the zero-finding production-dependency graph audited in the 2026-07-15
rescue. That preserves B4 only for the deployed production dependency graph;
it makes no claim about the dev/toolchain graph.

## Verification

Focused current-`dev` contracts:

```text
/home/lupin/pantheon/.venv/bin/python -m pytest \
  services/control-plane/bff/tests/test_bff_persona_create_paper_bundle.py \
  services/control-plane/bff/tests/test_bff_persona_allocation_policy.py \
  services/control-plane/bff/tests/test_bff_rebalance_proposals.py \
  services/control-plane/bff/tests/test_bff_emergency_containment.py \
  scripts/test_deploy_nonprod_bff_strict_auth_default_contract.py \
  services/control-plane/bff/test_bff_oidc_staging_env_contract.py \
  services/runtime-manager/test_promotion_authority.py \
  services/runtime-manager/test_stage_promotion.py -q
```

Result: `108 passed, 22 warnings in 42.41s`. The warnings are existing
Starlette/httpx, jsonschema resolver, FastAPI lifecycle, and naive UTC
deprecations.

Read-only hosted and delivery verification also included:

- `curl` probes of FE `deployment.json`, BFF health/readiness/version, and
  anonymous protected routes;
- `gh secret list --repo ajoe734/pantheon --env dev` for secret names only;
- `gh run view 29733822822 --repo ajoe734/pantheon --log-failed`;
- `gh run view 29726612084 --repo ajoe734/execute-plans` plus its sanitized
  integration evidence artifact;
- ancestry checks for both PPL commits against deployed and attempted SHAs;
- deployed Execute Plans lockfile hash verification; and
- `git diff --check`.

## Blocking Residual Risks

| ID | Blocking gap | Owner / recheck condition |
| --- | --- | --- |
| B1 | No one hosted governed Persona joins canonical quarterly ranking, Runtime/Telemetry, Capital binding, target, proposal, distinct approval/apply, receipt, and authoritative readback. | Persona + Runtime/Telemetry + Capital/BFF owners; rerun only after the current promotion and identity contracts deploy. |
| B2 | Five dedicated dev-login client secrets and `DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH` are absent, so a strict deploy containing PRs #3896/#3897 fails before mutation. | Human platform operator; provision the six secret values in the `dev` environment and rerun the strict deployment. |
| B3 | Authenticated smoke, live-write proof, PINT proof, and the joined desktop/mobile journey are skipped. | Frontend QA after B2 and hosted B1 fixture readiness; archive the exact FE/BFF pair and request/receipt chain. |
| B4 | Cleared only for the deployed production dependency graph. | Frontend Platform/Security; re-audit if the deployed lockfile changes. |
| B5 | Canonical Rankings/Governance/Performance centers supersede the original workbench without this task's current reviewer decision. | Reviewer Codex2; explicitly accept the IA supersession or reopen the route target after B1-B3. |

## Required Next Action

Keep PPL-ALLOC-009 blocked on `Human/Ops`. Provision the five dedicated
dev-login client secrets and `DEV_ASSISTANT_CONTROL_PASSPHRASE_HASH` in the
Pantheon `dev` environment, then rerun a strict BFF/root deploy at a ref that
contains PRs #3896 and #3897. Verify the new `/bff/version` posture and each
distinct subject/role, republish the FE against that exact BFF identity if
needed, and only then run the governed correlated B1 chain and authenticated
PINT desktop/mobile B3 proof. Codex2 must make the B5 IA decision before review
approval.
