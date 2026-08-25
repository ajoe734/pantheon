# Pantheon VM Dev／Staging／Prod 管理計畫

- 狀態：已確認的目標方案，尚待分階段實作
- 決策日期：2026-08-25
- 主要執行平台：Google Compute Engine VM
- 管理入口：GitHub Actions、`gcloud`、Docker Compose

## 1. 目的

本計畫要用盡量少的常駐資源，建立可管理、可驗證、可回退的三環境發布流程。

成功結果只有四個：

1. Dev 能快速整合 Pantheon BFF 與 `execute-plans` FE。
2. Staging 能在每次正式發布前重建、驗證、留下證據並自動銷毀。
3. Prod 只接受 Dev 與 Staging 驗證過的同一組不可變產物，並能快速切回上一版。
4. 平時不維持三套完整 Pantheon stack，也不為尚未出現的流量預先建立 Kubernetes、Cloud Run 或多機 HA。

最低資源目標：

```text
平時：     Dev VM + Prod control VM                         = 2 台常駐 VM
發布期間： Dev VM + 臨時 Staging VM + Prod control VM       = 3 台 VM
真實資金： Dev VM + Prod control VM + Prod execution VM     = 3 台常駐 VM
```

Staging 平時是零台 VM。真實資金 execution 是安全邊界，不因節省成本而和對外 BFF、開發工具或一般 worker 放在同一台主機。

## 2. 已確認的技術決策

### 2.1 選 VM，不在第一階段搬 Cloud Run

Pantheon 目前由 Docker Compose、多個長駐 worker、scheduler、projector、Postgres、NATS、MinIO 與具 ownership 的執行服務組成。VM 能直接承接這些特性，改動與營運風險最低。

Cloud Run 第一階段不採用，原因不是它不能執行 container，而是整套搬遷需要先完成：

- BFF 完全無狀態化。
- 本機檔案、資料庫與訊息系統外移。
- scheduler、singleton worker 與長期訊息 consumer 拆離 HTTP service。
- 重新設計 VPC、連線池、冷啟動與背景工作模型。

未來只有無狀態 BFF 或短期 Job 可以重新評估 Cloud Run。這不是本計畫的必要前置條件。

### 2.2 VM 與 `gcloud` 不是替代關係

- VM 是服務實際執行的位置。
- `gcloud` 是 GitHub Actions 用來建立、查詢、啟停及刪除 VM、磁碟與 snapshot 的管理工具。
- Docker Compose 管理 VM 內的服務。
- Caddy 管理 HTTPS、FE 靜態檔案與 BFF upstream 切換。

第一階段直接使用可重跑的 shell script 加 `gcloud`。資源形狀穩定後再決定是否轉成 Terraform，不把導入 Terraform 當成開始部署的阻擋條件。

### 2.3 不建立永久 Staging

Staging 是每個 release 的驗證資源，不是長期共用伺服器。每次發布建立一台具 release ID 與 TTL 的 VM，驗證完成後銷毀。

### 2.4 FE 與 BFF 是「配對版本」，不是同一個 SHA

FE 與 BFF 位於不同 repository，因此不會有同一個 Git SHA。一次 release 必須記錄：

- `execute-plans` FE commit。
- `pantheon` BFF/backend commit。
- FE artifact checksum。
- BFF image digest。
- compatibility manifest digest。
- 建置 workflow 與測試證據。

這組資料共同形成 `release_id`。任何一項改變都建立新 release，不在晉級途中偷換單一 component。

## 3. 現況基線

本節描述 2026-08-25 的事實，不代表目標能力已全部完成。

### 3.1 Dev

- GCP project：`pantheon-lupin-dev-20260719`。
- VM：`pantheon-lupin-dev`。
- 區域：`asia-east1-b`。
- 對外 FE：`https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io`。
- 對外 BFF：`https://pantheon-lupin-dev-bff.35.201.204.12.sslip.io`。
- Pantheon compose project：`pantheon`。
- FE repository：`ajoe734/execute-plans`，發布分支為 `dev`。
- BFF/backend repository：`ajoe734/pantheon`，發布分支為 `dev`。

