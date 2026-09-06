# Overlay checkpoint 複核與正式範圍修訂

取證截止2026-09-06 01:30 UTC；exact PR5618 head
`98a29570061dbf7f0b2102d6a8154fe7882745a5`，對照`bff2dec5636967b096fcc1c23f65c3b702fca65c`。
這是root/advisory agent審查，不是canonical reviewer approval。PR仍OPEN/BLOCKED；owner正式blocked。

## 有效進展與不可接受的推論

Main任意personas namespace forwarding確已撤回；Strategy局部ctx.overlay已移除，無writer時503。
但collection.py:112後仍嘗試query facade的upsert/create方法或直接寫rs._data。
因此owner「process-local fallback已完全移除」的文字不能當成已驗證事實。

本次11個checkpoint檔案與101passed宣告不等於乾淨exact-head或全BFF驗收。
Root未在新head乾淨工作樹重跑101項；先前combined92failed/524passed不能由單檔通過抵銷。
未提交的其他修改已由supervisor封存於
`/tmp/pantheon-worker-worktree-archive/overlay-retire-001-20260906T011932Z-1801618`，
manifest64copied/0skipped，有兩份patch；root沒有刪除或重新套用它們。

## 兩個 regression 檔案的精確審查

| 檔案（BFF下） | 有效保留 | 新增缺口 |
| --- | --- | --- |
| tests/test_strategy_ranking_router.py | 6test bodies/27assertions AST完全未變 | :40–90的fake給read port加upsert/private dictionaries及合成v1，剛好迎合production optional writer；非真實owner |
| tests/test_overlay_retirement.py | 10→12tests、27→43assertions；前8個symbol/read-path測試完整保留 | :225–288只複製list/rebuild fake，以shared-dict harness稱restart；:312–341接受無owner I/O的coordinator receipt |

舊test亦有fake restart過度宣稱，main/path surgery也非此次新增；不把歷史問題錯記成本次新引入。
但這不讓新fake writer或成功回條成為可接受架構。

## Migration 兩項已重現的錯誤（離線，不是hosted測試）

實作`services/control-plane/bff/migrations/overlay_retirement.py`，Git blob
`2f7b529c09c993098fdc25299a2b9c6b1a7aea68`。
獨立agent用實際Git-blob AST方法、pure-memory synthetic fixtures、15秒硬上限執行，exit0；
未載入BFF、未呼叫network/provider/DB、未寫檔。

1. Unsupported owner只有list_incidents，沒有insert/save。backfill(tenant-A,dry_run=False)
   回`backfilled=1, scanned=1`，owner.records仍空且不拋錯。:382只log accepted，:329仍加成功數。
2. Row只有`tenantId=tenant-B`，用tenant-A backfill，仍`backfilled=1`，保存後同時有
   `tenant_id=tenant-A`與`tenantId=tenant-B`且不拋錯。:302只檢snake-case缺欄，:320改寫scope。

必須先一致化／拒絕衝突與外租戶scope、fail-closed確認selected writer，再寫入和readback。
缺writer不能成功计數，外租戶必須零mutation。現有backfill test只測單tenant dict，沒有覆蓋這些負例。
上述不是production資料已被污染的宣告；只是實際方法在隔離fixture的可重現錯誤。

另有source-only缺口：CanonicalWriterCoordinator:402–422只比writer字串並組receipt；
MultiReplicaReadbackHarness:425–452只共用dict，restart清除的overlay從未填過。
應接真實command/query與durable backend，不可另造一個看似coordinator的第二套owner或假驗證框架。

## 正式contract與前置流程

Owner在01:19:26已提供authenticated blocker/checkpoint，canonical仍blocked generation5。
Root在此安全狀態透過Human/Ops artifact-contract正式加入且回讀確認：

- services/control-plane/bff/tests/test_overlay_retirement.py
- services/control-plane/bff/tests/test_strategy_ranking_router.py

01:28:33最後一項audit的updated_by=Human/Ops。這是未來必要修正範圍，不是追認先前
未宣告stage、目前fake persistence、101passed或canonical reviewer approval。
其餘undeclaredsource／整批tests沒有因而授權；原驗收、owner、reviewer、dependency保留。
Migration修正原本就在services/**/migrations/**，不需要再新開重複cleanup task。

Registry prerequisite V2已於01:28:53正式admitted，01:29:31 in_progress；其owner補真實
Registrydurability，Overlay保留自己的ports/composition/migration/restart驗收。
不得因已建立prerequisite就解除blocker、把獨立owner完成當成Overlay完成。
