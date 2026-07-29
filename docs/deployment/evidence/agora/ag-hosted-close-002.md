# AG-HOSTED-CLOSE-002 — Agora hosted exact-pair final closeout

Status: **completed, independently approved, and archived**

- Task owner: `Claude2`
- Reviewer: `Antigravity`
- Repository: `ajoe734/pantheon` (evidence and closeout only)
- Merge target: `dev`
- Environment: Pantheon dev — replacement VM `pantheon-lupin-dev-20260719` /
  `35.201.204.12`
- Succeeds: `AG-HOSTED-CLOSE-001` (superseded predecessor; see § Resolved blocker)
- Depends on: `AG-GOV-WORKSHOP-COMPAT-DEPLOY-001`,
  `OPS-DEV-LIFECYCLE-FRESHNESS-001`
- Lifecycle: reviewer `Antigravity` approved the packet at
  `2026-07-24T06:11:34Z`; owner `Claude2` finalized and archived the task at
  `2026-07-24T06:23:50Z`.
- Repository delivery: evidence PR #4050 merged as `874103d1a`; final
  review-approved brief PR #4051 merged as `cd4f42c4f`.

## Objective

Consume the repaired canonical `strategy_workshop` contract, the distinct
Registry/strategy identity fix, the strict-auth exact FE/BFF pair, and the
managed-dev lifecycle freshness fix, and archive one reviewer-consumable
closeout pinned to the exact accepted pair on the replacement dev VM.

This task adds no new product runtime. It qualifies, re-verifies as-of-now, and
records closeout for work already merged to `dev` by the upstream repair lanes.

## Resolved blocker

`AG-HOSTED-CLOSE-001` (evidence anchor `ce7ba393e`) was blocked because:

- Governance rejected the required `target_type=strategy_workshop` with `422`,
  so real research/conclude failed `APPROVAL_TARGET_TYPE_MISMATCH` (`409`); and
- distinct Registry and strategy identities failed
  `STRATEGY_SPEC_STRATEGY_ID_MISMATCH`.

Both defects were repaired and merged before this closeout:

- `AG-GOV-WORKSHOP-CONTRACT-001` — Governance producers and the canonical
  `ApprovalDecision` schema now expose exactly one Workshop target type
  (`strategy_workshop`); Workshop creation resolves `strategy_spec_ref` as a
  Registry identity, derives `strategy_id` from authoritative Registry
  readback, and persists the two identities separately. Merged to `dev` via
  PR #4036 (`0346b28790d9534cfff76625caeadee8d5ea13b8`) and fail-closed
  follow-up PR #4037 (`49cb982da66ccea5c117a1abc07cb3cb2d345f52`). Evidence:
  `docs/deployment/evidence/agora/ag-gov-workshop-contract-001/README.md`.
- `AG-GOV-WORKSHOP-COMPAT-DEPLOY-001` — regenerated the compatibility gate and
  deployed the exact accepted FE/BFF pair with strict auth, then ran the full
  hosted repair probe. Evidence:
  `docs/deployment/evidence/agora/ag-gov-workshop-compat-deploy-001.md` and
  `.../ag-gov-workshop-compat-deploy-001/qualification-20260724T045953Z.json`.

The former `422`/`409`/`STRATEGY_SPEC_STRATEGY_ID_MISMATCH` failure modes no
longer occur: the hosted probe created and approved a canonical
`strategy_workshop` target, preserved distinct Registry and strategy
identities, used handoff-only research, concluded the Workshop, restarted the
BFF through the governed workflow, and read every durable resource back.

## Accepted exact FE/BFF pair

| Field | Value |
|---|---|
| Pair ID | `ec91a4aaaee16719f6db6a3d7b6edba048c08e676d789bfb9301df92913c3de2` |
| Backend (BFF) SHA | `f71c1f8ba889ba64956006ef0f9159840be6d065` |
| Frontend SHA | `e4399e3ec68f882ace35d0349e6597cdd101525f` |
| Contract commit | `9e909de182f9f2379d23e8e6b81eefec29ffbce7` |
| Compatibility manifest SHA-256 | `d61e11cf2cead97d4a66ab153a2081ef4d633671ee4f962d271a7b3feeb86867` |
| Contract family | `agora.v1.13` |
| Release name | `20260724T045319Z-e4399e3ec68f-gate-30003411349-30067684910-1-3592355` |
| Deployed at | `20260724T045319Z` |
| Deployment state / profile | `accepted` / `read-only` |

