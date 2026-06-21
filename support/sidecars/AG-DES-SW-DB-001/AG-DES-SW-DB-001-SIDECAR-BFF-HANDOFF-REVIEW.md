# Review: AG-DES-SW-DB-001-SIDECAR-BFF-HANDOFF

**Reviewer:** Claude2  
**Date:** 2026-06-21  
**Task ID:** AG-DES-SW-DB-001-SIDECAR-BFF-HANDOFF  
**Outcome:** APPROVED — minor corrections requested for owner closeout

---

## Verdict

The packet is fundamentally sound and ready to hand off to the frontend team. All 13 routes, table mappings, ETag contract, status alignment strategy, and dependency list are accurate against their reference sources. Three minor editorial issues require correction before the packet is marked publication-ready, but none block the approval.

---

## Verification performed

| Check | Source | Result |
|---|---|---|
| §2.2 route/table mapping | `agora_v1_1.openapi.yaml` paths under `# agora.workshop.v1` | ✅ All 13 routes match; table queries are accurate |
| §2.3 status filter alignment | Design closure §6 / §6.1 | ✅ Correct — `status_group=active/closed` pattern matches design closure |
| §3.1 operator journey write sequence | Design closure §3.9 (9-step sequence) | ✅ BFF steps in journey match; redact → encrypt → INSERT order is correct |
| §3.3 status transitions (text) | Design closure §6 allowed-transition list | ✅ Transition guards match; conclude/archive mutation rejection is correct |
| §3.4 degraded-mode behavior | `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` §5.1 | ✅ No invented fallback; partial tab isolation matches §5.1 |
| §4 frontend checklist | OpenAPI + design closure | ✅ No contradictions with L1 policy |
| §5 dependency list | Design closure §11 | ✅ All four required design tasks listed; execution gate is accurately described |
| L1/L2 file modifications | `git status --short` | ✅ Only task-brief file is dirty; no canonical docs touched |

---

## Corrections required before publication (owner closeout)

### C-1 — Route #13 stream note is factually wrong (§2.2 row 13)

**Current text:**
> GET `/bff/agora/workshops/{id}/stream` — "Not in v1.1 OpenAPI explicitly; assumed via existing SSE infrastructure"

**Fact:** `agora_v1_1.openapi.yaml` explicitly defines this route as `streamAgoraWorkshop` at path `/bff/agora/workshops/{workshop_id}/stream` with a `text/event-stream` response. It is NOT assumed — it is explicitly contracted.

**Correction:** Replace the Notes cell with: "Explicitly defined in v1.1 OpenAPI as `streamAgoraWorkshop`; SSE stream sourced from `strategy_workshop_event` rows."

---

### C-2 — Status transition diagram missing `open → archived` path (§3.3)

**Current diagram** shows: `open → in_review`, `in_review → open` (reopen), `in_review → concluded`, `concluded → archived`, `in_review → archived`. The `open → archived` direct transition is absent.

**Design closure §6 allowed transitions include:**
```
open -> archived
```

**Correction:** Update the §3.3 diagram to add `open → archived`. Also add to the text below: "Archiving an `open` workshop directly (without conclude) is allowed." This matters for the frontend: an "Archive" action should be available in the `open` state as well as `in_review`.

---

### C-3 — §1.3 index list is not labeled as partial (minor)

The packet lists 12 BFF-relevant indexes but design closure §8 defines ~18 total (including `ux_workshop_event_sequence`, `ux_workshop_version_sequence`, `ix_workshop_version_strategy`, `ux_private_content_object_uri`, `ix_private_content_owner_expiry`, `ix_private_content_workshop_created`). The omissions are intentional — the packet correctly focuses on BFF read paths — but the omission could mislead a reader into thinking the migration only adds the listed 12.

**Correction:** Add a header note to §1.3: "This table lists only the indexes that directly serve BFF read paths. The full migration defines additional indexes (unique constraints, internal consistency, and private-content management) listed in design closure §8."

---

## Confirmed accurate items (no changes needed)

- **ETag format** `W/"workshop:{id}:v{lock_version}"`: matches OpenAPI `getAgoraWorkshop` response header definition.
- **Idempotency-Key required on all mutations**: matches OpenAPI parameter definitions.
- **`status_group=active` → `WHERE status IN ('open','in_review')`**: matches design closure §6.1 exactly.
- **`status=active` (deprecated) → treat as `status_group=active`**: correct migration note.
- **Private content: browser must not submit `private_content_ref`**: matches design closure §4.1.
- **503 `PRIVATE_CONTENT_REDACTION_UNAVAILABLE` fail-closed**: matches design closure §3.8.
- **Conclude requires existing version link (`WORKSHOP_VERSION_REQUIRED`)**: matches design closure §5.6.
- **`strategy_ref` nested object (`strategy_id` + `strategy_spec_registry_id`)**: the v1.1 OpenAPI has `strategy_spec_ref: string` (legacy field); the nested form is the v1.2 contract (AG-XR-OPENAPI-002). Packet's §3.2 correctly shows the v1.2 form and §5 correctly gates it on AG-XR-OPENAPI-002.
- **Dependency list** (AG-DES-SW-PRIV-001, AG-DES-SW-REF-001, AG-DES-SW-DB-001, AG-XR-OPENAPI-002 all required for AG-BE-SW-001): matches design closure §11 exactly.
- **No L1/L2 canonical files modified**: confirmed by `git status --short`.

---

## Review summary for 審查筆記

審查通過 (APPROVED with corrections)||
C-1: §2.2 row 13 stream note 有事實錯誤 — stream route 在 v1.1 OpenAPI 已明確定義，非假設||
C-2: §3.3 transition diagram 缺少 open→archived 箭頭 — design closure §6 明確列舉此 transition||
C-3: §1.3 index 清單應標明「僅列 BFF read path indexes」，完整清單見 design closure §8||
其餘內容（13 routes mapping、ETag contract、status filter、operator journey、dependency list）均通過核實
