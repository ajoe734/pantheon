# Task Brief: OPENCLAW-PERSONA-OODA-LOOP-WIRING

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Wire persona-create -> OODA cron registration -> OpenClaw drive so the persona OODA loop actually runs and produces packets
- Status: todo
- Owner: Claude
- Reviewer: Claude2
- Next: Assignment created

## Summary
照 SA/SD multi-persona OODA(docs/04/pantheon_sa_supplemental_2026-05-15/)實作第三步:把 persona 建立→OODA cron 註冊→OpenClaw 驅動的接線接通,讓 /bff/ooda/packets、loop-runs、interventions、evolution-programs 有引擎產出的真資料。已確認前置:OpenClaw model auth 已修(~/.openclaw/.env 有 OPENAI_API_KEY,probe ok,agent turn 可跑);OPENCLAW_PAPER_ADAPTER_ENABLED=true 已開。精確缺口(三段都沒接):(1)services/control-plane/cron/openclaw_client.py 的 OpenClawCronClient transport=None,dispatch_prepared 永遠回 dry_run/local_only——要接真 transport(WS/HTTP 到 OPENCLAW_GATEWAY_URL=ws://openclaw-gateway:18789)。(2)沒有東西把 4 個 WORKFLOW_CATALOG workflow(pantheon.ingest 0 */6、review 15 7 *1-5、retrain 0 2 *1-5、deploy */15)註冊成 OpenClaw gateway cron job(openclaw cron list 目前空)——要實作註冊(每 active persona/binding 一組,帶 persona_id/binding context)。(3)BFF POST /bff/personas 是 stub('create persona stub')——建立人格時要觸發上述 cron 註冊(你說的:建人格就該完成第三步)。流程跑通後:workflow 排程跑→OpenClaw 跑人格 agent 產 PersonaAllocationProposal/治理 handoff→approval/deployment/binding/evolution 物件鏈→OODA 模組(services/control-plane/ooda/,POST /api/ooda/packets/{id}/observe|orient|decide|act|learn|close)記 packet→.orchestrator/ooda/ooda_loop_packets.jsonl→BFF /bff/ooda/packets。驗收:建立一個 persona 後,該 persona 出現 active OODA loop;live /bff/ooda/packets count>0 且可 replay;loop-runs/evolution-programs 有料;paper only(live_capital_side_effects=False);contract+e2e test 綠。禁止造假/dry-run 充數。