現有 `Pantheon Nonprod Deploy` 已具備 exact FE/BFF pair admission、受管 deploy worktree、BFF exact-version probe、FE 原子 symlink 切換，以及 FE 失敗時補償 BFF 的能力。這些治理保留，不另造第二套 release authority。

### 3.2 Staging

舊 `pantheon-benjamin-20260528` project 已停用。舊 staging control/exec 雙 VM 只保留為歷史拓撲，不是可用的部署目標。

因此目標 ephemeral staging 在完成實作與 acceptance 前，必須標記為 `unavailable`；不得把舊 hostname、舊 VM 或範例 env 當作 staging 通過證據。

### 3.3 Prod

目前沒有可接受的 production GitHub Environment、正式 project、部署 lane 或 hosted identity 證據。本計畫不把「文件已合併」解讀成 production 已建立。

## 4. 目標拓撲

```text
GitHub Actions
  │
  ├── build once ──> Artifact Registry / release evidence
  │                         │
  │                         ├── exact release ──> Dev VM
  │                         ├── exact release ──> Ephemeral Staging VM
  │                         └── exact release ──> Prod control VM
  │
  └── gcloud
        ├── inspect/update permanent VMs
        ├── create/delete staging VM and disks
        └── create snapshots and collect resource identity

Non-production project
  ├── pantheon-lupin-dev                 always on
  └── pantheon-stg-<release-id>           normally absent

Production project
  ├── pantheon-prod-control               always on
  └── pantheon-prod-exec                  required only for real-capital mode
```

### 4.1 常駐共用資源

以下資源不需要每個 release 重建：

- GCP projects、VPC、subnets 與 firewall policy。
- Artifact Registry。
- Workload Identity Federation 與 deployment service accounts。
- DNS/HTTPS routing 基礎設定。
- Secret Manager secret definitions。
- Log、metric 與 alert backend。
- Staging instance template。

### 4.2 Staging 短生命週期資源

每個 release 才建立：

- 一台標準 Compute Engine VM。
- 一個獨立 boot disk。
- 一個由 sanitized snapshot 還原的 staging data disk。
- release-specific compose project、hostname、service account binding 與 evidence path。
- 必要時才建立的 temporary execution VM。

每項資源必須有：

```text
environment=staging
release_id=<release-id>
owner=<workflow-run>
created_at=<RFC3339>
expires_at=<RFC3339>
cleanup_policy=automatic
```

正式 release gate 使用標準 VM。Spot VM 只允許用於可自動重跑、被中斷也不影響 release 判定的預演。

## 5. 環境責任與設定

| 項目 | Dev | Staging | Prod |
| --- | --- | --- | --- |
| 生命週期 | 常駐 | 每個 release 臨時建立 | 常駐 |
| 主要用途 | 快速整合 | 正式發布演練 | 使用者服務 |
| Git 來源 | exact protected `dev` tips | 已通過 Dev 的 release manifest | 已通過 Staging 的相同 manifest |
| 外部 provider | 關閉 | 關閉或 sandbox | 逐項明確批准 |
| 真實 writes | 預設關閉 | 預設關閉 | 依 production policy |
| 資料 | Dev 專用 | sanitized snapshot | 正式資料 |
| Secrets | `dev` scope | `staging` scope | `production` scope |
| 人工批准 | 不需要 | 建立不需要；失敗不得晉級 | 切換前需要 |
| 自動銷毀 | 否 | 是 | 否 |

三個環境不得共用資料庫、service account、broker credential、JWT signing secret 或 machine-local `.env`。

## 6. VM 內的服務分層

不能把「有 60 多個 Compose service」解讀成每個環境必須全天啟動全部服務。實作時把 Compose 啟動集合收斂成以下 profiles：

### 6.1 `core`

每個環境的最低服務集合：

