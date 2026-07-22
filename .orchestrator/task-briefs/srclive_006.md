# Task Brief: SRCLIVE-006

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: 修 data_source_status.state badge:全綠卻顯示 partial readback
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude2
- Next: Supervisor resumed SRCLIVE-006 for finalize after successful dispatch.

## Summary
BUG:persona-tw-equity 五個 provider_statuses 全 read_ok(面板 5/5 可讀)但 summary badge 仍顯示 amber 的『partial readback』。真因:BFF _overlay_source_health_truth(services/control-plane/bff/main.py ~50386)只翻 provider_statuses,沒重算 data_source_status.state;state 仍是 read_store 靜態種子的 'partial_readback'。FE 證據(execute-plans src/management/pages/oversight/_core.tsx:355-367,465-467):badge 顏色=dataSourceTone(state),只有 state 含 read_ok|readback_ok|smoke_ok 才綠;label=formatToken(state);而『X/Y 可讀』count 是另外從 providerStatuses 算(所以 5/5 對)。修法:在 _overlay_source_health_truth 末端(provider_statuses 定稿後)加重算:若所有 provider 值都是 ok-tone(/read_ok|readback_ok|smoke_ok/)且目前 state 非 ok-tone,則 dss['state']='live_readback_ok'(formatToken→'live readback ok',dataSourceTone→ok→綠),並把 dss['summary'] 換成反映 live 全綠的句子(不要留舊的『TWSE/TPEx...default to unavailable』假敘述)。注意:(1) 只在『全 ok』時升級,US 1/7、有 credential_unavailable/failed 的維持原 state;(2) 不要動已是 ok-tone 的 state(crypto datasource_smoke_ok / us quote_readback_ok 保持);(3) 加 BFF 合約測試:全綠→state 升級且 dataSourceTone 會判綠、非全綠→不升級。完成定義:live curl persona-fleet persona-tw-equity 的 data_source_status.state 含 readback_ok(FE badge 變綠),且 US/crypto 的 badge 行為不變。

## Closeout Evidence
- Implementation PR: https://github.com/ajoe734/pantheon/pull/2545 merged into `dev` at `469d30205f8b935bd430f4dfbdc9a5d1ac7f8fd7`.
- Implementation commit: `78076b68b74c189be822b7fcb6d55b2312a3a961` (`SRCLIVE-006: fix all-green data source badge`).
- Reviewer state: `review_approved` by Claude2; review note says all-green state upgrade, non-green credential gaps, and already-ok states are correct.
- Local verification: `python3 -m pytest services/control-plane/bff/test_srclive_overlay_contract.py` -> 5 passed.
- Live verification: `curl -fsS -H 'Authorization: Bearer op-dev:admin:mfa' https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/management/persona-fleet?page_size=200 | jq ...`
  - `persona-tw-equity`: `state=live_readback_ok`, providers 5/5 read_ok.
  - `persona-us-equity`: `state=partial_readback`, providers 4/7 readable with credential gaps preserved.
- `persona-crypto`: `state=datasource_smoke_ok`, providers 2/2 readable and already-ok state preserved.
- PR branch refresh: merged `origin/dev` at `85c4cbd66bc26c8262d396181142f0905270c371` before the final closeout push for PR #2558.
- PR branch refresh: merged `origin/dev` at `f8f0ba710fe94c1c8a538316f52a4ad022fdb83b` after PR #2558 checks passed but base advanced again.
- PR branch refresh: merged `origin/dev` at `4ecd5f78fe44d5d4cf463a9c05d347819e2e0e34` after PR #2558 checks passed but base advanced to SRCLIVE-004 closeout.
