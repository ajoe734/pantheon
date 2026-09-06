# Strategy / Registry 寫入前置能力複驗

取證：2026-09-06 01:08–01:12 UTC。Baseline 為 `471dc5391a0f9cbde54d51730891583043708e42`，
唯讀 checkout `/tmp/pantheon-closure-audit-20260906.HKFCSg`。
這是 root 與獨立 agent 的 source audit，不是 canonical reviewer approval、hosted 或實際交易驗收。

## 最新正式處理（01:25 UTC）

### 01:30正式readback更新

V1在01:25:23因BP5-SVC-002只有legacyarchive、不是V2可解析canonical dependency而拒收，
沒有materialize任何task；拒收receipt完整保留，不能記為admitted。
V2只修正graph edge：BP5保留sourceprovenance，唯一dependency為canonicaldone DOMAIN-WRITERS-001；
所有owner能力／positive／strictauth／replay／scope要求不變，另存新immutable文件，不修改V1簽章。

V2於01:28:30queued，01:28:53 processed/admitted/errors=[]，01:29:31 canonical in_progress，
真實Claude worker running，reviewer Antigravity，26exact artifacts。
Packet digest `68e3cf3b1ebe098215f4d4787b98af128300dfebe56e5ddb0c0121ee67f223b5`。
[生效V2 SA/SD](REGISTRY_STRATEGY_PREREQUISITE_SA_SD_20260906_V2.md)，SHA256
`a028f255638346bdeb050d46d3daf7fdc86c141cb7405ea222342f446dde6a3f`。
OriginalOverlay仍blocked；兩個必要testartifacts已正式增列，但未接受其fakewriter/restart proof。
[Checkpoint複核與兩項實際migration負例](OVERLAY_CHECKPOINT_REVIEW_20260906.md)。

以下01:25內容按原時間保留，V1pending描述由本節取代。

- Owner 已於01:19:26提交真正 authenticated blocker：Overlay canonical blocked，PR5618 checkpoint
  `98a29570061dbf7f0b2102d6a8154fe7882745a5`，不是review/merge acceptance。01:21回讀generation5，
  supervisor保守fence/held，沒有把blocked owner再次強派。
- 01:19:32 supervisor將已停止worker的worktree封存後移除；archive
  `/tmp/pantheon-worker-worktree-archive/overlay-retire-001-20260906T011932Z-1801618`
  有64個copied files、0 skipped，以及diff.patch/diff-staged.patch。未提交內容有恢復資料，
  root未刪檔或恢復/覆寫worker內容。
- Authoritative V2 seq2519複驗14個unfinished tasks：無Registry/Strategy-adapter對應owner。
  經第二次plan review補齊strict既有JWT驗證、正向create/draft/metadata/revision能力、
  commit後response-lost及舊版本replay語義後，已於01:25:02透過現有signed bridge queued：
  `REGISTRY-STRATEGY-DURABILITY-PREREQUISITE-001`，26 exact artifacts，Claude/Antigravity，
  depends only terminal BP5-SVC-002、DOMAIN-WRITERS-001，不指回Overlay。
- [Immutable SA/SD](REGISTRY_STRATEGY_PREREQUISITE_SA_SD_20260906.md)，
  SHA256 `1fae1dc08121ef81e158d41f45cb9e7b7ddfec6100f01583c2ab62effeb0fea4`。
  Packet `pkt-registry-strategy-durability-prerequisite-20260906-v1`；queued不是admitted，待receipt。