Contract integrity hashes (from the compatibility-deploy qualification):

- Bundle index SHA-256:
  `b1d488c3b35aa1c691e5b464362ac5a2fdd1efc442249e15be9bb143f379f870`
- OpenAPI SHA-256:
  `36d1be5bc033ea1a55610f3f523fc478704fdfad1f06fec620e741bed9bf6f86`
- Capability manifest SHA-256:
  `7dfddaf220c00eddb7cbd0862eaa6f2aba7423dbd02e54d15db1d67a0cb4ded1`
- Backend handoff SHA-256:
  `8510946b40ec2adc11788dc40be7cd8db9fc824184c8b1faabe3e0f62f29312b`
- Frontend handoff SHA-256:
  `5fa6c75ae6e8c044c038570a7765522fa145c1b603cc66e4db72bdf6898b3f2b`

## Governed workflow runs (accepted evidence)

1. Pantheon BFF deploy
   [run 30065241892](https://github.com/ajoe734/pantheon/actions/runs/30065241892)
   — target `f71c1f8ba889ba64956006ef0f9159840be6d065`, `auth_stub=false`, strict auth.
2. Execute Plans integration gate
   [run 30003411349](https://github.com/ajoe734/execute-plans/actions/runs/30003411349)
   — frontend `e4399e3ec68f882ace35d0349e6597cdd101525f` + accepted backend.
3. Read-only frontend release
   [run 30067684910](https://github.com/ajoe734/execute-plans/actions/runs/30067684910)
   — immutable-artifact check, exact-pair gate, controller regression,
   pre/post-switch browser probe, and evidence seal.
4. Pantheon BFF governed restart
   [run 30068077516](https://github.com/ajoe734/pantheon/actions/runs/30068077516)
   — exact-pair gate, strict-auth floor, public version proof, generic Agora
   restart-persistence smoke, and identity-bound lease release.

## Hosted Governance/Registry/Workshop seed and post-restart readback

From `qualification-20260724T045953Z.json` (probe schema
`pantheon.agora.governance-workshop-repair-proof.v1`):

- Seed completed `2026-07-24T04:56:03Z`; 18 checks accepted, including
  `registry_strategy_ids_distinct`, `canonical_strategy_workshop_approval_created`,
  `approval_decided_by_distinct_actor`, `workshop_research_handoff_created`,
  `workshop_concluded`, and `conclusion_preserves_repaired_identity`.
- Verify-after-restart completed `2026-07-24T04:59:53Z`; 12 checks accepted,
  including `public_manifest_exact_safe_pair`,
  `initial_registry_identity_after_restart`,
  `active_version_registry_identity_after_restart`,
  `canonical_approval_after_restart`, and
  `concluded_repaired_workshop_after_restart`.
- Durable identities held distinct across the restart:
  strategy `strategy-ag-gov-workshop-20260724T045602Z-633423`,
  active version Registry `reg-ws-557d2e781d7c06e8cebb`,
  Workshop `fdf40067-8fa6-45b4-8d55-f78f07b8bde5`,
  version `wsv-557d2e781d7c06e8cebb`; observed approval `target_type`
  `strategy_workshop`, `decision_state` `decided`, `decision` `approved`,
  Workshop `status` `concluded`.
- Probe safety: no backing store edited directly, no credential/token emitted,
  execution authority `none`, research mode `handoff_only`, `no_live_capital`,
  and no deployment/order/broker/allocation/capital API called.

## Owner as-of-now independent re-probe (2026-07-24T05:58Z)

Read-only GET re-verification of the public endpoints on the replacement VM,
captured under `docs/deployment/evidence/agora/ag-hosted-close-002/`:

- `GET https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io/deployment.json`
  → `deploymentState=accepted`, `profile=read-only`,
  `pairId=ec91a4aa…c3de2`, `frontendSha=e4399e3ec68f…`,
  `bffCommit=f71c1f8b…`, `agoraCompatibility.compatibility_status=accepted`,
  `blocking_reasons=[]`, `manifest_sha256=d61e11cf…`. Build mode:
  `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
  `VITE_BFF_REAL_WRITES=false`, `VITE_BFF_ALLOW_DEV_STUB_WRITES=false`,
  `VITE_BFF_EMBEDDED_BEARER_TOKEN=false`.
  (`reprobe-deployment-manifest.json`)
- `GET …-bff.35.201.204.12.sslip.io/readyz` → `200`, `status=ok`, `live=true`,
  `ready=true`; dependencies `runtime_manager`/`governance`/`deployment`/
  `lifecycle_projector` all `ok`; lifecycle projector `mode=live`,
  `accepted_live=true`, `deployment_sha=f71c1f8b…`, freshness
  `age_seconds≈51`, `max_age_seconds=300`, `stale=false`; server timestamp
  `2026-07-24T05:58:41Z`. (`reprobe-bff-readyz.json`)
- `GET …-bff.35.201.204.12.sslip.io/bff/version` → `200`,
  `source_commit_sha=f71c1f8b…`, `config_posture` `auth_stub=false`,
  `auth_mode=strict`, `mfa_required=true`. (`reprobe-bff-version.json`)

This confirms the exact accepted pair is still served, strict, read-only with
safe write defaults, and healthy (`/readyz` `200`) as of closeout — the managed
freshness budget from `OPS-DEV-LIFECYCLE-FRESHNESS-001` (300 s) is honored and
the projector is not stale.

## Source freshness / as-of presentation

The lifecycle projector reports a bounded, non-stale freshness window
(`age_seconds` well under the `300 s` managed-dev budget) with an explicit
`last_successful_publish_at` and `deployment_sha`, so hosted read surfaces carry
an explicit as-of timestamp rather than an undated snapshot. The persistent
managed-dev freshness fix (`OPS-DEV-LIFECYCLE-FRESHNESS-001`, PR #4043, merge
`406abcad90421bc262961adb6cb3b6ab89c04962`) raised the budget to 300 s and kept
strict exact-SHA `operator-bff` ready through the accepted frontend switch and
the governed BFF restart.

## Local validation (upstream repair lanes)

Reproduced/recorded by the repair lanes this closeout consumes:

- Agora deployment manifest and isolation tests: `19 passed`.
- Governance decision, Governance API, Workshop, live-operation tests:
  `180 passed, 5 skipped` (skips are optional Postgres cases without
  `TEST_DATABASE_URL`).
- Full Agora suite from the manifest update: `466 passed, 8 skipped`.
- Contract bundle `--check`, frontend handoff verify, exact deployment manifest
  gate, hosted-probe compile and Ruff: all passed.

## Residual risks

- Repository `origin/dev` backend head is newer than the hosted accepted BFF
  `f71c1f8b…`. That is expected: hosted claims stay pinned to the accepted pair
  or a later gate-before-switch manifest, never to a bare repo head.
- Six workshop operations remain intentionally fail-closed at `501` (contract
  honesty via `AG-GAP-005`); implementing that deferred capability is separate
  `AG-WS-OPS-*` follow-up and is out of scope for this closeout.
- Strategy Performance truth and Trading Room candidate truth are tracked by
  the `AG-PERF-TRUTH-001-*` / `AG-CAND-TRUTH-001-*` lanes; this closeout only
  covers the Governance/Registry/Workshop exact-pair qualification.
- The hosted proof enabled governed dev writes only within the task probe; the
  watchdog restored the read-only profile, confirmed by the as-of-now re-probe
  above. No production or live-capital route was exercised.

## Exclusions honored

- No production/live-capital action.
- No evidence copied from the retired `35.201.239.38` VM and relabeled current;
  all live evidence is from `35.201.204.12`.
- No hand-edited stores or route interception; all writes went through governed
  authenticated routes with authoritative receipts/readback.
