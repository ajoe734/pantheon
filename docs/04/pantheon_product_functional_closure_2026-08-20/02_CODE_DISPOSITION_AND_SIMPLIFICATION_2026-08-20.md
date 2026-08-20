# Pantheon 重複機制、廢碼與簡化 Disposition — 2026-08-20

## 1. 原則

本輪不是「先加功能、以後再清」。每個 implementation task 都必須先列出：

1. 現行 canonical owner、store、queue、adapter與正常caller；
2. 已存在但未被正常caller使用的implementation；
3. 同一責任的第二個owner/store/UI path；
4. replacement驗收後可刪除或降為test/demo-only的路徑。

刪除必須晚於replacement current-dev proof。沒有caller證據時，不用「看起來舊」作為刪除理由；
先標記 `retain-pending-caller-audit`。反過來，已證明沒有production caller的假成功或duplicate path，
不得再加compatibility layer延命。

Disposition固定使用四種：

- `keep`：唯一owner或仍有明確production責任；
- `consolidate`：能力保留，但caller收斂到唯一owner/store/adapter；
- `retire-after-proof`：替代路徑驗收後移除production wiring；
- `delete`：無production caller，且不再作test/demo fixture。

## 2. Source Ingestion

| Code / mechanism | Current truth | Disposition | Task rule |
|---|---|---|---|
| `controller_worker.py` | current single desired-state/reconcile owner；也可做bounded pull | `keep` | 不新增第二scheduler；修state projection與one-shot selection |
| `controller_state.py` full nested snapshot | 每tick保存完整reconcile/actual；actual又包含controller state，造成約280 MB遞迴成長 | `consolidate` | 改保存bounded摘要、identity、cursor與必要terminal proof；提供舊state遷移/重建 |
| `SOURCE_INGEST_CONTROLLER_MODE=reconcile_only` | 能保證dev平時不做provider egress | `keep` | default必須維持；不得以closure為由改回continuous pull |
| `scheduler_worker.py` | 舊bounded HTTP `/run-scheduled` utility，不是current Compose owner | `retire-after-proof` 或 manual utility | caller audit後決定；不得重新成為daemon |
| `scripts/source_ingest_scheduler_once.py` | one-shot診斷/測試入口 | `consolidate` | 若沿用，必須呼叫canonical controller one-tick並明確選connector；不得繞過state/readback |
| `source-ingest-agora-projector` legacy profile | 依賴長駐scheduler正常退出，與current owner語意衝突 | `retire-after-proof` | 確認無hosted caller後移除profile；需要的projection改讀Source authority |
| `/readyz` full-store scan | 大store下超時，process running但不可用 | `consolidate` | 改讀bounded counters/cursors/cache；保留deep diagnostic但不作頻繁healthcheck |

## 3. Deployment / paper execution

| Code / mechanism | Current truth | Disposition | Task rule |
|---|---|---|---|
| Deployment dispatcher + Runtime Manager | canonical Loop 8 owners | `keep` | artifact projection必須在既有plan/binding contract完成，不建sidecar binder |
| `artifact_loader.py` | 已有loader能力，但正常binding未提供所需projection | `keep` | 讓Registry/Deployment自然產生descriptor/checksum，不能讓test metadata代填 |
| `paper_fleet_reconciler.py` | current dynamic binding-scoped owner | `keep` | active前做execution contract admission；缺契約不spawn child |
| static `pantheon-paper-runtime` profile | compatibility-only；current default由fleet child執行 | `retire-after-proof` | caller audit；若無dev script依賴則移除production profile，必要fixture搬test |
| `BoundedPaperStrategy` | explicit smoke fallback，不是default | `keep` as test-only | 明確限制在smoke/test；任何production selection都fail |
| lifecycle outbox single JSON snapshot | 每binding約179 MB，worker重複掃歷史 | `consolidate` | 改append/cursor/ack或等價bounded store；migration後刪除legacy full-scan reader |
| 9個invalid active bindings | canonical store中的真實資料，但無法執行 | `retire-after-proof` | 用Runtime Manager API retire/redeploy/migrate；不得手改JSON |
| producer source snapshot fallback | client呼叫不存在的Source endpoint | `consolidate` | 實作一個canonical stored snapshot contract；不加第二market feed service |

## 4. Agora

