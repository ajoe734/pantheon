# Pantheon 全產品運作 GAP 與退役處置矩陣 — 2026-08-30

## 0. 結論

Pantheon不是空殼，但目前不能宣稱全系統正常。Source、實測、GitHub/branch protection、hosted
exact-pair與cleanup baseline交叉比對後，確認 **25項current GAP**。共同根因是：composition/compat/
frontend形成第二路徑；無caller/owner/receipt仍可宣稱完成；safety proof未走正式入口；release policy
未被完整強制；migration後legacy code未刪。

處置為：**保留唯一owner、遷移caller、同delivery unit刪舊路徑、最後驗exact deployed effect**。

## 1. 正常運作定義

1. 每個command/entity/terminal state只有一個write owner。
2. Production entrypoint有natural caller，不只class/route/test。
3. Receipt可same-ID/version reload，restart後仍在。
4. Multi-replica/retry/SSE replay/outage/concurrency仍fail-closed。
5. Kill/rollback/MFA/two-person走正式路徑，無test bypass。
6. Required checks綁exact head；fail/skip/0-job/missing阻止merge。
7. Manifest/FE/BFF/workers/checkpoint同accepted candidate。
8. Task/git/deployment/retirement一致；legacy callers=0且files deleted。

原「code/test/CI/看板」四層排除不了單副本、假寫入、安全繞過、old hosted version與dead code。

## 2. 基線與實測

| 面向 | 結果 |
|---|---|
| Pantheon | `origin/dev@9c9adf426f04276d1b1a0a1401eb1f81bc0ebec4` |
| execute-plans | `origin/dev@bd03c863e3c2c1c64b9b7797f27cefaf84df17c1` |
| Hosted | FE `c230fc76...` / BFF `dcb14231...`；strict/read-only，非current source |
| BFF | main 68,171 lines；453 decorators；normalized collision=0；18 duplicate operation IDs/42 uses |
| `tests/bff` | 27 pass / 8 fail；second app的裸`import main`解析到persona main |
| EP5/evidence | 24 pass / 12 fail；7 safety blocked、5 copied-literal drift |
| Route suite | 15 pass；operation-ID只characterize，不是zero gate |
| Frontend | 11 non-test seed imports；8 overlay consumers |
| Promotion #5423 | 7 workflows failure/jobs=[]；Branch CI成功，仍merge |
| master | required三個Branch CI contexts；0 approvals；admins未enforce |
| Bot PR #5264 | action_required；repo approval policy first_time_contributors |

無完整Docker/Postgres/NATS的local結果不冒充hosted pass。

## 3. 25項 GAP

| ID | Sev | Current fact | 根因處置/完成邊界 | 唯一task |
|---|---:|---|---|---|
| OP-G01 | P0 | fallback可產real | real只由admitted receipt；fallback non-real；污染隔離/重建 | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| OP-G02 | P0 | suggestion producer無production caller | 接outbox並durable same-ID；否則producer/routes/UI一起刪 | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| OP-G03 | P0 | current FE/BFF未hosted accepted | gates後atomic switch；失敗留old pair | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| OP-G04 | P0 | acceptance把fail/skip包success | structured terminal；fail/skip/missing non-zero | `OPGAP-DELIVERY-POLICY-20260830` |
| OP-G05 | P1 | auth同步probe provider | local auth；readiness background cache | `OPGAP-BE-BFF-CORE-20260830` |
| OP-G06 | P0 | generic CRUD用overlay/拒絕 | 只留owner-backed actions；其餘刪/disabled | `OPGAP-FE-MGMT-BINDING-20260830` |
| OP-G07 | P1 | seed/overlay可達；transports雙向；4 dead NL files | bff-v1唯一transport；v5 pure；刪fallback/dead UI | `OPGAP-FE-TRANSPORT-RETIREMENT-20260830` |
| OP-G08 | P1 | main為domain owner；read-store fallback殘留 | routes進existing domains；main只組裝；刪shell/flag/fixtures | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` |
| OP-G09 | P1 | private router imports；second Workshop store | public ports注入；merge bootstrap後刪second store | `OPGAP-BE-AGORA-RESEARCH-20260830` |
| OP-G10 | P2 | dead generic action adapter | zero-caller後刪symbol/export/tests | `OPGAP-COMMAND-PLANE-RETIREMENT-20260830` |
| OP-G11 | P0 | 12-loop proof opt-in/skip | accepted candidate必跑，完整receipt/readback | `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` |
| OP-G12 | P1 | one-shot已存在但未current hosted；main aliases | 不建第二endpoint；沿用profile；proof後刪aliases | `OPGAP-BE-SOURCE-CLOSURE-20260830` |
| OP-G13 | P1 | sync TestClient deadlock | async ASGI/compat/deadline；timeout非pass | `OPGAP-BE-BFF-CORE-20260830` |
| OP-G14 | P1 | 缺current authenticated desktop evidence | short session、DOM/network/console/readback綁same pair | `OPGAP-HOSTED-E2E-ACCEPTANCE-20260830` |
| OP-G15 | P1 | capability/UI不一致 | UI只render contract；non-real不可promotion | `OPGAP-FE-AGORA-CAPABILITY-20260830` |
| OP-G16 | P0 | deploy lease與rollback共用GitHub | bounded forward grace；sealed local rollback | `OPGAP-DELIVERY-POLICY-20260830` |
| OP-G17 | P0 | binding可用caller metadata拼裝 | Registry immutable projection；Deployment reference；Runtime verify | `OPGAP-BE-RUNTIME-BINDING-20260830` |
| OP-G18 | P1 | owner/read已存在；frontend fake ID；old aliases | 接existing owner；刪fake derivation/aliases；no second owner | `OPGAP-FE-MGMT-BINDING-20260830` |
| OP-G19 | P0 | Source→Agora fix merged未current證 | exact candidate same-ID proof；不重寫 | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| OP-G20 | P0 | paper full chain未hosted | natural snapshot→signal→order→fill→position same trace | `OPGAP-HOSTED-DEV-PROMOTION-20260830` |
| OP-G21 | P0 | 8 multi-replica fails；18 duplicate IDs | router injection；no root import；ID-zero hard gate | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` |
| OP-G22 | P0 | EP5 harness 7 fail，fixture被formal gate阻擋 | governed packet/distinct actors；no bypass | `OPGAP-SAFETY-PROOF-CONTRACT-20260830` |
| OP-G23 | P1 | 5 tests copied derived literals | versioned evidence builder；test generator/provenance | `OPGAP-SAFETY-PROOF-CONTRACT-20260830` |
| OP-G24 | P0 | runtime-manager掛1,640-line central API | direct owners；parity後刪API/mount/env/callers/tests | `OPGAP-COMMAND-PLANE-RETIREMENT-20260830` |
| OP-G25 | P0 | 7 zero-job failures仍可merge；bot approval另阻 | one exact-head gate；branch audit；刪old workflows | `OPGAP-DELIVERY-POLICY-20260830` |

