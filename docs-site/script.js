const DATA_FILES = {
  status: "./ai-status.json",
  activity: "./ai-activity-log.jsonl",
  currentWork: "./current-work.md",
  orchestratorState: "./orchestrator-state.json",
  approvalQueue: "./approval-queue.json",
};

const BOARD_COLUMNS = [
  { key: "todo", label: "待開始", defaultCollapsed: false },
  { key: "in_progress", label: "進行中", defaultCollapsed: false },
  { key: "review", label: "待審查", defaultCollapsed: false },
  { key: "review_approved", label: "已批准", defaultCollapsed: false },
  { key: "blocked", label: "已阻塞", defaultCollapsed: true },
  { key: "done", label: "已完成", defaultCollapsed: true },
];

const BOARD_COLLAPSE_KEY = "pantheon-dashboard-board-collapse";
const PROVIDER_ORDER = ["Claude", "Gemini", "Codex", "Grok"];
const AGENT_NAME_MAP = { claude: "Claude", gemini: "Gemini", codex: "Codex", grok: "Grok", copilot: "Grok" };
const INTEGRATION_PREFIXES = new Set(["OC", "RS", "LP", "SPIKE"]);

const STATUS_LABELS = {
  idle: "待命",
  working: "工作中",
  reviewing: "審查中",
  ready: "可開工",
  waiting: "等前置",
  todo: "待開始",
  in_progress: "進行中",
  review: "待審查",
  review_approved: "已批准",
  blocked: "已阻塞",
  done: "已完成",
  pending: "待處理",
  manual_pending: "等待人工處理",
  waiting_approval: "等待批准",
  retry_backoff: "退避重試",
  started: "已啟動",
  running: "執行中",
  failed: "失敗",
  completed: "完成",
  stalled: "疑似卡住",
  open: "未解決",
  resolved: "已解決",
};

const ACTIVITY_LABELS = {
  worker_started: "Worker 啟動",
  worker_failed: "Worker 失敗",
  worker_completed: "Worker 完成",
  worker_resumed: "Worker 恢復",
  worker_retry_scheduled: "Worker 重試排程",
  approval_requested: "等待批准",
  approval_resolved: "批准完成",
  handoff: "交接",
  blocker: "阻塞",
  progress: "進度更新",
  start: "開始",
  assign: "指派",
  done: "完成",
};

const LANE_LABELS = {
  execution: "執行平面",
  "control-plane": "控制平面",
  "governance-review": "治理審查",
  gcp: "GCP",
  "ci-cd": "CI/CD",
  "runtime-packaging": "執行環境封裝",
  "worker-ops": "Worker 維運",
  integration: "整合契約",
  "status-system": "狀態系統",
  schema: "Schema",
  acceptance: "驗收",
  "research-ingest": "research-ingest",
  "external-search": "external-search",
  "spec-review": "spec-review",
  critique: "critique",
};

const byId = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function statusLabel(value) {
  return STATUS_LABELS[value] || value || "-";
}

