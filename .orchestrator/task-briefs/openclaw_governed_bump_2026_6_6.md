# OPENCLAW-GOVERNED-BUMP-2026.6.6

## 一句話
把受治理的 OpenClaw runtime pin 從 `2026.4.7` 升到 `2026.6.6`(latest),更新所有 pin 點 + 過 OSS smoke gates,讓 dev 已上線的 2026.6.6 + 帳號式 OAuth 不會被下次部署打回降級。

## 為什麼必須升(orchestrator 已實機確認,勿重查)
- 2026.4.7 對 OpenAI/Codex 帳號**只給 localhost-callback paste-back flow**,headless VM 上極難用;且其 onboarding 會嘗試 import `~/.codex`(已不適用)。
- **2026.6.6 才有 `openclaw models auth login --provider openai --device-code`**(ChatGPT device-code flow),這是 headless 環境綁訂閱帳號的正解。dev 已用它完成登入(`openai:lupinchen@cctech-support.com`, openai/oauth),實機 agent turn 通過,**全程零 api key**。
- 2026.6.6 模型 ref 改用 `openai/gpt-5.5` + `plugins.entries.codex.enabled=true`(`openai-codex/*` 變 legacy);`openclaw doctor --fix` 會遷移 config。**dev 的 `openclaw-data` volume 上 config 已是 2026.6.6 格式 → 若部署把 image 打回 2026.4.7,舊 binary 配新 config 會壞。**

## 現況(dev 已手動套用,需入庫固化)
- running gateway image = `ghcr.io/openclaw/openclaw:2026.6.6`(我手動 recreate)。
- `/home/lupin/pantheon-ci-deploy/dev-root/docker-compose.yml` 的 tag 已被我臨時 sed 改成 2026.6.6,但 dev-root 會被 `git reset --hard origin/dev` 打回 → **repo 還是 2026.4.7,這就是要修的。**

## 要做什麼
1. 解出上游 `openclaw/openclaw` `v2026.6.6` 對應的 commit hash(取代舊 pin `5050017543011b61df67744ebc6368d889c25a95` / `v2026.4.7`)。
2. 更新**所有** runtime/版本 pin 點(`git grep -lE "2026\.4\.7|5050017543011b61df67744ebc6368d889c25a95"`),至少:
   - `docker-compose.yml`(image tag → 2026.6.6)
   - `OSS_INTEGRATION_CHECKLIST.md`、`RESEARCH_BACKEND_MATURITY_MATRIX.md`
   - `integrations/openclaw/`(governance.md, integration.md, evidence_pack.md, adapter/README.md, adapter/gateway_runtime.py, smoke_test.md, spikes/openclaw_upstream_selection.md)
   - `services/control-plane/cron/`(README.md, test_cron.py 若有 hardcode)
   - 歷史檔(ai-task-archive/*, .coordination/reviews/*, support/sidecars/*)屬既有 evidence,**不要改寫歷史**;只改「當前有效 pin」的檔。判斷不準就在 PR 說明列出改了哪些、為何跳過哪些。
3. 重跑 smoke gates 並貼證據:`bash scripts/openclaw-smoke-test.sh`、`bash scripts/openclaw-gateway-adapter-smoke.sh`(對真 `ghcr.io/openclaw/openclaw:2026.6.6`)。
4. governance.md / evidence_pack.md 補一段 bump 紀錄:理由(device-code headless 帳號 auth)、auth 模式改為訂閱 OAuth(無 api key)、config 遷移(doctor)、`~/.codex` import 已移除。
5. 順手把 deploy compose 裡 gateway 的 `OPENAI_API_KEY` env 拿掉或標記 inert(現已不被 OpenClaw 使用,shellEnv off + .env 已清),避免誤導。

## 驗收
- repo 內已無「當前有效」的 2026.4.7 runtime pin(歷史 evidence 除外)。
- 兩個 smoke gate 對 2026.6.6 實跑通過,證據貼 PR。
- governance bump 紀錄齊全(理由 + auth 模式 + config 遷移)。
- 不破壞既有 OSS adoption fail-closed scaffold 的語意。

## 禁止
- 禁止改寫 ai-task-archive / 歷史 review evidence 的版本字串(那是當時事實)。
- 禁止只改 docker-compose.yml 就收工(治理 pin 散在多檔 + 要過 smoke gate)。
- 禁止動 supervisor poll/sleep cadence。

## 相關
- pin 來源:`OSS_INTEGRATION_CHECKLIST.md`、`integrations/openclaw/governance.md`
- smoke:`scripts/openclaw-smoke-test.sh`、`scripts/openclaw-gateway-adapter-smoke.sh`
- 部署 compose:`docker-compose.yml` service `openclaw-gateway`(line ~362)