- Postgres。
- NATS；只有實際依賴時才啟動 Redis/MinIO。
- operator BFF 與必要 adapter。
- lifecycle/read-model projector。
- 發布驗證必要的 owner service。
- Caddy 與健康檢查。

### 6.2 `workers`

- scheduler。
- queue consumer。
- reconciliation、evaluation 與 policy worker。
- 非同步 projector。

只有測試或正式工作需要時啟動。Singleton worker 在任何環境都必須只有一個 owner。

### 6.3 `research`

研究、資料擷取、分析與模型相關服務。預設不因 FE/BFF release 啟動；由專屬測試或研究工作明確開啟。

### 6.4 `management-ai`

OpenClaw 與產品診斷相關服務。它們是 product runtime，不取得 repository、shell 或 deployment authority。

### 6.5 `execution`

runtime manager、broker adapter、paper/live execution 與 execution-only telemetry。Dev 預設關閉；Staging 使用 sandbox/paper；真實資金只在獨立 Prod execution VM 啟動。

每個 container 都必須設 memory/CPU limit。VM 尺寸不得憑服務數量猜測，而是以 `core` 峰值、發布時額外一個 BFF candidate，以及至少 30% 可用記憶體餘裕量測後決定。

## 7. Release manifest 與不可變產物

### 7.1 Build once

同一 release 的 FE 與 BFF 各 build 一次。Dev、Staging、Prod 不重新 build，而是部署同一份 artifact checksum/image digest。

Human-readable tag 可以存在，但部署身份必須使用 digest 或 checksum。`latest`、未解析 branch name 與 VM 當下 checkout 都不能成為 production source of truth。

### 7.2 Manifest 最小格式

```json
{
  "schema_version": 1,
  "release_id": "pantheon-YYYYMMDD-NNN",
  "frontend": {
    "repository": "ajoe734/execute-plans",
    "commit": "40-char-sha",
    "artifact_sha256": "sha256:..."
  },
  "backend": {
    "repository": "ajoe734/pantheon",
    "commit": "40-char-sha",
    "images": {
      "operator_bff": "...@sha256:..."
    }
  },
  "compatibility_manifest_sha256": "sha256:...",
  "migration_set_sha256": "sha256:...",
  "config_schema_version": "...",
  "created_by_workflow": "owner/repo/actions/runs/id",
  "created_at": "RFC3339"
}
```

Manifest 進入 Staging 後不可改寫。FE、BFF、migration、configuration schema 或 compatibility evidence 任一改變，都產生新 `release_id` 並重新走 Dev、Staging。

### 7.3 Evidence bundle

每個 release 保存：

- release manifest 與 checksum。
- CI、security、compatibility 與 integration 結果。
- 三環境 deployment identity。
- migration dry-run、backup/restore 與 rollback rehearsal 結果。
- Staging 資源建立與清除證據。
- production approver、時間、workflow run 與 observation 結果。
- 最後可用 rollback target。

Evidence 記錄身分與結果，不保存 token、secret value、完整 `.env` 或 production data。

## 8. Dev 管理流程

### 8.1 觸發

Dev 不因每個普通 push 自動重部署。當兩個 repository 的 protected `dev` tips 形成準備驗證的 pair 時，由 `Pantheon Nonprod Deploy` 明確啟動 exact-pair release。

### 8.2 部署順序

1. 解析 Pantheon 與 execute-plans exact `dev` SHA。
2. 建立 compatibility manifest 與 release candidate ledger。
3. 記錄目前 hosted FE/BFF pair 作為 rollback baseline。
4. 建置並封存不可變 FE artifact 與 BFF image。
5. 部署 BFF candidate，驗證 `/health`、`/readyz`、`/bff/version` 與 CORS。
6. 使用 exact BFF 執行 FE integration gate。
7. 在變更 live symlink 前驗證 FE candidate。
8. 切換 FE，驗證 `deployment.json` 的 FE/BFF pair。
9. 若 FE 階段失敗，恢復先前 BFF 並證明 FE/BFF 均回到 baseline。
10. 成功後把 release 標記為 `dev_verified`。

