import {
  activityTypeLabel,
  boardColumns,
  logicalAgents,
  planningHighSignalTypes,
  workerStatusIcon,
} from "./dashboard-config.js?v=20260517-audit";
import {
  buildTruthMismatches,
  actorLabel,
  agentLabel,
  buildCodexSlotRoster,
  buildDependencySchedule,
  dependencyBatchState,
  deriveAgentState,
  DISPLAY_TIME_ZONE_LABEL,
  formatTime,
  laneLabel,
  normalizeDispatchQueue,
  normalizePlanningState,
  normalizeReviewNotes,
  normalizeWorkerRecords,
  qs,
  statusLabel,
  taskBadgeRow,
  taskDeliveryLayer,
  terminalTaskStatus,
  timeAgo,
  titleCase,
  truncate,
  workerLifecycleBadge,
} from "./dashboard-core.js?v=20260517-audit";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function compactWhitespace(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function shortText(value, maxLength = 180) {
  const compacted = compactWhitespace(value || "");
  if (!compacted) return "-";
  const clipped = compacted.length > maxLength ? `${compacted.slice(0, maxLength)}...` : compacted;
  return escapeHtml(clipped);
}

function summarizePausedReason(reason, provider) {
  const raw = compactWhitespace(reason || "");
  const lower = raw.toLowerCase();
  const providerName = agentLabel(provider);
  const looksLikeJsonDump = /^\s*[\{\[]/.test(raw) || raw.includes('"type":"assistant"') || raw.includes('"usage":{');
  if (!raw || looksLikeJsonDump) {
    return {
      summary: `${providerName} provider guardrail 已暫停此 lane 的新 dispatch。`,
      detail: "",
      kind: "pause",
    };
  }
  if (lower.includes("402") && lower.includes("no quota")) {
    return {
      summary: "402 You have no quota",
      detail: truncate(raw, 420),
      kind: "quota",
    };
  }
  if (lower.includes("hit your limit")) {
    return {
      summary: raw.match(/You've hit your limit[^|]*/i)?.[0] || "You've hit your limit",
      detail: truncate(raw, 420),
      kind: "quota",
    };
  }
  if (lower.includes("429") || lower.includes("rate limit") || lower.includes("capacity")) {
    return {
      summary: "Capacity / rate limit guardrail triggered",
      detail: truncate(raw, 420),
      kind: "quota",
    };
  }
  const firstMeaningfulSegment = raw
    .split(/\s+\|\s+|\n+/)
    .map((part) => compactWhitespace(part))
    .find((part) => part && !part.startsWith("./") && !part.startsWith("/"))
    || raw;
  return {
    summary: truncate(firstMeaningfulSegment, 180),
    detail: truncate(raw, 420),
    kind: "pause",
  };
}

export function renderWorkload(status, orchState = null) {
  const container = qs("#workload-grid");
  if (!container) return;
  container.innerHTML = "";

  const entries = Object.entries(status.workload_summary || {});
  if (!entries.length) {
    container.innerHTML = '<p class="empty">尚無 lane workload 摘要。</p>';
    return;
  }
  const pauseMap = new Map(
    pausedProviderEntries(orchState).map((entry) => [normalizedProviderKey(entry.provider), entry])
  );
  const agentBlockedMap = new Map(
    (status.agents || []).map((agent) => [normalizedProviderKey(agent.name), String(agent.status || "").toLowerCase()])
  );
  for (const [name, summary] of entries) {
    const target = status.workload?.[name] ?? 0;
    const fill = Math.min(summary.total * 15, 100);
    const providerKey = normalizedProviderKey(name);
    const pauseEntry = pauseMap.get(providerKey);
    const agentBlocked = agentBlockedMap.get(providerKey) === "blocked";
    const providerPaused = Boolean(pauseEntry);
    const impaired = providerPaused || agentBlocked;
    const pauseReason = pauseEntry
      ? summarizePausedReason(pauseEntry.summary || pauseEntry.reason, pauseEntry.provider)
      : null;
    const pauseLabel = providerPaused
      ? (pauseReason?.kind === "quota" ? "Quota 暫停" : "暫停派工")
      : agentBlocked
        ? "任務阻塞"
        : "";
    const pauseHint = pauseEntry?.blocked_until ? ` (until ${formatTime(pauseEntry.blocked_until)})` : "";
    const card = document.createElement("article");
    card.className = `workload-card${impaired ? " workload-card-paused" : ""}`;
    card.innerHTML = `
      <div class="lane-head">
        <strong>${name}</strong>
        <span class="status-pill ${impaired ? "status-blocked" : ""}">${impaired ? `${pauseLabel}${pauseHint}` : `目標 ${target}%`}</span>
      </div>
      <div class="workload-bar"><div class="workload-fill" style="width:${fill}%"></div></div>
      <div class="lane-meta">
        <span class="chip">總數 ${summary.total}</span>
        <span class="chip">活躍 ${providerPaused ? `${summary.active} (暫停)` : agentBlocked ? `${summary.active} (阻塞)` : summary.active}</span>
        <span class="chip">阻塞 ${summary.blocked}</span>
        <span class="chip">完成 ${summary.done}</span>
      </div>
    `;
    container.appendChild(card);
  }
}

export function renderAgentLanes(status, agentStates) {
  const container = qs("#agent-lanes");
  if (!container) return;
  container.innerHTML = "";
  const taskMap = new Map((status.tasks || []).map((task) => [task.id, task]));
  const lanes = agentStates || deriveAgentState(status, null);
  if (!lanes.length) {
    container.innerHTML = '<p class="empty">尚無 agent lane 狀態。</p>';
    return;
  }

  for (const agent of lanes) {
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
        ${Number.isFinite(agent.target_workload) ? `<span class="chip">目標 ${agent.target_workload}%</span>` : ""}
      </div>
      ${focusTasks ? `<ul class="note-list compact">${focusTasks}</ul>` : ""}
      <p class="lane-copy">下一步：${truncate(agent.next, 120)}</p>
      <p class="lane-copy">最後更新：${formatTime(agent.last_update)}</p>
    `;
    container.appendChild(card);
  }
}

export function renderArchiveRecords(dashboardBundle = null) {
  const container = qs("#archive-records");
  if (!container) return;
  container.innerHTML = "";

  const archive = dashboardBundle?.archive_summary || {};
  const counts = archive.counts || {};
  const records = Array.isArray(archive.recent_terminal_tasks) ? archive.recent_terminal_tasks : [];
  const total = Number.isFinite(counts.total) ? counts.total : 0;
  const completed = Number.isFinite(counts.completed) ? counts.completed : 0;
  const superseded = Number.isFinite(counts.superseded) ? counts.superseded : 0;

  if (!total && !records.length) {
    container.innerHTML = '<p class="empty">目前沒有封存紀錄。</p>';
    return;
  }

  const summary = document.createElement("article");
  summary.className = "archive-summary-card";
  summary.innerHTML = `
    <div class="stack-head">
      <strong>Archive index</strong>
      <span class="status-pill status-ready">${total} 筆</span>
    </div>
    <div class="lane-meta">
      <span class="chip">Completed ${completed}</span>
      <span class="chip">Superseded ${superseded}</span>
      <span class="chip">最近更新 ${formatTime(archive.updated_at)}</span>
    </div>
  `;
  container.appendChild(summary);

  if (!records.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Archive index 有統計，但目前的 dashboard bundle 沒帶最近封存任務摘要。";
    container.appendChild(empty);
    return;
  }

  const list = document.createElement("div");
  list.className = "archive-record-grid";
  for (const record of records) {
    const outcome = String(record.terminal_outcome || record.status || "done").toLowerCase();
    const card = document.createElement("article");
    card.className = "archive-record-card";
    card.innerHTML = `
      <div class="task-head">
        <strong>${escapeHtml(record.task_id || record.id || "-")}</strong>
        <span class="status-pill status-${outcome}">${statusLabel(outcome)}</span>
      </div>
      <p>${shortText(record.title, 140)}</p>
      <p class="task-summary">工作說明：${shortText(record.summary_zh, 180)}</p>
      <div class="lane-meta">
        <span class="chip">${escapeHtml(record.phase || "-")}</span>
        <span class="chip">負責人 ${escapeHtml(record.owner || "-")}</span>
        <span class="chip">審查者 ${escapeHtml(record.reviewer || "-")}</span>
      </div>
      <div class="lane-meta">
        <span class="chip">更新 ${formatTime(record.last_update)}</span>
        <span class="chip">封存 ${formatTime(record.archived_at)}</span>
      </div>
      <p class="card-copy">結案紀錄：${shortText(record.next, 220)}</p>
      ${record.snapshot_path ? `<p class="card-copy">Snapshot：<code>${escapeHtml(record.snapshot_path)}</code></p>` : ""}
    `;
    list.appendChild(card);
  }
  container.appendChild(list);
}

export function renderDeliveryLayers(status, planningState) {
  const container = qs("#delivery-layers");
  container.innerHTML = "";

  const tasks = (status.tasks || []).filter((task) => task.status !== "done");
  const planning = normalizePlanningState(planningState);
  const taskIds = new Set((status.tasks || []).map((task) => task.id));
  const layers = [
    {
      key: "planning",
      title: "Planning Outputs",
      copy: "已在 discussion planning 中提出，但尚未完全 materialize 成 execution task 的共識輸出。",
      tasks: (planning.proposed_execution_tasks || []).filter((task) => !taskIds.has(task.id)),
      planningLayer: true,
    },
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
      const displayStatus = layer.planningLayer ? "draft" : task.status;
      card.innerHTML = `
        <div class="task-head">
          <strong>${task.id}</strong>
          <span class="status-pill status-${displayStatus}">${statusLabel(displayStatus)}</span>
        </div>
        <p>${task.title}</p>
        <p class="task-summary">工作說明：${task.summary_zh || "尚未補上中文說明。"}</p>
        <div class="lane-meta">
          <span class="chip">${task.phase || "Planning Materialized"}</span>
          <span class="chip">負責人 ${task.owner}</span>
          <span class="chip">審查者 ${task.reviewer}</span>
        </div>
        ${layer.planningLayer ? "" : taskBadgeRow(task, "lane-meta")}
        <div class="lane-meta">
          <span class="chip">依賴 ${depends}</span>
        </div>
        <p class="card-copy">${
          layer.planningLayer
            ? task.summary_zh || "等待 human gate 或 materialization 動作。"
            : `下一步：${truncate(task.next, 120)}`
        }</p>
      `;
      grid.appendChild(card);
    }

    container.appendChild(section);
  }
}

function renderRuntimeLinkDrilldown(link) {
  const runId = link.worker_run_id || link.run_id || "-";
  const runtimeStatus = link.worker_status || link.status || "pending";
  const runtimeBucket = link.runtime_bucket || link.bucket || "pending";
  const actor = link.display_actor || actorLabel(link.actor || link.logical_agent_id, link.provider);
  const dispatchReason = link.dispatch_reason || link.reason || null;
  const mismatchBadges = (link.mismatch_flags || [])
    .map((flag) => `<span class="chip status-blocked">${flag}</span>`)
    .join("");
  const resolutionHints = Array.from(new Set((link.resolution_hints || []).filter(Boolean)));
  return `
    <article class="runtime-link-card runtime-${runtimeBucket}">
      <div class="runtime-link-head">
        <strong>${runId}</strong>
        <div class="chip-row">
          <span class="status-pill status-${runtimeStatus}">${statusLabel(runtimeStatus)}</span>
          ${link.queue_status ? `<span class="chip">queue ${link.queue_status}</span>` : ""}
          ${runtimeBucket ? `<span class="chip">bucket ${runtimeBucket}</span>` : ""}
        </div>
      </div>
      <div class="runtime-link-meta">
        <span class="chip">${actor}</span>
        ${link.mode ? `<span class="chip">${link.mode}</span>` : ""}
        ${dispatchReason ? `<span class="chip">${dispatchReason}</span>` : ""}
        ${link.queue_event_id ? `<span class="chip">evt ${link.queue_event_id}</span>` : ""}
      </div>
      <div class="runtime-link-meta">
        ${link.last_event_at ? `<span class="chip">worker ${timeAgo(link.last_event_at)}</span>` : ""}
        ${link.queue_last_event_at ? `<span class="chip">queue ${timeAgo(link.queue_last_event_at)}</span>` : ""}
        ${link.expected_actor ? `<span class="chip">expected ${agentLabel(link.expected_actor)}</span>` : ""}
      </div>
      ${mismatchBadges ? `<div class="runtime-link-meta">${mismatchBadges}</div>` : ""}
      ${resolutionHints.length ? `<ul class="note-list compact-list">${resolutionHints.map((hint) => `<li>${hint}</li>`).join("")}</ul>` : ""}
      ${link.last_error ? `<p class="card-copy error-text">last_error: ${truncate(link.last_error, 140)}</p>` : ""}
    </article>
  `;
}

export function renderTaskBoard(status, orchState, dashboardBundle = null) {
  const board = qs("#task-board");
  if (!board) return;
  board.innerHTML = "";

  const canonicalTasks = status.tasks || [];
  const archiveCounts = dashboardBundle?.archive_summary?.counts || {};
  const bridgeSummary = dashboardBundle?.bridge_summary || {};
  const pendingProposals = Array.isArray(bridgeSummary.pending_proposals) ? bridgeSummary.pending_proposals : [];
  const openTaskCount = canonicalTasks.filter((task) => !terminalTaskStatus(task.status)).length;
  const archivedTotal = Number.isFinite(archiveCounts.total) ? archiveCounts.total : 0;
  if (archivedTotal) {
    const archivedDone = Number.isFinite(archiveCounts.completed) ? archiveCounts.completed : 0;
    const archivedSuperseded = Number.isFinite(archiveCounts.superseded) ? archiveCounts.superseded : 0;
    const contextCard = document.createElement("article");
    contextCard.className = "stack-card board-context";
    contextCard.innerHTML = `
      <div class="stack-head">
        <strong>舊紀錄在 archive，Active task board 只顯示目前任務</strong>
        <span class="status-pill status-ready">archive intact</span>
      </div>
      <p class="card-copy">${
        openTaskCount
          ? `目前 active board 有 ${openTaskCount} 個 open task；以前完成或取代的工作已移到 <code>ai-task-archive</code>，不是被刪掉。`
          : "目前 active board 沒有 open task；以前完成或取代的工作已移到 <code>ai-task-archive</code>，不是被刪掉。"
      }</p>
      <div class="lane-meta">
        <span class="chip">Archived total ${archivedTotal}</span>
        <span class="chip">Completed ${archivedDone}</span>
        <span class="chip">Superseded ${archivedSuperseded}</span>
        <span class="chip">Active open ${openTaskCount}</span>
      </div>
      ${
        pendingProposals.length
          ? `
            <div class="review-block">
              <p class="review-title">Pending planning bridge</p>
              <ul class="note-list compact-list">
                ${pendingProposals.slice(0, 4).map((task) => `<li><strong>${escapeHtml(task.id || "-")}</strong>：${escapeHtml(task.title || task.summary_zh || "尚未填寫")}</li>`).join("")}
              </ul>
            </div>
          `
          : ""
      }
    `;
    board.appendChild(contextCard);
  }

  const truth = buildTruthMismatches(status, orchState);
  const bundleLinks = Array.isArray(dashboardBundle?.worker_task_links) ? dashboardBundle.worker_task_links : [];
  const mismatchItems = truth.mismatches.length ? truth.mismatches : (Array.isArray(dashboardBundle?.truth_mismatches) ? dashboardBundle.truth_mismatches : []);
  const mismatchByTask = new Map();
  for (const mismatch of mismatchItems) {
    if (!mismatch.task_id) continue;
    if (!mismatchByTask.has(mismatch.task_id)) mismatchByTask.set(mismatch.task_id, []);
    mismatchByTask.get(mismatch.task_id).push(mismatch);
  }
  const runtimeLinksByTask = new Map();
  for (const link of bundleLinks) {
    const taskId = String(link.task_id || "").trim();
    if (!taskId) continue;
    if (!runtimeLinksByTask.has(taskId)) runtimeLinksByTask.set(taskId, []);
    runtimeLinksByTask.get(taskId).push(link);
  }

  const displayTasks = (status.tasks || []).map((task) => {
    const bundleLinksForTask = runtimeLinksByTask.get(task.id) || [];
    const truthWorkersForTask = truth.liveWorkersByTask.get(task.id) || [];
    const liveWorkers = truthWorkersForTask.length ? truthWorkersForTask : bundleLinksForTask;
    const hasRunningWorker = liveWorkers.some((worker) => (worker.runtime_bucket || worker.bucket) === "running");
    const hasPendingWorker = liveWorkers.some((worker) => (worker.runtime_bucket || worker.bucket) === "pending");
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
      mismatch_count: (mismatchByTask.get(task.id) || []).length,
      mismatches: mismatchByTask.get(task.id) || [],
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
        ? `<span class="chip">live worker ${runtimeWorkers.map((worker) => `${agentLabel(worker.actor || worker.logical_agent_id)}:${worker.worker_status || worker.status}`).join(" / ")}</span>`
        : "";
      const mismatchBadge = task.mismatch_count
        ? `<span class="chip status-blocked">mismatch ${task.mismatch_count}</span>`
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
        ${taskBadgeRow(task)}
        <div class="task-foot">
          <span class="chip">依賴 ${depends}</span>
          <span class="chip">${formatTime(task.last_update)}</span>
        </div>
        <div class="task-meta">
          ${runtimeBadge}
          ${mismatchBadge}
          ${canonicalBadge}
          ${approvedFollowupBadge}
        </div>
        ${
          runtimeWorkers.length
            ? `<div class="review-block">
                <p class="review-title">Live Runtime Drilldown</p>
                <div class="runtime-link-grid">${runtimeWorkers.map((worker) => renderRuntimeLinkDrilldown(worker)).join("")}</div>
              </div>`
            : ""
        }
        ${
          task.mismatches.length
            ? `<div class="review-block">
                <p class="review-title">Truth mismatch</p>
                <ul class="note-list">${task.mismatches.map((item) => `<li>${item.title}：${item.summary}${item.resolution_hint ? ` 建議：${item.resolution_hint}` : ""}</li>`).join("")}</ul>
              </div>`
            : ""
        }
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

export function renderDependencySchedule(status) {
  const summary = qs("#dependency-summary");
  const container = qs("#dependency-schedule");
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
    const approvedGrid = approvedSection.querySelector(".dependency-grid");
    for (const task of schedule.approved) {
      const depends = (task.depends_on || []).length ? task.depends_on.join(", ") : "無";
      const card = document.createElement("article");
      card.className = "dependency-card batch-approved";
      card.innerHTML = `
        <div class="task-head">
          <strong>${task.id}</strong>
          <div class="chip-row">
            <span class="status-pill batch-pill batch-approved">待收尾</span>
            <span class="status-pill status-${task.status}">${statusLabel(task.status)}</span>
          </div>
        </div>
        <p>${task.title}</p>
        <p class="task-summary">工作說明：${task.summary_zh || "尚未補上中文說明。"}</p>
        <div class="dependency-meta">
          <span class="chip">負責人 ${task.owner}</span>
          <span class="chip">審查者 ${task.reviewer}</span>
          <span class="chip">前置 ${depends}</span>
        </div>
        ${taskBadgeRow(task, "dependency-meta")}
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
        ${taskBadgeRow(task, "dependency-meta")}
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

export function renderReviewNotes(status) {
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
      ${taskBadgeRow(task)}
      <p class="card-copy">Reviewer：${task.reviewer}</p>
      <ul class="note-list">${normalizeReviewNotes(task.review_notes_zh).map((note) => `<li>${note}</li>`).join("")}</ul>
      ${task.review_file ? `<p class="card-copy">詳細檔案：<code>${task.review_file}</code></p>` : ""}
    `
  );
}

export function renderAuditStatus(status) {
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
      ${taskBadgeRow(task, "lane-meta")}
      ${
        (task.artifacts || []).length
          ? `<p class="card-copy">輸出檔案：${task.artifacts.map((path) => `<code>${path}</code>`).join("、")}</p>`
          : ""
      }
      <p class="card-copy">下一步：${truncate(task.next, 120)}</p>
    `
  );
}

export function renderStackList(selector, items, emptyText, formatter) {
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

function coordinationStageLabel(stage) {
  const normalized = compactWhitespace(stage || "").toLowerCase();
  if (!normalized) return "未知";
  if (normalized === "loop_complete") return "loop-complete";
  if (normalized === "frontend_feedback_reviewed_followup") return "feedback reviewed / follow-up";
  if (normalized === "waiting_for_lovable") return "等待 Lovable / 前端";
  if (normalized === "ui_done_received") return "已收 ui-done";
  if (normalized === "frontend_feedback_received") return "已收 feedback";
  if (normalized === "bff_gap_open") return "BFF gap 開啟";
  if (normalized === "contract_ready") return "contract-ready";
  return titleCase(normalized.replaceAll("_", " "));
}

export function renderLovableCoordinationSummary(dashboardBundle = null) {
  const container = qs("#lovable-coordination");
  if (!container) return;
  container.innerHTML = "";

  const summary = dashboardBundle?.coordination_summary || {};
  const counts = summary.counts || {};
  const features = Array.isArray(summary.features) ? summary.features : [];
  const loopComplete = features.filter((feature) => feature.stage === "loop_complete").length;
  const followUp = features.filter((feature) => feature.stage && feature.stage !== "loop_complete").length;
  const runtimeVerified = Number.isFinite(counts.runtime_verified) ? counts.runtime_verified : 0;
  const runtimePending = Math.max(features.length - runtimeVerified, 0);

  const summaryCard = document.createElement("article");
  summaryCard.className = "stack-card";
  summaryCard.innerHTML = `
    <div class="stack-head">
      <strong>Coordination Records</strong>
      <span class="status-pill">${formatTime(summary.last_scan_at)}</span>
    </div>
    <p class="card-copy">這裡是前端交付協調紀錄，不等於 remaining workbench backlog；剩餘模組真相以 <code>WORKBENCH_DELIVERY_BACKLOG.md</code> 和 <code>ai-status.json</code> 為準。</p>
    <div class="lane-meta">
      <span class="chip">追蹤 feature ${counts.tracked_features || 0}</span>
      <span class="chip">loop-complete ${loopComplete}</span>
      <span class="chip">follow-up ${followUp}</span>
      <span class="chip">Lovable-ready ${counts.lovable_ready || 0}</span>
      <span class="chip">等待執行 ${counts.waiting_for_lovable || 0}</span>
      <span class="chip">ui-done ${counts.ui_done_received || 0}</span>
      <span class="chip">feedback ${counts.frontend_feedback_received || 0}</span>
      <span class="chip">open BFF gap ${counts.open_bff_gaps || 0}</span>
      <span class="chip">runtime verified ${runtimeVerified}/${features.length}</span>
      <span class="chip ${runtimePending ? "status-review" : "status-ready"}">runtime pending ${runtimePending}</span>
    </div>
  `;
  container.appendChild(summaryCard);

  if (!features.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "目前沒有 Lovable coordination feature。";
    container.appendChild(empty);
    return;
  }

  for (const feature of features) {
    const card = document.createElement("article");
    card.className = "stack-card";
    const paths = Object.entries(feature.paths || {})
      .filter(([, value]) => value)
      .map(([label, value]) => `<code>${escapeHtml(label)}: ${escapeHtml(value)}</code>`)
      .join("、");
    card.innerHTML = `
      <div class="stack-head">
        <strong>${escapeHtml(feature.feature_id || "-")}</strong>
        <span class="status-pill">${escapeHtml(coordinationStageLabel(feature.stage))}</span>
      </div>
      <p>${escapeHtml(feature.summary || feature.screen || "尚無摘要。")}</p>
      <div class="lane-meta">
        <span class="chip">畫面 ${escapeHtml(feature.screen || "-")}</span>
        <span class="chip">來源 ${escapeHtml(feature.source_repo || feature.source_repo_id || "-")}</span>
        <span class="chip">Agent ${escapeHtml(feature.target_agent || "-")}</span>
      </div>
      <div class="lane-meta">
        <span class="chip">Lovable-ready ${feature.lovable_ready ? "yes" : "no"}</span>
        <span class="chip">Mirrored ${feature.mirrored_to_target_repo ? "yes" : "no"}</span>
        <span class="chip">ui-done ${feature.has_ui_done ? "yes" : "no"}</span>
        <span class="chip">feedback ${feature.has_frontend_feedback ? "yes" : "no"}</span>
      </div>
      ${paths ? `<p class="card-copy">Artifacts：${paths}</p>` : ""}
      <p class="card-copy">下一步：${escapeHtml(truncate(feature.next_action || "-", 180))}</p>
    `;
    container.appendChild(card);
  }
}

export function renderSnapshot(snapshot) {
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

export function renderOverviewMetrics(status, orchState, approvalQueue, dashboardBundle = null) {
  const container = qs("#overview-metrics");
  if (!container) return;
  const tasks = status.tasks || [];
  const archiveCounts = dashboardBundle?.archive_summary?.counts || {};
  const bridgeSummary = dashboardBundle?.bridge_summary || {};
  const coordinationCounts = dashboardBundle?.coordination_summary?.counts || {};

  const todo          = tasks.filter((t) => String(t.status || "").toLowerCase() === "todo").length;
  const inProgress    = tasks.filter((t) => String(t.status || "").toLowerCase() === "in_progress").length;
  const review        = tasks.filter((t) => String(t.status || "").toLowerCase() === "review").length;
  const reviewApproved= tasks.filter((t) => String(t.status || "").toLowerCase() === "review_approved").length;
  const done          = tasks.filter((t) => String(t.status || "").toLowerCase() === "done").length;
  const blocked       = tasks.filter((t) => String(t.status || "").toLowerCase() === "blocked").length;
  const activeOpen = todo + inProgress + review + reviewApproved + blocked;

  const archivedTotal = Number.isFinite(archiveCounts.total) ? archiveCounts.total : 0;
  const archivedDone = Number.isFinite(archiveCounts.completed) ? archiveCounts.completed : 0;
  const archivedSuperseded = Number.isFinite(archiveCounts.superseded) ? archiveCounts.superseded : 0;
  if (!activeOpen && archivedTotal) {
    const pendingBridge = Number.isFinite(bridgeSummary.pending_materialization_count) ? bridgeSummary.pending_materialization_count : 0;
    const trackedFeatures = Number.isFinite(coordinationCounts.tracked_features) ? coordinationCounts.tracked_features : 0;
    const runtimeVerified = Number.isFinite(coordinationCounts.runtime_verified) ? coordinationCounts.runtime_verified : 0;
    const items = [
      { label: "Active Open", value: 0, tone: "card-done" },
      { label: "Archived Done", value: archivedDone, tone: "card-done" },
      { label: "Superseded", value: archivedSuperseded, tone: archivedSuperseded ? "card-review" : "" },
      { label: "Frontend Loops", value: trackedFeatures, tone: trackedFeatures ? "card-active" : "" },
      { label: "Runtime Proof", value: trackedFeatures ? `${runtimeVerified}/${trackedFeatures}` : "0/0", tone: runtimeVerified < trackedFeatures ? "card-review" : "card-done" },
      { label: "Pending Bridge", value: pendingBridge, tone: pendingBridge ? "card-review" : "card-done" },
    ];

    container.innerHTML = items.map((item) => `
      <article class="metric-card ${item.tone}">
        <div class="metric-label">${item.label}</div>
        <div class="metric-value">${item.value}</div>
      </article>
    `).join("");
    return;
  }

  const completedInSprint = Number.isFinite(archiveCounts.completed_in_sprint) ? archiveCounts.completed_in_sprint : 0;
  const items = [
    { label: "待開始",    value: todo,           tone: "" },
    { label: "進行中",    value: inProgress,     tone: inProgress  ? "card-active"   : "" },
    { label: "待審查",    value: review,         tone: review      ? "card-review"   : "" },
    { label: "待收尾",    value: reviewApproved, tone: reviewApproved ? "card-review" : "" },
    { label: "本輪完成",  value: completedInSprint + done, tone: "card-done" },
    ...(archivedTotal
      ? [
          { label: "歷史完成", value: archivedDone, tone: "card-done" },
          { label: "封存總數", value: archivedTotal, tone: "card-done" },
          { label: "已取代", value: archivedSuperseded, tone: archivedSuperseded ? "card-review" : "card-done" },
        ]
      : []),
    { label: "阻塞",      value: blocked,        tone: blocked     ? "card-blocked"  : "" },
  ];

  container.innerHTML = items.map((item) => `
    <article class="metric-card ${item.tone}">
      <div class="metric-label">${item.label}</div>
      <div class="metric-value">${item.value}</div>
    </article>
  `).join("");
}

function executionStatusCounts(status, dashboardBundle = null) {
  const tasks = status.tasks || [];
  const summary = dashboardBundle?.execution_summary || {};
  const archive = dashboardBundle?.archive_summary?.counts || {};
  return {
    total: tasks.length,
    done: tasks.filter((task) => String(task.status || "").toLowerCase() === "done").length,
    reviewApproved: tasks.filter((task) => String(task.status || "").toLowerCase() === "review_approved").length,
    inProgress: tasks.filter((task) => String(task.status || "").toLowerCase() === "in_progress").length,
    review: tasks.filter((task) => String(task.status || "").toLowerCase() === "review").length,
    blocked: tasks.filter((task) => String(task.status || "").toLowerCase() === "blocked").length,
    todo: tasks.filter((task) => String(task.status || "").toLowerCase() === "todo").length,
    readyNow: Number.isFinite(summary.ready_now) ? summary.ready_now : tasks.filter((task) => String(task.status || "").toLowerCase() === "todo").length,
    dependencyReady: Number.isFinite(summary.dependency_ready)
      ? summary.dependency_ready
      : tasks.filter((task) => String(task.status || "").toLowerCase() === "todo").length,
    liveAttached: Number.isFinite(summary.live_attached) ? summary.live_attached : 0,
    mismatchCount: Number.isFinite(summary.mismatch_count) ? summary.mismatch_count : 0,
    archivedDone: Number.isFinite(archive.completed) ? archive.completed : Number.isFinite(summary.done) ? summary.done : 0,
    archivedSuperseded: Number.isFinite(archive.superseded) ? archive.superseded : Number.isFinite(summary.superseded) ? summary.superseded : 0,
  };
}

function pausedProviderEntries(orchState) {
  const pauses = orchState?.provider_guardrails?.dispatch_pauses;
  if (!pauses || typeof pauses !== "object") return [];
  return Object.entries(pauses)
    .map(([provider, info]) => ({ provider, ...(info || {}) }))
    .sort((a, b) => String(b.blocked_until || b.paused_at || "").localeCompare(String(a.blocked_until || a.paused_at || "")));
}

function normalizedProviderKey(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "grok") return "copilot";
  return normalized;
}

function pauseSeverity(entry) {
  if (!entry) return null;
  const raw = [
    entry.pause_kind,
    entry.failure_kind,
    entry.summary,
    entry.reason,
    entry.detail,
  ].filter(Boolean).join(" ").toLowerCase();
  if (
    raw.includes("quota")
    || raw.includes("rate limit")
    || raw.includes("capacity")
    || raw.includes("402")
    || raw.includes("429")
  ) {
    return "quota";
  }
  return "error";
}

function agentRuntimeAvailability(agentId, details) {
  const { pause, running, failed, readyCount } = details;
  const pauseKind = pauseSeverity(pause);
  if (pauseKind === "quota") {
    return {
      icon: "⛔",
      className: "agent-card-quota",
      pillClass: "status-blocked",
      label: "Quota 暫停",
      canAccept: false,
    };
  }
  if (pauseKind === "error" || failed > 0) {
    return {
      icon: "🔴",
      className: "agent-card-error",
      pillClass: "status-blocked",
      label: pause ? "錯誤暫停" : "Worker 錯誤",
      canAccept: false,
    };
  }
  if (running > 0) {
    return {
      icon: "🟡",
      className: "agent-card-busy",
      pillClass: "status-working",
      label: "執行中",
      canAccept: false,
    };
  }
  if (readyCount > 0) {
    return {
      icon: "🟢",
      className: "agent-card-available",
      pillClass: "status-ready",
      label: "可接工",
      canAccept: true,
    };
  }
  return {
    icon: "⚪",
    className: "agent-card-idle",
    pillClass: "status-idle",
    label: "無可派工",
    canAccept: false,
  };
}

function planningGateProgress(planning) {
  const gate = planning.switch_gate || {};
  const keys = [
    "all_readouts_submitted",
    "cross_review_round_present",
    "divergence_resolved_or_escalated",
    "consensus_packet_drafted",
    "human_approved",
  ];
  const done = keys.filter((key) => gate[key]).length;
  return {
    done,
    total: keys.length,
    percent: keys.length ? Math.round((done / keys.length) * 100) : 0,
  };
}

function planningNextAction(planning, status) {
  const gate = planning.switch_gate || {};
  const missing = planningOutstandingRequirements(planning);
  const taskIds = new Set((status.tasks || []).map((task) => task.id));
  const proposals = planning.proposed_execution_tasks || [];
  const materializedCount = proposals.filter((task) => taskIds.has(task.id)).length;
  let owner = agentLabel(planning.baton_owner);
  let action = "等待下一個 planning 更新。";

  if (planning.status === "accepted") {
    owner = materializedCount < proposals.length ? "Execution owner" : "Execution lanes";
    action = materializedCount < proposals.length
      ? `把剩下 ${proposals.length - materializedCount}/${proposals.length} 個候選切片正式下放到 execution task board。`
      : "planning 已完成，接下來應回到 execution board 追蹤實作與 review。";
  } else if (planning.status === "human_required") {
    owner = "Human";
    action = "先處理需要人工裁決的分歧，再繼續讓 facilitator 收斂共識。";
  } else if (gate.ready_for_human) {
    owner = "Human";
    action = "檢查 consensus packet，決定是否批准進入 execution。";
  } else if (!gate.all_readouts_submitted) {
    owner = agentLabel(planning.baton_owner);
    action = "補齊剩餘 readouts 或明確標記 waived / tracking，避免一直停留在半收斂狀態。";
  } else if (!gate.cross_review_round_present) {
    owner = agentLabel(planning.next_reviewer || planning.baton_owner);
    action = "至少完成一輪 cross-review，讓共識不是只有單 lane 的看法。";
  }

  return {
    owner,
    action,
    missing,
    materializedCount,
    totalProposals: proposals.length,
  };
}

function executionNextAction(status, orchState, dashboardBundle = null) {
  const counts = executionStatusCounts(status, dashboardBundle);
  const pausedProviders = pausedProviderEntries(orchState);
  const reviewTasks = (status.tasks || []).filter((task) => String(task.status || "").toLowerCase() === "review");
  const blockedTasks = (status.tasks || []).filter((task) => String(task.status || "").toLowerCase() === "blocked");
  const readyTasks = (status.tasks || []).filter((task) => String(task.status || "").toLowerCase() === "todo");

  if (pausedProviders.length) {
    return {
      owner: "Supervisor",
      action: `先處理 ${pausedProviders.length} 個 paused provider，確認改派是否成功，避免 queue 持續把工作送回沒 quota 的 lane。`,
    };
  }

  if (reviewTasks.length) {
    return {
      owner: "Review lanes",
      action: `目前有 ${reviewTasks.length} 個任務在 review，先清掉 review queue，避免 owner 一直卡在待審查。`,
    };
  }

  if (blockedTasks.length) {
    return {
      owner: "Task owners",
      action: `先處理 ${blockedTasks.length} 個 blocker，否則 ready queue 再漂亮也推不動下游。`,
    };
  }

  if (counts.inProgress) {
    return {
      owner: "Execution owners",
      action: `目前有 ${counts.inProgress} 個任務在進行中，優先把 active slices 推到 review，而不是再盲目開新工作。`,
    };
  }

  if (readyTasks.length) {
    return {
      owner: "Available lanes",
      action: `目前有 ${readyTasks.length} 個 ready task，可由 supervisor 依 lane 容量繼續派工。`,
    };
  }

  return {
    owner: "Supervisor",
    action: "目前 execution 沒有明顯積壓，持續監看 queue、worker heartbeat 與 blocker 即可。",
  };
}

function currentPriorityNarrative(planning, status, orchState, dashboardBundle = null) {
  const focusMode = dashboardFocusMode(planning);
  const proposalStats = planningProposalStats(planning, status, dashboardBundle);
  const pausedProviders = pausedProviderEntries(orchState);
  const executionCounts = executionStatusCounts(status, dashboardBundle);
  const planningAction = planningNextAction(planning, status);
  const executionAction = executionNextAction(status, orchState, dashboardBundle);

  if (focusMode === "planning") {
    return {
      focusMode,
      badge: "討論收斂",
      tone: "status-active",
      title: "現在先把共識收斂清楚，再談大規模開工",
      body: "目前系統焦點在 discussion planning。execution 雖然可能仍有既有任務在跑，但新的切片、ownership 與 source-of-truth 決策應先在這一輪收斂完成。",
      owner: planningAction.owner,
      nextAction: planningAction.action,
      chips: [
        `Consensus ${statusLabel(planning.consensus_status)}`,
        `Readouts ${planning.counts.readouts_resolved || 0}/${Object.keys(planning.readouts || {}).length}`,
        `Open issues ${planning.counts.open_items || 0}`,
        `Rounds ${planning.counts.rounds_total || 0}`,
      ],
    };
  }

  if (planning.status === "accepted" && proposalStats.total && proposalStats.materialized < proposalStats.total) {
    return {
      focusMode: "bridge",
      badge: "共識下放",
      tone: "status-review",
      title: "共識已經過關，現在要把它穩定地下放成工作",
      body: "planning 本身已完成，但還有一部分候選切片尚未 materialize 成正式 execution tasks。這時候最重要的不是再討論，而是把橋接補齊。",
      owner: planningAction.owner,
      nextAction: planningAction.action,
      chips: [
        `Proposals ${proposalStats.total}`,
        `Materialized ${proposalStats.materialized}/${proposalStats.total}`,
        `Completed ${proposalStats.done}/${proposalStats.total}`,
        `Pending bridge ${Math.max(proposalStats.total - proposalStats.materialized, 0)}`,
      ],
    };
  }

  return {
    focusMode: "execution",
    badge: "執行追進度",
    tone: pausedProviders.length ? "status-blocked" : "status-ready",
    title: pausedProviders.length
      ? "Execution 正在跑，但容量與派工需要特別盯"
      : "共識已落地，現在應該回到 execution 追進度",
    body: pausedProviders.length
      ? `目前 execution 是主戰場，但有 ${pausedProviders.length} 個 provider 被暫停派工。重點是確認改派、review 與 blocker 是否仍能讓工作流暢推進。`
      : "planning 已經完成或不是目前焦點，現在最重要的是把 active slices 推進到 review / done，並保持 runtime 與 task board 對齊。",
    owner: executionAction.owner,
    nextAction: executionAction.action,
    chips: [
      `Open tasks ${executionCounts.total - executionCounts.done}`,
      `In progress ${executionCounts.inProgress}`,
      `In review ${executionCounts.review}`,
      `Blocked ${executionCounts.blocked}`,
    ],
  };
}

export function renderControlPlaneStrip(status, planningState, orchState = null, dashboardBundle = null) {
  const container = qs("#control-plane-strip");
  if (!container) return;
  const planning = normalizePlanningState(planningState);
  const focus = currentPriorityNarrative(planning, status, orchState, dashboardBundle);
  const proposalStats = planningProposalStats(planning, status, dashboardBundle);
  const executionCounts = executionStatusCounts(status, dashboardBundle);
  const pausedProviders = pausedProviderEntries(orchState);
  const blockers = (status.blockers || []).filter((blocker) => blocker.status === "open").length;
  const pendingBridge = Math.max(proposalStats.pendingMaterializationCount, 0);

  const items = [
    {
      label: "目前焦點",
      value: focus.badge,
      note: focus.title,
      statusClass: focus.tone,
    },
    {
      label: "Planning 狀態",
      value: `${statusLabel(planning.status)} / ${statusLabel(planning.consensus_status)}`,
      note: planning.session_id || "尚未建立 planning session",
      statusClass: `status-${planning.consensus_status || planning.status}`,
    },
    {
      label: "下放進度",
      value: proposalStats.total ? `${proposalStats.materialized}/${proposalStats.total}` : "0/0",
      note: proposalStats.total
        ? (pendingBridge ? `還有 ${pendingBridge} 個候選切片尚未正式下放` : "所有候選切片都已成功進入 execution")
        : "這輪 planning 目前沒有提出 execution slice",
      statusClass: pendingBridge ? "status-review" : "status-ready",
    },
    {
      label: "執行進度",
      value: `${executionCounts.done}/${executionCounts.total} done`,
      note: `進行中 ${executionCounts.inProgress} · 待審查 ${executionCounts.review} · 阻塞 ${executionCounts.blocked}`,
    },
    {
      label: "即時提醒",
      value: pausedProviders.length
        ? `${pausedProviders.length} paused`
        : blockers || executionCounts.mismatchCount
          ? `${blockers + executionCounts.mismatchCount} signals`
          : "All clear",
      note: pausedProviders.length
        ? `${pausedProviders.map((entry) => agentLabel(entry.provider)).join("、")} 暫停派工中`
        : blockers || executionCounts.mismatchCount
          ? `Blockers ${blockers} · Mismatches ${executionCounts.mismatchCount}`
          : "目前沒有高優先告警訊號",
      statusClass: pausedProviders.length || blockers || executionCounts.mismatchCount ? "status-blocked" : "status-ready",
    },
  ];

  container.innerHTML = items
    .map(
      (item) => `
      <article class="mode-card">
        <div class="metric-label">${item.label}</div>
        <div class="metric-value ${item.statusClass || ""}">${item.value}</div>
        <div class="metric-note">${item.note}</div>
      </article>
    `
    )
    .join("");
}

function planningDecisionSummary(planning, status) {
  const taskIds = new Set((status.tasks || []).map((task) => task.id));
  const proposals = planning.proposed_execution_tasks || [];
  const materializedCount = proposals.filter((task) => taskIds.has(task.id)).length;
  const pendingMaterialization = Math.max(proposals.length - materializedCount, 0);

  if (planning.status === "accepted") {
    if (pendingMaterialization > 0) {
      return {
        title: "Planning 已完成，共識已被接受",
        body: `這輪 planning 已完成收斂並通過 human gate。現在的重點不是再討論，而是把剩下 ${pendingMaterialization}/${proposals.length} 個候選切片正式下放到 execution board。`,
        tone: "status-ready",
      };
    }
    return {
      title: "Planning 已完成，execution 已接手",
      body: "這輪 planning 已完成收斂，主要輸出都已進入 execution board。現在應該回到 execution 追進度，而不是再調整 planning 工作區。",
      tone: "status-ready",
    };
  }

  if (planning.status === "human_required") {
    return {
      title: "Planning 卡在人工決策",
      body: "LLM 間已整理出主要分歧，但仍有需要人類裁決的問題。現在最重要的是把分歧解掉，而不是擴大 execution 範圍。",
      tone: "status-human_required",
    };
  }

  if (planning.switch_gate?.ready_for_human) {
    return {
      title: "Planning 已接近完成，等待人工確認",
      body: "主要共識已成型，現在卡在 human gate。此時應優先確認 consensus packet，而不是再擴散討論。",
      tone: "status-review",
    };
  }

  if (planning.status === "active") {
    return {
      title: "Planning 仍在收斂中",
      body: "這輪 planning 還在整合 readout、review 與分歧。現在應先把判斷收斂清楚，再決定哪些內容可以安全地下放到 execution。",
      tone: "status-active",
    };
  }

  return {
    title: "目前沒有 active planning session",
    body: "沒有正在進行中的 planning 收斂流程。若要重新跑規劃，應先建立新的 planning session。",
    tone: "status-done",
  };
}

function planningBridgeSummary(planning, status, dashboardBundle = null) {
  const taskMap = new Map((status.tasks || []).map((task) => [task.id, task]));
  const proposals = planning.proposed_execution_tasks || [];
  const fallback = {
    sourcePlane: "planning",
    sessionId: planning.session_id || null,
    phase: planning.phase || null,
    profile: planning.profile || null,
    planningDir: planning.planning_dir || null,
    sessionFile: planning.session_file || null,
    consensusPacket: planning.artifacts?.consensus_packet?.path || null,
    executionMaterialization: planning.artifacts?.execution_materialization?.path || null,
    total: proposals.length,
    materialized: proposals.filter((task) => taskMap.has(task.id)).length,
    done: proposals.filter((task) => taskMap.get(task.id)?.status === "done").length,
    reviewApproved: proposals.filter((task) => taskMap.get(task.id)?.status === "review_approved").length,
    inProgress: proposals.filter((task) => taskMap.get(task.id)?.status === "in_progress").length,
    review: proposals.filter((task) => taskMap.get(task.id)?.status === "review").length,
    todo: proposals.filter((task) => taskMap.get(task.id)?.status === "todo").length,
    blocked: proposals.filter((task) => taskMap.get(task.id)?.status === "blocked").length,
    pendingMaterializationCount: proposals.filter((task) => !taskMap.has(task.id)).length,
    pendingProposals: proposals.filter((task) => !taskMap.has(task.id)),
    activeMaterializedTasks: proposals
      .filter((task) => taskMap.has(task.id))
      .map((task) => taskMap.get(task.id))
      .filter((task) => task && String(task.status || "").toLowerCase() !== "done"),
    planningBackedTotal: 0,
    planningBackedActive: 0,
    currentSessionMaterialized: proposals.filter((task) => taskMap.has(task.id)).length,
    missingSourceRefCount: 0,
  };
  const bundleBridge = dashboardBundle?.bridge_summary || null;
  if (!bundleBridge || typeof bundleBridge !== "object") {
    return fallback;
  }
  return {
    ...fallback,
    sourcePlane: bundleBridge.source_plane ?? fallback.sourcePlane,
    sessionId: bundleBridge.session_id ?? fallback.sessionId,
    phase: bundleBridge.phase ?? fallback.phase,
    profile: bundleBridge.profile ?? fallback.profile,
    planningDir: bundleBridge.planning_dir ?? fallback.planningDir,
    sessionFile: bundleBridge.session_file ?? fallback.sessionFile,
    consensusPacket: bundleBridge.consensus_packet ?? fallback.consensusPacket,
    executionMaterialization: bundleBridge.execution_materialization ?? fallback.executionMaterialization,
    total: Number.isFinite(bundleBridge.proposed_total) ? bundleBridge.proposed_total : fallback.total,
    materialized: Number.isFinite(bundleBridge.materialized_count) ? bundleBridge.materialized_count : fallback.materialized,
    done: Number.isFinite(bundleBridge.done) ? bundleBridge.done : fallback.done,
    reviewApproved: Number.isFinite(bundleBridge.review_approved) ? bundleBridge.review_approved : fallback.reviewApproved,
    inProgress: Number.isFinite(bundleBridge.in_progress) ? bundleBridge.in_progress : fallback.inProgress,
    review: Number.isFinite(bundleBridge.review) ? bundleBridge.review : fallback.review,
    todo: Number.isFinite(bundleBridge.todo) ? bundleBridge.todo : fallback.todo,
    blocked: Number.isFinite(bundleBridge.blocked) ? bundleBridge.blocked : fallback.blocked,
    pendingMaterializationCount: Number.isFinite(bundleBridge.pending_materialization_count)
      ? bundleBridge.pending_materialization_count
      : fallback.pendingMaterializationCount,
    pendingProposals: Array.isArray(bundleBridge.pending_proposals) ? bundleBridge.pending_proposals : fallback.pendingProposals,
    activeMaterializedTasks: Array.isArray(bundleBridge.active_materialized_tasks)
      ? bundleBridge.active_materialized_tasks
      : fallback.activeMaterializedTasks,
    planningBackedTotal: Number.isFinite(bundleBridge.planning_backed_total)
      ? bundleBridge.planning_backed_total
      : fallback.planningBackedTotal,
    planningBackedActive: Number.isFinite(bundleBridge.planning_backed_active)
      ? bundleBridge.planning_backed_active
      : fallback.planningBackedActive,
    currentSessionMaterialized: Number.isFinite(bundleBridge.current_session_materialized)
      ? bundleBridge.current_session_materialized
      : fallback.currentSessionMaterialized,
    missingSourceRefCount: Number.isFinite(bundleBridge.missing_source_ref_count)
      ? bundleBridge.missing_source_ref_count
      : fallback.missingSourceRefCount,
  };
}

function planningProposalStats(planning, status, dashboardBundle = null) {
  return planningBridgeSummary(planning, status, dashboardBundle);
}

function planningHighlights(planning) {
  const items = [];
  if (planning.summary) {
    items.push({ label: "這輪在解什麼", note: planning.summary });
  }
  if (planning.current_round) {
    items.push({
      label: "評審進度",
      note: `目前到 round ${planning.current_round}，review 順序為 ${(planning.review_sequence || []).map(agentLabel).join(" -> ") || "-"}`,
    });
  }
  const latestRound = [...(planning.cross_review_rounds || [])].pop();
  if (latestRound) {
    items.push({
      label: "最近一次收斂",
      note: latestRound.summary || `Round ${latestRound.round} 已更新`,
    });
  }
  const latestEvent = [...(planning.recent_events || [])]
    .filter((entry) => !["execution_slice_proposed", "readout_updated"].includes(String(entry.type || "")))
    .pop();
  if (latestEvent) {
    items.push({
      label: "最新訊號",
      note: latestEvent.message || latestEvent.summary || String(latestEvent.type || "update"),
    });
  }
  if (!items.length) {
    items.push({
      label: "目前沒有額外摘要",
      note: "尚未累積足夠的 planning event，可先看 next step 與候選工作。",
    });
  }
  return items.slice(0, 3);
}

function planningOutstandingRequirements(planning) {
  const gate = planning.switch_gate || {};
  const missing = [];
  if (!gate.all_readouts_submitted) missing.push("readouts 尚未全部收斂");
  if (!gate.cross_review_round_present) missing.push("還沒有完成至少一輪 cross-review");
  if (!gate.divergence_resolved_or_escalated) missing.push("仍有分歧未解決或未升級");
  if (!gate.consensus_packet_drafted) missing.push("consensus packet 尚未成稿");
  if (!gate.human_approved) missing.push("human gate 尚未批准");
  return missing;
}

export function renderPlanningOverview(planningState, status, dashboardBundle = null) {
  const container = qs("#planning-overview");
  if (!container) return;
  const planning = normalizePlanningState(planningState);
  const readoutTotal = Object.keys(planning.readouts || {}).length;
  const proposals = planning.proposed_execution_tasks || [];
  const proposalStats = planningProposalStats(planning, status, dashboardBundle);
  const decision = planningDecisionSummary(planning, status);
  const highlights = planningHighlights(planning);
  const activeSession = dashboardBundle?.planning_summary?.active_session || {};
  const sessionUpdatedAt = planning.updated_at || activeSession.updated_at || null;
  const sessionScope = planning.objective || planning.summary || "這輪 planning 尚未寫入 scope 摘要。";
  const sessionRef = planning.planning_dir || activeSession.planning_dir || planning.session_file || activeSession.session_file || "-";

  container.innerHTML = `
    <div class="planning-hero">
      <article class="planning-hero-main">
        <div class="lane-head">
          <div>
            <strong>${decision.title}</strong>
            <p class="dependency-copy">${planning.session_id || "-"}</p>
          </div>
          <span class="status-pill ${decision.tone}">${statusLabel(planning.status)}</span>
        </div>
        <div class="inline-summary">
          <span class="chip">Phase ${planning.phase || "-"}</span>
          <span class="chip">Updated ${formatTime(sessionUpdatedAt)}</span>
          <span class="chip">Session ref ${escapeHtml(sessionRef)}</span>
        </div>
        <p class="card-copy">${sessionScope}</p>
        <p class="body-copy">${decision.body}</p>
        <div class="planning-kpis">
          <span class="chip">Consensus ${statusLabel(planning.consensus_status)}</span>
          <span class="chip">Human gate ${statusLabel(planning.human_gate_status)}</span>
          <span class="chip">Can materialize ${planning.switch_gate?.ready_to_materialize ? "Yes" : "No"}</span>
          <span class="chip">Open issues ${planning.counts.open_items || 0}</span>
          <span class="chip">Work total ${proposalStats.total}</span>
          <span class="chip">Completed ${proposalStats.done}/${proposalStats.total}</span>
          <span class="chip">Materialized ${proposalStats.materialized}/${proposalStats.total}</span>
          <span class="chip">In progress ${proposalStats.inProgress + proposalStats.review}</span>
          <span class="chip">Readouts ${planning.counts.readouts_resolved || 0}/${readoutTotal} resolved</span>
        </div>
      </article>
      <div class="planning-side-stack">
        <article class="planning-side-card">
          <div class="stack-head">
            <strong>協作角色</strong>
            <span class="chip">Round ${planning.current_round || 0}</span>
          </div>
          <div class="planning-list">
            <div class="planning-list-item">
              <div class="planning-callout-title">Facilitator</div>
              <div class="planning-callout-value">${agentLabel(planning.facilitator)}</div>
            </div>
            <div class="planning-list-item">
              <div class="planning-callout-title">Current baton</div>
              <div class="planning-callout-value">${agentLabel(planning.baton_owner)}</div>
              <p class="dependency-copy">Next reviewer：${agentLabel(planning.next_reviewer)}</p>
            </div>
          </div>
        </article>
        <article class="planning-side-card">
          <div class="stack-head">
            <strong>重要訊號</strong>
            <span class="chip">${highlights.length}</span>
          </div>
          <div class="planning-list">
            ${highlights
              .map(
                (item) => `
                <div class="planning-list-item">
                  <div class="planning-callout-title">${item.label}</div>
                  <p class="dependency-copy">${truncate(item.note, 140)}</p>
                </div>
              `
              )
              .join("")}
          </div>
        </article>
      </div>
    </div>
  `;
}

export function renderPlanningArtifacts(planningState) {
  const container = qs("#planning-artifacts");
  if (!container) return;
  const planning = normalizePlanningState(planningState);
  const artifacts = Object.entries(planning.artifacts || {});
  const readouts = Object.entries(planning.readouts || {});

  const sections = [];
  sections.push(`
    <article class="stack-card artifact-card">
      <div class="stack-head">
        <strong>Planning Artifacts</strong>
        <span class="chip">${artifacts.length}</span>
      </div>
      <p class="card-copy">這裡顯示的是 planning workspace 內部材料狀態，主要用於除錯與追文件流程，不是給使用者判斷「現在能不能開工」的主訊號。</p>
      <div class="artifact-grid">
        ${artifacts
          .map(
            ([key, artifact]) => `
            <div class="artifact-item">
              <div class="lane-head">
                <strong>${titleCase(key)}</strong>
                <span class="status-pill status-${artifact.status || "pending"}">${statusLabel(artifact.status)}</span>
              </div>
              <p class="card-copy artifact-path"><code class="path-code">${artifact.path || "-"}</code></p>
            </div>
          `
          )
          .join("")}
      </div>
    </article>
  `);

  sections.push(`
    <article class="stack-card artifact-card">
      <div class="stack-head">
        <strong>Lane Readouts</strong>
        <span class="chip">${planning.counts.readouts_resolved || 0}/${readouts.length} resolved</span>
      </div>
      <p class="card-copy"><code>submitted</code>、<code>accepted</code>、<code>waived</code> 都視為已收斂；這一格不是 live execution worker 數。</p>
      <div class="artifact-grid">
        ${readouts
          .map(
            ([agent, info]) => `
            <div class="artifact-item">
              <div class="lane-head">
                <strong>${agentLabel(agent)}</strong>
                <span class="status-pill status-${info.status || "pending"}">${statusLabel(info.status)}</span>
              </div>
              <p class="card-copy artifact-path"><code class="path-code">${info.path || "-"}</code></p>
              <p class="card-copy">更新：${formatTime(info.updated_at)}</p>
            </div>
          `
          )
          .join("")}
      </div>
    </article>
  `);

  container.innerHTML = sections.join("");
}

export function renderPlanningRounds(planningState) {
  const container = qs("#planning-rounds");
  if (!container) return;
  const planning = normalizePlanningState(planningState);
  const interesting = new Set([
    "planning_session_started",
    "cross_review_round_opened",
    "cross_review_round_completed",
    "consensus_packet_drafted",
    "consensus_status_updated",
    "consensus_accepted",
    "human_gate_updated",
    "planning_issue_updated",
  ]);
  const events = (planning.recent_events || [])
    .filter((entry) => interesting.has(String(entry.type || "")))
    .slice(-4)
    .reverse();

  if (!events.length) {
    container.innerHTML = `
      <article class="stack-card">
        <div class="stack-head">
          <strong>Recent Decisions</strong>
          <span class="status-pill status-pending">尚無摘要</span>
        </div>
        <p class="card-copy">目前還沒有足夠的 planning 轉折事件可摘要，可先看 next step 與候選工作。</p>
      </article>
    `;
    return;
  }

  container.innerHTML = `
    <article class="stack-card">
      <div class="stack-head">
        <strong>Recent Decisions</strong>
        <span class="chip">${events.length} 個關鍵轉折</span>
      </div>
      <div class="planning-list">
        ${events
          .map(
            (entry) => `
            <div class="planning-list-item">
              <div class="lane-head">
                <strong>${titleCase(String(entry.type || "update").replace(/_/g, " "))}</strong>
                <span class="activity-meta">${formatTime(entry.ts || entry.updated_at)}</span>
              </div>
              <p class="dependency-copy">${entry.message || entry.summary || "-"}</p>
            </div>
          `
          )
          .join("")}
      </div>
    </article>
  `;
}

export function renderPlanningGate(planningState, status) {
  const container = qs("#planning-gate");
  if (!container) return;
  const planning = normalizePlanningState(planningState);
  const gate = planning.switch_gate || {};
  const nextStep = planningNextAction(planning, status);
  const items = [
    ["all_readouts_submitted", "All readouts submitted"],
    ["cross_review_round_present", "At least one cross-review round"],
    ["divergence_resolved_or_escalated", "Divergences resolved or escalated"],
    ["consensus_packet_drafted", "Consensus packet drafted"],
    ["human_approved", "Human accepted the packet"],
  ];

  container.innerHTML = `
    <article class="stack-card">
      <div class="stack-head">
        <strong>Next Step</strong>
        <span class="status-pill ${gate.ready_to_materialize ? "status-ready" : gate.ready_for_human ? "status-review" : "status-pending"}">
          ${gate.ready_to_materialize ? "Ready to materialize" : gate.ready_for_human ? "Ready for human" : "Not ready"}
        </span>
      </div>
      <p class="card-copy">${nextStep.action}</p>
      <div class="planning-callouts">
        <div class="artifact-item">
          <div class="planning-callout-title">Current owner</div>
          <div class="planning-callout-value">${nextStep.owner}</div>
        </div>
        <div class="artifact-item">
          <div class="planning-callout-title">Materialization</div>
          <div class="planning-callout-value">${nextStep.materializedCount}/${nextStep.totalProposals}</div>
        </div>
        <div class="artifact-item">
          <div class="planning-callout-title">Still missing</div>
          <div class="planning-callout-value">${nextStep.missing.length}</div>
        </div>
      </div>
      ${nextStep.missing.length ? `<p class="dependency-copy">目前還差：${nextStep.missing.join("、")}。</p>` : ""}
      <div class="gate-checklist">
        ${items
          .map(
            ([key, label]) => `
            <div class="gate-item ${gate[key] ? "gate-ok" : "gate-wait"}">
              <span class="gate-dot">${gate[key] ? "yes" : "wait"}</span>
              <span>${label}</span>
            </div>
          `
          )
          .join("")}
      </div>
    </article>
  `;
}

export function renderPlanningIssues(planningState) {
  const planning = normalizePlanningState(planningState);
  const container = qs("#planning-issues");
  if (!container) return;
  const items = planning.unresolved_items || [];
  const blocking = items.filter((item) => !["tracking", "resolved", "accepted"].includes(String(item.status || "").toLowerCase()));
  const tracking = items.filter((item) => ["tracking", "resolved", "accepted"].includes(String(item.status || "").toLowerCase()));

  if (!items.length) {
    container.innerHTML = `
      <article class="stack-card">
        <div class="stack-head">
          <strong>Planning Issues</strong>
          <span class="status-pill status-ready">清空</span>
        </div>
        <p class="card-copy">目前沒有需要額外注意的 planning issue，沒有顯著風險阻擋這輪收斂。</p>
      </article>
    `;
    return;
  }

  container.innerHTML = `
    <article class="stack-card">
      <div class="stack-head">
        <strong>Planning Issues</strong>
        <span class="chip">Blocking ${blocking.length} · Tracking ${tracking.length}</span>
      </div>
      <p class="card-copy">${
        blocking.length
          ? "以下問題仍會影響是否能安全地下放。"
          : "目前沒有 blocking issue；以下項目主要是追蹤與後續收斂提醒。"
      }</p>
      <div class="planning-list">
        ${items
          .map(
            (item) => `
            <div class="planning-list-item">
              <div class="stack-head">
                <strong>${item.id || "DIV"}</strong>
                <span class="status-pill status-${item.status || "open"}">${statusLabel(item.status)}</span>
              </div>
              <p class="dependency-copy">${item.summary || "尚未填寫 divergence 說明。"}</p>
              <div class="lane-meta">
                <span class="chip">Severity ${item.severity || "-"}</span>
                <span class="chip">${["tracking", "resolved", "accepted"].includes(String(item.status || "").toLowerCase()) ? "不阻擋主流程" : "需優先處理"}</span>
              </div>
            </div>
          `
          )
          .join("")}
      </div>
    </article>
  `;
}

export function renderPlanningProposals(planningState, status, dashboardBundle = null) {
  const planning = normalizePlanningState(planningState);
  const container = qs("#planning-proposals");
  if (!container) return;
  const taskIds = new Set((status.tasks || []).map((task) => task.id));
  const proposals = planning.proposed_execution_tasks || [];
  const proposalStats = planningProposalStats(planning, status, dashboardBundle);
  const materialized = proposals.filter((task) => taskIds.has(task.id));
  const pending = proposalStats.pendingProposals?.length ? proposalStats.pendingProposals : proposals.filter((task) => !taskIds.has(task.id));
  const primaryList = pending.length ? pending : proposals;
  const preview = primaryList.slice(0, 6);
  const remaining = primaryList.slice(6);

  if (!proposals.length) {
    container.innerHTML = `
      <article class="stack-card">
        <div class="stack-head">
          <strong>Execution Slices</strong>
          <span class="status-pill status-pending">尚未提出</span>
        </div>
        <p class="card-copy">這輪 planning 還沒有整理出可下放的 execution slices。</p>
      </article>
    `;
    return;
  }

  const renderProposal = (task) => `
    <div class="planning-list-item">
      <div class="stack-head">
        <strong>${task.id}</strong>
        <span class="status-pill ${taskIds.has(task.id) ? "status-ready" : "status-pending"}">${taskIds.has(task.id) ? "已 materialize" : "待下放"}</span>
      </div>
      <p>${task.title || "尚未填寫標題。"}</p>
      <div class="lane-meta">
        <span class="chip">Owner ${agentLabel(task.owner)}</span>
        <span class="chip">Reviewer ${agentLabel(task.reviewer)}</span>
      </div>
      ${task.summary_zh ? `<p class="dependency-copy">${task.summary_zh}</p>` : ""}
    </div>
  `;

  container.innerHTML = `
    <article class="stack-card">
      <div class="stack-head">
        <strong>Execution Slices</strong>
        <span class="chip">總數 ${proposalStats.total} · 完成 ${proposalStats.done}</span>
      </div>
      <p class="card-copy">${
        pending.length
          ? `目前還有 ${pending.length} 個候選切片尚未正式進入 execution board。`
          : "所有候選切片都已經進入 execution board。"
      }</p>
      <div class="lane-meta">
        <span class="chip">已下放 ${proposalStats.materialized}/${proposalStats.total}</span>
        <span class="chip">進行中 ${proposalStats.inProgress + proposalStats.review}</span>
        <span class="chip">待開始 ${proposalStats.todo}</span>
        <span class="chip">已完成 ${proposalStats.done}</span>
        ${proposalStats.blocked ? `<span class="chip">阻塞 ${proposalStats.blocked}</span>` : ""}
      </div>
      <div class="planning-list">
        ${preview.map(renderProposal).join("")}
      </div>
      ${
        remaining.length
          ? `
          <details class="inline-details">
            <summary>查看剩餘 ${remaining.length} 個切片</summary>
            <div class="planning-list">
              ${remaining.map(renderProposal).join("")}
            </div>
          </details>
        `
          : ""
      }
    </article>
  `;
}

export function renderFocusSummary(status, planningState, orchState, dashboardBundle = null) {
  const container = qs("#focus-summary");
  if (!container) return;
  const planning = normalizePlanningState(planningState);
  const narrative = currentPriorityNarrative(planning, status, orchState, dashboardBundle);
  const proposalStats = planningProposalStats(planning, status, dashboardBundle);
  const pausedProviders = pausedProviderEntries(orchState);
  const evidence = [
    proposalStats.total ? `本輪 planning 產出 ${proposalStats.total} 個候選工作` : "目前沒有待橋接的 planning slice",
    pausedProviders.length
      ? `${pausedProviders.length} 個 provider 因 quota / rate limit 被暫停派工`
      : "目前沒有 provider pause guardrail",
    proposalStats.total
      ? `execution 已完成 ${proposalStats.done}/${proposalStats.total} 個本輪切片`
      : "execution 目前仍以既有 task board 為主要節奏",
  ];

  container.innerHTML = `
    <div class="focus-grid">
      <article class="focus-card focus-primary">
        <div class="stack-head">
          <strong>${narrative.title}</strong>
          <span class="status-pill ${narrative.tone}">${narrative.badge}</span>
        </div>
        <p class="body-copy">${narrative.body}</p>
        <div class="inline-summary">
          ${narrative.chips.map((chip) => `<span class="chip">${chip}</span>`).join("")}
        </div>
      </article>
      <article class="focus-card">
        <div class="stack-head">
          <strong>下一步由誰推進</strong>
          <span class="chip">${narrative.owner}</span>
        </div>
        <p class="body-copy">${narrative.nextAction}</p>
        <div class="focus-evidence">
          ${evidence.map((item) => `<div class="focus-evidence-item">${item}</div>`).join("")}
        </div>
      </article>
    </div>
  `;
}

export function renderProgressBreakdown(status, planningState, dashboardBundle = null) {
  const container = qs("#progress-breakdown");
  if (!container) return;
  const planning = normalizePlanningState(planningState);
  const planningProgress = planningGateProgress(planning);
  const proposalStats = planningProposalStats(planning, status, dashboardBundle);
  const executionCounts = executionStatusCounts(status, dashboardBundle);
  const bridgePercent = proposalStats.total ? Math.round((proposalStats.materialized / proposalStats.total) * 100) : (planning.switch_gate?.ready_to_materialize ? 100 : 0);

  const localTasks = status.tasks || [];
  const localDone = localTasks.filter((t) => String(t.status || "").toLowerCase() === "done").length;
  const localOpen = localTasks.filter((t) => String(t.status || "").toLowerCase() !== "done").length;
  const executionPercent = localTasks.length ? Math.round((localDone / localTasks.length) * 100) : 0;
  const executionNote = localTasks.length
    ? `${localDone}/${localTasks.length} 當前看板完成 · open ${localOpen}`
    : "目前 active board 沒有任務";

  const items = [
    {
      label: "Planning Gate",
      value: `${planningProgress.percent}%`,
      note: `${planningProgress.done}/${planningProgress.total} 條件已滿足`,
      tone: planning.status === "accepted" ? "status-ready" : planning.status === "active" ? "status-active" : "status-pending",
    },
    {
      label: "下放進度",
      value: `${bridgePercent}%`,
      note: `${proposalStats.materialized}/${proposalStats.total || 0} 已 materialize`,
      tone: proposalStats.total && proposalStats.materialized < proposalStats.total ? "status-review" : "status-ready",
    },
    {
      label: "當前看板完成度",
      value: `${executionPercent}%`,
      note: executionNote,
      tone: executionCounts.blocked ? "status-review" : "status-ready",
    },
  ];

  container.innerHTML = items
    .map(
      (item) => `
        <article class="progress-card">
          <div class="stack-head">
            <strong>${item.label}</strong>
            <span class="status-pill ${item.tone}">${item.value}</span>
          </div>
          <p class="dependency-copy">${item.note}</p>
        </article>
      `
    )
    .join("");
}

export function renderAlertStrip(status, orchState, planningState, approvalQueue, dashboardBundle = null) {
  const container = qs("#alert-strip");
  if (!container) return;
  const alerts = [];
  const pausedProviders = pausedProviderEntries(orchState);
  const planning = normalizePlanningState(planningState);
  const truth = buildTruthMismatches(status, orchState, approvalQueue);
  const blockers = (status.blockers || []).filter((blocker) => blocker.status === "open");
  const approvalPending = (approvalQueue?.pending || []).length;
  const mismatchItems = truth.mismatches.length ? truth.mismatches : (Array.isArray(dashboardBundle?.truth_mismatches) ? dashboardBundle.truth_mismatches : []);

  for (const pause of pausedProviders) {
    const pauseReason = summarizePausedReason(pause.summary || pause.reason, pause.provider);
    const detailLines = [];
    if (pauseReason.detail && pauseReason.detail !== pauseReason.summary) {
      detailLines.push(pauseReason.detail);
    }
    if (pause.raw_ref) {
      detailLines.push(`evidence: ${pause.raw_ref}`);
    }
    alerts.push({
      severity: "critical",
      priority: 10,
      title: `${agentLabel(pause.provider)} 暫停派工中`,
      body: pauseReason.summary,
      chips: [
        pauseReason.kind === "quota" ? "Quota / rate limit" : "Dispatch paused",
        pause.blocked_until ? `until ${formatTime(pause.blocked_until)}` : "until -",
        pause.task_id ? `task ${pause.task_id}` : "",
      ].filter(Boolean),
      detail: detailLines.join("\n"),
    });
  }

  if (mismatchItems.length) {
    alerts.push({
      severity: "warning",
      priority: 30,
      title: `Runtime / task board 還有 ${mismatchItems.length} 個 mismatch`,
      body: mismatchItems.length >= 2
        ? `目前最明顯的是 ${mismatchItems.slice(0, 2).map((item) => `${item.task_id}: ${item.summary}`).join("；")}。`
        : mismatchItems[0].summary,
      chips: mismatchItems.length ? mismatchItems.slice(0, 4).map((item) => item.task_id || "mismatch") : ["請看 Truth Mismatches"],
      detail: mismatchItems.map((item) => `${item.task_id || item.type || "-"} · ${item.summary}`).join("\n"),
    });
  }

  if (blockers.length) {
    alerts.push({
      severity: "warning",
      priority: 20,
      title: `${blockers.length} 個 blocker 正在卡 execution`,
      body: "這些不是一般待辦，而是會阻止工作繼續向下游推進的阻塞項目。",
      chips: blockers.slice(0, 3).map((item) => item.task_id || "-"),
      detail: blockers.map((item) => `${item.task_id || "-"} · ${compactWhitespace(item.summary || item.reason || "")}`).join("\n"),
    });
  }

  if (approvalPending) {
    const pendingApprovals = approvalQueue?.pending || [];
    alerts.push({
      severity: "info",
      priority: 40,
      title: `${approvalPending} 個待批准項目`,
      body: "工具批准還沒清掉時，相關 worker 可能停在 waiting_approval。",
      chips: pendingApprovals.length
        ? pendingApprovals.slice(0, 4).map((item) => item.task_id || item.tool_name || "approval")
        : ["Approval queue"],
      detail: pendingApprovals
        .map((item) => {
          const parts = [
            item.approval_id || "-",
            item.tool_name || "-",
            item.task_id ? `task ${item.task_id}` : "",
            item.tool_input_preview ? `input ${item.tool_input_preview}` : "",
            item.evidence_ref ? `evidence ${item.evidence_ref}` : "",
          ].filter(Boolean);
          return parts.join(" · ");
        })
        .join("\n"),
    });
  }

  if (planning.status === "human_required") {
    alerts.push({
      severity: "critical",
      priority: 0,
      title: "Planning 需要人工裁決",
      body: "LLM 已整理出主要分歧，但仍有需要 human 決策的問題，未裁決前不建議擴大下游 execution。",
      chips: ["Human gate"],
    });
  }

  if (!alerts.length) {
    alerts.push({
      severity: "ok",
      title: "目前沒有高優先告警",
      body: planning.status === "accepted"
        ? "planning 已被接受，execution 也沒有額外 approval / quota / blocker 壓力，現在最值得做的是穩定推進 active tasks。"
        : "目前沒有需要立刻處理的 quota、approval、blocker 或 mismatch 告警，系統狀態相對平穩。",
      chips: ["All clear"],
    });
  }

  const severityRank = { critical: 0, warning: 1, info: 2, ok: 3 };
  alerts.sort((a, b) => {
    const priorityDelta = Number(a.priority ?? 50) - Number(b.priority ?? 50);
    if (priorityDelta) return priorityDelta;
    return Number(severityRank[a.severity] ?? 9) - Number(severityRank[b.severity] ?? 9);
  });

  container.innerHTML = alerts
    .map(
      (alert) => `
        <article class="alert-card alert-${alert.severity}">
          <div class="stack-head">
            <strong>${escapeHtml(alert.title)}</strong>
            <span class="status-pill ${alert.severity === "critical" ? "status-blocked" : alert.severity === "warning" ? "status-review" : "status-ready"}">${
              {
                critical: "需立即處理",
                warning: "注意",
                info: "資訊",
                ok: "穩定",
              }[alert.severity] || titleCase(alert.severity)
            }</span>
          </div>
          <p class="body-copy">${escapeHtml(alert.body)}</p>
          ${alert.chips?.length ? `<div class="lane-meta">${alert.chips.map((chip) => `<span class="chip">${escapeHtml(chip)}</span>`).join("")}</div>` : ""}
          ${alert.detail ? `
            <details class="alert-detail">
              <summary>展開詳細原因</summary>
              <p class="alert-detail-copy">${escapeHtml(alert.detail)}</p>
            </details>
          ` : ""}
        </article>
      `
    )
    .join("");
}

export function renderBridgeCard(status, planningState, dashboardBundle = null) {
  const container = qs("#bridge-card");
  if (!container) return;
  const planning = normalizePlanningState(planningState);
  const proposalStats = planningProposalStats(planning, status, dashboardBundle);
  const pending = proposalStats.pendingProposals || [];
  const incomplete = proposalStats.activeMaterializedTasks || [];

  let title = "這輪 planning 還沒有橋接到 execution";
  let tone = "status-pending";
  let body = "目前還沒有提出可 materialize 的 execution slice，所以 bridge 層不會有太多內容。";

  if (proposalStats.total) {
    if (proposalStats.materialized < proposalStats.total) {
      title = "共識已成形，但還有工作尚未正式進入 execution";
      tone = "status-review";
      body = `目前 ${proposalStats.materialized}/${proposalStats.total} 個切片已 materialize。剩下的候選工作還需要轉成正式 task，execution 才能完整接手。`;
    } else if (proposalStats.done < proposalStats.total) {
      title = "橋接已完成，execution 正在把共識消化成成果";
      tone = "status-ready";
      body = `所有 ${proposalStats.total} 個候選切片都已進入 execution。現在重點是把還在進行中或待審查的工作收斂成 done。`;
    } else {
      title = "這輪 planning 的輸出已完整落地";
      tone = "status-ready";
      body = `本輪 planning 提出的 ${proposalStats.total} 個切片都已經 materialize，並且已全部完成。`;
    }
  }

  const leadList = (pending.length ? pending : incomplete).slice(0, 4);

  const statCards = [
    { label: "候選工作", value: proposalStats.total, note: "這輪 planning 提出的候選工作數" },
    { label: "已下放", value: `${proposalStats.materialized}/${proposalStats.total || 0}`, note: "已正式變成 execution task" },
    { label: "尚在執行", value: proposalStats.inProgress + proposalStats.review + proposalStats.todo, note: "已下放但尚未 done 的切片" },
    { label: "已完成", value: `${proposalStats.done}/${proposalStats.total || 0}`, note: "已真正走完 execution lifecycle 的切片" },
  ];

  container.innerHTML = `
    <article class="bridge-card bridge-lead">
      <div class="stack-head">
        <strong>${title}</strong>
        <span class="status-pill ${tone}">${proposalStats.materialized}/${proposalStats.total || 0}</span>
      </div>
      <p class="body-copy">${body}</p>
      <div class="inline-summary">
        <span class="chip">Consensus ${statusLabel(planning.consensus_status)}</span>
        <span class="chip">Human gate ${statusLabel(planning.human_gate_status)}</span>
        <span class="chip">Can materialize ${planning.switch_gate?.ready_to_materialize ? "Yes" : "No"}</span>
        ${proposalStats.sessionId ? `<span class="chip">Session ${proposalStats.sessionId}</span>` : ""}
      </div>
      ${
        leadList.length
          ? `
            <div class="bridge-preview">
              <strong>${pending.length ? "優先補下放的切片" : "目前仍在 execution 中的切片"}</strong>
              <ul class="note-list compact-list">
                ${leadList.map((task) => `<li><strong>${task.id}</strong>：${task.title || task.summary_zh || "尚未填寫"}</li>`).join("")}
              </ul>
            </div>
          `
          : ""
      }
      ${
        proposalStats.consensusPacket || proposalStats.executionMaterialization
          ? `
            <div class="bridge-preview">
              <strong>Bridge refs</strong>
              <div class="lane-meta">
                ${proposalStats.consensusPacket ? `<span class="chip">${proposalStats.consensusPacket}</span>` : ""}
                ${proposalStats.executionMaterialization ? `<span class="chip">${proposalStats.executionMaterialization}</span>` : ""}
              </div>
            </div>
          `
          : ""
      }
    </article>
    ${statCards
      .map(
        (item) => `
          <article class="bridge-card bridge-stat">
            <div class="metric-label">${item.label}</div>
            <div class="metric-value">${item.value}</div>
            <div class="metric-note">${item.note}</div>
          </article>
        `
      )
      .join("")}
  `;
}

export function renderExecutionSummary(status, orchState, dashboardBundle = null) {
  const container = qs("#execution-summary");
  if (!container) return;
  const tasks = status.tasks || [];
  const taskMap = new Map(tasks.map((task) => [task.id, task]));
  const truth = buildTruthMismatches(status, orchState);
  const summary = dashboardBundle?.execution_summary || {};
  const readyNow = tasks.filter((task) => {
    if (String(task.status || "").toLowerCase() !== "todo") return false;
    return (task.depends_on || []).every((depId) => String(taskMap.get(depId)?.status || "done").toLowerCase() === "done");
  }).length;
  const dependencyReady = Number.isFinite(summary.dependency_ready) ? summary.dependency_ready : readyNow;
  const activeNow = tasks.filter((task) => String(task.status || "").toLowerCase() === "in_progress").length;
  const reviewNow = tasks.filter((task) => String(task.status || "").toLowerCase() === "review").length;
  const blockedNow = tasks.filter((task) => String(task.status || "").toLowerCase() === "blocked").length;
  const attachedNow = [...truth.liveWorkersByTask.values()].filter((workers) => workers.some((worker) => worker.bucket === "running")).length;
  const cards = [
    { label: "Ready Now", value: Number.isFinite(summary.ready_now) ? summary.ready_now : readyNow, note: "lane 健康且目前空閒，supervisor 這一輪真的能 dispatch 的 todo task" },
    { label: "Deps Ready", value: dependencyReady, note: "依賴都完成，但可能仍在等 lane 恢復、空出，或避開 sidecar-only/guardrail 限制" },
    { label: "In Progress", value: activeNow, note: "task board 上已進入 in_progress 的任務" },
    { label: "In Review", value: reviewNow, note: "正在 review lane 的任務" },
    { label: "Live Attached", value: Number.isFinite(summary.live_attached) ? summary.live_attached : attachedNow, note: "目前有 live running worker 真的掛在 task 上" },
    { label: "Blocked", value: blockedNow, note: "已標記 blocker 的任務" },
    {
      label: "Mismatches",
      value: truth.counts.total,
      note: truth.counts.total ? `High ${truth.counts.high} · Medium ${truth.counts.medium}` : "目前 execution/runtime 對齊",
      statusClass: truth.counts.total ? "status-blocked" : "status-ready",
    },
    {
      label: "Archived Done",
      value: executionStatusCounts(status, dashboardBundle).archivedDone,
      note: "歷史封存完成數；不列入當前 sprint/open board 計算",
    },
  ];

  container.innerHTML = cards
    .map(
      (item) => `
      <article class="workload-card">
        <div class="lane-head">
          <strong>${item.label}</strong>
          <span class="status-pill ${item.statusClass || ""}">${item.value}</span>
        </div>
        <p class="dependency-copy">${item.note}</p>
      </article>
    `
    )
    .join("");
}

export function renderTruthMismatches(status, orchState, approvalQueue, dashboardBundle = null) {
  const container = qs("#truth-mismatches");
  if (!container) return;
  container.innerHTML = "";
  const truth = buildTruthMismatches(status, orchState, approvalQueue);
  const mismatchItems = truth.mismatches.length ? truth.mismatches : (Array.isArray(dashboardBundle?.truth_mismatches) ? dashboardBundle.truth_mismatches : []);

  if (!mismatchItems.length) {
    container.innerHTML = `
      <article class="stack-card mismatch-card mismatch-ok">
        <div class="stack-head">
          <strong>Runtime / Execution 已對齊</strong>
          <span class="status-pill status-ready">0 mismatch</span>
        </div>
        <p class="card-copy">目前看不到 live worker、queue record、task board 之間的高訊號不一致。</p>
      </article>
    `;
    return;
  }

  container.innerHTML = mismatchItems
    .map(
      (item) => `
      <article class="stack-card mismatch-card mismatch-${item.severity || "medium"}">
        <div class="stack-head">
          <strong>${item.title}</strong>
          <span class="status-pill status-${item.severity === "high" ? "blocked" : "pending"}">${item.severity || "medium"}</span>
        </div>
        <p class="card-copy">${item.summary}</p>
        <div class="lane-meta">
          ${item.task_id ? `<span class="chip">task ${item.task_id}</span>` : ""}
          ${item.worker_run_id ? `<span class="chip">run ${item.worker_run_id}</span>` : ""}
          ${item.queue_event_id ? `<span class="chip">queue ${item.queue_event_id}</span>` : ""}
          ${item.expected_actor ? `<span class="chip">expected ${item.expected_actor}</span>` : ""}
          ${item.actual_actor ? `<span class="chip">actual ${item.actual_actor}</span>` : ""}
          <span class="chip">${timeAgo(item.detected_at)}</span>
        </div>
        ${item.resolution_hint ? `<p class="card-copy"><strong>Resolution hint:</strong> ${item.resolution_hint}</p>` : ""}
      </article>
    `
    )
    .join("");
}

export function renderBoardSummary(status, orchState, dashboardBundle = null) {
  const containers = [qs("#board-summary"), qs("#work-panel-summary")].filter(Boolean);
  if (!containers.length) return;
  const tasks = status.tasks || [];
  const taskMap = new Map(tasks.map((task) => [task.id, task]));
  const truth = buildTruthMismatches(status, orchState);
  const summary = dashboardBundle?.execution_summary || {};
  const readyCount = tasks.filter((task) => {
    if (String(task.status || "").toLowerCase() !== "todo") return false;
    return (task.depends_on || []).every((depId) => String(taskMap.get(depId)?.status || "done").toLowerCase() === "done");
  }).length;
  const dependencyReady = Number.isFinite(summary.dependency_ready) ? summary.dependency_ready : readyCount;
  const reviewCount = tasks.filter((task) => String(task.status || "").toLowerCase() === "review").length;
  const attachedCount = [...truth.liveWorkersByTask.values()].filter((workers) => workers.some((worker) => worker.bucket === "running")).length;
  const chips = [
    `<span class="chip">Ready ${Number.isFinite(summary.ready_now) ? summary.ready_now : readyCount}</span>`,
    `<span class="chip">Deps ${dependencyReady}</span>`,
    `<span class="chip">Review ${reviewCount}</span>`,
    `<span class="chip">Live attached ${Number.isFinite(summary.live_attached) ? summary.live_attached : attachedCount}</span>`,
    `<span class="chip ${truth.counts.total ? "status-blocked" : ""}">Mismatch ${truth.counts.total}</span>`,
  ];
  for (const container of containers) {
    container.innerHTML = chips.join("");
  }
}

export function renderExecutionSectionSummary(status, orchState, planningState, dashboardBundle = null) {
  const container = qs("#execution-panel-summary");
  if (!container) return;
  const planning = normalizePlanningState(planningState);
  const counts = executionStatusCounts(status, dashboardBundle);
  const pausedProviders = pausedProviderEntries(orchState);
  const focusMode = dashboardFocusMode(planning);
  const chips = [
    `<span class="chip">${focusMode === "planning" ? "目前非焦點" : "目前焦點"}</span>`,
    `<span class="chip">Open ${counts.total - counts.done}</span>`,
    `<span class="chip">Done ${counts.done}/${counts.total}</span>`,
    `<span class="chip">In progress ${counts.inProgress}</span>`,
    `<span class="chip">Review ${counts.review}</span>`,
    `<span class="chip">Blocked ${counts.blocked}</span>`,
  ];
  if (pausedProviders.length) {
    chips.push(`<span class="chip status-blocked">Paused ${pausedProviders.length}</span>`);
  }
  container.innerHTML = chips.join("");
}

export function renderSystemStatus(status, orchState, approvalQueue, agentStates, dashboardBundle = null) {
  const statusEl = qs("#system-status");
  const historyEl = qs("#worker-history");
  if (!statusEl || !historyEl) return;
  statusEl.innerHTML = "";
  historyEl.innerHTML = "";
  const queueEvents = normalizeDispatchQueue(orchState, status);
  const runtimeSummary = dashboardBundle?.runtime_summary || {};
  const chairSummary = dashboardBundle?.chair_summary || {};
  const supervisor = orchState?.supervisor || {};
  const supervisorPid = supervisor?.pid || "-";
  const supervisorStartedAt = supervisor?.started_at || orchState?.initialized_at || null;
  const supervisorHeartbeat = supervisor?.last_heartbeat_at || orchState?.last_heartbeat_at || null;
  const lastScan = orchState?.last_scan_at || supervisorHeartbeat || null;
  const workers = normalizeWorkerRecords(orchState, status);
  const pausedProviders = pausedProviderEntries(orchState);
  const activeWorkerCount = Number.isFinite(runtimeSummary.running_workers) ? runtimeSummary.running_workers : workers.filter((w) => w.bucket === "running").length;
  const pending = Number.isFinite(runtimeSummary.pending_approvals) ? runtimeSummary.pending_approvals : (approvalQueue?.pending || []).length;
  const dispatchPolicy = dashboardBundle?.dispatch_policy || {};
  const recentHelperClaims = Array.isArray(dashboardBundle?.recent_helper_claims) ? dashboardBundle.recent_helper_claims : [];
  const idleClaimEnabled = Boolean(dispatchPolicy.claim_idle_work && dispatchPolicy.helper_claim_enabled !== false);
  const sidecarClaimEnabled = Boolean(dispatchPolicy.claim_sidecars_when_idle);
  const maxDispatchesPerTick = Number.isFinite(Number(dispatchPolicy.max_dispatches_per_tick))
    ? Number(dispatchPolicy.max_dispatches_per_tick)
    : "-";
  const maxTasksPerAgent = Number.isFinite(Number(dispatchPolicy.max_tasks_per_agent))
    ? Number(dispatchPolicy.max_tasks_per_agent)
    : "-";

  const supervisorCard = document.createElement("article");
  supervisorCard.className = "sys-card";
  supervisorCard.innerHTML = `
      <div class="sys-card-head"><span class="sys-icon">🖥</span><strong>Supervisor</strong></div>
      <div class="sys-card-body">
        <span class="status-pill ${supervisorHeartbeat ? "status-working" : "status-blocked"}">${supervisorHeartbeat ? "運作中" : "無資料"}</span>
      <span class="chip">PID：${runtimeSummary.supervisor_pid || supervisorPid}</span>
        <span class="chip">啟動：${formatTime(supervisorStartedAt)}</span>
      <span class="chip">絕對時間：${DISPLAY_TIME_ZONE_LABEL}</span>
      <span class="chip">Heartbeat：${timeAgo(runtimeSummary.heartbeat_at || supervisorHeartbeat)}</span>
        <span class="chip">上次掃描：${timeAgo(lastScan)}</span>
        <span class="chip">Active Workers：${activeWorkerCount}</span>
      </div>
  `;
  statusEl.appendChild(supervisorCard);

  const workerClaimCard = document.createElement("article");
  workerClaimCard.className = "sys-card";
  workerClaimCard.innerHTML = `
    <div class="sys-card-head"><span class="sys-icon">⇄</span><strong>Worker Claim Policy</strong></div>
    <div class="sys-card-body">
      <span class="status-pill ${idleClaimEnabled ? "status-working" : "status-review"}">${idleClaimEnabled ? "idle worker 可 claim" : "Supervisor 主導派工"}</span>
      <span class="chip">own work first</span>
      <span class="chip">idle claim ${idleClaimEnabled ? "on" : "off"}</span>
      <span class="chip">sidecar claim ${sidecarClaimEnabled ? "on" : "off"}</span>
      <span class="chip">per tick ${escapeHtml(maxDispatchesPerTick)}</span>
      <span class="chip">per agent ${escapeHtml(maxTasksPerAgent)}</span>
      <span class="chip">priority gate ${dispatchPolicy.require_owner_higher_priority_load ? "on" : "off"}</span>
      ${recentHelperClaims.length ? recentHelperClaims.slice(0, 5).map((claim) => `
        <div class="approval-item">
          <span class="chip">${escapeHtml(claim.task_id || "-")}</span>
          <span class="chip">${escapeHtml(actorLabel(claim.from_owner, null))} &rarr; ${escapeHtml(actorLabel(claim.to_owner, null))}</span>
          ${claim.new_reviewer ? `<span class="chip">reviewer ${escapeHtml(actorLabel(claim.new_reviewer, null))}</span>` : ""}
          <span class="chip">${escapeHtml(timeAgo(claim.ts))}</span>
        </div>
      `).join("") : '<div class="approval-item"><span class="chip">尚無 helper claim 紀錄</span></div>'}
    </div>
  `;
  statusEl.appendChild(workerClaimCard);

  const chairCard = document.createElement("article");
  chairCard.className = "sys-card";
  const chairReviewSummary = Array.isArray(chairSummary.last_review_summary) ? chairSummary.last_review_summary : [];
  chairCard.innerHTML = `
      <div class="sys-card-head"><span class="sys-icon">🪑</span><strong>Chair Review</strong></div>
      <div class="sys-card-body">
        <span class="status-pill ${chairSummary.pending_review_path ? "status-working" : chairSummary.last_chair_run_at ? "status-done" : "status-blocked"}">${
          chairSummary.pending_review_path ? "巡檢進行中" : chairSummary.last_chair_run_at ? "最近有巡檢" : "尚未巡檢"
        }</span>
        <span class="chip">Last Chair：${escapeHtml(actorLabel(chairSummary.last_chair_agent, null))}</span>
        <span class="chip">上次派出：${escapeHtml(timeAgo(chairSummary.last_chair_run_at))}</span>
        ${chairSummary.pending_review_agent ? `<span class="chip">Pending：${escapeHtml(actorLabel(chairSummary.pending_review_agent, null))}</span>` : ""}
        ${chairSummary.last_review_path ? `<span class="chip">${escapeHtml(chairSummary.last_review_path)}</span>` : ""}
        ${chairSummary.sidecar_approved_until ? `<span class="chip">Sidecar until ${escapeHtml(formatTime(chairSummary.sidecar_approved_until))}</span>` : ""}
        ${chairReviewSummary.map((line) => `<div class="approval-item">${escapeHtml(line)}</div>`).join("")}
      </div>
  `;
  statusEl.appendChild(chairCard);

  const dispatchCard = document.createElement("article");
  dispatchCard.className = "sys-card";
  dispatchCard.innerHTML = `
      <div class="sys-card-head"><span class="sys-icon">📬</span><strong>Dispatch Queue</strong></div>
      <div class="sys-card-body">
        <span class="status-pill ${queueEvents.length > 0 ? "status-review" : "status-done"}">${queueEvents.length > 0 ? `${queueEvents.length} 個待處理` : "清空"}</span>
        <span class="chip">bundle queue ${Number.isFinite(runtimeSummary.queue_depth) ? runtimeSummary.queue_depth : queueEvents.length}</span>
        <span class="chip">目前 active workers：${activeWorkerCount}</span>
      ${queueEvents.map((event) => `
        <div class="approval-item">
          <span class="chip">${event.task_id || "-"}</span>
          <span class="chip">${actorLabel(event.logical_agent_id, event.provider)}</span>
          <span class="chip">${event.status || "-"}</span>
          <span class="chip">${event.reason || "-"}</span>
          <span class="chip">${timeAgo(event.last_event_at || event.last_attempt_at || event.processed_at)}</span>
        </div>
      `).join("")}
    </div>
  `;
  statusEl.appendChild(dispatchCard);

  const codexSlots = buildCodexSlotRoster(orchState, status);
  const codexActiveCount = codexSlots.filter((slot) => slot.status === "running").length;
  const codexPendingCount = codexSlots.filter((slot) => slot.status === "pending").length;
  const codexIdleCount = codexSlots.filter((slot) => slot.status === "idle").length;
  const codexSlotCard = document.createElement("article");
  codexSlotCard.className = "sys-card codex-slot-card";
  codexSlotCard.innerHTML = `
    <div class="sys-card-head">
      <span class="sys-icon">▦</span>
      <strong>Codex Autoworker Slots</strong>
      <span class="chip">active ${codexActiveCount}</span>
      <span class="chip">pending ${codexPendingCount}</span>
      <span class="chip">idle ${codexIdleCount}</span>
      <span class="chip">total ${codexSlots.length}</span>
    </div>
    <div class="codex-slot-grid">
      ${codexSlots.map((slot) => `
        <div class="codex-slot-row codex-slot-${slot.status}">
          <div class="codex-slot-name">
            <strong>${escapeHtml(slot.label)}</strong>
            <span class="chip">${escapeHtml(slot.quota_group)}</span>
          </div>
          <div class="codex-slot-meta">
            <span class="status-pill ${slot.status === "running" ? "status-working" : slot.status === "pending" ? "status-review" : "status-idle"}">${slot.status === "idle" ? "idle" : slot.worker_status || slot.status}</span>
            <span class="chip">${escapeHtml(slot.id)}</span>
            ${slot.worker?.provider ? `<span class="chip">${escapeHtml(slot.worker.provider)}</span>` : ""}
            ${slot.task_id ? `<span class="chip">${escapeHtml(slot.task_id)}</span>` : `<span class="chip">空 slot</span>`}
            ${slot.worker?.reason ? `<span class="chip">${escapeHtml(slot.worker.reason)}</span>` : ""}
            ${slot.last_event_at ? `<span class="chip">${escapeHtml(timeAgo(slot.last_event_at))}</span>` : ""}
          </div>
        </div>
      `).join("")}
    </div>
  `;
  statusEl.appendChild(codexSlotCard);

  const approvalCard = document.createElement("article");
  approvalCard.className = "sys-card";
  approvalCard.innerHTML = `
    <div class="sys-card-head"><span class="sys-icon">⏳</span><strong>待批准佇列</strong></div>
    <div class="sys-card-body">
      <span class="status-pill ${pending > 0 ? "status-review" : "status-done"}">${pending > 0 ? `${pending} 個待處理` : "清空"}</span>
      ${(approvalQueue?.pending || []).map((a) => {
        const chips = [
          `<span class="chip">${escapeHtml(actorLabel(a.agent_id, a.provider))}</span>`,
          `<span class="chip">task=${escapeHtml(a.task_id || "-")}</span>`,
          `<span class="chip">${escapeHtml(a.tool_name || "-")}</span>`,
          a.risk_class ? `<span class="chip">${escapeHtml(a.risk_class)}</span>` : "",
          `<span class="chip">${escapeHtml(timeAgo(a.created_at))}</span>`,
          a.tool_input_preview ? `<span class="chip">input=${escapeHtml(a.tool_input_preview)}</span>` : "",
          a.evidence_ref ? `<span class="chip">evidence=${escapeHtml(a.evidence_ref)}</span>` : "",
        ].filter(Boolean).join("");
        return `
        <div class="approval-item">
          ${chips}
        </div>
      `;
      }).join("")}
    </div>
  `;
  statusEl.appendChild(approvalCard);

  if (pausedProviders.length) {
    const pauseCard = document.createElement("article");
    pauseCard.className = "sys-card";
    pauseCard.innerHTML = `
      <div class="sys-card-head"><span class="sys-icon">⛔</span><strong>Provider Guardrails</strong></div>
      <div class="sys-card-body">
        <span class="status-pill status-blocked">${pausedProviders.length} 個 lane 暫停派工</span>
        ${pausedProviders.map((entry) => `
          <div class="approval-item">
            <span class="chip">${agentLabel(entry.provider)}</span>
            <span class="chip">until ${formatTime(entry.blocked_until)}</span>
            ${entry.task_id ? `<span class="chip">task=${entry.task_id}</span>` : ""}
          </div>
        `).join("")}
      </div>
    `;
    statusEl.appendChild(pauseCard);
  }

  const agentStateMap = new Map((agentStates || []).map((a) => [normalizedProviderKey(a.name), a]));
  const pauseMap = new Map(pausedProviders.map((entry) => [normalizedProviderKey(entry.provider), entry]));
  const runtimeAgentIds = Array.from(new Set([
    ...logicalAgents,
    ...(agentStates || []).map((agent) => normalizedProviderKey(agent.name)),
    ...workers.map((worker) => normalizedProviderKey(worker.logical_agent_id || worker.provider || worker.agent_id)),
    ...pausedProviders.map((entry) => normalizedProviderKey(entry.provider)),
  ].filter(Boolean)));
  for (const agentId of runtimeAgentIds) {
    const pw = workers.filter((w) => normalizedProviderKey(w.logical_agent_id || w.provider || w.agent_id) === agentId);
    const running = pw.filter((w) => w.bucket === "running").length;
    const waiting = pw.filter((w) => w.bucket === "pending").length;
    const transition = pw.filter((w) => w.bucket === "transition").length;
    const failed = pw.filter((w) => w.status === "failed").length;
    const completed = pw.filter((w) => w.bucket === "completed").length;
    const agent = agentStateMap.get(agentId);
    const runningTasks = pw.filter((w) => w.bucket === "running").map((w) => w.task_id).filter(Boolean);
    const pause = pauseMap.get(agentId);
    const readyCount = agent?.ready_count || 0;
    const availability = agentRuntimeAvailability(agentId, { pause, running, failed, readyCount });
    const pauseReason = pause ? summarizePausedReason(pause.summary || pause.reason || pause.detail, pause.provider) : null;
    const card = document.createElement("article");
    card.className = `sys-card agent-runtime-card ${availability.className}`;
    card.innerHTML = `
      <div class="sys-card-head">
        <span class="sys-icon">${availability.icon}</span>
        <strong>${agentLabel(agentId)}</strong>
        <span class="status-pill ${availability.pillClass}">${availability.label}</span>
      </div>
      <div class="sys-card-body">
        <span class="chip ${availability.canAccept ? "agent-can-accept" : "agent-cannot-accept"}">${availability.canAccept ? "可接新工作" : "不可接新工作"}</span>
        <span class="chip">執行中 ${running}</span>
        <span class="chip">等待 ${waiting}</span>
        ${transition ? `<span class="chip">改派 ${transition}</span>` : ""}
        <span class="chip">失敗 ${failed}</span>
        <span class="chip">完成 ${completed}</span>
        ${runningTasks.length ? `<span class="chip">任務 ${runningTasks.join(", ")}</span>` : ""}
        ${agent ? `<span class="chip">可開工 ${readyCount}</span><span class="chip">等前置 ${agent.waiting_count || 0}</span>` : ""}
        ${pause ? `<span class="chip status-blocked">暫停到 ${formatTime(pause.blocked_until)}</span>` : ""}
        ${pause?.task_id ? `<span class="chip status-blocked">卡在 ${pause.task_id}</span>` : ""}
        ${pauseReason ? `<span class="chip status-blocked">${escapeHtml(pauseReason.summary)}</span>` : ""}
        ${pause?.raw_ref ? `<span class="chip">evidence ${escapeHtml(pause.raw_ref)}</span>` : ""}
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
                  ${w.reason ? `<span class="chip">${escapeHtml(w.reason)}</span>` : ""}
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

export function renderProgressBar(tasks, dashboardBundle = null) {
  const total = (tasks || []).length;
  const done = (tasks || []).filter((t) => t.status === "done").length;
  const approved = (tasks || []).filter((t) => t.status === "review_approved").length;
  const open = (tasks || []).filter((t) => ["todo", "in_progress", "review", "blocked"].includes(String(t.status || "").toLowerCase())).length;
  const archiveCounts = dashboardBundle?.archive_summary?.counts || {};
  const bridgeSummary = dashboardBundle?.bridge_summary || {};
  const archivedTotal = Number.isFinite(archiveCounts.total) ? archiveCounts.total : 0;
  const archivedDone = Number.isFinite(archiveCounts.completed) ? archiveCounts.completed : 0;
  const completedInSprint = Number.isFinite(archiveCounts.completed_in_sprint) ? archiveCounts.completed_in_sprint : 0;
  const pendingBridge = Number.isFinite(bridgeSummary.pending_materialization_count) ? bridgeSummary.pending_materialization_count : 0;
  const sprintDone = completedInSprint + done;
  const sprintTotal = sprintDone + open + approved;
  const pct = sprintTotal ? Math.round((sprintDone / sprintTotal) * 100) : 0;
  const label = qs("#progress-label");
  const fill = qs("#progress-fill");
  if (label) {
    label.textContent = open === 0 && archivedTotal
      ? `Active board 已清空 · Archive completed ${archivedDone} / ${archivedTotal} · Pending bridge ${pendingBridge}`
      : `Sprint 進度：本輪完成 ${sprintDone} / ${sprintTotal} (${pct}%) · 待收尾 ${approved} · 其他 open ${open}`;
  }
  if (fill) fill.style.width = open === 0 && archivedTotal ? "100%" : `${pct}%`;
}

export function renderActivity(entries) {
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
    ...planningHighSignalTypes,
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
        <strong>${entry.agent || entry.provider || entry.actor || "-"}</strong>
        <span class="activity-meta">${timeAgo(entry.ts || entry.updated_at)}</span>
      </div>
      <p class="activity-message">${msg}</p>
      <div class="lane-meta">
        <span class="chip">${typeLabel}</span>
        <span class="chip">${entry.task_id || entry.issue_id || (entry.round ? `round ${entry.round}` : "-")}</span>
      </div>
    `;
    container.appendChild(card);
  }
}

function safeReadStorage(key) {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeWriteStorage(key, value) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // Ignore storage failures in restricted browser contexts.
  }
}

function planningHasRenderableActivity(planning) {
  const counts = planning.counts || {};
  const proposed = planning.proposed_execution_tasks || [];
  const hasResolvedReadouts = Number(counts.readouts_resolved || 0) > 0;
  const hasRounds = Number(counts.rounds_total || 0) > 0;
  const hasOpenItems = Number(counts.open_items || 0) > 0;
  const hasProposals = proposed.length > 0;
  const consensusTouched = !["not_started", "accepted"].includes(String(planning.consensus_status || ""));
  const humanGateActive = ["pending", "rejected"].includes(String(planning.human_gate_status || ""));
  return ["active", "human_required"].includes(String(planning.status || "")) || hasResolvedReadouts || hasRounds || hasOpenItems || hasProposals || consensusTouched || humanGateActive;
}

function dashboardFocusMode(planning) {
  if (["active", "human_required"].includes(String(planning.status || ""))) {
    return "planning";
  }
  if (String(planning.runtime_mode || "") === "supervisor_managed_execution") {
    return "execution";
  }
  return "execution";
}

const LOVABLE_STAGE_LABEL = {
  needs_ui: "等待 Lovable 實作",
  contract_ready: "Contract Ready",
  waiting_for_lovable: "等待 Lovable 回傳",
  ui_done_received: "UI Done — 待整合",
  bff_gap_open: "BFF Gap 待解",
  frontend_feedback_received: "Feedback 收到",
  frontend_feedback_reviewed_followup: "Feedback reviewed / follow-up",
  loop_complete: "loop-complete",
  done: "已完成",
};
const LOVABLE_STAGE_TONE = {
  needs_ui: "",
  contract_ready: "",
  waiting_for_lovable: "",
  ui_done_received: "card-review",
  bff_gap_open: "card-blocked",
  frontend_feedback_received: "card-active",
  frontend_feedback_reviewed_followup: "card-review",
  loop_complete: "card-done",
  done: "card-done",
};

function deriveLovableStage(f) {
  const latestReq = f.latest_request || {};
  const reqType = String(latestReq.type || "").replace(/-/g, "_");
  if (reqType === "frontend_feedback") return "frontend_feedback_received";
  if (reqType === "ui_done") return "ui_done_received";
  const bffGapType = String((f.latest_request || {}).type || "").replace(/-/g, "_");
  if (bffGapType === "bff_gap" && !(f.latest_request || {}).resolved) return "bff_gap_open";
  if (f.lovable_task || f.lovable_task_path) return "waiting_for_lovable";
  const ct = String(f.current_payload_type || f.status || "").replace(/-/g, "_");
  if (ct === "contract_ready") return "contract_ready";
  return "needs_ui";
}

export function renderLovableCoordination(orchState, status, dashboardBundle = null) {
  const overview = qs("#lovable-overview");
  const featureList = qs("#lovable-features");
  const panelSummary = qs("#lovable-panel-summary");
  if (!overview || !featureList) return;

  const coord = orchState?.coordination || {};
  const rawFeatures = coord.features || {};
  const bundledFeatures = dashboardBundle?.coordination_summary?.features;
  const features = Array.isArray(bundledFeatures) && bundledFeatures.length
    ? bundledFeatures
    : Object.values(rawFeatures);

  // Build summary counts
  const counts = {
    needs_ui: 0,
    contract_ready: 0,
    waiting_for_lovable: 0,
    ui_done_received: 0,
    bff_gap_open: 0,
    frontend_feedback_received: 0,
    frontend_feedback_reviewed_followup: 0,
    loop_complete: 0,
    done: 0,
  };
  for (const f of features) {
    const stage = f.stage || deriveLovableStage(f);
    const key = Object.prototype.hasOwnProperty.call(counts, stage) ? stage : "needs_ui";
    counts[key]++;
  }

  // Map coordination tasks from status.tasks to features
  const coordTasks = (status?.tasks || []).filter((t) => {
    const meta = t.coordination || t.metadata?.coordination || {};
    return meta.feature_id || String(t.task_class || "") === "coordination";
  });

  // Overview metrics
  const metricItems = [
    { label: "協調紀錄", value: features.length, tone: "" },
    { label: "Loop Complete", value: counts.loop_complete + counts.done, tone: "card-done" },
    { label: "Follow-up", value: counts.frontend_feedback_reviewed_followup, tone: counts.frontend_feedback_reviewed_followup ? "card-review" : "" },
    { label: "BFF Gap", value: counts.bff_gap_open, tone: counts.bff_gap_open ? "card-blocked" : "" },
    { label: "整合任務", value: coordTasks.length, tone: coordTasks.length ? "card-active" : "" },
    { label: "等待前端", value: counts.needs_ui + counts.contract_ready + counts.waiting_for_lovable + counts.ui_done_received + counts.frontend_feedback_received, tone: "" },
  ];
  overview.innerHTML = metricItems.map((m) => `
    <article class="metric-card ${m.tone}">
      <div class="metric-label">${m.label}</div>
      <div class="metric-value">${m.value}</div>
    </article>
  `).join("");

  // Panel summary
  if (panelSummary) {
    const pending = counts.frontend_feedback_reviewed_followup + counts.bff_gap_open + counts.needs_ui + counts.contract_ready + counts.waiting_for_lovable;
    panelSummary.textContent = pending ? `${pending} follow-up / pending` : features.length ? `${counts.loop_complete + counts.done}/${features.length} loop-complete` : "無追蹤項目";
  }

  if (!features.length) {
    featureList.innerHTML = `<p class="empty">目前沒有追蹤中的 Lovable feature。</p>`;
    return;
  }

  featureList.innerHTML = features.map((f) => {
    const stage = f.stage || deriveLovableStage(f);
    const tone = LOVABLE_STAGE_TONE[stage] || "";
    const stageLabel = LOVABLE_STAGE_LABEL[stage] || coordinationStageLabel(stage);
    const featureId = escapeHtml(f.feature_id || "-");
    const screen = escapeHtml(f.screen || "-");
    const nextAction = escapeHtml(f.next_action || "");

    // Find linked execution task(s)
    const linked = coordTasks.filter((t) => {
      const meta = t.coordination || t.metadata?.coordination || {};
      return meta.feature_id === f.feature_id;
    });
    const linkedHtml = linked.length
      ? linked.map((t) => `<span class="chip">${escapeHtml(t.id)} · ${escapeHtml(t.status)}</span>`).join(" ")
      : "";

    return `
      <article class="stack-card ${tone}">
        <div class="stack-head">
          <strong>${featureId}</strong>
          <span class="status-pill">${stageLabel}</span>
        </div>
        <p class="card-copy">畫面：${screen}</p>
        ${nextAction ? `<p class="card-copy">${nextAction}</p>` : ""}
        ${linkedHtml ? `<div class="lane-meta">${linkedHtml}</div>` : ""}
      </article>
    `;
  }).join("");
}

export function applyModeVisibility(status, planningState) {
  const planning = normalizePlanningState(planningState);
  const planningPanel = qs("#planning-panel");
  const planningSummary = qs("#planning-panel-summary");
  const planningToggle = qs("#planning-panel-toggle");
  const executionShell = qs("#execution-shell");
  const executionToggle = qs("#execution-panel-toggle");
  if (!planningPanel || !planningSummary || !planningToggle || !executionShell || !executionToggle) return;

  const readoutTotal = Object.keys(planning.readouts || {}).length;
  const focusMode = dashboardFocusMode(planning);
  const hasPlanningActivity = planningHasRenderableActivity(planning);
  const shouldHidePlanning = !hasPlanningActivity;
  const defaultCollapsed = !shouldHidePlanning && focusMode !== "planning";
  const preferenceKey = `dashboard:panel:planning:${planning.session_id || "default"}`;
  const storedPreference = safeReadStorage(preferenceKey);
  const collapsed = shouldHidePlanning
    ? true
    : focusMode === "planning"
      ? false
      : true; // execution mode: always start collapsed, ignore stored preference

  planningPanel.hidden = shouldHidePlanning;
  planningPanel.classList.toggle("mode-collapsed", collapsed);
  planningToggle.dataset.preferenceKey = preferenceKey;
  planningToggle.textContent = collapsed ? "展開" : "收合";
  planningToggle.setAttribute("aria-expanded", String(!collapsed));
  planningToggle.hidden = shouldHidePlanning;

  const summaryChips = [
    `<span class="chip">${focusMode === "planning" ? "目前焦點" : "非目前焦點"}</span>`,
    `<span class="chip">${planning.session_id || "no-session"}</span>`,
    `<span class="chip">Phase ${planning.phase || "-"}</span>`,
    `<span class="chip">Session ${statusLabel(planning.status)}</span>`,
    `<span class="chip">Consensus ${statusLabel(planning.consensus_status)}</span>`,
    `<span class="chip">Can materialize ${planning.switch_gate?.ready_to_materialize ? "Yes" : "No"}</span>`,
    `<span class="chip">Issues ${planning.counts.open_items || 0}</span>`,
    `<span class="chip">Proposals ${(planning.proposed_execution_tasks || []).length}</span>`,
    `<span class="chip">Readouts ${planning.counts.readouts_resolved || 0}/${readoutTotal} resolved</span>`,
  ];
  if ((planning.counts.rounds_total || 0) > 0 || String(planning.status || "") !== "inactive") {
    summaryChips.push(`<span class="chip">Rounds ${planning.counts.rounds_total || 0}</span>`);
  }
  planningSummary.innerHTML = summaryChips.join("");

  const hasOpenExecutionWork = (status.tasks || []).some((task) => !terminalTaskStatus(task.status));
  const executionPreferenceKey = "dashboard:panel:execution";
  const storedExecutionPreference = safeReadStorage(executionPreferenceKey);
  const executionCollapsed = focusMode === "planning"
    ? (storedExecutionPreference ? storedExecutionPreference === "collapsed" : true)
    : hasOpenExecutionWork
      ? false
      : (storedExecutionPreference ? storedExecutionPreference === "collapsed" : false);

  executionShell.classList.toggle("mode-collapsed", executionCollapsed);
  executionToggle.textContent = executionCollapsed ? "展開" : "收合";
  executionToggle.setAttribute("aria-expanded", String(!executionCollapsed));
  executionToggle.dataset.preferenceKey = executionPreferenceKey;

  if (!planningToggle.dataset.bound) {
    planningToggle.dataset.bound = "true";
    planningToggle.addEventListener("click", () => {
      const nextCollapsed = !planningPanel.classList.contains("mode-collapsed");
      const activePreferenceKey = planningToggle.dataset.preferenceKey || preferenceKey;
      planningPanel.classList.toggle("mode-collapsed", nextCollapsed);
      planningToggle.textContent = nextCollapsed ? "展開" : "收合";
      planningToggle.setAttribute("aria-expanded", String(!nextCollapsed));
      safeWriteStorage(activePreferenceKey, nextCollapsed ? "collapsed" : "expanded");
    });
  }

  if (!executionToggle.dataset.bound) {
    executionToggle.dataset.bound = "true";
    executionToggle.addEventListener("click", () => {
      const nextCollapsed = !executionShell.classList.contains("mode-collapsed");
      const activePreferenceKey = executionToggle.dataset.preferenceKey || executionPreferenceKey;
      executionShell.classList.toggle("mode-collapsed", nextCollapsed);
      executionToggle.textContent = nextCollapsed ? "展開" : "收合";
      executionToggle.setAttribute("aria-expanded", String(!nextCollapsed));
      safeWriteStorage(activePreferenceKey, nextCollapsed ? "collapsed" : "expanded");
    });
  }
}


export function renderBffConsolidationTrack(status, dashboardBundle = null) {
  const section = qs("#bff-consol-section");
  const container = qs("#bff-consol-waves");
  const summaryEl = qs("#bff-consol-summary");
  if (!container) return;

  const waves = [
    { label: "Wave 1 — Ground truth & spec", range: [1, 7] },
    { label: "Wave 2 — Read depth & realtime & auth", range: [8, 15] },
    { label: "Wave 3 — Detail journey & command adapter", range: [16, 22] },
    { label: "Wave 4 — Cutover & cleanup", range: [23, 27] },
  ];

  const activeMap = new Map();
  for (const t of status?.tasks || []) {
    if (String(t.id || "").startsWith("BFF-CONSOL-") && /^BFF-CONSOL-\d{3}$/.test(t.id)) {
      activeMap.set(t.id, t);
    }
  }
  const archivedIdsArr = dashboardBundle?.archive_summary?.bff_consol_archived_ids || [];
  const archivedIds = new Set(archivedIdsArr);
  const recentTerminal = dashboardBundle?.archive_summary?.recent_terminal_tasks || [];
  const recentOutcome = new Map();
  for (const rec of recentTerminal) {
    if (rec.task_id) recentOutcome.set(rec.task_id, rec.terminal_outcome || "completed");
  }

  let anyKnown = false;
  const totals = { done: 0, review: 0, review_approved: 0, in_progress: 0, todo: 0, blocked: 0, superseded: 0, unknown: 0 };
  const html = waves.map((wave) => {
    const ids = [];
    for (let n = wave.range[0]; n <= wave.range[1]; n += 1) {
      ids.push(`BFF-CONSOL-${String(n).padStart(3, "0")}`);
    }
    const states = ids.map((id) => {
      const active = activeMap.get(id);
      if (active) {
        anyKnown = true;
        return { id, status: String(active.status || "todo").toLowerCase(), title: active.title || active.summary_zh || "" };
      }
      if (archivedIds.has(id)) {
        anyKnown = true;
        const outcome = recentOutcome.get(id);
        return { id, status: outcome === "superseded" ? "superseded" : "done", title: "" };
      }
      return { id, status: "unknown", title: "" };
    });
    const completed = states.filter((s) => s.status === "done" || s.status === "superseded").length;
    const pct = states.length ? Math.round((completed / states.length) * 100) : 0;
    for (const s of states) totals[s.status] = (totals[s.status] || 0) + 1;
    const taskHtml = states.map((s) => {
      const num = s.id.replace("BFF-CONSOL-", "");
      const label = s.status === "unknown" ? "未排入" : statusLabel(s.status);
      return `
        <div class="bff-consol-task">
          <span class="bff-consol-task-id">${num}</span>
          <span class="bff-consol-task-pill ${s.status}">${escapeHtml(label)}</span>
          <span class="bff-consol-task-title">${s.title ? escapeHtml(s.title.slice(0, 80)) : ""}</span>
        </div>
      `;
    }).join("");
    return `
      <section class="bff-consol-wave">
        <div class="bff-consol-wave-head">
          <strong>${escapeHtml(wave.label)}</strong>
          <span class="chip">${completed}/${states.length} (${pct}%)</span>
        </div>
        <div class="bff-consol-progress-bar"><div class="bff-consol-progress-fill" style="width:${pct}%"></div></div>
        <div class="bff-consol-task-list">${taskHtml}</div>
      </section>
    `;
  }).join("");

  if (section) {
    if (anyKnown) section.removeAttribute("hidden");
    else section.setAttribute("hidden", "");
  }
  container.innerHTML = html;
  if (summaryEl) {
    const totalCompleted = totals.done + totals.superseded;
    const totalAll = 27;
    summaryEl.innerHTML = `<span class="chip">完成 ${totalCompleted}/${totalAll}</span><span class="chip">進行 ${totals.in_progress}</span><span class="chip">審查 ${totals.review + totals.review_approved}</span><span class="chip">待開始 ${totals.todo}</span>${totals.blocked ? `<span class="chip">阻塞 ${totals.blocked}</span>` : ""}`;
  }
}
