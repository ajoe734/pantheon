# Pantheon GAP 根因治理系統設計（SD）— 2026-08-30

## 1. Design units

| Unit | Design | GAP |
|---|---|---|
| SD-01 | BFF auth/test | G05、G13 |
| SD-02 | BFF composition/multi-replica/route identity | G08、G21 |
| SD-03 | Agora truth/wiring/store | G01、G02、G09 |
| SD-04 | Runtime executable projection | G17 |
| SD-05 | Source one-shot/entrypoint retirement | G12 |
| SD-06 | Management/Postmortem binding | G06、G18 |
| SD-07 | Frontend transport/mock retirement | G07 |
| SD-08 | Agora capability UI | G15 |
| SD-09 | Command-plane retirement | G10、G24 |
| SD-10 | EP5 safety/evidence | G22、G23 |
| SD-11 | Release policy/rollback | G04、G16、G25 |
| SD-12 | Promotion/hosted acceptance | G03、G11、G14、G19、G20 |

## 2. Shared contracts

Mutation須回`receipt_id/entity_id/entity_version/owner/status/trace_id/readback_url`；缺owner/stable ID/
readback的HTTP success為failure，且不得形成generic entity store。Provenance只有real/simulated/unavailable；
real須有可向owning adapter讀回的receipt。

Cleanup PR附retirement receipt：artifact、replacement owner、callers before/after、forbidden refs、parity
tests、deleted。它是PR evidence；deprecated/rename/legacy folder不算deleted。

## 3. SD-01 — BFF auth/test

- `auth.py`承接main的local JWT/session/tenant/RBAC，無provider HTTP probe。
- Readiness background TTL cache；auth cache miss不發network。
- Integration用`AsyncClient(ASGITransport)`與deadline；timeout不轉skip/pass；刪sync fixture。
- Acceptance：provider outage不阻塞auth；auth與multi-replica同process可跑。

## 4. SD-02 — BFF composition

- Named dependency object聚合typed ports/auth/publisher/clock；routers只收窄介面。
- Route bodies搬existing domain packages，main decorators同PR刪；禁止generic routers God folder。
- 刪identity/personalization的`import main`，禁止module shim。
- Read-store helpers移named owners後刪shell/snapshot fallback/config/fixtures。
- Hard gates：normalized/operation-ID/static-shadowing皆0，無allowlist。

Main可留app factory/middleware/lifespan/wiring/includes，不得含domain validation/store mutation/route body；
不以任意行數驗收。`tests/bff`需three app instances全綠，domain→main imports=0。

## 5. SD-03 — Agora

- Real只由admitted durable receipt；fallback不能標real。
- Existing outbox註冊唯一suggestion consumer，以trigger ID+producer version冪等。
- Suggestion寫existing PostgreSQL owner並same-ID readback；若無需求則producer/routes/UI一起刪。
- Public application services取代private cross-router imports。
- Merge duplicate Workshop bootstrap store後刪second class。

## 6. SD-04 — Runtime projection

Registry簽immutable projection（artifact SHA、object-store URI、loader/market-policy digest、schema）。
Deployment只存ID/digest，不轉caller metadata；Runtime驗authority/digest後才建binding；missing/mismatch/
unknown fail-closed。Active binding保存digest供paper/manifest readback。

## 7. SD-05 — Source

- 沿用existing scheduler/projector one-shot，不建manual-refresh route。
- One-shot exact connector/allowlist、finite ticks/records/concurrency/timeout、restart=no；default固定
  reconcile-only/egress deny。
- Terminal/projector receipts共用connector/source/ingest-run IDs。
- Callers direct-import runtime/api_models/routers後刪main aliases/wrappers/compat exports。

## 8. SD-06 — Management/Postmortem

- `services/postmortems`唯一owner；Management router注入typed reader，BFF不存Postmortem。
- Frontend用existing `/bff/management/postmortems*`；刪timeline parsing/`pm_<incident>`。
- Generic create改named owner commands；無owner controls刪/disabled。
- Cutover後刪main `/api/v1/postmortems*` aliases/tests；不得建second store/service。

## 9. SD-07 — Frontend transport

- 修bff-v1 self-barrel，再逐domain吸收legacy network/auth/SSE/mutation；v5 pure-only。
- 11 seed consumers接real client或刪zero-caller screen。
- 刪writeOverlay/writeFallback、4 dead NL/stub files及production refs；保留active managementAi。
- Vite/Rollup gate production graph；mock/seed/overlay及transport SCCs均0。

## 10. SD-08 — Agora UI

Workshop/Trading/Performance只render backend capability；non-real disabled且不可promotion。Suggestion UI只在
SD-03接線後顯示，same-ID owner readback，不能seed補滿。

## 11. SD-09 — Command plane

- Adapters直連Runtime/Deployment/Governance/Capital/Persona/Incident owners，不fallback central URL。
- Commands驗owner receipt/readback；runtime-manager不存foreign state。
- Parity且legacy requests=0後同unit刪action adapter、internal callers/API、runtime mount、env、tests。
- Forbidden path/env/import gate；禁止compat_v2/catch-all facade。

## 12. SD-10 — EP5 safety/evidence

```text
DRAFT -> MFA_VERIFIED -> TWO_PERSON_APPROVED -> ACTIVATABLE
 -> RUNTIME_ACTIVE -> CONTAINED -> ROLLED_BACK/REPLACED
```

Missing MFA/same actor拒絕。Harness用canonical builder走正式activation，不暴露bypass。Evidence由schema+
source digest+generator version產UUID/tenant/conflict values。7 harness與5 drift tests綁formal revision；
tampered/missing proof負向fail。

## 13. SD-11 — Release/rollback

- Publish-promote在exact head呼叫one manifest-driven orchestrator，不依賴bot PR approval semantics。
- Required skip/action_required/0-job/missing/wrong SHA皆fail。
- Master只required one release result；policy audit驗contexts/review/admin enforcement。
- New gate有效後同unit刪superseded workflows/contexts，不留second success path。
- Forward lease bounded grace；switch前seal local baseline；rollback不需GitHub lease。

Owned：publish workflow、canonical workflow/manifest、publish script、`scripts/dev_environment_lease.py`、
`scripts/deploy_nonprod_vm.sh`。

## 14. SD-12 — Promotion/acceptance

```text
CANDIDATE -> POLICY_ACCEPTED -> PRE_SWITCH_VERIFIED -> ATOMIC_SWITCHED -> ACCEPTED
before failure -> OLD_PAIR_UNCHANGED
after failure  -> SEALED_BASELINE_RESTORED
```

Evidence：exact identities；Source→Agora same IDs；natural snapshot→signal→order→fill→position；12 loops
stimulus/receipt/terminal/readback；authenticated DOM/network/console/same-ID reload。Hosted scripts只產evidence，
不寫product/task truth。

## 15. Sequence

Safety/release先行；domain migrations平行且各自cutover+delete；single owners整合BFF main與frontend
App/barrel；source/frontend green後才hosted（pantheon-dev capacity=1）。Rollback不可復活retired path。
