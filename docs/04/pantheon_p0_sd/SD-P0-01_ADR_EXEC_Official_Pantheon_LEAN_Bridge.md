---
project: Pantheon
document_type: P0 System Design / Architecture Decision / Codex Implementation Packet
language: zh-TW
status: accepted-for-p0-implementation
revision: v1
baseline: >
  Based on Pantheon consolidated blueprint and latest implementation correction:
  current actual LEAN bridge is `pantheon/lean` submodule, repo-root path `lean`,
  remote `ajoe734/pantheon-lean.git`;
  `lean-platform` is not the current Pantheon execution target.
---

# SD-P0-01 — ADR-EXEC-001：Official Pantheon LEAN Bridge Repository and Submodule Policy

## 1. Purpose

本 ADR 定義 Pantheon 目前官方 execution bridge repository / submodule。  
此文件的目的不是討論完整 live broker readiness，而是先解決最關鍵的 repo authority 問題：

```text
目前 Pantheon 實際接入的 LEAN bridge 是誰？
Codex / engineer 的 P0 execution work 應該 patch 哪裡？
lean-platform 目前是什麼角色？
```

本 ADR 是後續所有 execution、runtime、telemetry、CI、Codex task 的前置文件。

---

## 2. Current Facts

依據目前最新盤點：

```text
task board:
  P0 execution wave is materialized in ai-status.json from planning session phase6-2026-05-01-pantheon-p0-paper-loop

pantheon branch:
  backend-dev-publish-20260429

execution bridge currently referenced by pantheon:
  pantheon/lean submodule

submodule remote:
  https://github.com/ajoe734/pantheon-lean.git

current bridge evidence:
  pantheon/lean HEAD = 0ca2bdbd Add PantheonAlgoBase — Pantheon LEAN bridge
  lean/Algorithm.Python/pantheon_algo/base.py contains PantheonAlgoBase
  docker-compose.exec.yml points to /workspace/lean/Launcher/config.json

not current execution target:
  ajoe734/lean-platform.git

lean-platform state:
  cloned
  branch resembles option auto exercise regression work
  no Pantheon / PANTHEON / RuntimeBinding / SignalStore hits in latest scan
  Launcher/config.json remains standard Lean-style config
```

### 2.1 Path notation and `.gitmodules` authority

`pantheon/lean` is the canonical planning and task-packet name for the LEAN bridge inside the `pantheon` repository. In `.gitmodules`, the same bridge is represented as:

```ini
[submodule "lean"]
  path = lean
  url = https://github.com/ajoe734/pantheon-lean.git
```

Therefore:

```text
canonical task path: pantheon/lean
repo-root filesystem path: lean/
runtime container mount: /workspace/lean
authoritative remote: https://github.com/ajoe734/pantheon-lean.git
```

---

## 3. Decision

### 3.1 Official current bridge

```text
Official current Pantheon LEAN bridge:
  path: pantheon/lean
  gitmodules_path: lean
  remote: https://github.com/ajoe734/pantheon-lean.git
  role: current Pantheon LEAN bridge / paper runtime bridge candidate
```

### 3.2 Not current target

```text
Not current P0 execution target:
  ajoe734/lean-platform
```

`lean-platform` remains one of:

```text
- migration candidate
- historical branch
- parked execution repo
- future cherry-pick target
```

but it is **not** the default target for P0 execution tasks.

### 3.3 Generic upstream Lean

The term `LEAN engine` remains a runtime concept.  
It does not imply the product code should target `ajoe734/lean-platform` or any generic upstream clone unless explicitly stated.

---

## 4. Scope

### 4.1 In scope

```text
1. Official bridge repository decision.
2. Submodule authority.
3. Codex target rules.
4. CI guard requirements.
5. Migration options for lean-platform.
6. Documentation update requirements.
```

### 4.2 Out of scope

```text
1. Full live broker SDK implementation.
2. Canary/live activation.
3. Qlib / FinRL / RLlib production activation.
4. OpenClaw live trading adapter.
5. BFF HA/LB production topology.
6. Full upstream Lean merge strategy beyond policy-level statement.
```

---

## 5. Rationale

Pantheon blueprint originally labels `lean-platform` as Execution Substrate.  
However, current implementation facts show that the product path now uses `pantheon/lean` submodule / `pantheon-lean.git`.

If this is not formalized, the system will suffer from:

```text
1. Codex patching the wrong repository.
2. CI verifying inactive code.
3. Operators debugging the wrong runtime.
4. Telemetry not knowing which engine bridge produced events.
5. Documentation and implementation diverging.
6. Future migration efforts becoming ambiguous.
```

