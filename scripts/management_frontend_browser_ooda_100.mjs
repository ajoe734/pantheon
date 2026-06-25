#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const repoRoot = process.cwd();
const fixturePath = process.argv[2];

if (!fixturePath) {
  fail("usage: node scripts/management_frontend_browser_ooda_100.mjs <fixture.json>");
}

function fail(message, details = {}) {
  console.error(JSON.stringify({ ok: false, message, ...details }, null, 2));
  process.exit(1);
}

function assert(condition, message, details = {}) {
  if (!condition) fail(message, details);
}

function readText(relPath) {
  return fs.readFileSync(path.join(repoRoot, relPath), "utf8");
}

function stripHtml(value) {
  return String(value ?? "")
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/\s+/g, " ")
    .trim();
}

class VirtualElement {
  constructor(tagName, { id = "", className = "" } = {}) {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.className = className;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this._innerHTML = "";
    this._textContent = "";
  }

  set innerHTML(value) {
    this._innerHTML = String(value ?? "");
    this._textContent = stripHtml(this._innerHTML);
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set textContent(value) {
    this._textContent = String(value ?? "");
    this._innerHTML = this._textContent;
  }

  get textContent() {
    return this._textContent || stripHtml(this._innerHTML);
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.id = String(value);
    if (name === "class") this.className = String(value);
  }

  getAttribute(name) {
    return this.attributes[name] ?? null;
  }

  addEventListener(type, handler) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(handler);
  }

  click() {
    for (const handler of this.listeners.click || []) {
      handler({ target: this, type: "click" });
    }
  }
}

class VirtualDocument {
  constructor() {
    this.byId = new Map();
    this.all = [];
    this.body = this.createElement("body");
  }

  createElement(tagName) {
    const element = new VirtualElement(tagName);
    this.all.push(element);
    return element;
  }

  register(tagName, { id = "", className = "" } = {}) {
    const element = new VirtualElement(tagName, { id, className });
    this.all.push(element);
    if (id) this.byId.set(id, element);
    this.body.appendChild(element);
    return element;
  }

  querySelector(selector) {
    if (selector.startsWith("#")) return this.byId.get(selector.slice(1)) || null;
    if (selector.startsWith(".")) {
      const className = selector.slice(1);
      return this.all.find((element) => String(element.className || "").split(/\s+/).includes(className)) || null;
    }
    return this.all.find((element) => element.tagName.toLowerCase() === selector.toLowerCase()) || null;
  }
}

class ManagementBrowser {
  constructor(rounds) {
    this.rounds = rounds;
    this.currentRoute = "/";
    this.clickLog = [];
    this.refreshCount = 0;
  }

  visit(route) {
    assert(route && route.startsWith("/"), "browser route must be absolute", { route });
    this.currentRoute = route;
  }

  click(selector) {
    assert(selector, "click selector is required");
    this.clickLog.push({ selector, route: this.currentRoute });
  }

  filterStage(stage) {
    return this.rounds
      .filter((round) => round.packet.stage === stage)
      .map((round) => round.packet.packet_id);
  }

  refresh() {
    this.refreshCount += 1;
  }
}

function copyDashboardModules() {
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "pantheon-dashboard-browser-"));
  const config = readText("docs-site/js/dashboard-config.js");
  const core = readText("docs-site/js/dashboard-core.js")
    .replaceAll("./dashboard-config.js?v=20260517-audit", "./dashboard-config.mjs");
  const renderers = readText("docs-site/js/dashboard-renderers.js")
    .replaceAll("./dashboard-config.js?v=20260517-audit", "./dashboard-config.mjs")
    .replaceAll("./dashboard-core.js?v=20260517-audit", "./dashboard-core.mjs");
  fs.writeFileSync(path.join(tempDir, "dashboard-config.mjs"), config);
  fs.writeFileSync(path.join(tempDir, "dashboard-core.mjs"), core);
  fs.writeFileSync(path.join(tempDir, "dashboard-renderers.mjs"), renderers);
  return tempDir;
}

function shellDocument() {
  const document = new VirtualDocument();
  document.register("div", { id: "supervisor-cockpit-summary", className: "inline-summary panel-summary" });
  document.register("div", { id: "operator-next-action", className: "operator-next-action" });
  document.register("div", { id: "runtime-health-strip", className: "runtime-health-strip" });
  document.register("div", { id: "active-work-matrix", className: "active-work-matrix" });
  return document;
}

