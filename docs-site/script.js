const DATA_FILES = {
  status: "./ai-status.json",
  activity: "./ai-activity-log.jsonl",
  currentWork: "./current-work.md",
  orchestratorState: "./orchestrator-state.json",
  approvalQueue: "./approval-queue.json",
};

const boardColumns = [
  { key: "todo", label: "待開始" },
  { key: "in_progress", label: "進行中" },
  { key: "review", label: "待審查" },
  { key: "review_approved", label: "已批准待收尾" },
  { key: "blocked", label: "已阻塞" },
  { key: "done", label: "已完成" },
];

const qs = (selector) => document.querySelector(selector);
const statusLabelMap = {
  idle: "待命",
  working: "工作中",
  reviewing: "審查中",
  finalize: "待收尾",
  ready: "可開工",
  waiting: "等前置",
  todo: "待開始",
  in_progress: "進行中",
  review: "待審查",
  review_approved: "已批准待收尾",
  blocked: "已阻塞",
  done: "已完成",
  pending: "待處理",
  superseded: "已接手",
  reassigned: "已改派",
  open: "未解決",
  resolved: "已解決",
  accepted: "已接收",
  start: "開始",
  progress: "進度更新",
  handoff: "交接",
  blocker: "阻塞",
  assign: "指派",
};
const laneLabelMap = {
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
};

const activeTaskStatuses = new Set(["in_progress", "review"]);
const scheduleOpenTaskStatuses = new Set(["todo", "in_progress", "review", "blocked"]);

const workerStatusIcon = {
  running: "🟡",
  completed: "🟢",
  failed: "🔴",
  manual_pending: "⚪",
  started: "🟡",
  superseded: "🔁",
  reassigned: "🔀",
};
const activityTypeLabel = {
  worker_started: "Worker 啟動",
  worker_failed: "Worker 失敗",
  worker_completed: "Worker 完成",
  worker_resumed: "Worker 恢復",
  approval_requested: "等待批准",
  approval_resolved: "批准完成",
  permission_hook: "權限事件",
  permission_rule_remembered: "規則記憶",
  decision: "決策",
  start: "開始",
  progress: "進度更新",
  handoff: "交接",
  blocker: "阻塞",
  assign: "指派",
  reopen: "打回修改",
  review_approved: "審查通過",
  done: "完成",
};

const integrationPrefixes = new Set(["OC", "RS", "LP", "SPIKE"]);

function statusLabel(value) {
  return statusLabelMap[value] || value || "-";
}

function laneLabel(value) {
  return laneLabelMap[value] || value;
}

function normalizeReviewNotes(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? [trimmed] : [];
  }
  return [];
}

function providerLabel(value) {
  if (!value) return "-";
  if (value === "copilot") return "GitHub Copilot";
  return value;
}

function titleCase(value) {
  return String(value || "")
    .split(/[-_\\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function agentLabel(value) {
  if (!value) return "-";
  const normalized = String(value).toLowerCase();
  if (normalized === "claude") return "Claude";
  if (normalized === "gemini") return "Gemini";
  if (normalized === "codex") return "Codex";
  if (normalized === "grok") return "Copilot";
  if (normalized === "copilot") return "Copilot";
  return value;
}

function actorLabel(agentId, provider) {
  const agent = agentLabel(agentId);
  const host = providerLabel(provider);
  if (agentId && provider && String(agentId).toLowerCase() !== String(provider).toLowerCase()) {
    return `${agent} (${host})`;
  }
  return agentId ? agent : host;
}

function terminalTaskStatus(value) {
  return String(value || "").toLowerCase() === "done";
}

function logicalWorkerAgentId(worker) {
  const candidates = [worker?.agent_id, worker?.target_agent, worker?.provider];
  for (const candidate of candidates) {
    const normalized = String(candidate || "").trim().toLowerCase();
    if (!normalized) continue;
    if (["claude", "gemini", "codex"].includes(normalized)) return normalized;
    if (["grok", "copilot"].includes(normalized)) return "copilot";
  }
  if (String(worker?.provider || "").toLowerCase() === "copilot") return "copilot";
  return String(worker?.agent_id || worker?.provider || "").trim().toLowerCase() || "unknown";
}

function normalizeWorkerRecords(orchState, status) {
  const taskMap = new Map((status?.tasks || []).map((task) => [task.id, task]));
  const workers = Object.entries(orchState?.workers || {}).map(([runId, worker]) => {
    const task = taskMap.get(worker?.task_id) || null;
    const taskStatus = task?.status || null;
    const logicalAgentId = logicalWorkerAgentId(worker);
    let bucket = "pending";

    if (["superseded", "reassigned"].includes(worker?.status)) {
      bucket = "transition";
    } else if (terminalTaskStatus(taskStatus) || ["completed", "failed"].includes(worker?.status)) {
      bucket = "completed";
    } else if (["running", "started"].includes(worker?.status)) {
      bucket = "running";
    } else if (["waiting_approval", "suspended_approval", "manual_pending", "retry_backoff", "stalled", "fallback"].includes(worker?.status)) {
      bucket = "pending";
    }

    return {
      ...worker,
      run_id: runId,
      logical_agent_id: logicalAgentId,
      task_status: taskStatus,
      bucket,
      display_actor: actorLabel(logicalAgentId, worker?.provider),
    };
  });

  return workers.sort((a, b) => (b.last_event_at || "").localeCompare(a.last_event_at || ""));
}

function workerLifecycleBadge(worker) {
  const status = String(worker?.status || "").toLowerCase();
  if (["running", "started"].includes(status)) {
    return { label: "active", className: "lifecycle-active" };
  }
  if (status === "superseded") {
    return { label: "superseded", className: "lifecycle-superseded" };
  }
  if (status === "reassigned") {
    return { label: "reassigned", className: "lifecycle-reassigned" };
  }
  return null;
}

function normalizeQueueEvents(orchState) {
  const byLegacyKey = orchState?.queue_events;
  if (byLegacyKey && typeof byLegacyKey === "object" && !Array.isArray(byLegacyKey)) {
    return Object.entries(byLegacyKey).map(([id, event]) => ({
      id,
      ...(event || {}),
    }));
  }

  const queueEvents = orchState?.queue?.events;
  if (queueEvents && typeof queueEvents === "object" && !Array.isArray(queueEvents)) {
    const workersByEvent = new Map(
      Object.values(orchState?.workers || {})
        .filter((worker) => worker?.queue_event_id)
        .map((worker) => [worker.queue_event_id, worker])
    );

    return Object.entries(queueEvents).map(([id, event]) => {
      const worker = workersByEvent.get(id) || {};
      return {
        id,
        ...(event || {}),
        task_id: event?.task_id || worker.task_id || null,
        agent_id: event?.agent_id || worker.agent_id || null,
        provider: event?.provider || worker.provider || worker.agent_id || null,
        reason: event?.reason || worker.reason || worker.status || null,
        last_event_at: event?.last_event_at || event?.processed_at || worker.last_event_at || event?.last_attempt_at || null,
      };
    });
  }

  return [];
}

function normalizeDispatchQueue(orchState, status) {
  const taskMap = new Map((status?.tasks || []).map((task) => [task.id, task]));
  return normalizeQueueEvents(orchState)
    .map((event) => {
      const taskStatus = taskMap.get(event.task_id)?.status || null;
      return {
        ...event,
        logical_agent_id: logicalWorkerAgentId(event),
        task_status: taskStatus,
        stale: terminalTaskStatus(taskStatus),
      };
    })
    .filter((event) => !["completed", "failed"].includes(event.status) && !event.stale);
}

function taskDeliveryLayer(task) {
  const prefix = (task.id || "").split("-", 1)[0];
  return integrationPrefixes.has(prefix) ? "upstream" : "product";
}

function formatTime(value) {
  if (!value || value === "-") return "-";
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

async function fetchJson(path) {
  const response = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`無法載入 ${path}: ${response.status}`);
  }
  return response.json();
}

async function fetchText(path) {
  const response = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`無法載入 ${path}: ${response.status}`);
  }
  return response.text();
}