### 8.3 Dev 接受條件

- Hosted BFF 回報 exact backend commit。
- Hosted FE manifest 回報 exact frontend commit 與配對 backend commit。
- `VITE_BFF_MODE=live`、`VITE_BFF_FALLBACK=strict`。
- Real writes 與 dev stub writes 維持 false，除非有具期限的明確操作授權。
- Browser smoke、auth readiness、CORS 與關鍵 read path 通過。
- Dev 部署失敗後沒有留下半套 candidate pair。

## 9. Ephemeral Staging 管理流程

### 9.1 建立前 gate

只有 `dev_verified` release 可以建立 Staging。Workflow 先驗證：

- release manifest checksum 未變。
- FE/BFF artifact 仍可取得且 digest 一致。
- Staging project、quota、instance template、service account 與 sanitized snapshot 可用。
- 同一 release 沒有另一個活動中的 Staging。

### 9.2 建立流程

```text
validate manifest
  -> create VM and disks
  -> attach staging-only service account
  -> checkout deployment controller
  -> deploy exact artifacts
  -> restore sanitized data
  -> run migrations
  -> start core profile
  -> run tests
  -> start optional profiles only when required
```

預設一台 VM 即可。只有下列 release 才臨時增加第二台 execution VM：

- 修改 runtime manager 或 broker adapter。
- 修改 control-to-execution network contract。
- 修改 kill switch、order lifecycle 或 capital-impacting path。
- 準備啟用真實資金模式。

這樣一般 FE/BFF release 不支付雙 VM staging 成本，但 execution release 仍能演練正式隔離邊界。

### 9.3 Staging 驗證集合

必要 gate：

- VM、磁碟、compose project 與 deployed digest 身分正確。
- FE/BFF compatibility、API integration、browser smoke、RBAC 與 tenant isolation。
- Provider-off、real-write-off、live-broker-off readback。
- Migration forward rehearsal。
- 舊 application 對新 schema 的相容性檢查。
- Scheduler/worker singleton ownership 與 queue 行為。
- Backup 建立、還原與資料可讀驗證。
- Application rollback 至上一 release。
- 清除腳本 dry-run 與資源 inventory 對帳。

Execution release 額外需要：

- Control VM 只能透過內部網路到 Execution VM。
- Broker secret 只存在 Execution VM。
- Paper/sandbox order、cancel、kill switch 與 fail-closed 測試。
- Control 或 Execution 任一側中斷時，不會自動轉成不受控交易。

### 9.4 TTL 與銷毀

- 成功：完成 evidence upload 後立即銷毀。
- 失敗：預設保留 2 小時供除錯。
- 需要人工檢查：可延長但總生命週期不得超過 24 小時。
- 定時 cleanup workflow 必須獨立於正常 teardown，清理 workflow 中斷後留下的孤兒資源。
- 銷毀前保存 bounded logs、manifest、test summary 與資源清單。
- 銷毀 VM、boot disk、temporary data disk、temporary IP 與 release-specific IAM binding。

Staging snapshot 不含 production secret、未遮罩 PII 或 broker credential。Snapshot 本身有明確 retention，不能因 VM 已刪除而永久累積。

## 10. Prod 管理流程

### 10.1 Production 建立前條件

正式部署 lane 開通前，至少完成：

- 獨立 production GCP project。
- `production` GitHub Environment、required reviewer 與 prevent-self-review。
- Production-only service account、WIF、Secret Manager 與 IAM。
- Prod VM、data disk、firewall、HTTPS routing、monitoring 與 backup policy。
- 經 Staging 實測的 bootstrap、deploy、rollback 與 restore runbook。
- 明確 RPO、RTO、on-call 與 rollback owner。

缺少其中任何一項時，release 只能停在 `staging_verified`，不能把 Dev 或 Staging hostname 當 Prod。

