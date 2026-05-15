---
project: Pantheon
document_type: P0 System Design / Architecture Decision / Codex Implementation Packet
language: zh-TW
status: draft-for-implementation
revision: v1
baseline: >
  Based on Pantheon consolidated blueprint and latest implementation correction:
  current actual LEAN bridge is `pantheon/lean` submodule, remote `ajoe734/pantheon-lean.git`;
  `lean-platform` is not the current Pantheon execution target.
---

# Pantheon P0 SD Package — Index

## 0. 目的

本 package 將 SA 報告中的 P0 gap 轉成可施工的 System Design / ADR / Contract / Codex task packet。

這組文件的設計目標是：

1. 鎖定 execution repo authority，避免 Codex / engineer patch 錯 repo。
2. 將 `DeploymentPlan → RuntimeBinding → runtime_bootstrap.py → pantheon/lean → TelemetryEvent` 轉成明確 contract。
3. 保留目前安全姿態：paper baseline 可以推進，live 預設 fail-closed。
4. 將前端 demo/auth islands、submodule / compose / health CI cleanup 轉成可驗收任務。
5. 明確區分：
   - hard invariants
   - policy-configurable rules
   - intentional deferrals
   - non-goals
   - Codex implementation tasks

## 1. 文件清單

| 文件 | 主題 | 主要 owner | 主要輸出 |
|---|---|---|---|
| SD-P0-01 | Official Pantheon LEAN Bridge ADR | Architecture / Execution | 決定 official execution bridge 是 `pantheon/lean` / `pantheon-lean.git` |
| SD-P0-02 | DeploymentPlan → runtime_bootstrap Contract | pantheon + execution | launch manifest / runtime bootstrap contract |
| SD-P0-03 | RuntimeBinding Context Propagation | pantheon + pantheon/lean | RuntimeBinding 如何進入 PantheonAlgoBase 與 telemetry |
| SD-P0-04 | Paper Runtime TelemetryEvent Contract | pantheon + pantheon/lean | paper runtime heartbeat / pnl / fill telemetry contract |
| SD-P0-05 | Frontend Production Adoption / Demo Cleanup | front-ai-trading-system + pantheon | demo auth / demo islands cleanup plan |
| SD-P0-06 | Submodule / Compose / Health CI Verification | pantheon + CI | .gitmodules / compose / health / no-wrong-repo CI |

## 2. P0 範圍聲明

本 package 只解決 paper operating loop 與 repo authority 的 P0 問題。
本 package **不啟用 live broker trading**，也不要求 BFF HA/LB、Qlib/FinRL production activation、OpenClaw live broker adapter 立即完成。

## 3. P0 最小閉環

```text
CandidateArtifact
→ ApprovalDecision
→ DeploymentPlan
→ RuntimeBinding
→ runtime_bootstrap.py paper role
→ pantheon/lean / PantheonAlgoBase
→ Paper Runtime TelemetryEvent
→ pantheon telemetry ingest
→ BFF runtime status projection
```

## 4. 共同 hard invariants

```text
1. P0 execution work targets `pantheon/lean` submodule, not `lean-platform`, unless ADR explicitly changes the decision.
2. live role remains fail-closed and health-only by default.
3. paper runtime may run without live broker SDK.
4. no broker secret may appear in frontend, artifact payload, telemetry payload, or launch manifest.
5. every runtime event must carry runtime identity when the binding exists.
6. every command affecting runtime must carry actor_ref, trace_id, and idempotency_key.
7. BFF / frontend shall not become canonical runtime truth source.
```

## 5. 使用方式

Codex / engineer 應先讀：

```text
SD-P0-01
→ SD-P0-02
→ SD-P0-03
→ SD-P0-04
```

前端工作讀：

```text
SD-P0-05
```

CI / compose / health cleanup 讀：

```text
SD-P0-06
```

每份文件底部都有 Codex task packets，可直接拆 PR。
