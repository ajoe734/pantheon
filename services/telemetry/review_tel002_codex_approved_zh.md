# TEL-002 審查核准（Codex）

**任務**: `TEL-002`  
**作者**: Qwen  
**審查者**: Codex  
**狀態**: APPROVED  
**日期**: 2026-04-10

## 結論

這版 `TEL-002` 現在可以核准，但不是原樣核准。review 過程中補了一個會造成 telemetry 靜默遺失的 blocker：當 writer 在高壓模式下延遲 `heartbeat` 這類 delayable event、且 queue 在重塞前被其他事件補滿時，原事件會被 dequeue 後直接掉到地上，既沒有寫入 canonical sink，也沒有進 DLQ。這輪已把該路徑改成「立即嘗試重塞；若 queue 仍滿就明確進 DLQ 並帶 `buffer_overflow` tag」，同時補上回歸測試把這個邊界鎖住。

另外，`BUFFER_CHOICE_ADR.md` 原本把「critical events 會同步寫本機 JSONL emergency spill」寫成既有 mitigation，但程式並沒有這條實作。這輪已把 ADR 改成符合現況的敘述：in-memory buffer 只適合 dev/research shim，需要 crash recovery 時必須切到 Redis Streams backend。

## 核准依據

1. `buffer.py`、`batch_writer.py`、`backpressure.py`、`dead_letter.py`、`ingest_svc.py` 已經把 TEL-002 要求的 shock-absorption path 補齊成一條可執行資料流：producer 不再直寫 canonical store，而是先進 buffer，再由 async writer 做 micro-batch / retry / partition flush，失敗時進 DLQ。
2. review blocker 已被修掉，而且不是只靠人工說明，而是新增了 `test_delayed_requeue_overflow_routes_event_to_dlq`，直接重現「delayable event 被 dequeue 後因 queue 滿而重塞失敗」的情境，驗證它現在會進 DLQ、不再 silent drop。
3. ADR 現在和實作一致，不再假裝 v1 內建 crash-safe emergency spill。這很重要，因為 durable ingest 的風險邊界必須被文件明確講清楚，不能讓 downstream 以為 memory backend 已經有 crash recovery。

## 驗證

- `python3 -m py_compile services/telemetry/batch_writer.py services/telemetry/test_ingest_shock_absorption.py services/telemetry/ingest_svc.py`
- `python3 -m unittest services.telemetry.test_ingest_shock_absorption`
- `python3 services/telemetry/smoke_test_ingest.py`

## 結果

`TEL-002` 目前已滿足本輪 reviewer 期待：shock-absorption path 存在、retry / DLQ / replay 路徑可跑、回歸測試有把最危險的 silent-loss 邊界鎖住，且 ADR 不再高估 durability。

結論：`TEL-002` 可進入 `review_approved`，並 handoff 給 owner Qwen 做最終收尾為 `done`。