### 10.2 低資源 Prod 形狀

第一版 Prod control VM 維持一份 stateful core，只在發布期間短暫啟動第二個 request-facing candidate：

```text
Caddy
  ├── FE current symlink -> blue FE release directory
  └── BFF upstream       -> blue BFF container

Candidate validation
  ├── green FE release directory
  └── green BFF container on alternate local port

Shared, never duplicated during switch
  ├── Postgres on separate persistent data disk
  ├── NATS / MinIO when required
  └── singleton schedulers and workers
```

Blue/green 解決版本切換與快速回退，不提供 VM-level HA。Prod VM 故障仍可能造成中斷；這是低資源第一版明確接受的限制，不得宣稱為高可用。

### 10.3 Production 發布

1. 驗證 `staging_verified` manifest、artifacts、evidence 與 approval。
2. 建立 pre-deploy database snapshot，記錄 snapshot identity。
3. 驗證 blue rollback target 仍存在且健康。
4. 以 alternate port 啟動 green BFF，不先接公開流量。
5. 建立 green FE release directory，不改 current symlink。
6. 對 green 執行 health、version、auth、CORS、read-only 與 schema compatibility smoke。
7. 確認舊 FE/新 BFF、新 FE/舊 BFF 在短切換窗內均符合 compatibility contract。
8. 暫停或 drain singleton scheduler/worker。
9. 切換 BFF upstream，再原子切換 FE symlink。
10. 驗證 hosted deployment manifest 及關鍵使用者流程。
11. 恢復 singleton scheduler/worker，確認 owner 唯一。
12. 進入 observation window。
13. 成功後標記 `prod_active`；保留 blue 直到 rollback retention 到期。

FE/BFF 不可能在兩個不同 endpoint 上做到真正同一 CPU instruction 的原子切換，因此 compatibility contract 與補償式 transaction 是必要條件。任一步驟失敗，都必須把兩者恢復到完整 baseline pair，而不是留下混合版本。

### 10.4 Observation window

第一版使用 30 分鐘觀察窗；實際閾值在 production 開通前依基線量測寫入 environment policy。至少監看：

- BFF 5xx/error rate 與 P95/P99 latency。
- `/health`、`/readyz` 與 exact version identity。
- Authentication、authorization、tenant isolation 異常。
- DB connection、disk、CPU 與 memory saturation。
- Queue backlog、worker retry、scheduler ownership。
- 關鍵 read/write business flow 成功率。
- Execution 模式下的 order lifecycle、kill switch 與 broker session。

健康檢查失敗、版本錯誤、ownership 重複或安全邊界失敗立即 abort。其他 SLO 指標需有具體 threshold 與持續時間，不能只依人工看 dashboard 感覺判斷。

## 11. 資料庫、Migration 與 Backup

### 11.1 環境隔離

- Dev 使用 Dev 專用資料與磁碟。
- Staging 每次由 sanitized snapshot 還原獨立 data disk/database。
- Prod 使用 production-only database 與獨立 persistent data disk。
- 三環境不共用 schema 或 database credential。

### 11.2 Expand/contract migration

每次 migration 必須：

1. 先新增向後相容欄位或結構。
2. 讓 blue 與 green application 都能使用新 schema。
3. 切換 application。
4. 經過至少一個 rollback retention window。
5. 在後續獨立 release 才移除舊結構。

不可逆 migration 不與 FE/BFF traffic switch 綁成單一步驟。Application rollback 不等於資料 rollback。

### 11.3 Backup policy

- Prod 部署前建立 checkpoint snapshot。
- Prod 有排程 snapshot 與異地/不同 failure domain 的必要備份。
- Staging 每個 release 執行 restore rehearsal；只建立 snapshot 不算已驗證復原。
- 定期執行 production-shaped restore drill，但不把正式資料還原進 non-production。
- Retention、RPO 與 RTO 在 production environment 開通前由 operator 明確批准。

