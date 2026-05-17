import {
  activeTaskStatuses,
  integrationPrefixes,
  laneLabelMap,
  scheduleOpenTaskStatuses,
  statusLabelMap,
} from "./dashboard-config.js?v=20260517-audit";

export const DISPLAY_TIME_ZONE = "Asia/Taipei";
export const DISPLAY_TIME_ZONE_LABEL = "台灣時間 (UTC+8)";

export const qs = (selector) => document.querySelector(selector);

export function defaultPlanningState() {
  return {
    session_id: "phase1-bootstrap",
    status: "inactive",
    planning_mode: "discussion_planning",
    runtime_mode: "supervisor_managed_execution",
    facilitator: "Claude",
    baton_owner: "Codex",
    starter_owner: "Codex",
    next_reviewer: "Codex2",
    current_round: 0,
    consensus_status: "not_started",
    human_gate_status: "not_requested",
    artifacts: {},
    readouts: {},
    cross_review_rounds: [],
    unresolved_items: [],
    switch_gate: {
      all_readouts_submitted: false,
      cross_review_round_present: false,
      divergence_resolved_or_escalated: true,
      consensus_packet_drafted: false,
      human_approved: false,
      ready_for_human: false,
      ready_to_materialize: false,
    },
    proposed_execution_tasks: [],
    recent_events: [],
    counts: {
      readouts_submitted: 0,
      readouts_resolved: 0,
      rounds_total: 0,
      open_items: 0,
      proposed_execution_tasks: 0,
    },
  };
}

export function defaultDashboardBundle() {
  return {
    generated_at: null,
    focus_mode: "execution",
    runtime_summary: {
      supervisor_pid: null,
      heartbeat_at: null,
      queue_depth: 0,
      pending_approvals: 0,
      running_workers: 0,
      pending_workers: 0,
      mismatch_count: 0,
      mode_occupancy: {
        planning: { running: 0, pending: 0, queued: 0 },
        execution: { running: 0, pending: 0, queued: 0 },
        coordination: { running: 0, pending: 0, queued: 0 },
        chair_review: { running: 0, pending: 0, queued: 0 },
      },
      lanes: {},
      dispatch_targets: {},
    },
    execution_summary: {
      ready_now: 0,
      dependency_ready: 0,
      in_progress: 0,
      in_review: 0,
      blocked: 0,
      review_approved: 0,
      done: 0,
      live_attached: 0,
      mismatch_count: 0,
    },
    planning_summary: {
      status: "inactive",
      consensus_status: "not_started",
      human_gate_status: "not_requested",
      counts: {},
      materialized_count: 0,
      proposed_execution_tasks: 0,
    },
    coordination_summary: {
      last_scan_at: null,
      counts: {
        tracked_features: 0,
        lovable_ready: 0,
        mirrored_to_target_repo: 0,
        waiting_for_lovable: 0,
        ui_done_received: 0,
        frontend_feedback_received: 0,
        open_bff_gaps: 0,
      },
      features: [],
    },
    bridge_summary: {
      source_plane: "planning",
      session_id: null,
      phase: null,
      profile: null,
      planning_dir: null,
      session_file: null,
      consensus_packet: null,
      execution_materialization: null,
      proposed_total: 0,
      materialized_count: 0,
      pending_materialization_count: 0,
      done: 0,
      review_approved: 0,
      in_progress: 0,
      review: 0,
      todo: 0,
      blocked: 0,
      materialized_task_ids: [],
      pending_proposals: [],
      active_materialized_tasks: [],
      planning_backed_total: 0,
      planning_backed_active: 0,
      current_session_materialized: 0,
      missing_source_ref_count: 0,
    },
    chair_summary: {
      current_index: 0,
      last_chair_agent: null,
      last_chair_run_at: null,
      last_chair_reason: null,
      last_review_path: null,
      last_review_summary: [],
      pending_review_path: null,
      pending_review_agent: null,
      sidecar_approved_until: null,
    },
    dispatch_policy: {
      mode: "supervisor_owned_dispatch",
      helper_claim_enabled: true,
      claim_idle_work: false,
      claim_sidecars_when_idle: false,
      require_owner_higher_priority_load: true,
      owned_work_first: true,
      max_dispatches_per_tick: 4,
      max_tasks_per_agent: 1,
      sidecar_only_agents: [],
      disabled_agents: [],
    },
    recent_helper_claims: [],
    worker_task_links: [],
    truth_mismatches: [],
  };
}