This ADR formalizes the current reality while allowing a future migration decision.

---

## 6. Alternatives Considered

### Option A — Keep `pantheon/lean` / `pantheon-lean.git` as official current bridge

Decision status: **Selected for current P0 phase**

Pros:

```text
- Matches actual implementation.
- Least migration cost.
- Aligns with docker-compose.exec.yml current path.
- Preserves PantheonAlgoBase bridge work.
- Allows fast paper runtime hardening.
```

Cons:

```text
- Requires updating blueprint repo mapping.
- lean-platform must be reclassified.
- Requires submodule / bridge governance.
```

### Option B — Migrate bridge back to `lean-platform`

Decision status: **Not selected for P0**

Pros:

```text
- Matches old blueprint naming.
- Could isolate product fork if desired.
```

Cons:

```text
- Requires cherry-picking PantheonAlgoBase and bridge code.
- Requires .gitmodules rewrite.
- Requires compose / CI / docs migration.
- High risk of losing current bridge details.
```

### Option C — Create / rename to `pantheon-lean-runtime`

Decision status: **Future option**

Pros:

```text
- Most explicit name.
- Removes ambiguity.
```

Cons:

```text
- Highest repo migration cost.
- Requires coordination across docs, CI, compose, deployment.
```

### Option D — Keep bridge thin, move production launch/telemetry into sidecar

Decision status: **Recommended for later evaluation**

Pros:

```text
- Reduces modification pressure inside LEAN engine.
- Centralizes Pantheon-specific launch and telemetry.
- Helps upstream sync.
```

Cons:

```text
- Sidecar becomes critical runtime infrastructure.
- Requires its own reliability and retry model.
```

---

## 7. Hard Invariants

```text
INV-EXEC-001:
  P0 execution tasks MUST target `pantheon/lean` unless ADR-EXEC-001 is revised.

INV-EXEC-002:
  P0 execution tasks MUST NOT target `ajoe734/lean-platform` unless explicitly marked as migration-only.

INV-EXEC-003:
  docker-compose.exec.yml MUST remain consistent with .gitmodules and point to the official bridge path.

INV-EXEC-004:
  runtime events MUST include engine bridge identity once telemetry contract is implemented.

INV-EXEC-005:
  live role MUST remain fail-closed until explicit live activation criteria are approved.

INV-EXEC-006:
  broker credentials MUST NOT be embedded in submodule code, frontend, telemetry payload, or artifact payload.

INV-EXEC-007:
  paper runtime hardening MUST NOT implicitly enable live broker execution.
```

---

## 8. Policy-configurable Rules

These are not hardcoded architecture facts and may be policy-controlled:

```text
1. Whether canary/live activation is allowed in a given environment.
2. Whether `lean-platform` can be used for migration testing.
3. Which operator roles can approve bridge migration.
4. Which CI jobs are required for staging vs prod.
5. Whether sidecar pattern is required before live activation.
```

---

## 9. Required Documentation Changes

### 9.1 Pantheon blueprint repo mapping

Change from:

```text
lean-platform = Execution Substrate
```

to:

```text
pantheon/lean submodule / ajoe734/pantheon-lean.git = current Pantheon LEAN bridge
lean-platform = not current execution target; migration candidate or historical branch
```

### 9.2 SD / SA documents

Update all execution references:

```text
Lean / lean-platform ambiguous wording
→ pantheon/lean submodule / pantheon-lean.git
```

### 9.3 Codex task packets

Every P0 execution packet must include:

```yaml
official_execution_target:
  repo: pantheon
  path: pantheon/lean
  gitmodules_path: lean
  remote: https://github.com/ajoe734/pantheon-lean.git
  runtime_mount: /workspace/lean
not_target:
  - ajoe734/lean-platform
```

---

## 10. CI Requirements

```text
CI-EXEC-001:
  Assert .gitmodules contains `[submodule "lean"]` with path `lean`.

CI-EXEC-002:
  Assert submodule remote normalizes to `ajoe734/pantheon-lean.git`.

CI-EXEC-003:
  Assert `lean/Algorithm.Python/pantheon_algo/base.py` exists.

CI-EXEC-004:
  Assert docker-compose.exec.yml references `/workspace/lean/Launcher/config.json`.

CI-EXEC-005:
  Fail if P0 execution task metadata targets `lean-platform` without ADR override.

CI-EXEC-006:
  Publish bridge identity in runtime metadata:
    engine_bridge_repo
    engine_bridge_commit
    launch_manifest_hash
```

---

## 11. Migration Policy for `lean-platform`

`lean-platform` may only be used in one of these explicit modes:

```text
1. archive_only
2. historical_reference
3. migration_candidate
4. cherry_pick_target
5. future_official_bridge_after_ADR_revision
```

Any use outside these modes is invalid.

### Migration acceptance

If a future decision chooses `lean-platform`, migration must prove:

```text
1. PantheonAlgoBase exists in lean-platform.
2. runtime_bootstrap / compose path is migrated.
3. Telemetry contract tests pass.
4. P0 paper runtime smoke test passes.
5. No stale pantheon/lean references remain.
```

---

## 12. Failure Behavior

If bridge identity cannot be verified:

```text
- CI must fail.
- Runtime deploy must fail closed.
- BFF must report runtime bridge unknown.
- Frontend must show degraded / unverifiable state.
- No live/canary activation may proceed.
```

---

## 13. Acceptance Criteria

```text
AC-001:
  ADR-EXEC-001 is landed in `docs/04/pantheon_p0_sd/SD-P0-01_ADR_EXEC_Official_Pantheon_LEAN_Bridge.md` and referenced by SD-P0-02, SD-P0-03, SD-P0-04, SD-P0-06.

AC-002:
  `.gitmodules` and compose path are checked by CI.

AC-003:
  Codex execution task packets target `pantheon/lean` and record `.gitmodules` path `lean`.

AC-004:
  `lean-platform` is explicitly labeled not-current-runtime or migration-only.

AC-005:
  Runtime metadata can report bridge repo and commit.

AC-006:
  No P0 task modifies lean-platform unless tagged migration-only.
```

---

## 14. Codex Task Packets

### P0-EXEC-ADR-001 — Commit ADR and update repo mapping docs

```yaml
task_id: P0-EXEC-ADR-001
repo: pantheon
goal: Add ADR-EXEC-001 and update docs that incorrectly name lean-platform as current execution substrate.
official_execution_target:
  canonical_path: pantheon/lean
  gitmodules_path: lean
  remote: https://github.com/ajoe734/pantheon-lean.git
not_current_runtime:
  - ajoe734/lean-platform
target_paths:
  - docs/04/pantheon_p0_sd/SD-P0-01_ADR_EXEC_Official_Pantheon_LEAN_Bridge.md
  - docs/04/SUPERVISOR_PLANNING_P0_NEXT_DEV_WORK.md
verify_paths:
  - .gitmodules
  - docker-compose.exec.yml
  - lean/Algorithm.Python/pantheon_algo/base.py
acceptance:
  - docs state pantheon/lean is current bridge
  - .gitmodules path lean maps to pantheon/lean in task language
  - lean-platform is labeled not-current-runtime or migration-only
  - no wording implies lean-platform is currently wired runtime
non_goals:
  - do not migrate code
  - do not enable live trading
```

### P0-CI-BRIDGE-001 — Add submodule authority check

```yaml
task_id: P0-CI-BRIDGE-001
repo: pantheon
goal: Add CI/script check for .gitmodules and bridge file presence.
official_execution_target:
  canonical_path: pantheon/lean
  gitmodules_path: lean
  remote: https://github.com/ajoe734/pantheon-lean.git
target_paths:
  - scripts/check_execution_bridge.py
  - .github/workflows/*
acceptance:
  - verifies submodule remote
  - verifies lean/Algorithm.Python/pantheon_algo/base.py exists
  - verifies docker-compose.exec.yml path
non_goals:
  - do not modify bridge code
```

### P0-CI-BRIDGE-001 / target guard slice — Add task target guard

```yaml
task_id: P0-CI-BRIDGE-001
repo: pantheon
goal: Prevent P0 execution task packets from targeting lean-platform by mistake.
official_execution_target:
  canonical_path: pantheon/lean
  gitmodules_path: lean
  remote: https://github.com/ajoe734/pantheon-lean.git
target_paths:
  - .orchestrator/*
  - docs/codex/*
acceptance:
  - task metadata must include official_execution_target
  - CI warns/fails on lean-platform target unless migration_only=true
non_goals:
  - no runtime code changes
```

---

## 15. Open Questions

```text
1. Should `pantheon-lean.git` be renamed for clarity?
2. Should production live require sidecar before activation?
3. Should `lean-platform` be archived or kept as migration candidate?
4. Should bridge version be published as part of /readyz?
5. How often should `pantheon-lean` sync with upstream LEAN?
```

---

## 16. Final Decision Statement

For the current P0 phase:

```text
Pantheon official current LEAN bridge is `pantheon/lean` submodule,
remote `ajoe734/pantheon-lean.git`.

`lean-platform` is not the current execution target.
All P0 execution work must target the submodule bridge unless an explicit migration ADR revises this decision.
```