若未來要求低於單 VM + snapshot 能提供的 RPO/RTO，下一步是外移資料庫或使用 managed database，而不是在同一 VM 上增加更多 container。

## 12. Secrets、IAM 與網路

### 12.1 Secrets

- Git repository、release manifest、artifact 與 evidence 都不保存 secret value。
- GitHub Environment 只持有 workflow 取得短期 GCP 身分所需設定與無法避免的 CI secret。
- Runtime secret 優先由 GCP Secret Manager 或 machine-local protected env 取得。
- Dev、Staging、Prod secret name 與 service account 分離。
- Broker/TWS/exchange secret 只存在 execution boundary。
- Secret rotation 不藉由重新 build image 完成。

### 12.2 IAM

- GitHub Actions 使用 WIF/短期身分，不保存長期 GCP JSON key。
- Dev deployer 不能管理 Prod。
- Staging deployer 只能建立帶指定 labels、template 與 TTL 的資源。
- Prod deployer 只能部署已批准 manifest，不能讀取 broker secret。
- Production approval 與 release initiation 分權。

### 12.3 Network

- 對外只開放必要 HTTPS endpoint。
- Postgres、NATS、MinIO、runtime-manager 與 execution API 不直接暴露 public internet。
- SSH 只允許受管 CI transport、IAP 或明確受限來源，並驗證 host identity。
- Prod control 到 execution 只走內部網路及明確 firewall rule。
- FE origin、BFF CORS、OIDC issuer/audience 與 tenant scope 都是 manifest/evidence 的驗證項目。

## 13. GitHub Environments 與發布狀態

需要三個 GitHub Environments：

| Environment | 保護方式 | 可執行動作 |
| --- | --- | --- |
| `dev` | exact-pair admission、shared deployment lease | 部署目前 protected `dev` pair |
| `staging` | 只接受 `dev_verified` manifest、concurrency 1 | 建立/驗證/銷毀 staging |
| `production` | required reviewer、prevent self-review、concurrency 1 | 部署 green、切換、rollback |

Release 狀態保持單純：

```text
candidate_built
  -> dev_verified
  -> staging_provisioned
  -> staging_verified
  -> production_approved
  -> prod_green_verified
  -> prod_active
  -> release_complete
```

失敗狀態至少包含：

```text
dev_failed
staging_failed
prod_aborted
prod_rolled_back
cleanup_failed
```

開發 supervisor 管 development task 與 worker lease；GitHub Environment/workflow 管 deployment。不要把 product BFF、Management AI 或 supervisor 任一方擴張成另一套部署入口。

## 14. 回退與故障處理

### 14.1 Application rollback

- Caddy BFF upstream 指回 blue。
- FE current symlink 指回配對 blue release directory。
- 驗證 hosted FE/BFF pair、health、auth 與關鍵流程。
- singleton worker 回到上一版且 owner 唯一。

### 14.2 Configuration rollback

- Config 以 immutable version/reference 管理。
- 回復上一版 config reference，不手動拼湊 VM 上的 `.env`。
- Secret rotation 與 config rollback 分開處理。

### 14.3 Data recovery

- 只有資料毀損或 migration 無法 forward-fix 時才進入 data recovery runbook。
- 先停止可能繼續寫入的 service。
- 使用已驗證 snapshot/backup 建立新 disk/database，再切換 reference。
- 不直接覆寫唯一正式磁碟。

### 14.4 VM 故障

第一版單 VM Prod 的復原方式是：

1. 從 instance template/machine image 建立替代 VM。
2. 掛載 last-known-good data disk 或由 snapshot 建立新 disk。
3. 部署 last-known-good release manifest。
4. 驗證後切換 DNS/routing。

這是 disaster recovery，不是 automatic HA。若量測後的 RTO 不符合業務要求，就升級為多 VM/load balancer 或 managed database。

## 15. 成本與資源控制

### 15.1 固定成本

- 一台 Dev VM。
- 一台 Prod control VM。
- 真實資金模式增加一台最小必要 Prod execution VM。
- Persistent disks、snapshot、Artifact Registry 與必要 logs。