- 明確分階段：Registry owner capability不宣稱目前未傳Authorization的Workshop caller已相容；
  原AGORA-CHAIN-001的agora/**範圍可承接該caller，Overlay承接Strategy ports/composition。
  必須完成consumer positive gates才能rollout，禁止匿名/body identity fallback。
- Exact checkpoint回讀仍有collection.py optional writer及私有rs._data寫入；owner文字
  「已移除process-local fallback」過度概括。503確有改善，但未通過真正writer/多副本驗收。
  其101passed是owner checkpoint報告；尚未root以乾淨exact-head重跑，不冒稱fresh-head驗證。

以下01:08–01:12來源盤點保留；其「尚未建立prerequisite」為當時狀態，由本節正式進展取代。

## 1. 結論與既有任務邊界

Strategy 不是只差 router 接線。既有 Registry owner 有 API，但 entry store 仍是 process-local；
現有 Strategy command adapter 回條並不來自此 API。不能把其中任一項稱為已存在的 durable writer。

`DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001` 已正式 admitted/todo，但其精確 artifacts 不包含
`services/registry/**` 或 `bff/command_adapters/strategy_adapter.py`，原 section E 也不是任意
Strategy metadata 更新設計。不能靠 Human/Ops note 就把這個缺口偷偷塞進它的 contract。
目前正核對其他 active/archived owner tasks；尚未建立新的 Registry prerequisite，也未改 Overlay artifacts。

原 Overlay acceptance 仍須完整達成。必要路徑是先補既有 owner 能力，再在原 Overlay scope
接單一 typed command/query；不能為先關 Overlay 而降格 fresh-process/multi-replica 驗收。
若需要調整 active task 的 scope/排序，owner 應保留 scoped checkpoint 並正式提交 authenticated blocker；
不能手改 dependencies，不能把目前排在 Overlay 後面的 corrective 反向加回形成 cycle。

## 2. 實際現有 owner 與限制

| 位置（皆相對 baseline checkout） | 實際行為 | 不能推論的事 |
| --- | --- | --- |
| services/control-plane/bff/command_adapters/strategy_adapter.py:83–112 | action 映射 status，直接組 receipt 與 authoritative_readback dict | 沒有 Registry write/readback，不代表真的 pause/activate/update |
| services/registry/service.py:67–71；storage.py:23–31,197–202 | RegistryService(get_store()) 固定用 lock＋兩個 dict 的 RegistryStore | storage_ref 的 DB/GCS 只是 artifact reference，不是 entry durable backend |
| services/registry/service.py:423–449 | POST /api/registry/strategy-specs；固定 registry_id 可 create-if-absent | 不等於 tenant-scoped、跨重啟 Idempotency-Key contract |
| services/control-plane/bff/agora/strategy_workshop/operations.py:164–186 | 真正 POST 後 GET、核對 registry_id/strategy_id | 同一 memory owner 的立即 readback 不證明 restart/replica |
| services/control-plane/bff/ports/read_surface_ports.py:472–476；ports/research_knowledge_source.py:409,1407,1479 | 委派 research port，仍讀其 _strategy_specs dict | 不會自動讀回 Registry 新資料 |

### 建立與更新不可混為一談

Registry StrategySpec create request 在 service.py:88–101、138–202；需要 strategy_id、semver、
lineage/source seed，及 storage ref/checksum 或 inline strategy_spec；只允許 draft/candidate。
現在 BFF 只要求 name 的建立流程，不能假裝已具備以上 schema/lineage。

既有版本更新流程在 Agora strategy_workshop/routes/versions.py:345–395：驗證 base digest、
合法 patch/schema、增加 semver、註冊 parent lineage 新 draft。StrategyArtifact mutable-parameter
child revision（Registry service.py:553–585）不是 StrategySpec，也不是 BFF metadata PATCH。
UI name/risk/derived metrics 與不可變 spec version 的 owner 欄位必須明確；禁止把 derived values
寫成另一本產品真相或複製整套 Agora workflow 當新 Strategy store。

### 身分與衝突

Registry service.py:212–234 比對固定 ID 重送內容，不同內容拒絕；無 ID 則隨機建立。
目前 Registry routes 無對應 tenant/actor 篩選。Workshop 上層有 scope/MFA/If-Match/key，
但 operations.py:104–108 只送 Accept/Content-Type。記在 metadata 的 tenant/user
不等於 owner 本身已執行授權。應保留已有合法驗證，补實際 owner isolation 與 CAS/replay，
不得偽造 token、controller proof、lineage 或 downstream_verified。

## 3. 有界、離線的實際來源負向證據

Probe：`/tmp/pantheon-strategy-owner-negative-probe-20260906.py`；25 秒外層 timeout，
使用 env-i、PYTHONDONTWRITEBYTECODE、Python audit hook 阻止網路/子程序/檔案修改。
本次執行 exit 0，source hashes 前後相同，沒有載入 BFF main。

- 實際 RegistryStore A 建立 synthetic record：A 可讀，另一新 RegistryStore B 不可讀。
  這是 fresh-instance 反例，不冒稱已做真實 service restart 或 multi-replica acceptance。
- 執行原 `_execute_strategy_action` AST method body，僅 receipt formatter 換成記錄 double：
  對 synthetic nonexistent strategy 的 pause 仍組出 `status=paused` 與
  `authoritative_readback.status=paused`；不需任何外部 owner 符號/呼叫。
  這是 method-body 證據，不冒稱已走 HTTP/hosted mutation。

Source SHA256：

- registry/storage.py：`f77819e0464cf384d200a7a537b6eea85503ae7dd74bf7e10be923f4350e2f31`
- registry/models.py：`f15dfa9012680d91761d7a640ee4db97605e4706a208acb3ed90ef3980e07e8d`
- strategy_adapter.py：`7996d49d3bd81697312b2af9d1ab87c8e9c7be3a29a19437d438f879d656a6f5`

## 4. Overlay 當前進展，不抹掉有效修正

01:12 回讀的 main WIP 已撤掉前述任意 `__getattr__` personas forwarding，現在對 committed head
只多回 `PersonaDirectorySnapshot` 的 dataclass decorator。ControlLoopsService 已改用明確 callbacks，
不再以 loaded main 覆蓋 injected store/monitor；新增 control_loops/router.py 仍需 scope 處理。
Strategy 已去掉 ctx.strategy_overlay dict 並在未寫入時 503，但 collection.py 仍以
optional read-facade writer / rs._data 寫入，因此沒有滿足 owner/restart 契約。

Worker terminal：overlay regression 21 passed/14.99s；migration regression 14 passed/10.32s；
strategy-persona contract 19 passed/17.20s。這些局部綠燈不抵銷先前 combined 的92 failed/524 passed，
也不證明 fake read-store 寫入是 durable。

combined/isolated 原因已交叉核對：control loop injected MockReadStore 被舊 main lookup 蓋掉；
Persona provisioning 的 bare main fixture 與 canonical main owner lookup 可能選到不同 transport，
符合整批502/unconfigured、單檔6passed。後者尚缺實際 object-identity trace，保留推論界線。
应修 fixture/package identity 與 context injection，保留 fail-closed provider 行為，不能以新 global
forwarding、class-name exception matching 或配置真實 provider 掩蓋測試污染。