function laneLabel(value) {
  return LANE_LABELS[value] || value;
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function timeAgo(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  const diff = Math.floor((Date.now() - date.getTime()) / 1000);
  if (diff < 0) return "剛剛";
  if (diff < 60) return `${diff} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

async function fetchJson(path) {
  const response = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`無法載入 ${path}: ${response.status}`);
  return response.json();
}

async function fetchText(path) {
  const response = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`無法載入 ${path}: ${response.status}`);
  return response.text();
}

function parseJsonLines(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("{"))
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function parseCurrentWork(markdown) {
  const objectiveMatch = markdown.match(/## Objective\s+([\s\S]*?)\n## /);
  const sprintMatch = markdown.match(/## Current Sprint\s+([\s\S]*?)\n## /);
  return {
    objective: objectiveMatch ? objectiveMatch[1].trim() : "",
    sprint: sprintMatch
      ? sprintMatch[1]
          .trim()
          .split("\n")
          .filter((line) => line.startsWith("- "))
          .map((line) => line.replace(/^- /, "").trim())
      : [],
  };
}

function normalizeQueueEvents(orchState) {
  return Object.entries(orchState?.queue?.events || {}).map(([eventId, record]) => ({ eventId, ...record }));
}

function normalizeWorkers(orchState) {
  return Object.values(orchState?.workers || {}).sort((a, b) => (b.last_event_at || "").localeCompare(a.last_event_at || ""));
}

function workerAgentLabel(worker) {
  const key = String(worker?.agent_id || worker?.provider || "").toLowerCase();
  return AGENT_NAME_MAP[key] || worker?.agent_id || worker?.provider || "-";
}

function workerBucket(status) {
  const value = String(status || "").toLowerCase();
  if (["running", "started", "retry_backoff"].includes(value)) return "active";
  if (["waiting_approval", "manual_pending", "stalled", "failed"].includes(value)) return "waiting";
  return "completed";
}

function providerTone(providerSummary) {
  if (providerSummary.active > 0) return "working";
  if (providerSummary.waiting > 0) return "review";
  if (providerSummary.failed > 0) return "blocked";
  return "idle";
}

function loadBoardCollapseState() {
  try {
    const raw = localStorage.getItem(BOARD_COLLAPSE_KEY);
    if (!raw) return Object.fromEntries(BOARD_COLUMNS.map((column) => [column.key, column.defaultCollapsed]));
    const parsed = JSON.parse(raw);
    return Object.fromEntries(
      BOARD_COLUMNS.map((column) => [column.key, typeof parsed?.[column.key] === "boolean" ? parsed[column.key] : column.defaultCollapsed])
    );
  } catch {
    return Object.fromEntries(BOARD_COLUMNS.map((column) => [column.key, column.defaultCollapsed]));
  }
}

function saveBoardCollapseState(state) {
  localStorage.setItem(BOARD_COLLAPSE_KEY, JSON.stringify(state));
}

function toArray(value) {
  if (Array.isArray(value)) return value;
  if (value === null || value === undefined || value === "") return [];
  return [value];
}

function normalizedReviewNotes(task) {
  return toArray(task?.review_notes_zh).map((note) => String(note)).filter(Boolean);
}

function taskLayer(task) {
  const prefix = String(task.id || "").split("-", 1)[0];
  return INTEGRATION_PREFIXES.has(prefix) ? "upstream" : "product";
}

function deriveAgentCards(status) {
  const tasks = status.tasks || [];
  const taskMap = new Map(tasks.map((task) => [task.id, task]));
  return (status.agents || []).map((agent) => {
    const owned = tasks.filter((task) => task.owner === agent.name);
    const active = owned.filter((task) => ["in_progress", "review", "review_approved", "blocked"].includes(task.status));
    const ready = owned.filter(
      (task) =>
        task.status === "todo" &&
        (task.depends_on || []).every((depId) => ["done", "review_approved"].includes(taskMap.get(depId)?.status || "done"))
    );
    const waiting = owned.filter((task) => task.status === "todo" && !ready.includes(task));

    let derivedStatus = "idle";
    if (active.some((task) => task.status === "blocked")) derivedStatus = "blocked";
    else if (active.some((task) => task.status === "review")) derivedStatus = "reviewing";
    else if (active.some((task) => ["in_progress", "review_approved"].includes(task.status))) derivedStatus = "working";
    else if (ready.length) derivedStatus = "ready";
    else if (waiting.length) derivedStatus = "waiting";

    const focusTasks = active.length ? active : ready.slice(0, 2);
    const latest = [...active, ...ready, ...waiting].sort((a, b) => (b.last_update || "").localeCompare(a.last_update || ""))[0];

    return {
      ...agent,
      status: derivedStatus,
      focusTasks,
      readyCount: ready.length,
      waitingCount: waiting.length,
      activeCount: active.length,
      next: latest?.next || agent.next || "尚未指定",
      lastUpdate: latest?.last_update || agent.last_update || null,
    };
  });
}

function deriveOverview(status, orchState, approvalQueue, activityEntries) {
  const tasks = status.tasks || [];
  const workers = normalizeWorkers(orchState);
  const queueEvents = normalizeQueueEvents(orchState);
  const approvalPending = approvalQueue?.pending || [];
  const counts = {
    total: tasks.length,
    active: tasks.filter((task) => ["in_progress", "review"].includes(task.status)).length,
    ready: tasks.filter((task) => task.status === "todo").length,
    approved: tasks.filter((task) => task.status === "review_approved").length,
    done: tasks.filter((task) => task.status === "done").length,
  };

  return [
    { label: "任務總數", value: counts.total, note: `進行中 ${counts.active} · 待開始 ${counts.ready}` },
    { label: "Review / 已批准", value: `${tasks.filter((task) => task.status === "review").length} / ${counts.approved}`, note: "先看待審查，再看 review_approved 的收尾" },
    { label: "Dispatch Queue", value: queueEvents.length, note: queueEvents.length ? queueEvents.map((item) => item.status).join(" / ") : "目前清空" },
    { label: "Approval Queue", value: approvalPending.length, note: approvalPending.length ? `最新 ${timeAgo(approvalPending[0]?.created_at)}` : "目前清空" },
    { label: "Auto Worker", value: workers.filter((worker) => workerBucket(worker.status) === "active").length, note: `${workers.filter((worker) => workerBucket(worker.status) === "waiting").length} 筆等待處理` },
    { label: "活動日誌", value: activityEntries.length, note: activityEntries.length ? `最後一筆 ${timeAgo(activityEntries[activityEntries.length - 1]?.ts)}` : "尚無活動" },
  ];
}

function deriveProviderSummaries(orchState) {
  const workers = normalizeWorkers(orchState);
  const providers = PROVIDER_ORDER.map((label) => ({
    label,
    providerKey: label === "Grok" ? "copilot" : label.toLowerCase(),
    workers: workers.filter((worker) => workerAgentLabel(worker) === label),
  }));
  return providers.map((entry) => ({
    ...entry,
    active: entry.workers.filter((worker) => workerBucket(worker.status) === "active").length,
    waiting: entry.workers.filter((worker) => workerBucket(worker.status) === "waiting").length,
    completed: entry.workers.filter((worker) => workerBucket(worker.status) === "completed").length,
    failed: entry.workers.filter((worker) => worker.status === "failed").length,
  }));
}

function buildDependencySchedule(tasks) {
  const remaining = (tasks || []).filter((task) => !["done", "review_approved"].includes(task.status));
  const taskMap = new Map((tasks || []).map((task) => [task.id, task]));
  const remainingIds = new Set(remaining.map((task) => task.id));
  const dependents = new Map();
  const indegree = new Map();

  for (const task of remaining) {
    const unresolvedDeps = (task.depends_on || []).filter((depId) => remainingIds.has(depId));
    indegree.set(task.id, unresolvedDeps.length);
    for (const depId of unresolvedDeps) {
      if (!dependents.has(depId)) dependents.set(depId, []);
      dependents.get(depId).push(task.id);
    }
  }

  const sortTasks = (list) =>
    [...list].sort((a, b) => {
      const aActive = ["in_progress", "review"].includes(a.status) ? 0 : 1;
      const bActive = ["in_progress", "review"].includes(b.status) ? 0 : 1;
      if (aActive !== bActive) return aActive - bActive;
      return a.id.localeCompare(b.id);
    });

  let frontier = sortTasks(remaining.filter((task) => (indegree.get(task.id) || 0) === 0));
  const waves = [];
  const scheduled = new Set();

  while (frontier.length) {
    waves.push(frontier);
    const nextIds = new Set();
    for (const task of frontier) {
      scheduled.add(task.id);
      for (const dependentId of dependents.get(task.id) || []) {
        indegree.set(dependentId, (indegree.get(dependentId) || 0) - 1);
        if ((indegree.get(dependentId) || 0) === 0 && !scheduled.has(dependentId)) nextIds.add(dependentId);
      }
    }
    frontier = sortTasks([...nextIds].map((taskId) => taskMap.get(taskId)).filter(Boolean));
  }

  return {
    waves,
    cyclic: sortTasks(remaining.filter((task) => !scheduled.has(task.id))),
    readyNow: remaining.filter((task) => task.status === "todo" && (task.depends_on || []).every((depId) => ["done", "review_approved"].includes(taskMap.get(depId)?.status || "done"))).length,
    activeNow: remaining.filter((task) => ["in_progress", "review"].includes(task.status)).length,
    waitingNow: remaining.filter((task) => task.status === "todo" && !(task.depends_on || []).every((depId) => ["done", "review_approved"].includes(taskMap.get(depId)?.status || "done"))).length,
    blockedNow: remaining.filter((task) => task.status === "blocked").length,
  };
}

function metricCardHtml(metric) {
  return `
    <article class="metric-card">
      <div class="metric-value">${escapeHtml(metric.value)}</div>
      <div class="metric-label">${escapeHtml(metric.label)}</div>
      <p class="metric-note">${escapeHtml(metric.note)}</p>
    </article>
  `;
}

function queueItemHtml({ title, status, meta = [], copy = "" }) {
  return `
    <article class="queue-item">
      <div class="queue-item-head">
        <strong>${escapeHtml(title)}</strong>
        <span class="status-pill status-${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>
      </div>
      <div class="chip-row">${meta.map((value) => `<span class="chip">${escapeHtml(value)}</span>`).join("")}</div>
      ${copy ? `<p class="queue-copy">${escapeHtml(copy)}</p>` : ""}
    </article>
  `;
}

function stackCardHtml(title, subtitle, body, chips = []) {
  return `
    <article class="stack-card">
      <div class="stack-head">
        <strong>${escapeHtml(title)}</strong>
        ${subtitle ? `<span class="stack-subtitle">${escapeHtml(subtitle)}</span>` : ""}
      </div>
      ${chips.length ? `<div class="chip-row">${chips.map((chip) => `<span class="chip">${escapeHtml(chip)}</span>`).join("")}</div>` : ""}
      ${body}
    </article>
  `;
}

function renderOverview(metrics) {
  const container = byId("overview-metrics");
  if (!container) return;
  container.innerHTML = metrics.map(metricCardHtml).join("");
}

function renderRuntime(orchState, approvalQueue) {
  const runtimeCards = byId("runtime-cards");
  const workerGroups = byId("worker-groups");
  if (!runtimeCards || !workerGroups) return;

  const supervisor = orchState?.supervisor || {};
  const queueEvents = normalizeQueueEvents(orchState);
  const workers = normalizeWorkers(orchState);
  const approvals = approvalQueue?.pending || [];
  const providerSummaries = deriveProviderSummaries(orchState);

  runtimeCards.innerHTML = [
    stackCardHtml(
      "Supervisor",
      supervisor.last_heartbeat_at ? "有資料" : "無資料",
      `<p class="body-copy">初始化：${escapeHtml(formatTime(orchState?.initialized_at))}</p>
       <p class="body-copy">上次掃描：${escapeHtml(timeAgo(orchState?.last_scan_at))}</p>
       <p class="body-copy">Heartbeat：${escapeHtml(timeAgo(supervisor.last_heartbeat_at))}</p>`,
      [
        `PID ${supervisor.pid || "-"}`,
        `Queue ${queueEvents.length}`,
        `Workers ${workers.length}`,
      ]
    ),
    stackCardHtml(
      "Dispatch Queue",
      queueEvents.length ? `${queueEvents.length} 筆待處理` : "清空",
      queueEvents.length
        ? queueEvents
            .map((record) => {
              const worker = workers.find((item) => item.queue_event_id === record.eventId);
              return queueItemHtml({
                title: worker?.task_id || record.eventId,
                status: record.status || "pending",
                meta: [workerAgentLabel(worker || {}), timeAgo(record.last_attempt_at || record.processed_at)],
                copy: worker?.request_snapshot?.reason || worker?.metadata?.reason || "無原因說明",
              });
            })
            .join("")
        : '<p class="empty">目前沒有 dispatch queue 項目。</p>'
    ),
    stackCardHtml(
      "Approval Queue",
      approvals.length ? `${approvals.length} 個待處理` : "清空",
      approvals.length
        ? approvals
            .map((approval) =>
              queueItemHtml({
                title: approval.task_id || approval.worker_run_id || approval.approval_id,
                status: approval.status || "pending",
                meta: [workerAgentLabel({provider: approval.provider}) || "-", approval.tool_name || "-", timeAgo(approval.created_at)],
                copy: approval.tool_input?.description || approval.tool_name || "等待批准",
              })
            )
            .join("")
        : '<p class="empty">目前沒有 approval queue 項目。</p>'
    ),
    stackCardHtml(
      "Provider Summary",
      "看哪條 auto lane 正在工作",
      providerSummaries
        .map(
          (summary) => `
            <div class="provider-row provider-${providerTone(summary)}">
              <strong>${escapeHtml(summary.label)}</strong>
              <div class="chip-row">
                <span class="chip">進行中 ${summary.active}</span>
                <span class="chip">等待 ${summary.waiting}</span>
                <span class="chip">完成 ${summary.completed}</span>
                <span class="chip">失敗 ${summary.failed}</span>
              </div>
            </div>`
        )
        .join("")
    ),
  ].join("");

  const grouped = new Map(PROVIDER_ORDER.map((label) => [label, []]));
  for (const worker of workers) grouped.get(workerAgentLabel(worker))?.push(worker);

  workerGroups.innerHTML = PROVIDER_ORDER.map((label) => {
    const items = grouped.get(label) || [];
    const active = items.filter((item) => workerBucket(item.status) === "active");
    const waiting = items.filter((item) => workerBucket(item.status) === "waiting");
    const completed = items.filter((item) => workerBucket(item.status) === "completed");

    const renderWorkerItems = (bucketItems, emptyText) =>
      bucketItems.length
        ? bucketItems
            .map((worker) =>
              stackCardHtml(
                worker.task_id || worker.run_id,
                statusLabel(worker.status),
                `${worker.request_snapshot?.reason ? `<p class="body-copy">原因：${escapeHtml(worker.request_snapshot.reason)}</p>` : ""}
                 ${worker.last_error ? `<p class="body-copy error-text">錯誤：${escapeHtml(worker.last_error)}</p>` : ""}`,
                [workerAgentLabel(worker), worker.mode || "-", timeAgo(worker.last_event_at)]
              )
            )
            .join("")
        : `<p class="empty">${escapeHtml(emptyText)}</p>`;

    return `
      <section class="worker-group">
        <div class="worker-group-head">
          <div>
            <h3>${escapeHtml(label)}</h3>
            <p class="subpanel-copy">共 ${items.length} 筆 worker record</p>
          </div>
          <div class="chip-row">
            <span class="chip">進行中 ${active.length}</span>
            <span class="chip">等待 ${waiting.length}</span>
            <span class="chip">已完成 ${completed.length}</span>
          </div>
        </div>
        <div class="worker-buckets">
          <section class="worker-bucket">
            <div class="worker-bucket-head"><strong>進行中</strong><span class="chip">${active.length}</span></div>
            <div class="worker-bucket-body">${renderWorkerItems(active, "目前沒有進行中的自動工作。")}</div>
          </section>
          <section class="worker-bucket">
            <div class="worker-bucket-head"><strong>等待處理</strong><span class="chip">${waiting.length}</span></div>
            <div class="worker-bucket-body">${renderWorkerItems(waiting, "目前沒有等待處理的自動工作。")}</div>
          </section>
          <details class="worker-bucket worker-bucket-completed">
            <summary class="worker-bucket-head"><strong>已完成</strong><span class="chip">${completed.length}</span></summary>
            <div class="worker-bucket-body">${renderWorkerItems(completed, "目前沒有已完成的自動工作。")}</div>
          </details>
        </div>
      </section>
    `;
  }).join("");
}

function renderAgents(status) {
  const container = byId("agent-lanes");
  if (!container) return;
  const cards = deriveAgentCards(status);
  container.innerHTML = cards
    .map((agent) => {
      const focusList = agent.focusTasks.length
        ? `<ul class="compact-list">${agent.focusTasks.map((task) => `<li><strong>${escapeHtml(task.id)}</strong>：${escapeHtml(task.summary_zh || task.title)}</li>`).join("")}</ul>`
        : '<p class="empty">目前沒有焦點任務。</p>';
      return `
        <article class="agent-card">
          <div class="agent-head">
            <div>
              <h3>${escapeHtml(agent.name)}</h3>
              <p class="subpanel-copy">${(agent.capability_lane || []).map(laneLabel).map(escapeHtml).join(" · ")}</p>
            </div>
            <span class="status-pill status-${escapeHtml(agent.status)}">${escapeHtml(statusLabel(agent.status))}</span>
          </div>
          <div class="chip-row">
            <span class="chip">${escapeHtml(agent.branch || "未指定分支")}</span>
            <span class="chip">活躍 ${agent.activeCount}</span>
            <span class="chip">可開工 ${agent.readyCount}</span>
            <span class="chip">等前置 ${agent.waitingCount}</span>
          </div>
          ${focusList}
          <div class="note-box">
            <strong>下一步</strong>
            <p class="body-copy">${escapeHtml(agent.next || "尚未指定")}</p>
          </div>
          <p class="meta-line">最後更新：${escapeHtml(formatTime(agent.lastUpdate))}</p>
        </article>
      `;
    })
    .join("");
}

function renderTaskBoard(status) {
  const board = byId("task-board");
  const summary = byId("board-summary");
  if (!board || !summary) return;

  const collapseState = loadBoardCollapseState();
  const tasks = status.tasks || [];
  const taskMap = new Map(tasks.map((task) => [task.id, task]));
  const openTasks = tasks.filter((task) => !["done"].includes(task.status));
  const readyNow = tasks.filter((task) => task.status === "todo" && (task.depends_on || []).every((depId) => ["done", "review_approved"].includes(taskMap.get(depId)?.status || "done"))).length;

  summary.innerHTML = `
    <span class="chip">未完成 ${openTasks.length}</span>
    <span class="chip">可開工 ${readyNow}</span>
    <span class="chip">待審查 ${tasks.filter((task) => task.status === "review").length}</span>
    <span class="chip">已批准 ${tasks.filter((task) => task.status === "review_approved").length}</span>
  `;

  board.innerHTML = BOARD_COLUMNS.map((column) => {
    const items = tasks.filter((task) => task.status === column.key);
    const collapsed = collapseState[column.key];
    return `
      <section class="board-column ${collapsed ? "collapsed" : ""}" data-column-key="${column.key}">
        <div class="board-column-head">
          <div>
            <h3>${column.label}</h3>
            <p class="subpanel-copy">${items.length} 項</p>
          </div>
          <button class="column-toggle" type="button">${collapsed ? "展開" : "收合"}</button>
        </div>
        <div class="column-stack">
          ${
            items.length
              ? items
                  .map((task) => {
                    const depends = (task.depends_on || []).length ? task.depends_on.join(", ") : "無";
                    return `
                      <article class="task-card status-${escapeHtml(task.status)}">
                        <div class="task-head">
                          <strong>${escapeHtml(task.id)}</strong>
                          <span class="status-pill status-${escapeHtml(task.status)}">${escapeHtml(statusLabel(task.status))}</span>
                        </div>
                        <p class="task-title">${escapeHtml(task.title)}</p>
                        <p class="body-copy">${escapeHtml(task.summary_zh || "尚未補上中文說明。")}</p>
                        <div class="chip-row">
                          <span class="chip">${escapeHtml(task.phase || "-")}</span>
                          <span class="chip">Owner ${escapeHtml(task.owner || "-")}</span>
                          <span class="chip">Reviewer ${escapeHtml(task.reviewer || "-")}</span>
                        </div>
                        <div class="chip-row">
                          <span class="chip">依賴 ${escapeHtml(depends)}</span>
                          <span class="chip">更新 ${escapeHtml(formatTime(task.last_update))}</span>
                        </div>
                        ${
                          normalizedReviewNotes(task).length
                            ? `<details class="inline-details"><summary>審查重點 ${normalizedReviewNotes(task).length} 則</summary><ul class="compact-list">${normalizedReviewNotes(task).map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul></details>`
                            : ""
                        }
                        <div class="note-box compact">
                          <strong>下一步</strong>
                          <p class="body-copy">${escapeHtml(task.next || "尚未指定")}</p>
                        </div>
                      </article>`;
                  })
                  .join("")
              : '<p class="empty">目前沒有任務。</p>'
          }
        </div>
      </section>
    `;
  }).join("");

  board.querySelectorAll(".board-column").forEach((section) => {
    const key = section.dataset.columnKey;
    const button = section.querySelector(".column-toggle");
    button?.addEventListener("click", () => {
      collapseState[key] = !section.classList.contains("collapsed");
      section.classList.toggle("collapsed", collapseState[key]);
      button.textContent = collapseState[key] ? "展開" : "收合";
      saveBoardCollapseState(collapseState);
    });
  });
}

function renderCoordination(status, snapshot, activityEntries) {
  renderList("handoff-list", (status.handoffs || []).filter((item) => item.status !== "done"), "目前沒有待交接項目。", (item) =>
    stackCardHtml(item.task_id, `${item.from} → ${item.to}`, `<p class="body-copy">${escapeHtml(item.message || "-")}</p><p class="meta-line">${escapeHtml(formatTime(item.created_at))}</p>`, [item.status || "-"])
  );

  renderList("blocker-list", (status.blockers || []).filter((item) => item.status === "open"), "目前沒有阻塞項目。", (item) =>
    stackCardHtml(item.task_id, `等待 ${item.waiting_for}`, `<p class="body-copy">${escapeHtml(item.message || "-")}</p>`, [item.owner || "-", item.status || "open"])
  );

  const snapshotContainer = byId("snapshot");
  if (snapshotContainer) {
    const items = [snapshot.objective || "目前沒有可顯示的目標。", ...(snapshot.sprint || [])];
    snapshotContainer.innerHTML = items.length
      ? items.map((item) => `<article class="snapshot-card"><p>${escapeHtml(item)}</p></article>`).join("")
      : '<p class="empty">目前沒有 Sprint 摘要。</p>';
  }

  renderList("activity-list", activityEntries.slice(-20).reverse(), "目前還沒有活動紀錄。", (entry) =>
    stackCardHtml(entry.agent || entry.provider || "-", timeAgo(entry.ts), `<p class="body-copy">${escapeHtml(entry.message || "-")}</p>`, [ACTIVITY_LABELS[entry.type] || entry.type || "-", entry.task_id || "-"])
  );
}

function renderInsights(status) {
  const tasks = status.tasks || [];
  const schedule = buildDependencySchedule(tasks);

  const dependencySummary = byId("dependency-summary");
  if (dependencySummary) {
    dependencySummary.innerHTML = [
      { label: "可開工", value: schedule.readyNow, note: "所有前置都已完成" },
      { label: "進行中", value: schedule.activeNow, note: "進行中與待審查" },
      { label: "等待前置", value: schedule.waitingNow, note: "依賴尚未完成" },
      { label: "明確阻塞", value: schedule.blockedNow, note: "task.status = blocked" },
    ].map(metricCardHtml).join("");
  }

  renderList("dependency-schedule", schedule.waves, "目前沒有可排程的未完成任務。", (wave, index) =>
    stackCardHtml(
      index === 0 ? "現在這一波" : `第 ${index + 1} 波`,
      `${wave.length} 個任務`,
      `<ul class="compact-list">${wave.map((task) => `<li><strong>${escapeHtml(task.id)}</strong>：${escapeHtml(task.title)}</li>`).join("")}</ul>`
    )
  );

  const audits = tasks.filter((task) => task.phase === "Audit" || String(task.id).startsWith("AUD-"));
  const auditSummary = byId("audit-summary");
  if (auditSummary) {
    auditSummary.innerHTML = [
      { label: "總 Audit", value: audits.length, note: "目前追蹤中" },
      { label: "待開始", value: audits.filter((task) => task.status === "todo").length, note: "尚未啟動" },
      { label: "進行中", value: audits.filter((task) => ["in_progress", "review"].includes(task.status)).length, note: "需要持續追" },
      { label: "已完成", value: audits.filter((task) => ["done", "review_approved"].includes(task.status)).length, note: "已有結果" },
    ].map(metricCardHtml).join("");
  }

  renderList("audit-list", audits, "目前沒有 audit 任務。", (task) =>
    stackCardHtml(task.id, task.title, `<p class="body-copy">${escapeHtml(task.summary_zh || "尚未補上中文說明。")}</p><p class="body-copy">下一步：${escapeHtml(task.next || "尚未指定")}</p>`, [task.owner || "-", task.reviewer || "-", task.status || "-"])
  );

  renderList("review-note-list", tasks.filter((task) => normalizedReviewNotes(task).length), "目前沒有 reviewer 備註。", (task) =>
    stackCardHtml(task.id, task.title, `<ul class="compact-list">${normalizedReviewNotes(task).map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul>`, [task.reviewer || "-", task.status || "-"])
  );

  const openTasks = tasks.filter((task) => !["done"].includes(task.status));
  const layers = [
    {
      title: "Pantheon 產品工作",
      copy: "產品本體、執行邊界、registry、feedback、repo 邊界與 audit。",
      tasks: openTasks.filter((task) => taskLayer(task) === "product"),
    },
    {
      title: "Upstream OpenClaw / OSS 整合工作",
      copy: "OpenClaw、DSPy、Qlib、imitation、MLflow 等外部整合。",
      tasks: openTasks.filter((task) => taskLayer(task) === "upstream"),
    },
  ];

  renderList("delivery-layers", layers, "目前沒有分層資料。", (layer) =>
    stackCardHtml(layer.title, `${layer.tasks.length} 個未完成任務`, `<p class="body-copy">${escapeHtml(layer.copy)}</p><ul class="compact-list">${layer.tasks.map((task) => `<li><strong>${escapeHtml(task.id)}</strong>：${escapeHtml(task.summary_zh || task.title)}</li>`).join("") || "<li>目前沒有未完成任務。</li>"}</ul>`)
  );
}

function renderList(containerId, items, emptyText, formatter) {
  const container = byId(containerId);
  if (!container) return;
  container.innerHTML = items.length ? items.map(formatter).join("") : `<p class="empty">${escapeHtml(emptyText)}</p>`;
}

function buildViewModel(status, activityText, currentWorkText, orchState, approvalQueue) {
  const activityEntries = parseJsonLines(activityText);
  const snapshot = parseCurrentWork(currentWorkText);
  return {
    status,
    orchState,
    approvalQueue,
    activityEntries,
    snapshot,
    overview: deriveOverview(status, orchState, approvalQueue, activityEntries),
  };
}

function renderDashboard(model) {
  const objective = byId("objective");
  const updatedAt = byId("updated-at");
  if (objective) objective.textContent = model.status.objective || model.snapshot.objective || "目前沒有可顯示的目標。";
  if (updatedAt) updatedAt.textContent = formatTime(model.status.updated_at);

  renderOverview(model.overview);
  renderRuntime(model.orchState, model.approvalQueue);
  renderAgents(model.status);
  renderTaskBoard(model.status);
  renderCoordination(model.status, model.snapshot, model.activityEntries);
  renderInsights(model.status);
}

async function render() {
  try {
    const [status, activityText, currentWorkText, orchState, approvalQueue] = await Promise.all([
      fetchJson(DATA_FILES.status),
      fetchText(DATA_FILES.activity),
      fetchText(DATA_FILES.currentWork),
      fetchJson(DATA_FILES.orchestratorState),
      fetchJson(DATA_FILES.approvalQueue),
    ]);
    renderDashboard(buildViewModel(status, activityText, currentWorkText, orchState, approvalQueue));
  } catch (error) {
    const objective = byId("objective");
    if (objective) objective.textContent = `協作資料載入失敗：${error.message}`;
  }
}

byId("refresh-button")?.addEventListener("click", render);
render();