### 15.2 變動成本

- Staging VM 只按 release 驗證時間存在。
- Staging temporary disks/IP。
- 發布期間同機 green BFF 的短暫額外 CPU/RAM。
- Cloud Build/Artifact Registry transfer 與測試流量。

### 15.3 防止浪費

- Staging TTL 與獨立 cleanup schedule。
- Compose profiles，不啟動 release 不需要的服務。
- Container resource limits 與 VM memory headroom gate。
- Artifact、snapshot、log retention policy。
- Budget alerts 與按 environment/release ID 的 labels。
- Staging final gate 不使用不可預測的 Spot VM。
- 不建立永久 staging load balancer、GKE cluster 或第三套全天運作 stack。

Dev 是否夜間關機由 supervisor/worker 是否需要 24 小時運作決定；不能只為省 VM 費用而讓 development tooling 與 product runtime 在不明狀態下被排程停止。

## 16. 實作階段

### Phase 0：盤點與量測

交付與基線成果（詳見 [`docs/deployment/evidence/ops-vm-env-phase0-20260825/`](evidence/ops-vm-env-phase0-20260825/README.md)）：

- `core`（14 服務）、`workers`（17 服務）、`research`（24 服務）、`management-ai`（8 服務）、`execution`（4 服務）完整盤點與 singleton 邊界：[`service-inventory.md`](evidence/ops-vm-env-phase0-20260825/service-inventory.md)。
- Dev 現況 CPU（12 vCPU）、memory（47.04 GiB 總量、32.55 GiB 可用、50 container 使用 11.04 GiB / 11,303.08 MiB）、disk（241.13 GiB 88% 使用率）、uptime（420h+）、開機/啟動耗時與部署耗時基線：[`measurements-baseline.json`](evidence/ops-vm-env-phase0-20260825/measurements-baseline.json)。
- FE/BFF 配對發布、DB expand/contract migration、worker singleton lifecycle 與 backup/restore 四大領域能力差距矩陣：[`capability-gap-matrix.md`](evidence/ops-vm-env-phase0-20260825/capability-gap-matrix.md)。
- 依 `core` 峰值（4.18 - 4.31 GiB，含切換期候選 BFF 與主機 OS/Caddy 緩衝）加 $\ge 30\%$ 餘裕之 instance sizing 建議（Prod Control 建議 `e2-standard-2`，提供 48.9% 記憶體餘裕）：[`instance-sizing-recommendation.md`](evidence/ops-vm-env-phase0-20260825/instance-sizing-recommendation.md)。
- Production RPO（<1h）、RTO（<30m）、real-capital scope（Phase 4）與 on-call owner 決策明確標記為待營運批准：[`production-gates.md`](evidence/ops-vm-env-phase0-20260825/production-gates.md)。

完成條件：能以量測結果選 instance size，不以完整 service 數量推測（已由 `OPS-VM-ENV-PHASE0-20260825` 完成實測基線）。

### Phase 1：不可變 release 與 profiles

交付：

- Compose profiles 與 resource limits。
- Release manifest schema、builder、checksum 與 evidence layout。
- FE artifact/BFF image build-once。
- 現有 Dev exact-pair transaction 改為部署 manifest/digest。

完成條件：Dev hosted identity 可由 `release_id` 反查 FE/BFF commit、artifact、build run 與 rollback baseline。

### Phase 2：Ephemeral Staging

交付：

- Staging instance template。
- `provision`、`deploy`、`verify`、`collect`、`destroy` workflow/jobs。
- Sanitized snapshot pipeline。
- TTL labels、scheduled cleanup 與 orphan reconciliation。
- Migration、restore、rollback 與 provider-off gates。

完成條件：連續兩個 release 能從零建立 Staging、通過驗證、銷毀，事後 inventory 無孤兒 VM/disk/IP/IAM binding。

### Phase 3：低資源 Prod control

交付：