export function normalizeDashboardBundle(value) {
  const base = defaultDashboardBundle();
  if (!value || typeof value !== "object") return base;
  return {
    ...base,
    ...value,
    runtime_summary: { ...base.runtime_summary, ...(value.runtime_summary || {}) },
    execution_summary: { ...base.execution_summary, ...(value.execution_summary || {}) },
    planning_summary: { ...base.planning_summary, ...(value.planning_summary || {}) },
    coordination_summary: {
      ...base.coordination_summary,
      ...(value.coordination_summary || {}),
      counts: { ...base.coordination_summary.counts, ...((value.coordination_summary || {}).counts || {}) },
      features: Array.isArray((value.coordination_summary || {}).features) ? value.coordination_summary.features : [],
    },
    bridge_summary: { ...base.bridge_summary, ...(value.bridge_summary || {}) },
    chair_summary: { ...base.chair_summary, ...(value.chair_summary || {}) },
    dispatch_policy: {
      ...base.dispatch_policy,
      ...(value.dispatch_policy || {}),
      sidecar_only_agents: Array.isArray((value.dispatch_policy || {}).sidecar_only_agents)
        ? value.dispatch_policy.sidecar_only_agents
        : base.dispatch_policy.sidecar_only_agents,
      disabled_agents: Array.isArray((value.dispatch_policy || {}).disabled_agents)
        ? value.dispatch_policy.disabled_agents
        : base.dispatch_policy.disabled_agents,
    },
    recent_helper_claims: Array.isArray(value.recent_helper_claims) ? value.recent_helper_claims : [],
    worker_task_links: Array.isArray(value.worker_task_links) ? value.worker_task_links : [],
    truth_mismatches: Array.isArray(value.truth_mismatches) ? value.truth_mismatches : [],
  };
}

export function normalizePlanningState(value) {
  const base = defaultPlanningState();
  if (!value || typeof value !== "object") {
    return base;
  }
  return {
    ...base,
    ...value,
    artifacts: { ...base.artifacts, ...(value.artifacts || {}) },
    readouts: { ...base.readouts, ...(value.readouts || {}) },
    switch_gate: { ...base.switch_gate, ...(value.switch_gate || {}) },
    counts: { ...base.counts, ...(value.counts || {}) },
    cross_review_rounds: Array.isArray(value.cross_review_rounds) ? value.cross_review_rounds : [],
    unresolved_items: Array.isArray(value.unresolved_items) ? value.unresolved_items : [],
    proposed_execution_tasks: Array.isArray(value.proposed_execution_tasks) ? value.proposed_execution_tasks : [],
    recent_events: Array.isArray(value.recent_events) ? value.recent_events : [],
  };
}

export function statusLabel(value) {
  return statusLabelMap[value] || value || "-";
}

export function laneLabel(value) {
  return laneLabelMap[value] || value;
}

export function normalizeReviewNotes(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? [trimmed] : [];
  }
  return [];
}

export function providerLabel(value) {
  if (!value) return "-";
  const normalized = String(value).toLowerCase().replace(/_/g, "-");
  if (/^codex[12]-[1-4]$/.test(normalized)) return normalized;
  if (value === "copilot") return "GitHub Copilot";
  return value;
}

