# Pantheon Product Functional Closure — Governed Execution Tasks

日期：2026-08-20

Machine source：[`execution-tasks.json`](execution-tasks.json)

狀態真相以authoritative V2 TaskStore為準；本文件不把queue file、worker process、PR merge或
catalog本身當成產品完成證據。

## 1. Dedup / reuse decisions

保留既有task的terminal fact：

- `L12-GAP-F06-BFF-FUNCTIONAL-HEALTH-20260818`：已由PR #5061合併為
  `cd93c201076f7767366a868a1b45d75a91e9317e`並terminal completed；新cross-loop task保留這個
  已滿足的external dependency，不重建另一monitor，也不重做F06。

新task是follow-up而不是重開terminal history：

- `L12-GAP-F03-EXECUTABLE-RUNTIME-BINDING-20260818`的delivery主要是evidence，live 9/9 binding
  readback仍不可執行，因此使用`PFG-RUNTIME-BINDING-R2-20260820`。
- `L12-GAP-F04-CONTINUOUS-MARKET-INPUT-20260818`新增了client fallback，但Source沒有對應endpoint，
  因此使用`PFG-SOURCE-SNAPSHOT-20260820`。
- Source Distillation event admission已在code閉合，不建立F02重工task。

Open PR reuse：

- Pantheon PR #5064 (`OPS-DEV-SOURCE-MANUAL-PULL-20260820-V2`) 已包含manual-only Compose/
  one-tick candidate，checks已通過但尚未canonical review/merge。`PFG-SOURCE-MANUAL-ONCE-20260820`
  必須先重驗、rebase或採用這個head；不得從零做第二份同功能PR。Source state recursion/readiness
  修復是它的前置條件，未完成前不得直接部署candidate。
- execute-plans PR #569 是舊CandidateReviewDrawer closeout；current `dev`已有可重用的BFF-wired
  drawer元件，但active Trading Room仍使用page-local duplicate。新Agora frontend task採用現有元件，
  不把stale open PR或terminal task當完成，也不重做第三個drawer。

在新catalog canonical materialized後，以下舊nonterminal工作應由Human/Ops標記superseded，避免
舊acceptance繞過新前置條件：

- `AGORA-HOSTED-DEPLOY-REACCEPT-20260815` → `PFG-HOSTED-ACCEPT-20260820`
- `AGORA-HOSTED-SERVICE-PROOF-RERUN-20260817` → `PFG-HOSTED-ACCEPT-20260820`
- `L12-GAP-F07-E2E-RUNTIME-20260818` → `PFG-L12-RUNTIME-E2E-20260820`
- `L12-GAP-F07-E2E-CROSS-LOOP-20260818` → `PFG-L12-TRUTH-CROSSLOOP-20260820`
- `L12-GAP-F07-HOSTED-MANAGEMENT-ACCEPT-20260818` → `PFG-HOSTED-ACCEPT-20260820`
- `L12-GAP-CLEAN-COMPAT-RUNTIMES-20260818` → `PFG-BE-CONSOLIDATE-20260820`
- `L12-GAP-CLEAN-LOOP-CATALOG-20260818` → `PFG-BE-CONSOLIDATE-20260820`

`L12-GAP-CLEAN-POLICY-LEGACY-20260818` 是已知archive generation collision的orphaned blocked row；
不以手改JSON處理，也不把它當新計畫blocker。

## 2. 27-task DAG

| Wave | Task | Repo | Owner | Reviewer | Depends on |
|---|---|---|---|---|---|
| W0 | `PFG-PLAN-FREEZE-20260820` | pantheon | Codex2 | Antigravity2 | — |
| W1 | `PFG-SOURCE-STATE-20260820` | pantheon | Codex | Antigravity | plan |
| W1 | `PFG-PAPER-STATE-20260820` | pantheon | Antigravity | Codex2 | plan |
| W1 | `PFG-FE-HONEST-LIVE-20260820` | execute-plans | Codex2 | Antigravity2 | plan |
| W1 | `PFG-RUNTIME-BINDING-R2-20260820` | pantheon | Antigravity2 | Codex | plan, paper state |
| W1 | `PFG-SOURCE-SNAPSHOT-20260820` | pantheon | Codex2 | Antigravity | plan, source state |
| W1 | `PFG-AGORA-RECON-WORKER-20260820` | pantheon | Codex | Antigravity2 | plan |
| W1 | `PFG-AGORA-RESEARCH-CONSUMER-20260820` | pantheon | Antigravity | Codex2 | plan |
| W1 | `PFG-AGORA-PROJECTIONS-20260820` | pantheon | Antigravity2 | Codex | plan, Agora Research consumer |
| W1 | `PFG-MGMT-DOMAIN-ACTIONS-20260820` | pantheon | Codex2 | Antigravity | plan |
| W2 | `PFG-MGMT-READ-MODELS-20260820` | pantheon | Antigravity | Codex2 | Management actions |
| W2 | `PFG-MGMT-AI-PROVIDER-20260820` | pantheon | Codex | Antigravity2 | Management reads |
| W2 | `PFG-SOURCE-MANUAL-ONCE-20260820` | pantheon | Antigravity2 | Codex | Source state |
| W2 | `PFG-AGORA-FE-LIVE-20260820` | execute-plans | Codex2 | Antigravity2 | FE honest, 3 Agora backend tasks |
| W2 | `PFG-MGMT-FE-REAL-20260820` | execute-plans | Antigravity | Codex2 | FE honest, Management actions/reads |
| W2 | `PFG-MGMT-AI-FE-ACTIONS-20260820` | execute-plans | Antigravity2 | Codex | FE honest, Management actions, AI provider |
| W2 | `PFG-MGMT-BFF-PERF-20260820` | pantheon | Codex2 | Antigravity | Management reads/AI |
| W3 | `PFG-DEV-INTEGRATION-20260820` | pantheon | Codex | Antigravity2 | backend W1/W2 components |
| W4 | `PFG-L12-RESEARCH-E2E-20260820` | pantheon | Antigravity | Codex2 | dev integration |
| W4 | `PFG-L12-HUMAN-E2E-20260820` | pantheon | Codex2 | Antigravity | dev integration, Agora backend |
| W4 | `PFG-L12-RUNTIME-E2E-20260820` | pantheon | Antigravity2 | Codex | dev integration, paper/binding/snapshot |
| W4 | `PFG-AGORA-JOURNEY-E2E-20260820` | execute-plans | Codex | Antigravity2 | Agora FE, human E2E |
| W4 | `PFG-MGMT-JOURNEY-E2E-20260820` | execute-plans | Antigravity | Codex2 | Management FE/AI/perf/integration |
| W5 | `PFG-L12-TRUTH-CROSSLOOP-20260820` | pantheon | Codex2 | Antigravity | three L12 E2E, existing F06 |
| W5 | `PFG-BE-CONSOLIDATE-20260820` | pantheon | Antigravity2 | Codex | cross-loop, Agora/Management journeys |
| W5 | `PFG-FE-CONSOLIDATE-20260820` | execute-plans | Codex | Antigravity2 | Agora/Management journeys |
| W6 | `PFG-HOSTED-ACCEPT-20260820` | pantheon | Antigravity | Codex2 | truth, journeys, both consolidation tasks |

