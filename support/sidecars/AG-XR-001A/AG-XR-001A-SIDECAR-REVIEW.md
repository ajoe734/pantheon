# AG-XR-001A Sidecar Review Packet

- **Sidecar task:** AG-XR-001A-SIDECAR-REVIEW
- **Parent task:** AG-XR-001A — Additive Agora contract extension bundle (v1.1)
- **Helper kind:** review_packet
- **Prepared by:** Claude (sidecar owner)
- **Reviewer:** Claude2
- **Prepared at:** 2026-06-20
- **Parent task status:** `done` (archived 2026-06-20T15:23:35Z)

---

## 1. Parent Task Summary

AG-XR-001A built the additive Agora v1.1 schema bundle per the
`contract-closure/02_schema_coexistence_and_migration.md` spec.
The iron rule throughout: frozen AG-XR-001 (v1.0) files must not be
touched in any way, and the v1 `bundle_index.json` sha256 must remain
unchanged.

**Owner:** Codex (helper-claimed from Claude)
**Reviewer:** Claude
**Delivery PRs:**
- PR #1828 — `task/AG-XR-001A`: add additive Agora v1.1 bundle
  (merged 2026-06-20T15:08:53Z into dev)
- PR #1833 — `task/AG-XR-001A`: close out additive Agora bundle
  (merged 2026-06-20T15:22:53Z into dev)

---

## 2. Artifacts Delivered

All six files are new additions under the sidecar's allowed scope.
No existing file was deleted or modified.

| File | Type |
|---|---|
| `services/control-plane/specs/agora/v2/widget_spec_v2.schema.json` | JSON Schema v2 |
| `services/control-plane/specs/agora/v2/chart_spec_v1.schema.json` | JSON Schema v1 (new file) |
| `services/control-plane/specs/agora/v2/dashboard_recipe_v2.schema.json` | JSON Schema v2 |
| `services/control-plane/specs/agora/v2/compatibility_manifest.schema.json` | JSON Schema |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | Manifest JSON |
| `services/control-plane/specs/agora/bundle_index.v1_1.json` | Bundle index extension |

---

## 3. Integrity Evidence

### 3.1 Frozen v1 bundle — `agora_schema_bundle.py --verify`

Run by this sidecar at 2026-06-20. All 15 frozen files pass:

```
OK: specs/agora/agora_user_scope.schema.json
OK: specs/agora/servant_profile.schema.json
OK: specs/agora/strategy_workshop.schema.json
OK: specs/agora/strategy_completeness.schema.json
OK: specs/agora/research_plan.schema.json
OK: specs/agora/research_run_summary.schema.json
OK: specs/agora/candidate_pool.schema.json
OK: specs/agora/dashboard_recipe.schema.json
OK: specs/agora/widget_spec.schema.json
OK: specs/agora/trading_event.schema.json
OK: specs/agora/trading_intent.schema.json
OK: specs/agora/shadow_decision.schema.json
OK: specs/agora/personalization_event.schema.json
OK: specs/agora/capability_manifest.json
OK: openapi/agora_v1.openapi.yaml
```

### 3.2 Frozen v1 `bundle_index.json` sha256

Recorded in `bundle_index.v1_1.json` under `extends.bundle_index_sha256`:
```
286891c6bb900d6b5e9f9037d357c2016f8ecac33927056556a848f95fb4bd0b
```

Current on-disk sha256 (`sha256sum`):
```
286891c6bb900d6b5e9f9037d357c2016f8ecac33927056556a848f95fb4bd0b
```

**Match: YES**

### 3.3 v2 artifact sha256 cross-check

`bundle_index.v1_1.json` entries vs `sha256sum` output:

| File | Bundle index sha256 | On-disk sha256 | Match |
|---|---|---|---|
| `v2/widget_spec_v2.schema.json` | `d360a17a…facb993f` | `d360a17a…facb993f` | ✓ |
| `v2/chart_spec_v1.schema.json` | `0bcd0fa5…fed0967` | `0bcd0fa5…fed0967` | ✓ |
| `v2/dashboard_recipe_v2.schema.json` | `34c7e0fa…a45c13a` | `34c7e0fa…a45c13a` | ✓ |
| `v2/compatibility_manifest.schema.json` | `84c36071…ca7827` | `84c36071…ca7827` | ✓ |
| `v2/capability_manifest_v1_1.json` | `6a729d12…cab3db41` | `6a729d12…cab3db41` | ✓ |

All five v2 files match their recorded digests.

---

## 4. Iron Rule Compliance

| Rule | Status |
|---|---|
| Frozen AG-XR-001 files not modified | PASS — v1 files are untouched, `--verify` all green |
| `bundle_index.json` sha256 unchanged | PASS — sha256 unchanged (recorded and on-disk identical) |
| Capability allowlist not expanded | PASS — `capability_manifest_v1_1.json` extends v1 with `agora.dashboard.v2` (additive capability using v2 schemas only) |
| Agora cannot directly place orders / bind funds / write RuntimeBinding | PASS — no `execution_authority` field other than `none` in v1.1 manifest; v2 schemas carry no order-route or binding semantics |
| A3 content adopted under v2 filenames/IDs, not overwriting v1 | PASS — `widget_spec_v2.schema.json` is a new file; `widget_spec.schema.json` (v1) is untouched |

---

## 5. Review Notes from Parent Task (Claude as reviewer)

Recorded in the AG-XR-001A archive (`review_notes_zh`):

> 審查通過：frozen v1 驗證全綠，sha256 一致，五個 v2 檔案 sha256
> 完全匹配，commit 純增量無刈除，鐵律全部滿足。

Review file path: `/tmp/AG-XR-001A-review.md` (ephemeral worker artifact).

---

## 6. Bundle Extension Structure

`bundle_index.v1_1.json` correctly extends the v1 index:

```json
{
  "bundle_version": "1.1",
  "extends": {
    "bundle_path": "services/control-plane/specs/agora/bundle_index.json",
    "bundle_version": "1.0",
    "frozen_by": "AG-XR-001",
    "bundle_index_sha256": "286891c6bb900d6b5e9f9037d357c2016f8ecac33927056556a848f95fb4bd0b"
  },
  "files": { ... five v2 entries ... }
}
```

The `extends` block anchors the v1 sha256, making any tampering with
the frozen base detectable by any consumer of the bundle index.

---

## 7. Follow-on Considerations for Claude2 Reviewer

1. **No open follow-up tasks** from AG-XR-001A were left unresolved.
   The companion sidecar `AG-XR-001A-SIDECAR-ACCEPTANCE` (PR #1832)
   was also merged before closeout.
2. **`capability_manifest_v1_1.json`** only adds `agora.dashboard.v2`.
   No execution authority changes. Compatible with existing v1 consumers.
3. **WidgetSpec v1 legacy readability:** `widget_spec.schema.json`
   (frozen v1) is unchanged. Downgrade projection is expected to fail
   with `LEGACY_WIDGET_MAPPING_REQUIRED` — this is the documented
   contract, not a bug.
4. **Scope boundary intact:** This sidecar writes no canonical truth.
   The review packet above is the sole output. Parent owner (Codex)
   and parent reviewer (Claude) already closed the parent task.

---

## 8. Handoff to Claude2

This packet is ready for Claude2 review. Claude2 should:

- Confirm the iron-rule table in §4 is correctly assessed
- Spot-check at least one sha256 from §3.3 against the file
- Accept or request changes via the standard `approve` / `reopen` flow
  on task `AG-XR-001A-SIDECAR-REVIEW`

No further canonical file changes are expected from this sidecar.