## 4. Cleanup alignment

102筆決策仍以
[`DISPOSITION_MATRIX_2026-08-27.json`](../pantheon_architecture_cleanup_gap_2026-08-27/DISPOSITION_MATRIX_2026-08-27.json)
為準。Normalized routes已0但main/IDs未完；ReadSurfaceStore class已刪但helper/fallback仍在；second
runtime-manager已刪且不重開；Workshop second store、Source compat exports、dead NL files仍待刪；Agora
worker launcher source-fixed只待hosted proof。

Devtool TaskStore/supervisor gaps由
[`development-tooling-four-gap-2026-08-30`](../../operations/development-tooling-four-gap-2026-08-30/INDEX.md)
單獨擁有，本catalog不建第二task/writer。

## 5. Mandatory retirement

| Target | Cutover | Done proof |
|---|---|---|
| action adapter/internal API/mount/env | direct owner commands | callers/symbols/tests=0；files deleted |
| router import main | dependency injection | domain→main=0 |
| overlay/writeFallback/seed | typed clients/test fixtures | production graph=0 |
| four NL/stub files | active managementAi retained | callers=0；deleted |
| read-store shell/fallback | helpers to owners | config/symbol/fixtures=0 |
| second Workshop store | bootstrap merge | class/constructors/tests=0 |
| Source aliases | direct module imports | compat/callers=0 |
| Postmortem aliases/fake ID | existing owner/read | aliases/derivation=0 |
| superseded workflows | canonical gate/audit | workflows/contexts/docs removed |

禁止legacy2、compat_v2、generic router folder、新Postmortem store、第二Source endpoint。

## 6. Reproduction/limits

```bash
.venv/bin/pytest -q tests/bff  # 27 passed, 8 failed
.venv/bin/pytest -q tests/governance/test_kill_switch_harness.py \
 tests/governance/test_rollback_drill_harness.py tests/governance/test_persona_lineage.py \
 tests/governance/test_sponsor_resolver.py tests/evolution  # 24 passed, 12 failed
.venv/bin/pytest -q services/control-plane/bff/test_normalized_route_uniqueness.py  # 15 passed
gh pr view 5423 --repo ajoe734/pantheon --json statusCheckRollup,mergedAt,headRefOid
gh api repos/ajoe734/pantheon/branches/master/protection
```

G03/G11/G12/G14/G19/G20待hosted proof；old pair健康不等於current部署。`action_required`與zero-job
failure不同。大檔不是刪除理由；重複owner/跨層/無caller/fallback truth/不可驗證才是GAP。Source
default reconcile-only/egress deny；snapshot須經calendar admission再natural paper chain。
