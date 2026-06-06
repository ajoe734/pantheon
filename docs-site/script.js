import { DATA_FILES } from "./js/dashboard-config.js?v=20260517-audit";
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
} from "./js/dashboard-core.js?v=20260517-audit";
import {
  applyModeVisibility,
  renderActiveWorkMatrix,
  renderAlertStrip,
  renderActivity,
  renderAgentLanes,
  renderArchiveRecords,
  renderAuditStatus,
  renderBffConsolidationTrack,
  renderBoardSummary,
  renderBridgeCard,
  renderControlPlaneStrip,
  renderDeliveryLayers,
  renderDependencySchedule,
  renderExecutionSectionSummary,
  renderExecutionSummary,
  renderFocusSummary,
  renderLovableCoordination,
  renderLovableCoordinationSummary,
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
  renderSupervisorCockpit,
  renderSystemStatus,
  renderTaskBoard,
  renderTruthMismatches,
  renderWorkerHealthDigest,
  renderWorkload,
  renderDependencyRunway,
} from "./js/dashboard-renderers.js?v=20260518-archive-fix";

let renderInFlight = false;

function runRenderStep(label, failures, fn) {
  try {
    fn();
  } catch (error) {
    console.error(`[dashboard] ${label} failed`, error);
    failures.push({
      label,
      message: error instanceof Error ? error.message : String(error),
    });
  }
}

function renderFailureNotice(failures) {
  const alertStrip = qs("#alert-strip");
  if (!alertStrip || !failures.length) return;
  const card = document.createElement("article");
  card.className = "alert-card alert-warning";
  card.innerHTML = `
    <div class="stack-head">
      <strong>部分面板渲染失敗</strong>
      <span class="status-pill status-blocked">${failures.length} 項</span>
    </div>
    <p class="card-copy">資料已載入，但有部分前端面板在瀏覽器端渲染失敗；其他區塊仍會繼續顯示。</p>
    <ul class="note-list compact-list">
      ${failures.slice(0, 5).map((failure) => `<li><strong>${failure.label}</strong>：${failure.message}</li>`).join("")}
    </ul>
  `;
  alertStrip.prepend(card);
}

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
      fetchText(DATA_FILES.activity).catch(() => ""),
      fetchText(DATA_FILES.currentWork).catch(() => ""),
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

    {
      const objectiveText = status.objective || snapshot.objective || "目前沒有可顯示的目標。";
      const objectiveEl = qs("#objective");
      objectiveEl.textContent = objectiveText;
      objectiveEl.setAttribute("title", objectiveText);
    }
    qs("#updated-at").textContent = formatTime(status.updated_at);
    if (projectBadge) {
      projectBadge.textContent = `${projectName} Runtime`;
    }
    document.title = `${projectName} 協作看板`;

    const agentStates = deriveAgentState(status, orchState);
    const renderFailures = [];

    runRenderStep("progress_bar", renderFailures, () => renderProgressBar(status.tasks, dashboardBundle));
    runRenderStep("progress_breakdown", renderFailures, () => renderProgressBreakdown(status, planningState, dashboardBundle));
    runRenderStep("overview_metrics", renderFailures, () => renderOverviewMetrics(status, orchState, approvalQueue, dashboardBundle));
    runRenderStep("control_plane_strip", renderFailures, () => renderControlPlaneStrip(status, planningState, orchState, dashboardBundle));
    runRenderStep("focus_summary", renderFailures, () => renderFocusSummary(status, planningState, orchState, dashboardBundle));
    runRenderStep("alert_strip", renderFailures, () => renderAlertStrip(status, orchState, planningState, approvalQueue, dashboardBundle));
    runRenderStep("supervisor_cockpit", renderFailures, () => renderSupervisorCockpit(status, orchState, approvalQueue, dashboardBundle));
    runRenderStep("active_work_matrix", renderFailures, () => renderActiveWorkMatrix(status, orchState, approvalQueue, dashboardBundle));
    runRenderStep("dependency_runway", renderFailures, () => renderDependencyRunway(status));
    runRenderStep("worker_health_digest", renderFailures, () => renderWorkerHealthDigest(status, orchState, approvalQueue, dashboardBundle));
    runRenderStep("bff_consolidation_track", renderFailures, () => renderBffConsolidationTrack(status, dashboardBundle));
    runRenderStep("bridge_card", renderFailures, () => renderBridgeCard(status, planningState, dashboardBundle));
    runRenderStep("execution_section_summary", renderFailures, () => renderExecutionSectionSummary(status, orchState, planningState, dashboardBundle));
    runRenderStep("mode_visibility", renderFailures, () => applyModeVisibility(status, planningState));
    runRenderStep("planning_overview", renderFailures, () => renderPlanningOverview(planningState, status, dashboardBundle));
    runRenderStep("planning_artifacts", renderFailures, () => renderPlanningArtifacts(planningState));
    runRenderStep("planning_rounds", renderFailures, () => renderPlanningRounds(planningState));
    runRenderStep("planning_gate", renderFailures, () => renderPlanningGate(planningState, status));
    runRenderStep("planning_issues", renderFailures, () => renderPlanningIssues(planningState));
    runRenderStep("planning_proposals", renderFailures, () => renderPlanningProposals(planningState, status, dashboardBundle));
    runRenderStep("system_status", renderFailures, () => renderSystemStatus(status, orchState, approvalQueue, agentStates, dashboardBundle));
    runRenderStep("truth_mismatches", renderFailures, () => renderTruthMismatches(status, orchState, approvalQueue, dashboardBundle));
    runRenderStep("workload", renderFailures, () => renderWorkload(status, orchState));
    runRenderStep("delivery_layers", renderFailures, () => renderDeliveryLayers(status, planningState, dashboardBundle));
    runRenderStep("agent_lanes", renderFailures, () => renderAgentLanes(status, agentStates));
    runRenderStep("archive_records", renderFailures, () => renderArchiveRecords(dashboardBundle));
    runRenderStep("lovable_coordination_summary", renderFailures, () => renderLovableCoordinationSummary(dashboardBundle));
    runRenderStep("lovable_coordination", renderFailures, () => renderLovableCoordination(orchState, status, dashboardBundle));
    runRenderStep("execution_summary", renderFailures, () => renderExecutionSummary(status, orchState, dashboardBundle));
    runRenderStep("board_summary", renderFailures, () => renderBoardSummary(status, orchState, dashboardBundle));
    runRenderStep("task_board", renderFailures, () => renderTaskBoard(status, orchState, dashboardBundle));
    runRenderStep("review_notes", renderFailures, () => renderReviewNotes(status));
    runRenderStep("audit_status", renderFailures, () => renderAuditStatus(status));
    runRenderStep("dependency_schedule", renderFailures, () => renderDependencySchedule(status));
    runRenderStep("handoff_list", renderFailures, () => renderStackList(
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
    ));
    runRenderStep("blocker_list", renderFailures, () => renderStackList(
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
    ));
    runRenderStep("snapshot", renderFailures, () => renderSnapshot(snapshot));
    runRenderStep("activity", renderFailures, () => renderActivity(combinedActivity));
    runRenderStep("render_failure_notice", renderFailures, () => renderFailureNotice(renderFailures));
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