async function requestDashboardRefresh() {
  const response = await fetch(`./__refresh?t=${Date.now()}`, {
    method: "POST",
    cache: "no-store",
  });
  if (!response.ok) {
    let detail = "";
    try {
      const payload = await response.json();
      detail = payload?.stderr || payload?.stdout || "";
    } catch {
      detail = await response.text();
    }
    throw new Error(`同步失敗 (${response.status})${detail ? `: ${detail.trim()}` : ""}`);
  }
  return response.json().catch(() => ({ ok: true }));
}

function parseJsonLines(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && line.startsWith("{"))
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch (e) {
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

function renderWorkload(status) {
  const container = qs("#workload-grid");
  if (!container) return;
  container.innerHTML = "";

  const entries = Object.entries(status.workload_summary || {});
  for (const [name, summary] of entries) {
    const target = status.workload?.[name] ?? 0;
    const fill = Math.min(summary.total * 15, 100);
    const card = document.createElement("article");
    card.className = "workload-card";
    card.innerHTML = `
      <div class="lane-head">
        <strong>${name}</strong>
        <span class="status-pill">目標 ${target}%</span>
      </div>
      <div class="workload-bar"><div class="workload-fill" style="width:${fill}%"></div></div>
      <div class="lane-meta">
        <span class="chip">總數 ${summary.total}</span>
        <span class="chip">活躍 ${summary.active}</span>
        <span class="chip">阻塞 ${summary.blocked}</span>
        <span class="chip">完成 ${summary.done}</span>
      </div>
    `;
    container.appendChild(card);
  }
}

function deriveAgentState(status, orchState) {
  const taskMap = new Map((status.tasks || []).map((task) => [task.id, task]));
  const workers = normalizeWorkerRecords(orchState, status);
  return (status.agents || []).map((agent) => {
    const agentId = String(agent.name || "").toLowerCase();
    const owned = (status.tasks || []).filter((task) => task.owner === agent.name);
    const active = owned.filter((task) => ["in_progress", "review", "blocked"].includes(task.status));
    const queued = owned.filter((task) => task.status === "todo");
    const ready = queued.filter((task) =>
      (task.depends_on || []).every((depId) => (taskMap.get(depId)?.status || "done") === "done")
    );
    const waiting = queued.filter((task) => !ready.includes(task));

    const liveRunningWorkers = workers.filter((worker) => worker.logical_agent_id === agentId && worker.bucket === "running");
    const livePendingWorkers = workers.filter((worker) => worker.logical_agent_id === agentId && worker.bucket === "pending");
    const liveTransitionWorkers = workers.filter((worker) => worker.logical_agent_id === agentId && worker.bucket === "transition");

    let derivedStatus = "idle";
    let derivedTaskIds = [];
    let derivedNext = agent.next || "尚未指定";
    let derivedLastUpdate = agent.last_update || null;

    if (liveRunningWorkers.length) {
      derivedStatus = "working";
      derivedTaskIds = liveRunningWorkers.map((worker) => worker.task_id).filter(Boolean);
      const latest = [...liveRunningWorkers].sort((a, b) => (b.last_event_at || "").localeCompare(a.last_event_at || ""))[0];
      derivedNext = latest?.last_error || taskMap.get(latest?.task_id)?.next || derivedNext;
      derivedLastUpdate = latest?.last_event_at || derivedLastUpdate;
    } else if (active.some((task) => task.status === "blocked")) {
      derivedStatus = "blocked";
      derivedTaskIds = active.map((task) => task.id);
    } else if (active.some((task) => task.status === "in_progress")) {
      derivedStatus = "working";
      derivedTaskIds = active.map((task) => task.id);
    } else if (active.some((task) => task.status === "review")) {
      derivedStatus = "reviewing";
      derivedTaskIds = active.map((task) => task.id);
    } else if (livePendingWorkers.length || liveTransitionWorkers.length) {
      derivedStatus = "pending";
      derivedTaskIds = [...livePendingWorkers, ...liveTransitionWorkers].map((worker) => worker.task_id).filter(Boolean);
      const latest = [...livePendingWorkers, ...liveTransitionWorkers].sort((a, b) => (b.last_event_at || "").localeCompare(a.last_event_at || ""))[0];
      derivedNext = latest?.last_error || taskMap.get(latest?.task_id)?.next || derivedNext;
      derivedLastUpdate = latest?.last_event_at || derivedLastUpdate;
    } else if (ready.length) {
      derivedStatus = "ready";
      derivedTaskIds = ready.map((task) => task.id);
      derivedNext = ready[0].next || derivedNext;
      derivedLastUpdate = ready[0].last_update || derivedLastUpdate;
    } else if (waiting.length) {
      derivedStatus = "waiting";
      derivedTaskIds = waiting.slice(0, 3).map((task) => task.id);
      derivedNext = waiting[0].next || derivedNext;
      derivedLastUpdate = waiting[0].last_update || derivedLastUpdate;
    }

    if (!liveRunningWorkers.length && active.length) {
      const latest = [...active].sort((a, b) => (b.last_update || "").localeCompare(a.last_update || ""))[0];
      derivedNext = latest?.next || derivedNext;
      derivedLastUpdate = latest?.last_update || derivedLastUpdate;
    }

    const approved = owned.filter((task) => task.status === "review_approved");

    return {
      ...agent,
      status: derivedStatus,
      current_task_ids: derivedTaskIds,
      next: derivedNext,
      last_update: derivedLastUpdate,
      ready_count: ready.length,
      waiting_count: waiting.length,
      approved_count: approved.length,
      live_running_count: liveRunningWorkers.length,
      live_pending_count: livePendingWorkers.length,
    };
  });
}

function renderAgentLanes(status, orchState) {
  const container = qs("#agent-lanes");
  if (!container) return;
  container.innerHTML = "";
  const taskMap = new Map((status.tasks || []).map((task) => [task.id, task]));

  for (const agent of deriveAgentState(status, orchState)) {
    const focusTasks = (agent.current_task_ids || [])
      .map((taskId) => taskMap.get(taskId))
      .filter(Boolean)
      .map((task) => `<li><strong>${task.id}</strong>：${task.summary_zh || task.title}</li>`)
      .join("");
    const card = document.createElement("article");
    card.className = "lane";
    const activeTasks = (agent.current_task_ids || []).length ? agent.current_task_ids.join(", ") : "目前沒有焦點任務";
    card.innerHTML = `
      <div class="lane-head">
        <strong>${agent.name}</strong>
        <span class="status-pill status-${agent.status}">${statusLabel(agent.status)}</span>
      </div>
      <p class="lane-copy">${(agent.capability_lane || []).map(laneLabel).join(" · ")}</p>
      <div class="lane-meta">
        <span class="chip">${agent.branch || "未指定分支"}</span>
        <span class="chip">${activeTasks}</span>
        <span class="chip">可開工 ${agent.ready_count || 0}</span>
        <span class="chip">等前置 ${agent.waiting_count || 0}</span>
        <span class="chip">已批准 ${agent.approved_count || 0}</span>
      </div>
      ${focusTasks ? `<ul class="note-list compact">${focusTasks}</ul>` : ""}
      <p class="lane-copy">下一步：${truncate(agent.next, 120)}</p>
      <p class="lane-copy">最後更新：${formatTime(agent.last_update)}</p>
    `;
    container.appendChild(card);
  }
}

function renderDeliveryLayers(status) {
  const container = qs("#delivery-layers");
  container.innerHTML = "";

  const tasks = (status.tasks || []).filter((task) => task.status !== "done");
  const layers = [
    {
      key: "product",
      title: "產品本體工作",
      copy: "產品本體、執行邊界、registry、feedback、repo 邊界與 audit 等工作。",
      tasks: tasks.filter((task) => taskDeliveryLayer(task) === "product"),
    },
    {
      key: "upstream",
      title: "外部 / 上游整合工作",
      copy: "針對外部框架、adapter、整合點與 smoke test 的工作。",
      tasks: tasks.filter((task) => taskDeliveryLayer(task) === "upstream"),
    },
  ];

  for (const layer of layers) {
    const section = document.createElement("section");
    section.className = "delivery-layer";
    section.innerHTML = `
      <div class="delivery-layer-head">
        <div>
          <h3>${layer.title}</h3>
          <p class="section-copy">${layer.copy}</p>
        </div>
        <span class="status-pill">${layer.tasks.length} 個未完成任務</span>
      </div>
      <div class="delivery-layer-grid"></div>
    `;
    const grid = section.querySelector(".delivery-layer-grid");

    if (!layer.tasks.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "目前這一層沒有未完成任務。";
      grid.appendChild(empty);
    }

    for (const task of layer.tasks) {
      const card = document.createElement("article");
      card.className = "delivery-card";
      const depends = (task.depends_on || []).length ? task.depends_on.join(", ") : "無";
      card.innerHTML = `
        <div class="task-head">
          <strong>${task.id}</strong>
          <span class="status-pill status-${task.status}">${statusLabel(task.status)}</span>
        </div>
        <p>${task.title}</p>
        <p class="task-summary">工作說明：${task.summary_zh || "尚未補上中文說明。"}</p>
        <div class="lane-meta">
          <span class="chip">${task.phase}</span>
          <span class="chip">負責人 ${task.owner}</span>
          <span class="chip">審查者 ${task.reviewer}</span>
        </div>
        <div class="lane-meta">
          <span class="chip">依賴 ${depends}</span>
        </div>
        <p class="card-copy">下一步：${truncate(task.next, 120)}</p>
      `;
      grid.appendChild(card);
    }

    container.appendChild(section);
  }
}

function renderTaskBoard(status, orchState) {
  const board = qs("#task-board");
  if (!board) return;
  board.innerHTML = "";

  const workers = normalizeWorkerRecords(orchState, status);
  const liveWorkersByTask = new Map();
  for (const worker of workers) {
    if (!worker.task_id) continue;
    if (!["running", "pending"].includes(worker.bucket)) continue;
    if (!liveWorkersByTask.has(worker.task_id)) liveWorkersByTask.set(worker.task_id, []);
    liveWorkersByTask.get(worker.task_id).push(worker);
  }

  const displayTasks = (status.tasks || []).map((task) => {
    const liveWorkers = liveWorkersByTask.get(task.id) || [];
    const hasRunningWorker = liveWorkers.some((worker) => worker.bucket === "running");
    const hasPendingWorker = liveWorkers.some((worker) => worker.bucket === "pending");
    let displayStatus = task.status;
    if (!terminalTaskStatus(task.status) && hasRunningWorker) {
      displayStatus = "in_progress";
    } else if (task.status === "todo" && hasPendingWorker) {
      displayStatus = "in_progress";
    }
    return {
      ...task,
      display_status: displayStatus,
      live_workers: liveWorkers,
    };
  });

  for (const column of boardColumns) {
    const wrapper = document.createElement("section");
    wrapper.className = "board-column";
    const tasks = displayTasks.filter((task) => task.display_status === column.key);
    wrapper.innerHTML = `<h3>${column.label}</h3><div class="column-stack"></div>`;
    const stack = wrapper.querySelector(".column-stack");

    if (!tasks.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "目前沒有任務";
      stack.appendChild(empty);
    }

    for (const task of tasks) {
      const card = document.createElement("article");
      card.className = "task-card";
      const depends = (task.depends_on || []).length ? task.depends_on.join(", ") : "無";
      const runtimeWorkers = task.live_workers || [];
      const runtimeBadge = runtimeWorkers.length
        ? `<span class="chip">live worker ${runtimeWorkers.map((worker) => `${agentLabel(worker.logical_agent_id)}:${worker.status}`).join(" / ")}</span>`
        : "";
      const canonicalBadge = task.display_status !== task.status
        ? `<span class="chip">canonical ${statusLabel(task.status)}</span>`
        : "";
      const approvedFollowupBadge = task.status === "review_approved"
        ? `<span class="chip">待收尾回到 ${task.owner}</span>`
        : "";
      card.innerHTML = `
        <div class="task-head">
          <strong>${task.id}</strong>
          <span class="status-pill status-${task.display_status}">${statusLabel(task.display_status)}</span>
        </div>
        <p>${task.title}</p>
        <p class="task-summary">工作說明：${task.summary_zh || "尚未補上中文說明。"}</p>
        <div class="task-meta">
          <span class="chip">${task.phase}</span>
          <span class="chip">負責人 ${task.owner}</span>
          <span class="chip">審查者 ${task.reviewer}</span>
        </div>
        <div class="task-foot">
          <span class="chip">依賴 ${depends}</span>
          <span class="chip">${formatTime(task.last_update)}</span>
        </div>
        <div class="task-meta">
          ${runtimeBadge}
          ${canonicalBadge}
          ${approvedFollowupBadge}
        </div>
        ${
          normalizeReviewNotes(task.review_notes_zh).length
            ? `<div class="review-block">
                <p class="review-title">審查重點</p>
                <ul class="note-list">${normalizeReviewNotes(task.review_notes_zh).map((note) => `<li>${note}</li>`).join("")}</ul>
                ${task.review_file ? `<p class="card-copy">參考檔案：<code>${task.review_file}</code></p>` : ""}
              </div>`
            : ""
        }
        <p class="card-copy">下一步：${truncate(task.next, 120)}</p>
      `;
      stack.appendChild(card);
    }

    board.appendChild(wrapper);
  }
}

function buildDependencySchedule(tasks) {
  const allTasks = tasks || [];
  const remaining = allTasks.filter((task) => scheduleOpenTaskStatuses.has(String(task.status || "").toLowerCase()));
  const approved = allTasks.filter((task) => String(task.status || "").toLowerCase() === "review_approved");
  const taskMap = new Map(allTasks.map((task) => [task.id, task]));
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
      const aActive = activeTaskStatuses.has(a.status) ? 0 : 1;
      const bActive = activeTaskStatuses.has(b.status) ? 0 : 1;
      if (aActive !== bActive) return aActive - bActive;
      return a.id.localeCompare(b.id);
    });

  let frontier = sortTasks(remaining.filter((task) => (indegree.get(task.id) || 0) === 0));
  const scheduled = new Set();
  const waves = [];

  while (frontier.length) {
    waves.push(frontier);
    const nextIds = new Set();

    for (const task of frontier) {
      scheduled.add(task.id);
      for (const dependentId of dependents.get(task.id) || []) {
        indegree.set(dependentId, (indegree.get(dependentId) || 0) - 1);
        if ((indegree.get(dependentId) || 0) === 0 && !scheduled.has(dependentId)) {
          nextIds.add(dependentId);
        }
      }
    }

    frontier = sortTasks(
      [...nextIds]
        .map((taskId) => taskMap.get(taskId))
        .filter(Boolean)
    );
  }

  const cyclic = sortTasks(remaining.filter((task) => !scheduled.has(task.id)));

  const depDone = (depId) => {
    const depStatus = String(taskMap.get(depId)?.status || "done").toLowerCase();
    return depStatus === "done";
  };
  const readyNow = remaining.filter((task) => ((task.depends_on || []).filter((depId) => !depDone(depId))).length === 0 && task.status === "todo").length;
  const activeNow = remaining.filter((task) => activeTaskStatuses.has(task.status)).length;
  const waitingNow = remaining.filter((task) => ((task.depends_on || []).filter((depId) => !depDone(depId))).length > 0).length;
  const explicitBlocked = remaining.filter((task) => task.status === "blocked").length;
  const approvedNow = approved.length;

  return { waves, cyclic, taskMap, readyNow, activeNow, waitingNow, explicitBlocked, approvedNow, approved };
}

