const DATA_FILES = {
  status: "./ai-status.json",
  activity: "./ai-activity-log.jsonl",
  currentWork: "./current-work.md",
};

const boardColumns = [
  { key: "todo", label: "待開始" },
  { key: "in_progress", label: "進行中" },
  { key: "review", label: "待審查" },
  { key: "blocked", label: "已阻塞" },
  { key: "done", label: "已完成" },
];

const qs = (selector) => document.querySelector(selector);
const statusLabelMap = {
  idle: "待命",
  working: "工作中",
  reviewing: "審查中",
  ready: "可開工",
  waiting: "等前置",
  todo: "待開始",
  in_progress: "進行中",
  review: "待審查",
  blocked: "已阻塞",
  done: "已完成",
  pending: "待處理",
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

function statusLabel(value) {
  return statusLabelMap[value] || value || "-";
}

function laneLabel(value) {
  return laneLabelMap[value] || value;
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

function parseJsonLines(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
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

function deriveAgentState(status) {
  const taskMap = new Map((status.tasks || []).map((task) => [task.id, task]));
  return (status.agents || []).map((agent) => {
    const owned = (status.tasks || []).filter((task) => task.owner === agent.name);
    const active = owned.filter((task) => ["in_progress", "review", "blocked"].includes(task.status));
    const queued = owned.filter((task) => task.status === "todo");
    const ready = queued.filter((task) =>
      (task.depends_on || []).every((depId) => (taskMap.get(depId)?.status || "done") === "done")
    );
    const waiting = queued.filter((task) => !ready.includes(task));

    let derivedStatus = "idle";
    let derivedTaskIds = [];
    let derivedNext = agent.next || "尚未指定";
    let derivedLastUpdate = agent.last_update || null;

    if (active.some((task) => task.status === "blocked")) {
      derivedStatus = "blocked";
      derivedTaskIds = active.map((task) => task.id);
    } else if (active.some((task) => task.status === "in_progress")) {
      derivedStatus = "working";
      derivedTaskIds = active.map((task) => task.id);
    } else if (active.some((task) => task.status === "review")) {
      derivedStatus = "reviewing";
      derivedTaskIds = active.map((task) => task.id);
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

    if (active.length) {
      const latest = [...active].sort((a, b) => (b.last_update || "").localeCompare(a.last_update || ""))[0];
      derivedNext = latest?.next || derivedNext;
      derivedLastUpdate = latest?.last_update || derivedLastUpdate;
    }

    return {
      ...agent,
      status: derivedStatus,
      current_task_ids: derivedTaskIds,
      next: derivedNext,
      last_update: derivedLastUpdate,
      ready_count: ready.length,
      waiting_count: waiting.length,
    };
  });
}

function renderAgentLanes(status) {
  const container = qs("#agent-lanes");
  container.innerHTML = "";
  const taskMap = new Map((status.tasks || []).map((task) => [task.id, task]));

  for (const agent of deriveAgentState(status)) {
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
      </div>
      ${focusTasks ? `<ul class="note-list compact">${focusTasks}</ul>` : ""}
      <p class="lane-copy">下一步：${agent.next || "尚未指定"}</p>
      <p class="lane-copy">最後更新：${formatTime(agent.last_update)}</p>
    `;
    container.appendChild(card);
  }
}

function renderTaskBoard(status) {
  const board = qs("#task-board");
  board.innerHTML = "";

  for (const column of boardColumns) {
    const wrapper = document.createElement("section");
    wrapper.className = "board-column";
    const tasks = (status.tasks || []).filter((task) => task.status === column.key);
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
      card.innerHTML = `
        <div class="task-head">
          <strong>${task.id}</strong>
          <span class="status-pill status-${task.status}">${statusLabel(task.status)}</span>
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
        ${
          (task.review_notes_zh || []).length
            ? `<div class="review-block">
                <p class="review-title">審查重點</p>
                <ul class="note-list">${(task.review_notes_zh || []).map((note) => `<li>${note}</li>`).join("")}</ul>
                ${task.review_file ? `<p class="card-copy">參考檔案：<code>${task.review_file}</code></p>` : ""}
              </div>`
            : ""
        }
        <p class="card-copy">下一步：${task.next || "-"}</p>
      `;
      stack.appendChild(card);
    }

    board.appendChild(wrapper);
  }
}

function buildDependencySchedule(tasks) {
  const remaining = (tasks || []).filter((task) => task.status !== "done");
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

  const readyNow = remaining.filter((task) => ((task.depends_on || []).filter((depId) => taskMap.get(depId)?.status !== "done")).length === 0 && task.status === "todo").length;
  const activeNow = remaining.filter((task) => activeTaskStatuses.has(task.status)).length;
  const waitingNow = remaining.filter((task) => ((task.depends_on || []).filter((depId) => taskMap.get(depId)?.status !== "done")).length > 0).length;
  const explicitBlocked = remaining.filter((task) => task.status === "blocked").length;

  return { waves, cyclic, taskMap, readyNow, activeNow, waitingNow, explicitBlocked };
}

function renderDependencySchedule(status) {
  const summary = qs("#dependency-summary");
  const container = qs("#dependency-schedule");
  summary.innerHTML = "";
  container.innerHTML = "";

  const schedule = buildDependencySchedule(status.tasks || []);
  const summaryItems = [
    { label: "現在可開工", value: schedule.readyNow, note: "所有前置都已完成，且尚未開始" },
    { label: "目前進行中", value: schedule.activeNow, note: "包含進行中與待審查" },
    { label: "等待前置", value: schedule.waitingNow, note: "依賴尚未完成，不能安全開工" },
    { label: "明確阻塞", value: schedule.explicitBlocked, note: "已有 blocker 狀態記錄" },
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

  if (!schedule.waves.length && !schedule.cyclic.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "目前沒有可排程的未完成任務。";
    container.appendChild(empty);
    return;
  }

  schedule.waves.forEach((wave, index) => {
    const section = document.createElement("section");
    section.className = "dependency-wave";
    const title = index === 0 ? "現在這一波" : index === 1 ? "下一波" : `第 ${index + 1} 波`;
    section.innerHTML = `
      <div class="dependency-wave-head">
        <h3>${title}</h3>
        <span class="section-copy">${wave.length} 個任務</span>
      </div>
      <div class="dependency-grid"></div>
    `;
    const grid = section.querySelector(".dependency-grid");

    for (const task of wave) {
      const unresolvedDeps = (task.depends_on || []).filter((depId) => schedule.taskMap.get(depId)?.status !== "done");
      const depends = (task.depends_on || []).length ? task.depends_on.join(", ") : "無";
      const unresolved = unresolvedDeps.length ? unresolvedDeps.join(", ") : "無";
      const card = document.createElement("article");
      const isReadyNow = index === 0 && task.status === "todo";
      card.className = `dependency-card ${isReadyNow ? "ready-now" : "blocked-now"}`;
      card.innerHTML = `
        <div class="task-head">
          <strong>${task.id}</strong>
          <span class="status-pill status-${task.status}">${statusLabel(task.status)}</span>
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
        <p class="dependency-copy">排程判斷：${index === 0 ? "這一波可進行" : "等待前一波完成後可開始"}</p>
        <p class="card-copy">下一步：${task.next || "-"}</p>
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
  const tasksWithNotes = (status.tasks || []).filter((task) => (task.review_notes_zh || []).length);
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
      <ul class="note-list">${(task.review_notes_zh || []).map((note) => `<li>${note}</li>`).join("")}</ul>
      ${task.review_file ? `<p class="card-copy">詳細檔案：<code>${task.review_file}</code></p>` : ""}
    `
  );
}

function renderAuditStatus(status) {
  const audits = (status.tasks || []).filter((task) => task.phase === "Audit" || task.id.startsWith("AUD-"));
  const summary = qs("#audit-summary");
  summary.innerHTML = "";

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
    summary.appendChild(card);
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
      <p class="card-copy">下一步：${task.next || "-"}</p>
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

function renderActivity(entries) {
  const container = qs("#activity-list");
  container.innerHTML = "";
  const recent = entries.slice(-20).reverse();

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
    card.innerHTML = `
      <div class="lane-head">
        <strong>${entry.agent}</strong>
        <span class="activity-meta">${formatTime(entry.ts)}</span>
      </div>
      <p class="activity-message">${entry.message}</p>
      <div class="lane-meta">
        <span class="chip">${statusLabel(entry.type)}</span>
        <span class="chip">${entry.task_id || "-"}</span>
      </div>
    `;
    container.appendChild(card);
  }
}

async function render() {
  try {
    const [status, activityText, currentWorkText] = await Promise.all([
      fetchJson(DATA_FILES.status),
      fetchText(DATA_FILES.activity),
      fetchText(DATA_FILES.currentWork),
    ]);

    const logs = parseJsonLines(activityText);
    const snapshot = parseCurrentWork(currentWorkText);

    qs("#objective").textContent = status.objective || snapshot.objective || "目前沒有可顯示的目標。";
    qs("#updated-at").textContent = formatTime(status.updated_at);

    renderWorkload(status);
    renderAgentLanes(status);
    renderTaskBoard(status);
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
  }
}

qs("#refresh-button").addEventListener("click", () => {
  render();
});

render();
