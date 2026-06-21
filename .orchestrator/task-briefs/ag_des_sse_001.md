# Task Brief: AG-DES-SSE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Typed workshop SSE event contract (v1.3)
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Supervisor resumed AG-DES-SSE-001 for finalize after successful dispatch.

## Summary
依 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/03_workshop_sse_contract.md 落地 v4 schema workshop_stream_event:typed envelope(§C1 event_id/type/aggregate/sequence_no/causal_parent/times/trace/idempotency/data_cutoff/visibility/payload_schema/payload)、event catalog(§C2 ~25 種 workshop.*/research.*/consultation.*/stream.*)、latency(§C3 p95 首個 persisted ack <2s,LLM/研究非同步)、ordering/replay(§C4 per-workshop 單調序、at-least-once、dedupe、Last-Event-ID replay≥24h/10k、heartbeat 15s、45s 降級、缺 replay 回 SSE_REPLAY_UNAVAILABLE)。 【有疑問一定要 STOP 開 blocker】動工前讀完權威設計 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/ (MASTER_SD_RESPONSE.md + 對應 0X 文件 + schemas/*.json + 08 OpenAPI delta)。鐵律:不可改動或重雜湊 frozen v1/v1.1/v1.2(bundle_index.json/.v1_1/.v1_2、agora_v1*.openapi.yaml);一律 additive 到 services/control-plane/specs/agora/v4/ 與 agora_v1_3.openapi.yaml / bundle_index.v1_3.json(後者 extends 並 hash v1.2 精確 bytes)。schema 內容以 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/ 同名檔為準逐欄落地,不自創欄位/route/enum;Agora 永不下單/綁資金/寫 RuntimeBinding(governed handoff 只建 request)。遇任何不確定先 blocker。