function dependencyBatchState(task, index, unresolvedDeps = []) {
  const status = String(task?.status || "").toLowerCase();
  if (status === "done") {
    return { key: "completed", label: "已完成" };
  }
  if (status === "review_approved") {
    return { key: "approved", label: "待收尾" };
  }
  if (status === "blocked") {
    return { key: "blocked", label: "已阻塞" };
  }
  if (activeTaskStatuses.has(status)) {
    return unresolvedDeps.length > 0
      ? { key: "waiting", label: "等待前置" }
      : { key: "active", label: "進行中" };
  }
  if (status === "todo" && unresolvedDeps.length === 0 && index === 0) {
    return { key: "ready", label: "可開工" };
  }
  return { key: "waiting", label: "等待前置" };
}

function renderDependencySchedule(status) {
  const summary = qs("#dependency-summary");
  const container = qs("#dependency-schedule");
  summary.innerHTML = "";
  container.innerHTML = "";
  if (summary) summary.innerHTML = "";
  if (container) container.innerHTML = "";

  const schedule = buildDependencySchedule(status.tasks || []);
  const summaryItems = [
    { label: "現在可開工", value: schedule.readyNow, note: "所有前置都已完成，且尚未開始" },
    { label: "目前進行中", value: schedule.activeNow, note: "包含進行中與待審查" },
    { label: "等待前置", value: schedule.waitingNow, note: "依賴尚未完成，不能安全開工" },
    { label: "明確阻塞", value: schedule.explicitBlocked, note: "已有 blocker 狀態記錄" },
    { label: "已批准待收尾", value: schedule.approvedNow, note: "review_approved 尚未正式完成，需由 owner 收尾成 done" },
  ];

  for (const item of summaryItems) {
    const card = document.createElement("article");
    card.className = "workload-card";
    card.innerHTML = `
      <div class="lane-head">
        <strong>${item.label}</strong>
        <span class="status-pill">${item.value}</span>
      </div>
      <p class="dependency-copy">${item.note}</p>
    `;
    summary.appendChild(card);
  }

  if (schedule.approved.length) {
    const approvedSection = document.createElement("section");
    approvedSection.className = "dependency-wave dependency-approved";
    approvedSection.innerHTML = `
      <div class="dependency-wave-head">
        <div>
          <h3>已批准待收尾</h3>
          <p class="section-copy">${schedule.approved.length} 個任務</p>
        </div>
        <div class="chip-row">
          <span class="status-pill batch-pill batch-approved">review_approved ${schedule.approved.length}</span>
        </div>
      </div>
      <div class="dependency-grid"></div>
    `;
    const approvedGrid = approvedSection.querySelector('.dependency-grid');
    for (const task of schedule.approved) {
      const depends = (task.depends_on || []).length ? task.depends_on.join(', ') : '無';
      const card = document.createElement('article');
      card.className = 'dependency-card batch-approved';
      card.innerHTML = `
        <div class="task-head">
          <strong>${task.id}</strong>
          <div class="chip-row">
            <span class="status-pill batch-pill batch-approved">待收尾</span>
            <span class="status-pill status-${task.status}">${statusLabel(task.status)}</span>
          </div>
        </div>
        <p>${task.title}</p>
        <p class="task-summary">工作說明：${task.summary_zh || '尚未補上中文說明。'}</p>
        <div class="dependency-meta">
          <span class="chip">負責人 ${task.owner}</span>
          <span class="chip">審查者 ${task.reviewer}</span>
          <span class="chip">前置 ${depends}</span>
        </div>
        <p class="dependency-copy">這些任務已通過 review gate，但尚未正式完成；owner 收尾成 done 後，才會解除下游依賴。</p>
      `;
      approvedGrid.appendChild(card);
    }
    container.appendChild(approvedSection);
  }

  if (!schedule.waves.length && !schedule.cyclic.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = schedule.approved.length ? "目前沒有新的開發 / 審查波次；上方仍有已批准待 owner 收尾的任務。" : "目前沒有可排程的未完成任務。";
    container.appendChild(empty);
    return;
  }

  schedule.waves.forEach((wave, index) => {
    const section = document.createElement("section");
    section.className = "dependency-wave";
    const title = index === 0 ? "現在這一波" : index === 1 ? "下一波" : `第 ${index + 1} 波`;
    const counts = wave.reduce(
      (acc, task) => {
        const unresolvedDeps = (task.depends_on || []).filter((depId) => String(schedule.taskMap.get(depId)?.status || "done").toLowerCase() !== "done");
        const batch = dependencyBatchState(task, index, unresolvedDeps).key;
        acc[batch] = (acc[batch] || 0) + 1;
        return acc;
      },
      { completed: 0, active: 0, ready: 0, waiting: 0, blocked: 0 }
    );
    section.innerHTML = `
      <div class="dependency-wave-head">
        <div>
          <h3>${title}</h3>
          <p class="section-copy">${wave.length} 個任務</p>
        </div>
        <div class="chip-row">
          ${counts.active ? `<span class="status-pill status-running">進行中 ${counts.active}</span>` : ""}
          ${counts.ready ? `<span class="status-pill status-ready">可開工 ${counts.ready}</span>` : ""}
          ${counts.waiting ? `<span class="status-pill status-pending">等待前置 ${counts.waiting}</span>` : ""}
          ${counts.blocked ? `<span class="status-pill status-blocked">阻塞 ${counts.blocked}</span>` : ""}
        </div>
      </div>
      <div class="dependency-grid"></div>
    `;
    const grid = section.querySelector(".dependency-grid");

    for (const task of wave) {
      const unresolvedDeps = (task.depends_on || []).filter((depId) => String(schedule.taskMap.get(depId)?.status || "done").toLowerCase() !== "done");
      const depends = (task.depends_on || []).length ? task.depends_on.join(", ") : "無";
      const unresolved = unresolvedDeps.length ? unresolvedDeps.join(", ") : "無";
      const card = document.createElement("article");
      const batchState = dependencyBatchState(task, index, unresolvedDeps);
      card.className = `dependency-card batch-${batchState.key}`;
      card.innerHTML = `
        <div class="task-head">
          <strong>${task.id}</strong>
          <div class="chip-row">
            <span class="status-pill batch-pill batch-${batchState.key}">${batchState.label}</span>
            <span class="status-pill status-${task.status}">${statusLabel(task.status)}</span>
          </div>
        </div>
        <p>${task.title}</p>
        <p class="task-summary">工作說明：${task.summary_zh || "尚未補上中文說明。"}</p>
        <div class="dependency-meta">
          <span class="chip">${task.phase}</span>
          <span class="chip">負責人 ${task.owner}</span>
          <span class="chip">審查者 ${task.reviewer}</span>
        </div>
        <div class="dependency-meta">
          <span class="chip">全部前置 ${depends}</span>
          <span class="chip">未完成前置 ${unresolved}</span>
        </div>
        <p class="dependency-copy">排程判斷：${
          batchState.key === "completed"
            ? "這一波中的工作已完成，後續可以往下一波推進。"
            : batchState.key === "active"
              ? "這一波正在被執行或審查，完成後會推動下一波。"
              : batchState.key === "ready"
                ? "這一波可直接開工。"
                : batchState.key === "blocked"
                  ? "這一波存在明確阻塞，需先解除阻塞。"
                  : "仍有未完成前置；待前置正式 done 後才適合開始。"
        }</p>
        <p class="card-copy">下一步：${truncate(task.next, 120)}</p>
      `;
      grid.appendChild(card);
    }

    container.appendChild(section);
  });

  if (schedule.cyclic.length) {
    const section = document.createElement("section");
    section.className = "dependency-wave";
    section.innerHTML = `
      <div class="dependency-wave-head">
        <h3>循環或異常依賴</h3>
        <span class="section-copy">${schedule.cyclic.length} 個任務</span>
      </div>
      <div class="dependency-grid"></div>
    `;
    const grid = section.querySelector(".dependency-grid");
    for (const task of schedule.cyclic) {
      const card = document.createElement("article");
      card.className = "dependency-card blocked-now";
      card.innerHTML = `
        <div class="task-head">
          <strong>${task.id}</strong>
          <span class="status-pill status-blocked">需檢查</span>
        </div>
        <p>${task.title}</p>
        <div class="dependency-meta">
          <span class="chip">前置 ${(task.depends_on || []).join(", ") || "無"}</span>
        </div>
        <p class="dependency-copy">這些任務沒有被正常排進波次，通常代表依賴循環或缺少狀態收斂。</p>
      `;
      grid.appendChild(card);
    }
    container.appendChild(section);
  }
}