export function titleCase(value) {
  return String(value || "")
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function agentLabel(value) {
  if (!value) return "-";
  const normalized = String(value).toLowerCase().replace(/-/g, "_");
  if (normalized === "claude") return "Claude";
  if (normalized === "claude2") return "Claude (2)";
  if (normalized === "gemini") return "Gemini";
  if (normalized === "gemini2") return "Gemini (2)";
  if (normalized === "codex") return "Codex";
  if (normalized === "codex2") return "Codex (2)";
  if (/^codex1_[1-4]$/.test(normalized)) return "Codex";
  if (/^codex2_[1-4]$/.test(normalized)) return "Codex (2)";
  if (normalized === "grok") return "Copilot";
  if (normalized === "copilot") return "Copilot";
  return value;
}

export function actorLabel(agentId, provider) {
  const agent = agentLabel(agentId);
  const host = providerLabel(provider);
  const normalizedProvider = String(provider || "").toLowerCase().replace(/_/g, "-");
  if (/^codex[12]-[1-4]$/.test(normalizedProvider)) {
    return `${agent} · ${normalizedProvider}`;
  }
  if (agentId && provider && String(agentId).toLowerCase() !== String(provider).toLowerCase()) {
    return `${agent} (${host})`;
  }
  return agentId ? agent : host;
}

export function terminalTaskStatus(value) {
  return String(value || "").toLowerCase() === "done";
}

export function logicalWorkerAgentId(worker) {
  const explicit = String(worker?.logical_agent_id || "").trim().toLowerCase().replace(/-/g, "_");
  if (explicit) {
    if (/^codex1_[1-4]$/.test(explicit)) return "codex";
    if (/^codex2_[1-4]$/.test(explicit)) return "codex2";
    return explicit;
  }
  const candidates = [worker?.agent_id, worker?.target_agent, worker?.provider];
  for (const candidate of candidates) {
    const normalized = String(candidate || "").trim().toLowerCase().replace(/-/g, "_");
    if (!normalized) continue;
    if (["claude", "claude2", "gemini", "gemini2", "codex", "codex2"].includes(normalized)) return normalized;
    if (/^codex1_[1-4]$/.test(normalized)) return "codex";
    if (/^codex2_[1-4]$/.test(normalized)) return "codex2";
    if (["grok", "copilot"].includes(normalized)) return "copilot";
  }
  if (String(worker?.provider || "").toLowerCase() === "copilot") return "copilot";
  return String(worker?.agent_id || worker?.provider || "").trim().toLowerCase() || "unknown";
}

export const codexWorkerSlots = [
  { id: "codex1-1", agent_id: "codex1_1", logical_agent_id: "codex", quota_group: "codex1", account_label: "Codex1", auth_home: "~/.codex" },
  { id: "codex1-2", agent_id: "codex1_2", logical_agent_id: "codex", quota_group: "codex1", account_label: "Codex1", auth_home: "~/.codex" },
  { id: "codex1-3", agent_id: "codex1_3", logical_agent_id: "codex", quota_group: "codex1", account_label: "Codex1", auth_home: "~/.codex" },
  { id: "codex1-4", agent_id: "codex1_4", logical_agent_id: "codex", quota_group: "codex1", account_label: "Codex1", auth_home: "~/.codex" },
  { id: "codex2-1", agent_id: "codex2_1", logical_agent_id: "codex2", quota_group: "codex2", account_label: "Codex2", auth_home: "~/.codex2" },
  { id: "codex2-2", agent_id: "codex2_2", logical_agent_id: "codex2", quota_group: "codex2", account_label: "Codex2", auth_home: "~/.codex2" },
  { id: "codex2-3", agent_id: "codex2_3", logical_agent_id: "codex2", quota_group: "codex2", account_label: "Codex2", auth_home: "~/.codex2" },
  { id: "codex2-4", agent_id: "codex2_4", logical_agent_id: "codex2", quota_group: "codex2", account_label: "Codex2", auth_home: "~/.codex2" },
];

function normalizeCodexSlotId(value) {
  const normalized = String(value || "").trim().toLowerCase().replace(/_/g, "-");
  return /^codex[12]-[1-4]$/.test(normalized) ? normalized : "";
}

function codexSlotWorkerScore(worker) {
  const bucket = String(worker?.bucket || "").toLowerCase();
  const priority = bucket === "running" ? 3 : bucket === "pending" ? 2 : bucket === "transition" ? 1 : 0;
  const timestamp = Date.parse(worker?.last_event_at || worker?.updated_at || worker?.started_at || "") || 0;
  return [priority, timestamp];
}

function preferCodexSlotWorker(candidate, current) {
  if (!current) return true;
  const [candidatePriority, candidateTimestamp] = codexSlotWorkerScore(candidate);
  const [currentPriority, currentTimestamp] = codexSlotWorkerScore(current);
  if (candidatePriority !== currentPriority) return candidatePriority > currentPriority;
  return candidateTimestamp > currentTimestamp;
}

export function buildCodexSlotRoster(orchState, status) {
  const workers = normalizeWorkerRecords(orchState, status);
  const bySlot = new Map();
  for (const worker of workers) {
    const slotId = normalizeCodexSlotId(
      worker.dispatch_slot || worker.slot_id || worker.provider || worker.agent_id || worker.dispatch_slot_id
    );
    if (!slotId || !preferCodexSlotWorker(worker, bySlot.get(slotId))) continue;
    bySlot.set(slotId, worker);
  }
  return codexWorkerSlots.map((slot) => {
    const worker = bySlot.get(slot.id) || null;
    const active = worker && ["running", "pending"].includes(worker.bucket);
    return {
      ...slot,
      label: slot.id.replace(/^codex/, "Codex"),
      worker,
      status: active ? worker.bucket : "idle",
      task_id: active ? worker.task_id || null : null,
      worker_status: active ? worker.status || null : null,
      last_event_at: active ? worker.last_event_at || null : null,
    };
  });
}

export function normalizeWorkerRecords(orchState, status) {
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
      dispatch_mode: runtimeDispatchMode(worker),
      bucket,
      reason: worker?.reason || worker?.request_snapshot?.reason || null,
      display_actor: actorLabel(logicalAgentId, worker?.provider),
    };
  });

  return workers.sort((a, b) => (b.last_event_at || "").localeCompare(a.last_event_at || ""));
}