function statusFor(round) {
  const blocked = round.flow.requires_human_gate || round.flow.incident_like;
  const taskStatus = blocked ? "blocked" : round.round_number % 3 === 0 ? "review" : "in_progress";
  return {
    tasks: [
      {
        id: round.round_id,
        title: `${round.flow.human_action} · ${round.packet.packet_id}`,
        status: taskStatus,
        owner: "human_ops",
        reviewer: "codex",
        phase: "management_frontend_ooda",
        summary_zh: `${round.flow.frontend_surface} renders ${round.packet.stage} for ${round.packet.persona_id}`,
        next: `${round.flow.expected_operator_state}; selected tool ${round.cognitive.selected_tool}; memory stance ${round.memory.memory_adjusted_persona_stance}`,
        depends_on: [],
        last_update: round.timestamp,
      },
    ],
    agents: [
      { name: "human_ops", active: true, current: round.round_id },
      { name: "codex", active: true, current: round.cognitive.case_id },
    ],
    workload: { human_ops: 1, codex: 1 },
  };
}

function orchStateFor(round) {
  const queueReason = round.flow.requires_human_gate
    ? `human gate required for ${round.packet.packet_id}`
    : `operator action ${round.flow.human_action} queued for readback`;
  return {
    supervisor: {
      pid: 1000 + round.round_number,
      last_heartbeat_at: round.timestamp,
    },
    workers: {
      [`worker-${round.round_number}`]: {
        task_id: round.round_id,
        agent_id: "codex",
        logical_agent_id: "codex",
        provider: "codex",
        status: "running",
        runtime_bucket: "running",
        is_live_runtime: true,
        last_event_at: round.timestamp,
      },
    },
    queue: {
      events: {
        [`queue-${round.round_number}`]: {
          task_id: round.round_id,
          agent_id: "human_ops",
          provider: "human_ops",
          status: "pending",
          reason: queueReason,
          last_event_at: round.timestamp,
        },
      },
    },
    provider_guardrails: {
      dispatch_pauses: {},
    },
  };
}

function approvalQueueFor(round) {
  return {
    pending: round.flow.requires_human_gate
      ? [
          {
            id: `approval-${round.round_id}`,
            target_id: round.packet.packet_id,
            reason: "human gate required before capital mutation",
          },
        ]
      : [],
  };
}

function dashboardBundleFor(round) {
  return {
    runtime_summary: {
      supervisor_pid: 1000 + round.round_number,
      heartbeat_at: round.timestamp,
      queue_depth: 1,
      running_workers: 1,
      pending_workers: round.flow.requires_human_gate ? 1 : 0,
      mismatch_count: 0,
      lanes: {
        codex: { running: 1, pending: 0, queued: 0 },
        human_ops: { running: 0, pending: round.flow.requires_human_gate ? 1 : 0, queued: 1 },
      },
      dispatch_targets: {
        codex: 1,
        human_ops: 1,
      },
    },
    worker_task_links: [
      {
        task_id: round.round_id,
        worker_run_id: `worker-${round.round_number}`,
        worker_status: "running",
        runtime_bucket: "running",
        provider: "codex",
        actor: "codex",
      },
    ],
    truth_mismatches: [],
  };
}

function visibleText(document, selector) {
  const element = document.querySelector(selector);
  assert(element, `selector ${selector} must exist`);
  assert(element.innerHTML.trim().length > 0, `selector ${selector} must render html`);
  return element.textContent;
}

function coverage(rounds) {
  const count = (items) => items.reduce((acc, item) => {
    acc[item] = (acc[item] || 0) + 1;
    return acc;
  }, {});
  return {
    flow_counts: count(rounds.map((round) => round.flow.flow_id)),
    surface_counts: count(rounds.map((round) => round.flow.frontend_surface)),
    stage_counts: count(rounds.map((round) => round.packet.stage)),
    action_counts: count(rounds.map((round) => round.flow.human_action)),
    selected_tool_counts: count(rounds.map((round) => round.cognitive.selected_tool)),
  };
}