function renderReviewNotes(status) {
  const tasksWithNotes = (status.tasks || []).filter((task) => normalizeReviewNotes(task.review_notes_zh).length);
  renderStackList(
    "#review-note-list",
    tasksWithNotes,
    "目前沒有 reviewer 備註。",
    (task) => `
      <div class="stack-head">
        <strong>${task.id}</strong>
        <span class="status-pill status-${task.status}">${statusLabel(task.status)}</span>
      </div>
      <p>${task.title}</p>
      <p class="task-summary">工作說明：${task.summary_zh || "尚未補上中文說明。"}</p>
      <p class="card-copy">Reviewer：${task.reviewer}</p>
      <ul class="note-list">${normalizeReviewNotes(task.review_notes_zh).map((note) => `<li>${note}</li>`).join("")}</ul>
      ${task.review_file ? `<p class="card-copy">詳細檔案：<code>${task.review_file}</code></p>` : ""}
    `
  );
}

function renderAuditStatus(status) {
  const audits = (status.tasks || []).filter((task) => task.phase === "Audit" || task.id.startsWith("AUD-"));
  const summaryContainer = qs("#audit-status");
  summaryContainer.innerHTML = "";

  const summaryItems = [
    { label: "總 Audit", value: audits.length, note: "目前被追蹤的對齊檢查任務數" },
    {
      label: "待開始",
      value: audits.filter((task) => task.status === "todo").length,
      note: "已指派但還沒開始的 audit",
    },
    {
      label: "進行中",
      value: audits.filter((task) => ["in_progress", "review"].includes(task.status)).length,
      note: "已開始或正在審查中的 audit",
    },
    {
      label: "已完成",
      value: audits.filter((task) => task.status === "done").length,
      note: "已產出檢查結果的 audit",
    },
  ];

  for (const item of summaryItems) {
    const card = document.createElement("article");
    card.className = "workload-card";
    card.innerHTML = `
      <div class="lane-head">
        <strong>${item.label}</strong>
        <span class="status-pill">${item.value}</span>
      </div>
      <p class="dependency-copy">${item.note}</p>
    `;
    summaryContainer.appendChild(card);
  }

  renderStackList(
    "#audit-list",
    audits,
    "目前沒有 audit 任務。",
    (task) => `
      <div class="stack-head">
        <strong>${task.id}</strong>
        <span class="status-pill status-${task.status}">${statusLabel(task.status)}</span>
      </div>
      <p>${task.title}</p>
      <p class="task-summary">工作說明：${task.summary_zh || "尚未補上中文說明。"}</p>
      <div class="lane-meta">
        <span class="chip">負責人 ${task.owner}</span>
        <span class="chip">審查者 ${task.reviewer}</span>
      </div>
      ${
        (task.artifacts || []).length
          ? `<p class="card-copy">輸出檔案：${task.artifacts.map((path) => `<code>${path}</code>`).join("、")}</p>`
          : ""
      }
      <p class="card-copy">下一步：${truncate(task.next, 120)}</p>
    `
  );
}

