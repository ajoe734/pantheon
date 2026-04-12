# APP-001A Codex Review

Status: changes requested
Reviewer: Codex
Task: APP-001A
Artifact: `services/control-plane/bff/BFF_SURFACE_INVENTORY.md`
Reviewed at: 2026-04-10

## 結論

APP-001A 目前不建議直接進 `review_approved`。

主要原因不是內容太少，而是 inventory 已超出本 task 的 acceptance 邊界，混入非 L1 / 非 canonical 真相來源，且交接摘要與文件實際範圍不一致，會讓 APP-001 owner 之後以錯誤前提繼續設計。

## Blockers

1. `surface inventory cites canonical objects only` 尚未達成。

- 文件自述「this document cites canonical L1 policy objects only」但實際上混入大量 task-level 或 follow-on 來源，例如 `OC-003`、`FB-001/FB-002/FB-003`、`EV-001`、`EV-002`、`LP-001/LP-002/LP-004`、`REG-001/REG-002/REG-003`、`RS-001/RS-002/RS-003`。
- 對應位置：
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L42)
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L52)
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L54)
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L57)
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L58)
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L59)
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L190)
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L205)
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L219)
- 這些來源不在目前共享真相的 canonical L1 清單內，因此不能在 APP-001A 裡被宣告成「canonical object catalog」的一部分。
- 同類問題也包含把 `artifact_ref`、`evidence_links`、`promoted_metadata`、`rollback_target` 這些欄位或派生資料當成 canonical object 引用：
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L106)
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L180)
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L210)

2. 交接摘要與文件實際範圍不一致，APP-001 owner 無法信任此 inventory 的 coverage 敘述。

- handoff / task next 宣稱是「38 surfaces across 11 domains」。
- 但文件實際列出 44 個 surface ID：PS(6) + CP(5) + DP(4) + RT(4) + TL(3) + LN(3) + IN(5) + EV(4) + FB(4) + RG(3) + RS(3)。
- 對應文件區段：
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L64)
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L217)
- 在 inventory 這種交接文件裡，數量不一致不是小 typo，因為 APP-001 owner 會拿它做 scope 切分與 API 覆蓋規劃。

3. degraded-path 區段把尚未 canonical 化的 cache / stale-data policy 寫成既定要求，超出 APP-001A 盤點任務邊界。

- 文件定義了明確 TTL 分級與 stale serving 規則：
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L292)
  - [BFF_SURFACE_INVENTORY.md](/home/ajoe734/code/pantheon/services/control-plane/bff/BFF_SURFACE_INVENTORY.md#L299)
- 但 canonical BFF HA 文件把 cache strategy 明確列為後續細化項，而不是目前生效的 L1 真相：
  - [BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md](/home/ajoe734/code/pantheon/BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md#L81)
  - [BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md](/home/ajoe734/code/pantheon/BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md#L152)
- 此外，telemetry authoritative-query 規則只允許某些情境回 Postgres，不等於可以自由把所有 surface 分級成固定 TTL：
  - [TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md](/home/ajoe734/code/pantheon/TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md#L200)

## 可接受的修正方向

1. 把 catalog 和 surface 範圍收斂到「目前有 canonical L1 依據」的 object / read path。
2. 將 `FB-* / EV-* / LP-* / REG-* / RS-* / OC-*` 相關內容移到單獨的「future / follow-on / non-L1 inputs」附錄，而不是放在 canonical catalog。
3. 修正文內 surface 總數，並讓 handoff 敘述與文件本體一致。
4. degraded-path 只保留已有 L1 依據的原則：
   - partial degradation
   - total outage 不影響 active runtimes
   - consultation/workbench 可 degraded
   - kill-switch 不能只靠 BFF
5. 若要保留 cache / TTL 建議，明確標成 non-canonical implementation note，不可寫成 inventory acceptance 的一部分。