export function workerLifecycleBadge(worker) {
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

export function normalizeQueueEvents(orchState) {
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
        reason: event?.reason || worker.reason || worker.request_snapshot?.reason || worker.status || null,
        metadata: event?.metadata || worker.metadata || worker.request_snapshot?.metadata || null,
        last_event_at: event?.last_event_at || event?.processed_at || worker.last_event_at || event?.last_attempt_at || null,
      };
    });
  }

  return [];
}

export function normalizeDispatchQueue(orchState, status) {
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

function normalizedActorName(value) {
  const lowered = String(value || "").trim().toLowerCase();
  const aliases = {
    "claude 2": "claude2",
    "gemini 2": "gemini2",
    "codex (2)": "codex2",
    "codex 2": "codex2",
    "codex (3)": "codex",
    codex3: "codex",
    grok: "copilot",
    "copilot host": "copilot",
    copilot_host: "copilot",
  };
  return aliases[lowered] || lowered;
}

function runtimeDispatchMode(payload) {
  const explicit = String(payload?.dispatch_mode || "").trim();
  if (explicit) return explicit;
  const requestSnapshot = payload?.request_snapshot && typeof payload.request_snapshot === "object" ? payload.request_snapshot : {};
  const metadata = payload?.metadata && typeof payload.metadata === "object"
    ? payload.metadata
    : (requestSnapshot.metadata && typeof requestSnapshot.metadata === "object" ? requestSnapshot.metadata : {});
  if (metadata?.planning && typeof metadata.planning === "object" && Object.keys(metadata.planning).length) {
    return String(metadata.planning.mode || "discussion_planning");
  }
  if (metadata?.coordination && typeof metadata.coordination === "object" && Object.keys(metadata.coordination).length) {
    return "coordination";
  }
  if (metadata?.chair && typeof metadata.chair === "object" && Object.keys(metadata.chair).length) {
    return "chair_review";
  }
  const reason = String(payload?.reason || requestSnapshot.reason || "").trim();
  if (reason.startsWith("discussion_planning_")) return "discussion_planning";
  if (reason.startsWith("coordination:")) return "coordination";
  if (reason.startsWith("chair_review:")) return "chair_review";
  return "execution";
}

function expectedTaskActor(task) {
  const status = String(task?.status || "").toLowerCase();
  if (status === "review") return String(task?.reviewer || "").trim();
  return String(task?.owner || "").trim();
}

export function buildTruthMismatches(status, orchState, approvalQueue = null) {
  const taskMap = new Map((status?.tasks || []).map((task) => [task.id, task]));
  const workers = normalizeWorkerRecords(orchState, status);
  const queueEvents = normalizeDispatchQueue(orchState, status);
  const approvals = approvalQueue?.pending || [];
  const pendingApprovalTaskIds = new Set(
    approvals.map((approval) => String(approval?.task_id || "").trim()).filter(Boolean)
  );
  const pendingApprovalRunIds = new Set(
    approvals.map((approval) => String(approval?.worker_run_id || "").trim()).filter(Boolean)
  );
  const liveWorkers = workers.filter((worker) => ["running", "pending"].includes(worker.bucket));
  const liveWorkersByTask = new Map();
  const mismatches = [];
  const seen = new Set();

  const pushMismatch = (payload) => {
    const key = String(payload.id || `${payload.type}:${payload.task_id || payload.worker_run_id || payload.queue_event_id || payload.title}`);
    if (!key || seen.has(key)) return;
    seen.add(key);
    mismatches.push(payload);
  };

  const workerMatchesExpectedActor = (worker, expectedActor) => {
    const expected = normalizedActorName(expectedActor);
    const actual = normalizedActorName(agentLabel(worker.logical_agent_id));
    return Boolean(expected && actual && expected === actual);
  };

  const relatedLiveWorkerCoversTask = (task) => {
    const expectedActor = expectedTaskActor(task);
    if (!expectedActor) return false;
    const taskId = String(task?.id || "").trim();
    if (!taskId) return false;
    const parentId = String(task?.helper_parent || "").trim();
    if (parentId) {
      const parentWorkers = liveWorkersByTask.get(parentId) || [];
      if (parentWorkers.some((worker) => workerMatchesExpectedActor(worker, expectedActor))) return true;
    }
    for (const [relatedTaskId, relatedTask] of taskMap.entries()) {
      if (String(relatedTask?.helper_parent || "").trim() !== taskId) continue;
      const relatedWorkers = liveWorkersByTask.get(relatedTaskId) || [];
      if (relatedWorkers.some((worker) => workerMatchesExpectedActor(worker, expectedActor))) return true;
    }
    return false;
  };

  for (const worker of liveWorkers) {
    if (worker.task_id) {
      if (!liveWorkersByTask.has(worker.task_id)) liveWorkersByTask.set(worker.task_id, []);
      liveWorkersByTask.get(worker.task_id).push(worker);
    }

    if (!worker.task_id) {
      if (runtimeDispatchMode(worker) === "chair_review") {
        continue;
      }
      pushMismatch({
        id: `worker-without-task:${worker.run_id}`,
        type: "worker_without_task",
        severity: "medium",
        title: "Live worker 沒有綁到 task",
        summary: `${worker.display_actor} 的 worker 已在跑，但沒有 task_id。`,
        worker_run_id: worker.run_id,
        detected_at: worker.last_event_at || worker.started_at || null,
      });
      continue;
    }

    const task = taskMap.get(worker.task_id);
    if (!task) {
      if (["discussion_planning", "coordination", "chair_review"].includes(runtimeDispatchMode(worker))) {
        continue;
      }
      pushMismatch({
        id: `worker-task-missing:${worker.run_id}`,
        type: "worker_task_missing",
        severity: "high",
        title: "Live worker 指向不存在的 task",
        summary: `${worker.display_actor} 的 worker 綁到 ${worker.task_id}，但 task board 找不到這個 task。`,
        task_id: worker.task_id,
        worker_run_id: worker.run_id,
        detected_at: worker.last_event_at || worker.started_at || null,
      });
      continue;
    }

    const taskStatus = String(task.status || "").toLowerCase();
    const expectedActor = expectedTaskActor(task);
    const expectedNormalized = normalizedActorName(expectedActor);
    const actualNormalized = normalizedActorName(agentLabel(worker.logical_agent_id));

    if (expectedNormalized && actualNormalized && expectedNormalized !== actualNormalized) {
      pushMismatch({
        id: `worker-assignment:${worker.run_id}`,
        type: "worker_assignment_mismatch",
        severity: taskStatus === "review" ? "medium" : "high",
        title: "Live worker 與 task 指派對不上",
        summary: `${worker.task_id} 目前應由 ${expectedActor} 接手，但 live worker 來自 ${agentLabel(worker.logical_agent_id)}。`,
        task_id: worker.task_id,
        worker_run_id: worker.run_id,
        expected_actor: expectedActor,
        actual_actor: agentLabel(worker.logical_agent_id),
        detected_at: worker.last_event_at || worker.started_at || null,
      });
    }

    if (worker.bucket === "running" && taskStatus === "todo") {
      pushMismatch({
        id: `running-worker-on-todo:${worker.run_id}`,
        type: "running_worker_on_todo",
        severity: "medium",
        title: "Worker 已在跑，但 task 還是 todo",
        summary: `${worker.task_id} 有 live running worker，但 task status 仍是 todo。`,
        task_id: worker.task_id,
        worker_run_id: worker.run_id,
        detected_at: worker.last_event_at || worker.started_at || null,
      });
    }

    if (worker.bucket === "running" && taskStatus === "done") {
      pushMismatch({
        id: `running-worker-on-done:${worker.run_id}`,
        type: "running_worker_on_done",
        severity: "high",
        title: "Task 已完成，但 worker 仍在跑",
        summary: `${worker.task_id} 已是 done，但還有 live running worker。`,
        task_id: worker.task_id,
        worker_run_id: worker.run_id,
        detected_at: worker.last_event_at || worker.started_at || null,
      });
    }
  }

  for (const task of status?.tasks || []) {
    const taskStatus = String(task.status || "").toLowerCase();
    const live = liveWorkersByTask.get(task.id) || [];
    if (pendingApprovalTaskIds.has(String(task.id || "").trim())) {
      continue;
    }
    if (taskStatus === "in_progress" && live.length === 0) {
      if (relatedLiveWorkerCoversTask(task)) {
        continue;
      }
      pushMismatch({
        id: `active-task-without-worker:${task.id}`,
        type: "active_task_without_worker",
        severity: "medium",
        title: "Active task 沒有 live worker",
        summary: `${task.id} 在 task board 上是 ${statusLabel(task.status)}，但目前沒有對應的 live worker。`,
        task_id: task.id,
        expected_actor: expectedTaskActor(task),
        detected_at: task.last_update || null,
      });
    }
  }

  for (const event of queueEvents) {
    const hasLiveWorker = liveWorkers.some((worker) => worker.queue_event_id === event.id);
    if (["discussion_planning", "coordination", "chair_review"].includes(runtimeDispatchMode(event))) {
      continue;
    }
    if (
      pendingApprovalRunIds.has(String(event.run_id || "").trim())
      || pendingApprovalTaskIds.has(String(event.task_id || "").trim())
    ) {
      continue;
    }
    if (["started", "manual_pending"].includes(String(event.status || "").toLowerCase()) && !hasLiveWorker) {
      pushMismatch({
        id: `queue-without-worker:${event.id}`,
        type: "queue_started_without_worker",
        severity: "medium",
        title: "Queue record 已啟動，但找不到 live worker",
        summary: `${event.task_id || event.id} 的 queue record 已是 ${event.status}，但 runtime 沒有對應 worker。`,
        task_id: event.task_id || null,
        queue_event_id: event.id,
        detected_at: event.last_event_at || event.last_attempt_at || event.processed_at || null,
      });
    }
  }

  for (const approval of approvals) {
    if (approval?.task_id && !taskMap.has(approval.task_id)) {
      pushMismatch({
        id: `approval-missing-task:${approval.id || approval.approval_id || approval.task_id}`,
        type: "approval_missing_task",
        severity: "medium",
        title: "Approval queue 指向不存在的 task",
        summary: `待批准項目 ${approval.task_id} 已不在 task board 中。`,
        task_id: approval.task_id,
        detected_at: approval.created_at || null,
      });
    }
  }

  const severityOrder = { high: 0, medium: 1, low: 2 };
  mismatches.sort((a, b) => {
    const bySeverity = (severityOrder[a.severity] ?? 9) - (severityOrder[b.severity] ?? 9);
    if (bySeverity !== 0) return bySeverity;
    return String(b.detected_at || "").localeCompare(String(a.detected_at || ""));
  });

  return {
    mismatches,
    liveWorkers,
    liveWorkersByTask,
    queueEvents,
    counts: {
      total: mismatches.length,
      high: mismatches.filter((item) => item.severity === "high").length,
      medium: mismatches.filter((item) => item.severity === "medium").length,
      low: mismatches.filter((item) => item.severity === "low").length,
    },
  };
}

export function taskDeliveryLayer(task) {
  const prefix = (task.id || "").split("-", 1)[0];
  return integrationPrefixes.has(prefix) ? "upstream" : "product";
}

export function taskBadgeChips(task) {
  const chips = [];
  if (String(task?.task_class || "").toLowerCase() === "sidecar") {
    chips.push('<span class="chip">sidecar</span>');
  }
  if (task?.auto_generated) {
    chips.push('<span class="chip">auto-generated</span>');
  }
  if (task?.helper_parent) {
    chips.push(`<span class="chip">parent ${task.helper_parent}</span>`);
  }
  return chips.join("");
}

export function taskBadgeRow(task, className = "task-meta") {
  const chips = taskBadgeChips(task);
  return chips ? `<div class="${className}">${chips}</div>` : "";
}

export function formatTime(value) {
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
    timeZone: DISPLAY_TIME_ZONE,
  });
}