const fixture = JSON.parse(fs.readFileSync(path.resolve(fixturePath), "utf8"));
assert(fixture.suite_id === "human-management-frontend-persona-ooda-100", "unexpected suite id", {
  suite_id: fixture.suite_id,
});
assert(Array.isArray(fixture.rounds), "fixture.rounds must be an array");
assert(fixture.rounds.length === 100, "fixture must contain exactly 100 rounds", {
  round_count: fixture.rounds.length,
});

const moduleDir = copyDashboardModules();
const document = shellDocument();
globalThis.document = document;
globalThis.window = { document };

const renderers = await import(pathToFileURL(path.join(moduleDir, "dashboard-renderers.mjs")).href);
assert(typeof renderers.renderSupervisorCockpit === "function", "renderSupervisorCockpit must import");
assert(typeof renderers.renderActiveWorkMatrix === "function", "renderActiveWorkMatrix must import");

const browser = new ManagementBrowser(fixture.rounds);
const renderProofs = [];

for (const round of fixture.rounds) {
  browser.visit(round.frontend.route);
  renderers.renderSupervisorCockpit(
    statusFor(round),
    orchStateFor(round),
    approvalQueueFor(round),
    dashboardBundleFor(round),
  );
  renderers.renderActiveWorkMatrix(
    statusFor(round),
    orchStateFor(round),
    approvalQueueFor(round),
    dashboardBundleFor(round),
  );

  const summaryText = visibleText(document, "#supervisor-cockpit-summary");
  const operatorText = visibleText(document, "#operator-next-action");
  const runtimeText = visibleText(document, "#runtime-health-strip");
  const matrixText = visibleText(document, "#active-work-matrix");

  assert(summaryText.includes("Open"), "supervisor summary must expose open count", { round_id: round.round_id });
  assert(operatorText.includes(round.round_id), "operator card must render round task id", { round_id: round.round_id });
  assert(operatorText.includes(round.flow.human_action), "operator card must render human action", {
    round_id: round.round_id,
    human_action: round.flow.human_action,
  });
  assert(runtimeText.includes("Supervisor"), "runtime strip must render supervisor card", { round_id: round.round_id });
  assert(runtimeText.includes("Approval"), "runtime strip must render approval card", { round_id: round.round_id });
  assert(matrixText.includes(round.round_id), "active work matrix must render round task id", { round_id: round.round_id });

  const stagePacketIds = browser.filterStage(round.packet.stage);
  assert(stagePacketIds.includes(round.packet.packet_id), "stage filter must retain current packet", {
    round_id: round.round_id,
    stage: round.packet.stage,
    packet_id: round.packet.packet_id,
  });

  browser.click("#operator-next-action");
  if (round.flow.human_action === "refresh_and_reconcile_readback") browser.refresh();

  renderProofs.push({
    round_id: round.round_id,
    route: browser.currentRoute,
    packet_id: round.packet.packet_id,
    stage: round.packet.stage,
    rendered_selectors: [
      "#supervisor-cockpit-summary",
      "#operator-next-action",
      "#runtime-health-strip",
      "#active-work-matrix",
    ],
    operator_text_length: operatorText.length,
    runtime_text_length: runtimeText.length,
  });
}

const packetIds = new Set(fixture.rounds.map((round) => round.packet.packet_id));
const cognitiveCaseIds = new Set(fixture.rounds.map((round) => round.cognitive.case_id));
assert(packetIds.size === 100, "browser fixture must use 100 unique OODA packets", { packet_count: packetIds.size });
assert(cognitiveCaseIds.size === 100, "browser fixture must use 100 unique cognitive cases", {
  cognitive_case_count: cognitiveCaseIds.size,
});
assert(browser.clickLog.length === 100, "browser must click operator card once per round", {
  click_count: browser.clickLog.length,
});

const cov = coverage(fixture.rounds);
for (const [name, counts] of Object.entries(cov)) {
  assert(Object.values(counts).every((count) => count > 0), `${name} must cover every declared bucket`, counts);
}

console.log(JSON.stringify({
  ok: true,
  suite_id: fixture.suite_id,
  round_count: fixture.rounds.length,
  render_count: renderProofs.length,
  click_count: browser.clickLog.length,
  refresh_count: browser.refreshCount,
  unique_packet_count: packetIds.size,
  unique_cognitive_case_count: cognitiveCaseIds.size,
  coverage: cov,
  rendered_selectors: renderProofs[0].rendered_selectors,
}, null, 2));