function renderStackList(selector, items, emptyText, formatter) {
  const container = qs(selector);
  container.innerHTML = "";

  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = emptyText;
    container.appendChild(empty);
    return;
  }

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "stack-card";
    card.innerHTML = formatter(item);
    container.appendChild(card);
  }
}

function renderSnapshot(snapshot) {
  const container = qs("#snapshot");
  container.innerHTML = "";

  const blocks = [
    snapshot.objective || "目前沒有可顯示的目標。",
    ...(snapshot.sprint || []),
  ];

  for (const block of blocks) {
    const card = document.createElement("article");
    card.className = "snapshot-card";
    card.innerHTML = `<p class="snapshot-item">${block}</p>`;
    container.appendChild(card);
  }
}

function timeAgo(value) {
  if (!value) return "-";
  const diff = Math.floor((Date.now() - new Date(value).getTime()) / 1000);
  if (diff < 60) return `${diff} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

function truncate(text, maxLen = 100) {
  if (!text || text.length <= maxLen) return text || "-";
  const safeText = text.replace(/"/g, '&quot;');
  return `
    <span class="truncated-wrapper">
      <span class="text-short">${text.slice(0, maxLen)}...</span>
      <span class="text-full" style="display:none">${safeText}</span>
      <button class="expand-btn" onclick="const p=this.parentElement; const s=p.querySelector('.text-short'); const f=p.querySelector('.text-full'); if(f.style.display==='none'){f.style.display='inline'; s.style.display='none'; this.textContent='收合'}else{f.style.display='none'; s.style.display='inline'; this.textContent='展開'}">展開</button>
    </span>`;
}

function renderOverviewMetrics(status, orchState, approvalQueue) {
  const container = qs("#overview-metrics");
  if (!container) return;
  const tasks = status.tasks || [];
  const total = tasks.length;
  const done = tasks.filter((t) => t.status === "done").length;
  const approvedTasks = tasks.filter((t) => t.status === "review_approved");
  const approved = approvedTasks.length;
  const backlogTasks = tasks.filter((t) => ["todo", "in_progress", "review", "blocked"].includes(String(t.status || "").toLowerCase()));
  const backlogOpen = backlogTasks.length;
  const queueEvents = normalizeDispatchQueue(orchState, status);
  const approvalPending = (approvalQueue?.pending || []).length;
  const activeWorkerRows = normalizeWorkerRecords(orchState, status).filter((w) => w.bucket === "running");
  const activeWorkers = activeWorkerRows.length;

  function renderTaskDetail(task) {
    return `
      <div class="metric-task-item">
        <div class="metric-task-head">
          <strong>${task.id}</strong>
          <span class="status-pill status-${task.status}">${statusLabel(task.status)}</span>
        </div>
        <div class="metric-task-meta">
          <span class="chip">Owner ${task.owner}</span>
          <span class="chip">Reviewer ${task.reviewer}</span>
        </div>
        <p class="metric-task-copy">${truncate(task.summary_zh || task.title || "", 80)}</p>
      </div>
    `;
  }

  function renderQueueDetail(event) {
    return `
      <div class="metric-task-item">
        <div class="metric-task-head">
          <strong>${event.task_id || event.id || "-"}</strong>
          <span class="status-pill status-${event.status || "pending"}">${statusLabel(event.status || "pending")}</span>
        </div>
        <div class="metric-task-meta">
          <span class="chip">${actorLabel(event.logical_agent_id, event.provider)}</span>
          <span class="chip">${event.reason || "-"}</span>
          <span class="chip">${timeAgo(event.last_event_at || event.last_attempt_at || event.processed_at)}</span>
        </div>
      </div>
    `;
  }

  function renderWorkerDetail(worker) {
    const lifecycle = workerLifecycleBadge(worker);
    return `
      <div class="metric-task-item">
        <div class="metric-task-head">
          <strong>${worker.task_id || worker.run_id || "-"}</strong>
          <span class="status-pill status-${worker.status || "running"}">${statusLabel(worker.status || "running")}</span>
          ${lifecycle ? `<span class="chip ${lifecycle.className}">${lifecycle.label}</span>` : ""}
        </div>
        <div class="metric-task-meta">
          <span class="chip">${worker.display_actor}</span>
          <span class="chip">${worker.reason || worker.mode || "-"}</span>
          <span class="chip">${timeAgo(worker.last_event_at || worker.started_at)}</span>
        </div>
      </div>
    `;
  }

  const items = [
    { label: "正式完成", value: `${done} / ${total}`, note: `待收尾 ${approved} · 其他 backlog ${backlogOpen}` },
    {
      label: "已批准待收尾",
      value: approved,
      note: approved ? "review 通過，但仍需 owner finalize 成 done" : "目前沒有 review_approved 任務",
      tasks: approvedTasks,
      emptyLabel: "目前沒有待收尾任務",
    },
    {
      label: "其他 open backlog",
      value: backlogOpen,
      note: backlogOpen ? "待開始、審查中或阻塞中的非 finalize 任務" : "目前沒有其他 open backlog",
      tasks: backlogTasks,
      emptyLabel: "目前沒有其他 open backlog",
    },
    {
      label: "Dispatch Queue",
      value: queueEvents.length,
      note: queueEvents.length ? "有事件待處理" : "目前清空",
      details: queueEvents,
      emptyLabel: "目前沒有 dispatch queue 項目",
      renderItem: renderQueueDetail,
    },
    { label: "Approval Queue", value: approvalPending, note: approvalPending ? "有待批准項目" : "目前清空" },
    {
      label: "Active Workers",
      value: activeWorkers,
      note: "目前執行中的 worker 數",
      details: activeWorkerRows,
      emptyLabel: "目前沒有 active worker",
      renderItem: renderWorkerDetail,
    },
  ];

  container.innerHTML = items.map((item) => {
    const taskList = Array.isArray(item.tasks) ? item.tasks : [];
    const detailList = Array.isArray(item.details) ? item.details : [];
    const detailRender = item.renderItem || renderTaskDetail;
    const detailRows = item.details ? detailList : taskList;
    const detailHtml = detailRows.length
      ? `
        <details class="metric-details">
          <summary>查看 ${detailRows.length} 個項目</summary>
          <div class="metric-task-list">
            ${detailRows.map((entry) => detailRender(entry)).join("")}
          </div>
        </details>
      `
      : (item.tasks || item.details)
        ? `<div class="metric-empty">${item.emptyLabel || "目前沒有項目"}</div>`
        : "";
    return `
      <article class="metric-card">
        <div class="metric-label">${item.label}</div>
        <div class="metric-value">${item.value}</div>
        <div class="metric-note">${item.note}</div>
        ${detailHtml}
      </article>
    `;
  }).join("");
}

function renderSystemStatus(status, orchState, approvalQueue, agentStates) {
  const statusEl = qs("#system-status");
  const historyEl = qs("#worker-history");
  if (!statusEl || !historyEl) return;
  statusEl.innerHTML = "";
  historyEl.innerHTML = "";
  const queueEvents = normalizeDispatchQueue(orchState, status);
  const supervisor = orchState?.supervisor || {};
  const supervisorPid = supervisor?.pid || "-";
  const supervisorStartedAt = supervisor?.started_at || orchState?.initialized_at || null;
  const supervisorHeartbeat = supervisor?.last_heartbeat_at || orchState?.last_heartbeat_at || null;
  const lastScan = orchState?.last_scan_at || supervisorHeartbeat || null;
  const workers = normalizeWorkerRecords(orchState, status);
  const activeWorkerCount = workers.filter((w) => w.bucket === "running").length;
  const pending = (approvalQueue?.pending || []).length;

  // Supervisor card
  const supervisorCard = document.createElement("article");
  supervisorCard.className = "sys-card";
  supervisorCard.innerHTML = `
    <div class="sys-card-head"><span class="sys-icon">🖥</span><strong>Supervisor</strong></div>
    <div class="sys-card-body">
      <span class="status-pill ${supervisorHeartbeat ? 'status-working' : 'status-blocked'}">${supervisorHeartbeat ? '運作中' : '無資料'}</span>
      <span class="chip">PID：${supervisorPid}</span>
      <span class="chip">啟動：${formatTime(supervisorStartedAt)}</span>
      <span class="chip">Heartbeat：${timeAgo(supervisorHeartbeat)}</span>
      <span class="chip">上次掃描：${timeAgo(lastScan)}</span>
      <span class="chip">Active Workers：${activeWorkerCount}</span>
    </div>
  `;
  statusEl.appendChild(supervisorCard);

  const dispatchCard = document.createElement("article");
  dispatchCard.className = "sys-card";
  dispatchCard.innerHTML = `
    <div class="sys-card-head"><span class="sys-icon">📬</span><strong>Dispatch Queue</strong></div>
    <div class="sys-card-body">
      <span class="status-pill ${queueEvents.length > 0 ? 'status-review' : 'status-done'}">${queueEvents.length > 0 ? `${queueEvents.length} 個待處理` : '清空'}</span>
      <span class="chip">目前 active workers：${activeWorkerCount}</span>
      ${queueEvents.map((event) => `
        <div class="approval-item">
          <span class="chip">${event.task_id || '-'}</span>
          <span class="chip">${actorLabel(event.logical_agent_id, event.provider)}</span>
          <span class="chip">${event.status || '-'}</span>
          <span class="chip">${event.reason || '-'}</span>
          <span class="chip">${timeAgo(event.last_event_at || event.last_attempt_at || event.processed_at)}</span>
        </div>
      `).join("")}
    </div>
  `;
  statusEl.appendChild(dispatchCard);

  // Pending approvals card
  const approvalCard = document.createElement("article");
  approvalCard.className = "sys-card";
  approvalCard.innerHTML = `
    <div class="sys-card-head"><span class="sys-icon">⏳</span><strong>待批准佇列</strong></div>
    <div class="sys-card-body">
      <span class="status-pill ${pending > 0 ? 'status-review' : 'status-done'}">${pending > 0 ? `${pending} 個待處理` : '清空'}</span>
      ${(approvalQueue?.pending || []).map(a => `
        <div class="approval-item">
          <span class="chip">${actorLabel(a.agent_id, a.provider)}</span>
          <span class="chip">task=${a.task_id || '-'}</span>
          <span class="chip">${a.tool_name}</span>
          <span class="chip">${timeAgo(a.created_at)}</span>
        </div>
      `).join("")}
    </div>
  `;
  statusEl.appendChild(approvalCard);

  // Worker summary cards by logical worker lane
  const logicalAgents = ["claude", "gemini", "codex", "copilot"];
  const agentStateMap = new Map((agentStates || []).map((a) => [a.name.toLowerCase(), a]));
  for (const agentId of logicalAgents) {
    const pw = workers.filter((w) => w.logical_agent_id === agentId);
    const running = pw.filter((w) => w.bucket === "running").length;
    const waiting = pw.filter((w) => w.bucket === "pending").length;
    const transition = pw.filter((w) => w.bucket === "transition").length;
    const failed = pw.filter(w => w.status === "failed").length;
    const completed = pw.filter((w) => w.bucket === "completed").length;
    const agent = agentStateMap.get(agentId);
    const runningTasks = pw.filter((w) => w.bucket === "running").map((w) => w.task_id).filter(Boolean);
    const card = document.createElement("article");
    card.className = "sys-card";
    card.innerHTML = `
      <div class="sys-card-head"><span class="sys-icon">${running > 0 ? "🟡" : failed > 0 ? "🔴" : "⚪"}</span><strong>${agentLabel(agentId)}</strong></div>
      <div class="sys-card-body">
        <span class="chip">執行中 ${running}</span>
        <span class="chip">等待 ${waiting}</span>
        ${transition ? `<span class="chip">改派 ${transition}</span>` : ""}
        <span class="chip">失敗 ${failed}</span>
        <span class="chip">完成 ${completed}</span>
        ${runningTasks.length ? `<span class="chip">任務 ${runningTasks.join(", ")}</span>` : ""}
        ${agent ? `<span class="chip">可開工 ${agent.ready_count || 0}</span><span class="chip">等前置 ${agent.waiting_count || 0}</span>` : ""}
      </div>
    `;
    statusEl.appendChild(card);
  }

  const workerGroups = logicalAgents.map((agentId) => {
    const groupWorkers = workers.filter((worker) => worker.logical_agent_id === agentId);
    return {
      agentId,
      running: groupWorkers.filter((worker) => worker.bucket === "running"),
      pending: groupWorkers.filter((worker) => worker.bucket === "pending"),
      transition: groupWorkers.filter((worker) => worker.bucket === "transition"),
      completed: groupWorkers.filter((worker) => worker.bucket === "completed"),
    };
  });

  if (!workerGroups.some((group) => group.running.length || group.pending.length || group.transition.length || group.completed.length)) {
    historyEl.innerHTML = '<p class="empty">尚無 Worker 記錄。</p>';
    return;
  }

  historyEl.innerHTML = workerGroups
    .map((group) => {
      const total = group.running.length + group.pending.length + group.transition.length + group.completed.length;
      if (!total) return "";
      const renderBucket = (label, items, open = true, options = {}) => {
        const { hideWhenEmpty = false } = options;
        if (hideWhenEmpty && !items.length) return "";
        return `
        <details class="worker-bucket" ${open ? "open" : ""}>
          <summary class="worker-bucket-head">
            <strong>${label}</strong>
            <span class="chip">${items.length}</span>
          </summary>
          <div class="worker-bucket-body">
            ${items.length ? items.map((w) => `
              <article class="queue-item worker-row worker-${w.status}">
                <div class="queue-item-head">
                  <strong>${w.task_id || "-"}</strong>
                  <div class="chip-row">
                    ${workerLifecycleBadge(w) ? `<span class="status-pill worker-lifecycle ${workerLifecycleBadge(w).className}">${workerLifecycleBadge(w).label}</span>` : ""}
                    <span class="status-pill status-${w.status}">${workerStatusIcon[w.status] || "?"} ${w.status}</span>
                  </div>
                </div>
                <div class="lane-meta">
                  <span class="chip"><code>${w.mode || "-"}</code></span>
                  <span class="chip">${w.display_actor}</span>
                  ${w.task_status ? `<span class="chip">task=${statusLabel(w.task_status)}</span>` : ""}
                  <span class="chip">${timeAgo(w.last_event_at)}</span>
                </div>
                ${w.last_error ? `<p class="meta-line">${truncate(w.last_error, 120)}</p>` : ""}
              </article>
            `).join("") : '<p class="empty">目前沒有項目。</p>'}
          </div>
        </details>
      `;
      };

      return `
        <section class="worker-group">
          <div class="worker-group-head">
            <strong>${agentLabel(group.agentId)}</strong>
            <span class="chip">共 ${total} 筆</span>
          </div>
          <div class="worker-buckets">
            ${renderBucket("進行中", group.running, true)}
            ${renderBucket("等待處理", group.pending, true)}
            ${renderBucket("已改派 / 已接手", group.transition, false, { hideWhenEmpty: true })}
            ${renderBucket("已完成", group.completed, false, { hideWhenEmpty: true })}
          </div>
        </section>
      `;
    })
    .join("");
}

function renderProgressBar(tasks) {
  const total = (tasks || []).length;
  const done = (tasks || []).filter(t => t.status === "done").length;
  const approved = (tasks || []).filter(t => t.status === "review_approved").length;
  const open = (tasks || []).filter(t => ["todo", "in_progress", "review", "blocked"].includes(String(t.status || "").toLowerCase())).length;
  const pct = total ? Math.round((done / total) * 100) : 0;
  const label = qs("#progress-label");
  const fill = qs("#progress-fill");
  if (label) label.textContent = `Sprint 進度：正式完成 ${done} / ${total} (${pct}%) · 待收尾 ${approved} · 其他 open ${open}`;
  if (fill) fill.style.width = `${pct}%`;
}

function renderActivity(entries) {
  const container = qs("#activity-list");
  container.innerHTML = "";
  const highSignalTypes = new Set([
    "worker_started",
    "worker_failed",
    "worker_resumed",
    "worker_recovered",
    "worker_stalled",
    "worker_superseded",
    "approval_requested",
    "approval_resolved",
    "task_reassigned",
    "handoff",
    "review_approved",
    "done",
    "blocker",
  ]);
  const recent = entries
    .filter((entry) => highSignalTypes.has(entry.type))
    .slice(-8)
    .reverse();

  if (!recent.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "目前還沒有活動紀錄。";
    container.appendChild(empty);
    return;
  }

  for (const entry of recent) {
    const card = document.createElement("article");
    card.className = "activity-card";
    const typeLabel = activityTypeLabel[entry.type] || entry.type || "-";
    const msg = (entry.message || "").slice(0, 120) + ((entry.message || "").length > 120 ? "…" : "");
    card.innerHTML = `
      <div class="lane-head">
        <strong>${entry.agent || entry.provider || "-"}</strong>
        <span class="activity-meta">${timeAgo(entry.ts)}</span>
      </div>
      <p class="activity-message">${msg}</p>
      <div class="lane-meta">
        <span class="chip">${typeLabel}</span>
        <span class="chip">${entry.task_id || "-"}</span>
      </div>
    `;
    container.appendChild(card);
  }
}

let renderInFlight = false;

async function render({ syncFirst = false } = {}) {
  if (renderInFlight) return;
  renderInFlight = true;
  try {
    const refreshButton = qs("#refresh-button");
    if (refreshButton) {
      refreshButton.disabled = true;
      refreshButton.textContent = syncFirst ? "同步中..." : "重新整理中...";
    }
    if (syncFirst) {
      await requestDashboardRefresh();
    }
    const [status, activityText, currentWorkText, orchState, approvalQueue] = await Promise.all([
      fetchJson(DATA_FILES.status),
      fetchText(DATA_FILES.activity),
      fetchText(DATA_FILES.currentWork),
      fetchJson(DATA_FILES.orchestratorState).catch(() => null),
      fetchJson(DATA_FILES.approvalQueue).catch(() => null),
    ]);

    const logs = parseJsonLines(activityText);
    const snapshot = parseCurrentWork(currentWorkText);
    const projectName = titleCase(status.project || snapshot.project || "project");
    const projectBadge = qs("#project-badge");

    qs("#objective").textContent = status.objective || snapshot.objective || "目前沒有可顯示的目標。";
    qs("#updated-at").textContent = formatTime(status.updated_at);
    if (projectBadge) {
      projectBadge.textContent = `${projectName} Runtime`;
    }
    document.title = `${projectName} 協作看板`;

    const agentStates = deriveAgentState(status, orchState);
    renderProgressBar(status.tasks);
    renderOverviewMetrics(status, orchState, approvalQueue);
    renderSystemStatus(status, orchState, approvalQueue, agentStates);
    renderWorkload(status);
    renderDeliveryLayers(status);
    renderAgentLanes(status, orchState);
    renderTaskBoard(status, orchState);
    renderReviewNotes(status);
    renderAuditStatus(status);
    renderDependencySchedule(status);
    renderStackList(
      "#handoff-list",
      (status.handoffs || []).filter((handoff) => handoff.status !== "done"),
      "目前沒有待交接項目。",
      (handoff) => `
        <div class="stack-head">
          <strong>${handoff.task_id}</strong>
          <span class="status-pill">${statusLabel(handoff.status)}</span>
        </div>
        <p>${handoff.from} -> ${handoff.to}</p>
        <p class="card-copy">${handoff.message}</p>
        <p class="card-copy">${formatTime(handoff.created_at)}</p>
      `
    );
    renderStackList(
      "#blocker-list",
      (status.blockers || []).filter((blocker) => blocker.status === "open"),
      "目前沒有阻塞項目。",
      (blocker) => `
        <div class="stack-head">
          <strong>${blocker.task_id}</strong>
          <span class="status-pill status-blocked">${statusLabel(blocker.status)}</span>
        </div>
        <p>負責人：${blocker.owner}</p>
        <p>等待對象：${blocker.waiting_for}</p>
        <p class="card-copy">${blocker.message}</p>
      `
    );
    renderSnapshot(snapshot);
    renderActivity(logs);
  } catch (error) {
    qs("#objective").textContent = `協作資料載入失敗：${error.message}`;
  } finally {
    const refreshButton = qs("#refresh-button");
    if (refreshButton) {
      refreshButton.disabled = false;
      refreshButton.textContent = "重新整理";
    }
    renderInFlight = false;
  }
}

qs("#refresh-button").addEventListener("click", () => {
  render({ syncFirst: true });
});

render();
