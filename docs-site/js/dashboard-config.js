export const DATA_FILES = {
  status: "./ai-status.json",
  activity: "./ai-activity-log.jsonl",
  currentWork: "./current-work.md",
  orchestratorState: "./orchestrator-state.json",
  approvalQueue: "./approval-queue.json",
  planningState: "./planning-state.json",
  dashboardBundle: "./dashboard-bundle.json",
};

export const boardColumns = [
  { key: "todo", label: "待開始" },
  { key: "in_progress", label: "進行中" },
  { key: "review", label: "待審查" },
  { key: "review_approved", label: "已批准待收尾" },
  { key: "blocked", label: "已阻塞" },
  { key: "done", label: "已完成" },
];

export const statusLabelMap = {
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
  inactive: "未啟用",
  active: "進行中",
  human_required: "需人工決策",
  superseded: "已接手",
  reassigned: "已改派",
  open: "未解決",
  resolved: "已解決",
  accepted: "已接收",
  rejected: "已拒絕",
  submitted: "已提交",
  draft: "草稿",
  in_review: "審閱中",
  ready_for_human: "待人工確認",
  not_requested: "未送審",
  not_started: "未開始",
  start: "開始",
  progress: "進度更新",
  handoff: "交接",
  blocker: "阻塞",
  assign: "指派",
};

export const laneLabelMap = {
  execution: "執行平面",
  "control-plane": "控制平面",
  "governance-review": "治理審查",
  "code-agent": "Code Agent",
  gcp: "GCP",
  "ci-cd": "CI/CD",
  "runtime-packaging": "執行環境封裝",
  "worker-ops": "Worker 維運",
  integration: "整合契約",
  "status-system": "狀態系統",
  schema: "Schema",
  acceptance: "驗收",
};

export const activeTaskStatuses = new Set(["in_progress", "review"]);
export const scheduleOpenTaskStatuses = new Set(["todo", "in_progress", "review", "blocked"]);

export const workerStatusIcon = {
  running: "🟡",
  completed: "🟢",
  failed: "🔴",
  manual_pending: "⚪",
  started: "🟡",
  superseded: "🔁",
  reassigned: "🔀",
};

export const activityTypeLabel = {
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
  superseded: "已取代結案",
  planning_session_started: "Planning 啟動",
  readout_submitted: "Readout 已提交",
  readout_updated: "Readout 更新",
  baton_transferred: "Baton 轉移",
  cross_review_round_opened: "Cross-review 開始",
  cross_review_round_completed: "Cross-review 完成",
  cross_review_round_updated: "Cross-review 更新",
  consensus_packet_drafted: "Consensus 草案",
  consensus_status_updated: "Consensus 更新",
  consensus_human_required: "需人工裁決",
  human_gate_updated: "Human Gate 更新",
  consensus_accepted: "Consensus 已接受",
  execution_slice_proposed: "候選任務提出",
};

export const integrationPrefixes = new Set(["OC", "RS", "LP", "OSS", "SPIKE"]);

export const planningHighSignalTypes = new Set([
  "planning_session_started",
  "readout_submitted",
  "baton_transferred",
  "cross_review_round_opened",
  "cross_review_round_completed",
  "consensus_packet_drafted",
  "consensus_human_required",
  "consensus_accepted",
  "execution_slice_proposed",
  "human_gate_updated",
]);

export const logicalAgents = ["claude", "claude2", "gemini", "gemini2", "codex", "codex2", "copilot"];
