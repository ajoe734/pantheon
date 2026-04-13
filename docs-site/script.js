import { DATA_FILES } from "./js/dashboard-config.js?v=20260413-1745";
import {
  deriveAgentState,
  fetchJson,
  fetchText,
  formatTime,
  normalizeDashboardBundle,
  normalizePlanningState,
  parseCurrentWork,
  parseJsonLines,
  qs,
  requestDashboardRefresh,
  statusLabel,
  titleCase,
} from "./js/dashboard-core.js?v=20260413-1745";
import {
  applyModeVisibility,
  renderAlertStrip,
  renderActivity,
  renderAgentLanes,
  renderAuditStatus,
  renderBoardSummary,
  renderBridgeCard,
  renderControlPlaneStrip,
  renderDeliveryLayers,
  renderDependencySchedule,
  renderExecutionSectionSummary,
  renderExecutionSummary,
  renderFocusSummary,
  renderOverviewMetrics,
  renderPlanningArtifacts,
  renderPlanningGate,
  renderPlanningIssues,
  renderPlanningOverview,
  renderPlanningProposals,
  renderPlanningRounds,
  renderProgressBar,
  renderProgressBreakdown,
  renderReviewNotes,
  renderSnapshot,
  renderStackList,
  renderSystemStatus,
  renderTaskBoard,
  renderTruthMismatches,
  renderWorkload,
} from "./js/dashboard-renderers.js?v=20260413-1745";

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

    const [status, activityText, currentWorkText, orchState, approvalQueue, rawPlanningState, rawDashboardBundle] = await Promise.all([
      fetchJson(DATA_FILES.status),
      fetchText(DATA_FILES.activity),
      fetchText(DATA_FILES.currentWork),
      fetchJson(DATA_FILES.orchestratorState).catch(() => null),
      fetchJson(DATA_FILES.approvalQueue).catch(() => null),
      fetchJson(DATA_FILES.planningState).catch(() => null),
      fetchJson(DATA_FILES.dashboardBundle).catch(() => null),
    ]);

    const logs = parseJsonLines(activityText);
    const planningState = normalizePlanningState(rawPlanningState);
    const dashboardBundle = normalizeDashboardBundle(rawDashboardBundle);
    const planningEvents = (planningState.recent_events || []).map((entry) => ({
      ...entry,
      agent: entry.agent || entry.actor || planningState.facilitator,
      ts: entry.ts || entry.updated_at,
    }));
    const combinedActivity = [...logs, ...planningEvents].sort((a, b) => String(a.ts || "").localeCompare(String(b.ts || "")));
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
    renderProgressBreakdown(status, planningState, dashboardBundle);
    renderOverviewMetrics(status, orchState, approvalQueue, dashboardBundle);
    renderControlPlaneStrip(status, planningState, orchState, dashboardBundle);
    renderFocusSummary(status, planningState, orchState, dashboardBundle);
    renderAlertStrip(status, orchState, planningState, approvalQueue, dashboardBundle);
    renderBridgeCard(status, planningState, dashboardBundle);
    renderExecutionSectionSummary(status, orchState, planningState, dashboardBundle);
    applyModeVisibility(status, planningState);
    renderPlanningOverview(planningState, status, dashboardBundle);
    renderPlanningArtifacts(planningState);
    renderPlanningRounds(planningState);
    renderPlanningGate(planningState, status);
    renderPlanningIssues(planningState);
    renderPlanningProposals(planningState, status, dashboardBundle);
    renderSystemStatus(status, orchState, approvalQueue, agentStates, dashboardBundle);
    renderTruthMismatches(status, orchState, approvalQueue, dashboardBundle);
    renderWorkload(status);
    renderDeliveryLayers(status, planningState);
    renderAgentLanes(status, agentStates);
    renderExecutionSummary(status, orchState, dashboardBundle);
    renderBoardSummary(status, orchState, dashboardBundle);
    renderTaskBoard(status, orchState, dashboardBundle);
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
    renderActivity(combinedActivity);
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