| Code / mechanism | Current truth | Disposition | Task rule |
|---|---|---|---|
| Workshop create/message store | durable且已被active UI使用 | `keep` | reconstruction採同一command/outbox/store identity |
| synchronous `/reconstruct` endpoint | algorithm存在，只有test/explicit endpoint使用 | `consolidate` | 由durableworker採用；endpoint成read/request surface，不另建reconstruction engine |
| `ResearchDispatcher.execute_stage` | implementation存在，只有tests呼叫 | `keep` + wire | 建立唯一outbox consumer；不得新增第二dispatcher或直接UI execution |
| production candidate fallback | 已改成production empty、demo/test才fixture | `keep` | 不恢復default candidates |
| `AgoraDatasetAuthority` direct DB discovery | durable Agora→policy handoff已存在，direct scanner是第二個discovery path | `retire-after-proof` | 真handoff E2E後移除scheduler的automatic direct discovery；明確refs可保留diagnostic |
| fixed `lens-A..E` | UI展示模型被誤用為candidate pool identity | `delete` from production identity | lens改由authoritative pool/workspace recipe投影；fixture只留story/test |
| page-local `CandidateReviewDrawer` | 只改React state；另有完整BFF-wired元件 | `delete` after adoption | active page採共用BFF drawer後刪page-local duplicate與duplicate tests |
| widget local placeholders | 多數widget沒有producer/query | `consolidate` | 以decision/performance/telemetry store投影，不建每widget一個store |
| decision/performance store methods | implementation只在tests直接呼叫 | `keep` + wire | 建唯一production producer；事件identity可追回candidate/review/runtime |
| Consultation executor/provider | 已存在且可durable handoff | `keep` | Agora不得另建consult engine或fabricated terminal memo |

## 5. Management / frontend

| Code / mechanism | Current truth | Disposition | Task rule |
|---|---|---|---|
| `runActionSafe` UI wrapper | 18個production call sites共用 | `keep` | disabled profile回unavailable；enabled profile等domain terminal/readback才顯示完成 |
| mock mutation completed receipt | real writes off時模擬完成 | `delete` from production | mock只可在explicit demo/test transport；strict live不得顯示成功 |
| BFF generic action adapter | 回`admitted`但不做domain effect | `consolidate` | route到既有domain command clients；沒有owner的action明確unavailable |
| 61個 `NonProductionActionButton` | 誠實disabled | `keep` | 不為了表面可點而接假action |
| `safeAdapt(..., seedFn)` | 200 contract mismatch回seed | `delete` from strict live | strict live保留typed error/degraded；seed只在explicit demo profile |
| `src/mocks/seed.ts` | tests/demo仍大量使用 | `keep` as fixture | production chunks/runtime adapter不可import；不必刪整個fixture庫 |
| Formula Studio synthetic job | seed + timer + fixed metrics | `retire-after-proof` | 接真job/terminal/readback後刪fake runner |
| Activity Monitor synthetic events | 前端生成卻標live | `delete` | 改讀canonical audit/event feed；無資料顯示empty/unavailable |
| Strategy Paper/Live hash chart | hash生成series | `delete` | 改讀paper telemetry；沒有series就明確empty |
| Postmortem fixed library | 3筆static rows | `delete` | 接既有postmortem API/BFF adapter |
| `src/lib/bff` 與 `src/lib/bff-v1` 重疊 | legacy、fixtures與active adapter混用 | `consolidate` | 以caller audit逐一遷移；不做無邊界一次性rewrite |
| Management AI UI action registry | 7種contract存在，3種未完成 | `keep` + wire | 使用既有registry；不建第二agent action bus |

## 6. Loop truth / tests / delivery

| Code / mechanism | Current truth | Disposition | Task rule |
|---|---|---|---|
| `services/loop-control` | canonical owner observation store/projector | `keep` | 不建第三套loop state；補owner writers與BFF composition |
| static loop catalog runtime/task fields | stable spec與runtime truth混在一起 | `consolidate` | 只留loop ID/spec/owner contract；maturity/health由current records投影 |
| misnamed “deployed” in-process tests | 名稱比證據強 | `consolidate` | 保留component價值並改名；另用真正deployed suite作closure |
| prebuilt-ID cross-loop verifier | 能驗證readback，不能證明trigger chain | `keep` as verifier | 名稱/報告標明identity verifier；不作closure gate |
| inline Object Store / fixed closes fixtures | 適合component tests，會掩蓋產品缺口 | `keep` as fixture | product E2E明確禁止 |
| hosted manifest `accepted` with wrong live BFF | release metadata與runtime不一致 | `retire-after-proof` | 新release gate-before-switch後取代；舊manifest留歷史不可作current truth |

## 7. 每個 task 的強制 code-disposition acceptance

所有 catalog tasks 至少要在task evidence中保存：

```json
{
  "canonical_owner": "...",
  "stores_and_queues": ["..."],
  "production_callers": ["..."],
  "duplicate_paths": ["..."],
  "retired_after_proof": ["..."],
  "retained_with_reason": ["..."],
  "new_parallel_owner_created": false
}
```

Reviewer 必須拒絕以下交付：

- 用新worker繞過未接線的existing worker；
- 用新store複製已有authority；
- 為了讓舊測試通過保留兩套normal production path；
- 沒有replacement proof就刪唯一caller；
- 把fixture/mock改名成fallback後繼續放在strict-live；
- 只在文件宣稱dead code，卻沒有caller search、runtime profile與test分類證據。
