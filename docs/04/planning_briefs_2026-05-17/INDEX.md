# Planning Briefs — 2026-05-17

> 三份文件由 Claude (主席輪值) 於 2026-05-17 完成，作為後續 planning session / operational tweak 的起點

| 文件 | 性質 | 動到 L1 嗎 | 下一步 |
|---|---|---|---|
| [WAVE_CADENCE_ADJUSTMENT_PROPOSAL.md](WAVE_CADENCE_ADJUSTMENT_PROPOSAL.md) | operational proposal | 否（純 L2 工具行為） | 人類選 A/B/C → 派 OPS-WAVE-001/002/003 task |
| [BROKER_LIVE_ACTIVATION_PLANNING_BRIEF.md](BROKER_LIVE_ACTIVATION_PLANNING_BRIEF.md) | planning session kickoff | 是（PAPER_CANARY_LIVE_POLICY / KILL_SWITCH_*） | 開 `phase8-2026-05-XX-broker-live-activation-criteria` session |
| [BFF_HA_TOPOLOGY_PLANNING_BRIEF.md](BFF_HA_TOPOLOGY_PLANNING_BRIEF.md) | planning session kickoff | 是（BFF_HA_AND_CONTROL_PLANE_RESILIENCE） | 開 `phase8-2026-05-XX-bff-ha-topology-poc` session |

## 為什麼分這兩類

Wave cadence 是「工具行為的微調」，只動 `scripts/ai_status.py` 跟 `.orchestrator/` 的 wave 邏輯，不動 L1 canonical，所以可以走「proposal → 你選 → 我派 task」的 light path。

Broker live 跟 BFF HA 都動到 L1，依 `AI_COLLABORATION_GUIDE.md` § 2.5 必須走 `discussion_planning` mode：每個 lane 寫獨立 readout、Codex 起 draft、依 baton sequence cross-review、Claude 統稿、最後人類 human-gate 簽核。**單一 AI 不能拍板**。

## 啟動時機建議

- Wave cadence proposal：你看完 → 直接挑選項 → 我立刻派 OPS-WAVE-001/002/003（並行）
- Broker live session：等當前 Sprint 8 結束、Track E M7-CANARY-CLOSEOUT done 後再開（需要 M7 evidence 作為 session 輸入）
- BFF HA session：可隨時開，跟 broker live 平行，不互相阻擋

## 不在這三份內的東西

- Sprint 8 主題 — 已直接派成 execution task（OPS-DISPATCH-SPRINT-8，commit 9d1a06b1）
- Sprint 9 主題候選 — 由 Sprint 8 closeout task (SPRINT-8-CLOSEOUT) 自動產出
- Capital binding live 啟動 — 跟 broker live 高度耦合，可以是同 session 的延伸 round，或另起 session（建議：等 broker live session 中段再決定）