export async function fetchJson(path) {
  const response = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`無法載入 ${path}: ${response.status}`);
  }
  return response.json();
}

export async function fetchText(path) {
  const response = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`無法載入 ${path}: ${response.status}`);
  }
  return response.text();
}

export async function requestDashboardRefresh() {
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

export function parseJsonLines(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && line.startsWith("{"))
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

export function parseCurrentWork(markdown) {
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

export function deriveAgentState(status, orchState) {
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
      target_workload: Number.isFinite(Number(status?.workload?.[agent.name])) ? Number(status.workload[agent.name]) : null,
    };
  });
}

export function buildDependencySchedule(tasks) {
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

export function dependencyBatchState(task, index, unresolvedDeps = []) {
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

export function timeAgo(value) {
  if (!value) return "-";
  const diff = Math.floor((Date.now() - new Date(value).getTime()) / 1000);
  if (diff < 60) return `${diff} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

export function truncate(text, maxLen = 100) {
  if (!text || text.length <= maxLen) return text || "-";
  const safeText = text.replace(/"/g, "&quot;");
  return `
    <span class="truncated-wrapper">
      <span class="text-short">${text.slice(0, maxLen)}...</span>
      <span class="text-full" style="display:none">${safeText}</span>
      <button class="expand-btn" onclick="const p=this.parentElement; const s=p.querySelector('.text-short'); const f=p.querySelector('.text-full'); if(f.style.display==='none'){f.style.display='inline'; s.style.display='none'; this.textContent='收合'}else{f.style.display='none'; s.style.display='inline'; this.textContent='展開'}">展開</button>
    </span>`;
}
