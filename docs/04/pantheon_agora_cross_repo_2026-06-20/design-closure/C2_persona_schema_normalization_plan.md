# C2 — Persona 核心 Schema 正規化計畫

> 狀態：Decision Frozen v1.0  
> 決策：Phase 1 先使用既有 Persona metadata/policy；在可觀測門檻觸發後，以 additive migration 正規化，不另建 Persona service。

---

## 1. Phase 1 Metadata Fields

現階段放入既有 Persona metadata／policy：

```text
persona_class = agora_servant | institutional_expert
owner_scope = user_private | desk | platform
visibility_scope = private | redacted_management | governed_shared
memory_scope = private_user | institutional
agora_user_id
servant_profile_version
```

必須有 application-level validation、BFF projection 與 index-friendly generated column/query strategy。

---

## 2. 正規化觸發門檻

任一條件達成即開 migration：

1. 任一欄位被 3 個以上 BFF route family 作 filter/sort。
2. 任一欄位被 policy engine 當 hard predicate。
3. Persona records >= 10,000。
4. metadata filter query p95 > 200ms，連續 7 日。
5. 需要 DB foreign key、unique constraint、partial index 或 row-level security。
6. 兩個以上 service 出現相同 metadata parsing logic。
7. Schema drift incident >= 2 次／季度。

預期 Agora Phase 2 前至少正規化 `persona_class`、`owner_scope`、`visibility_scope`、`agora_user_id`。

---

## 3. Target Columns

```text
persona_class VARCHAR NOT NULL
owner_scope VARCHAR NOT NULL
visibility_scope VARCHAR NOT NULL
memory_scope VARCHAR NOT NULL
agora_user_id UUID NULL
servant_profile_version INTEGER NULL
metadata JSONB NOT NULL
```

Constraints：

- `persona_class=agora_servant` → `agora_user_id IS NOT NULL`。
- `owner_scope=user_private` → `visibility_scope IN (private, redacted_management)`。
- `institutional_expert` 不得綁定 `agora_user_id`。
- Persona class 不賦予 execution authority。

---

## 4. Migration Steps

1. Add nullable columns。
2. Backfill from metadata，產生 discrepancy report。
3. Dual-read：column 優先，metadata fallback。
4. Dual-write 兩個 release。
5. Build indexes / constraints as `NOT VALID`。
6. Validate constraints。
7. Compare route results 14 日。
8. Cut read path to columns。
9. Metadata 保留 compatibility 90 日。
10. 移除 fallback 與重複 parser。

全程可回滾，不改 persona ID／lineage。

---

## 5. Indexes

```text
(persona_class, status)
(owner_scope, lifecycle_state)
(agora_user_id, status)
(visibility_scope, status)
```

多租戶環境加 `tenant_id` 前綴。

---

## 6. Acceptance

- Backfill discrepancy = 0 或有人工處置。
- 新舊 read result 100% 一致。
- p95 query 改善或不退化。
- Cross-user scope tests 通過。
- OpenClaw provisioning、Persona Registry、Management projection 無 regression。