- 獨立 production project、GitHub Environment、IAM、secrets 與網路。
- Prod control VM、persistent data disk、Caddy 與 monitoring。
- 同機 FE/BFF blue/green、補償式 pair switch 與一鍵 rollback。
- Backup、restore、VM replacement 與 observation runbooks。

完成條件：使用非真實資金資料連續完成兩次 production-shaped deploy/rollback drill，並符合已批准 RPO/RTO。

### Phase 4：真實資金 execution 邊界

只有 operator 明確決定啟用 real-capital mode 才進行。

交付：

- 獨立 Prod execution VM、service account、subnet/firewall 與 broker secret boundary。
- Control/Execution 內網 contract。
- Kill switch、order/cancel、session loss、control loss 與 fail-closed drill。
- Capital-impacting release 的 ephemeral dual-VM Staging profile。

完成條件：所有 live-readiness gate 與人工 approval 通過；單 VM control 模式仍不得持有 broker credential 或啟用真實交易。

### Phase 5：需要時才擴充

只有觀測資料或業務要求觸發才做：

- BFF 無狀態化後評估 Cloud Run。
- Prod 第二台 control VM 與 load balancer。
- Database 外移、PITR 或 managed HA。
- Terraform 化已穩定的 GCP resource model。
- Canary percentage routing 與自動 abort。

## 17. 不在本計畫第一階段做的事

- 不導入 Kubernetes/GKE。
- 不把整套 Pantheon 搬到 Cloud Run。
- 不建立永久 Staging。
- 不同時重寫 CI、task authority 與 deployment authority。
- 不讓 product BFF 或 Management AI 取得 VM/repository 部署權限。
- 不使用舊 suspended project 或歷史 staging endpoint。
- 不在沒有獨立 execution boundary 時啟用真實資金交易。
- 不把相同 Git SHA 誤當成相同 build artifact；必須驗證 digest/checksum。

## 18. 最終驗收標準

整體計畫完成時，任一 production release 都能回答並證明：

1. FE 與 BFF 各是哪個 commit、artifact checksum/image digest？
2. 這組 pair 在 Dev 與哪個 ephemeral Staging 被驗證？
3. Migration、backup restore 與 application rollback 是否實際演練？
4. 誰批准 production，實際切換的是哪個 manifest？
5. Hosted FE/BFF、worker 與 schema 是否與 manifest 相符？
6. 發生故障時，上一組完整 pair、設定與資料 recovery target 在哪裡？
7. Staging 是否已銷毀，且沒有孤兒資源持續計費？
8. 若涉及真實資金，broker secret 與 execution runtime 是否仍在獨立邊界？

只完成 workflow 或成功 build 不算部署成功；只有 hosted identity、測試 evidence、approval、observation 與 cleanup 全部完成，release 才能標記為 `release_complete`。

## 19. 相關現行文件

- [`nonprod-ci-cd.md`](nonprod-ci-cd.md)：目前 Dev 與歷史 staging-live workflow 行為。
- [`staging-live-topology.md`](staging-live-topology.md)：目前可用 Dev 與已停用 staging 拓撲事實。
- [`single-vm-runbook.md`](single-vm-runbook.md)：現有單 VM Compose 操作基線。
- [`execute-plans-dev-hosting.md`](../frontend/execute-plans-dev-hosting.md)：目前 Dev FE host、build 與 BFF contract。
- [`development-tooling-product-boundary.md`](../02-architecture/development-tooling-product-boundary.md)：development tooling、product runtime 與 deployment authority 邊界。

外部平台能力參考：

- [Google Compute Engine instance creation overview](https://cloud.google.com/compute/docs/instances/instance-creation-overview)
- [Google Compute Engine snapshots](https://cloud.google.com/compute/docs/disks/snapshots)
- [GitHub deployment environments](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [Cloud Run overview](https://cloud.google.com/run/docs/overview/what-is-cloud-run)；僅供 Phase 5 評估，不是本計畫第一階段平台。
