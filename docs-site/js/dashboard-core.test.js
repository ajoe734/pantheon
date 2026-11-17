// Run with: node --test docs-site/js/dashboard-core.test.js
import assert from "node:assert/strict";
import test from "node:test";
import {
  actorRunsAutoWorkers,
  buildActiveWorkRows,
  buildTruthMismatches,
  buildWorkerCapacity,
  bundleRuntimeSummary,
  deriveAgentState,
  workerRoleLabel,
} from "./dashboard-core.js";

const dispatchPolicy = {
  max_parallel_by_agent: { claude: 3, claude2: 3, antigravity: 4, antigravity2: 4, copilot: 0 },
};

function task(id, owner, status, extra = {}) {
  return { id, owner, reviewer: "Claude", status, depends_on: [], last_update: "2026-11-16T14:00:00Z", ...extra };
}

// Same shape as worker-runtime/state.json records the page receives.
function worker(runId, agent, taskId, reason, extra = {}) {
  return {
    run_id: runId,
    provider: agent,
    agent_id: agent,
    logical_agent_id: agent,
    task_id: taskId,
    status: "running",
    request_snapshot: { reason },
    workspace_branch: `task/${taskId}`,
    lease_acquired_at: "2026-11-16T14:00:24Z",
    last_heartbeat_at: "2026-11-16T14:36:00Z",
    last_event_at: "2026-11-16T14:00:24Z",
    commit_progress_count: 2,
    ...extra,
  };
}

const status = {
  tasks: [
    task("HUMAN-ACCEPTANCE-001", "Human/Ops", "in_progress"),
    task("COPILOT-NOTES-001", "Copilot", "in_progress"),
    task("BLOCKED-001", "Codex", "blocked"),
    task("RUNNING-001", "Antigravity", "in_progress"),
    task("REVIEWING-001", "Claude2", "review"),
    task("STALLED-001", "Antigravity2", "in_progress"),
  ],
  agents: [
    { name: "Antigravity", branch: "task/FE-PORTFOLIO-CURRENCY-DISPLAY-20261026", next: "note" },
    { name: "Claude", branch: "task/HOSTED-ALL12-ACCEPTED-MANAGED-SOURCE-20261025", next: "note" },
    { name: "Human/Ops", branch: "task/OPS-TERMINAL-RUNNER-CAPACITY-CAS-20261025", next: "note" },
  ],
};

const orchState = {
  workers: {
    "antigravity-run": worker("antigravity-run", "antigravity", "RUNNING-001", "owned_in_progress_dispatch"),
    "claude-run": worker("claude-run", "claude", "REVIEWING-001", "review_ready_dispatch"),
  },
};

test("only agents with worker capacity are expected to run auto-workers", () => {
  assert.equal(actorRunsAutoWorkers("Antigravity2", dispatchPolicy), true);
  assert.equal(actorRunsAutoWorkers("Human/Ops", dispatchPolicy), false);
  assert.equal(actorRunsAutoWorkers("Copilot", dispatchPolicy), false);
  // An older bundle without a capacity table keeps the previous behavior.
  assert.equal(actorRunsAutoWorkers("Human/Ops", null), true);
});

test("in-progress tasks of owners without auto-workers are not missing a worker", () => {
  const flagged = (policy) =>
    new Set(
      buildTruthMismatches(status, orchState, null, policy)
        .mismatches.filter((item) => item.type === "active_task_without_worker")
        .map((item) => item.task_id)
    );

  assert.deepEqual(flagged(dispatchPolicy), new Set(["STALLED-001"]));
  assert.deepEqual(flagged(null), new Set(["HUMAN-ACCEPTANCE-001", "COPILOT-NOTES-001", "STALLED-001"]));
});

test("work matrix lists real problems, then tasks a worker is running, then the rest", () => {
  const { rows } = buildActiveWorkRows(status, orchState, { dispatch_policy: dispatchPolicy, worker_task_links: [], truth_mismatches: [] });
  const order = rows.map((row) => row.task.id);
  const state = Object.fromEntries(rows.map((row) => [row.task.id, row.state]));

  assert.equal(state["STALLED-001"], "attention");
  assert.notEqual(state["HUMAN-ACCEPTANCE-001"], "attention");
  assert.notEqual(state["COPILOT-NOTES-001"], "attention");
  assert.deepEqual(order, [
    "STALLED-001",
    "RUNNING-001",
    "REVIEWING-001",
    "BLOCKED-001",
    "HUMAN-ACCEPTANCE-001",
    "COPILOT-NOTES-001",
  ]);
});

test("agent lanes carry the live worker records, including reviewer runs", () => {
  const lanes = Object.fromEntries(deriveAgentState(status, orchState).map((lane) => [lane.name, lane]));

  assert.deepEqual(lanes.Antigravity.live_workers.map((item) => item.task_id), ["RUNNING-001"]);
  assert.equal(lanes.Antigravity.live_workers[0].workspace_branch, "task/RUNNING-001");
  assert.equal(workerRoleLabel(lanes.Antigravity.live_workers[0]), "實作");
  assert.deepEqual(lanes.Claude.live_workers.map((item) => item.task_id), ["REVIEWING-001"]);
  assert.equal(workerRoleLabel(lanes.Claude.live_workers[0]), "審查");
  assert.deepEqual(lanes["Human/Ops"].live_workers, []);
});

// A bundle written before FINISHED-001's worker exited: it still links that
// worker, counts 3 running, and carries a mismatch for a Human/Ops task.
const staleBundle = {
  dispatch_policy: dispatchPolicy,
  runtime_summary: {
    supervisor_pid: 1410671,
    heartbeat_at: "2026-11-16T15:38:49Z",
    running_workers: 3,
    pending_workers: 0,
    mismatch_count: 1,
    lanes: { Antigravity: { running: 3, pending: 0 } },
  },
  worker_task_links: [{ task_id: "FINISHED-001", runtime_bucket: "running" }],
  truth_mismatches: [{ type: "active_task_without_worker", task_id: "HUMAN-ACCEPTANCE-001" }],
};
const staleStatus = { tasks: [...status.tasks, task("FINISHED-001", "Antigravity", "review_approved")], agents: status.agents };
const liveRunningOnly = { workers: { "antigravity-run": orchState.workers["antigravity-run"] } };

test("live runtime state wins over a lagging bundle", () => {
  const { rows } = buildActiveWorkRows(staleStatus, liveRunningOnly, staleBundle);
  const state = Object.fromEntries(rows.map((row) => [row.task.id, row.state]));
  assert.notEqual(state["HUMAN-ACCEPTANCE-001"], "attention");
  assert.notEqual(state["FINISHED-001"], "running");
  assert.equal(state["RUNNING-001"], "running");

  assert.deepEqual(bundleRuntimeSummary(liveRunningOnly, staleBundle), {});
  const capacity = buildWorkerCapacity(staleStatus, liveRunningOnly, staleBundle);
  assert.equal(capacity.totals.running, 1);
});

test("the bundle is still used when the runtime state could not be read", () => {
  const { rows } = buildActiveWorkRows(staleStatus, null, staleBundle);
  const finished = rows.find((row) => row.task.id === "FINISHED-001");
  assert.deepEqual(finished.runtimeWorkers, staleBundle.worker_task_links);
  assert.equal(bundleRuntimeSummary(null, staleBundle).running_workers, 3);
});