## 3. Dispatch rules

1. 所有implementation tasks依賴`PFG-PLAN-FREEZE-20260820`。Planner只寫文件/catalog，
   plan-freeze worker做獨立review、PR evidence與merge；未freeze前不得實作產品。
2. Frontend task的`repo`固定`execute-plans`、merge target固定`dev`；artifact以
   `execute-plans:`prefix表示外部repo path，不得寫進Pantheon checkout。
3. `docker-compose.yml`只有`PFG-DEV-INTEGRATION-20260820`能改；component tasks只能交付code/
   contract/tests，避免平行衝突。
4. 每個task evidence都要有code-disposition JSON；reviewer要確認沒有新增平行owner/store。
5. Cleanup tasks只能刪已被W4 journey證明替代的production path，並在刪除後重跑相關journey。
6. Source所有waves都不得把`reconcile_and_pull`設成長駐default；只有
   `PFG-SOURCE-MANUAL-ONCE-20260820`與research E2E可啟動bounded one-tick。
7. Runtime只接受paper；任何live broker/capital activation都是out of scope。
8. Supervisor可根據live auth/quota做governed reassignment，但owner與reviewer不可相同，且應
   優先跨provider family review。

## 3A. Functional-first completion tracks

The W4 journey tasks expose two independent completion tracks. A task may
remain `blocked` or `in_progress` for a hosted proof while its `functional`
track is already complete. Downstream tasks opt into a track with the
machine-readable `dependency_tracks` map; an ordinary dependency remains a
terminal dependency for backward compatibility.

- `functional`: worktree-local paper/replay tests, component tests, and code
  review evidence. It never acquires the shared dev environment lease.
- `hosted`: one exact FE/BFF pair on the Pantheon-owned dev host. It is run by
  the final hosted controller only.
- `operator-live/write-proof`: an external evidence item recorded under the
  hosted track. Missing credentials produce `external_wait`; they do not
  block a functional dependency and never justify enabling capital writes.

Workers record a track through the governed command:

```bash
TASK_MILESTONE_EVIDENCE='path/to/evidence||run-url-or-id' \
  scripts/ai-status.sh milestone <task-id> functional done 'local paper proof complete'
```

Human/Ops changes a dependency's track only through the audited command below;
task IDs and artifact ownership stay unchanged:

```bash
scripts/human-ops-status.sh dependency-track \
  <task-id> <dependency-id> functional 'release independent functional lane'
```

The supervisor remains the only dispatcher. This is a completion projection,
not a second queue or a bypass around review, artifact conflict, or hosted
promotion gates.

## 4. Materialization contract

- Program ID：`pantheon-product-functional-closure-20260820`
- 建立方式：`scripts/human-ops-status.sh assign`
- Canonical store：live V2 TaskStore，由`live-supervisor-mainroot-config.json`解析
- 禁止：手改`ai-status.json`、task event log、queue JSON或偽造dev-bridge authorization
- Catalog digest、task contract digest、source document與target repo必須寫入每個task metadata
- 建立後要做canonical readback；只看到catalog或queue不算accepted
- 只由supervisor依DAG/capacity啟動auto-workers，chatbox不直接start implementation task

## 5. Completion reporting

最終報告至少包含：

- 27/27 task canonical IDs及terminal outcomes；
- Pantheon/execute-plans各PR、merge SHAs；
- Source manual pull的connector、tick、SourceRecord與停止readback；
- replacement後刪除/保留的code disposition；
- Loops/Agora/Management journeys的同一run IDs；
- exact hosted FE/BFF SHAs與served manifest；
- required tests的pass/skip/fail數，required cases不得skip。
