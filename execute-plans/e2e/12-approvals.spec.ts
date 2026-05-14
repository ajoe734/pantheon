/**
 * FE-INT-GATE-C03 / F12 - approval governance decide, two-man, and batch.
 *
 * Coverage:
 *   1. Single /decide returns CommandResponse and updates both approval and
 *      linked HIQ state.
 *   2. /two-man-sign enforces distinct signer semantics and exposes quorum
 *      progress in the operator queue.
 *   3. /batch-decide partial failure opens BulkResultDrawer and keeps failed
 *      approvals selected for retry.
 *
 * Runner: Playwright browser test with a local BFF contract harness.
 */

import { expect, test, type Page } from "@playwright/test";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";

const OPERATOR_ID = "op-fe-gate";
const AUTH_HEADER = `Bearer ${OPERATOR_ID}:operator,reviewer,approver:mfa`;
const SNAPSHOT_AT = "2026-05-14T01:20:00Z";

const SINGLE_APPROVAL_ID = "approval-f12-single";
const SINGLE_HIQ_ID = "hiq-f12-single";
const TWO_MAN_APPROVAL_ID = "approval-f12-two-man";
const BATCH_OK_APPROVAL_ID = "approval-f12-batch-ok";
const BATCH_FAILED_APPROVAL_ID = "approval-f12-batch-blocked";

type ApprovalState = "pending" | "approved" | "rejected";
type StageState = "pending" | "approved" | "rejected" | "skipped";
type Decision = "approve" | "reject";

type JsonRecord = Record<string, unknown>;

type ApprovalStage = {
  decidedAt?: string;
  decidedBy?: string;
  name: string;
  roleFamily: "operator" | "risk" | "capital" | "ops";
  slaHours: number;
  startedAt?: string;
  state: StageState;
};

type ApprovalDto = {
  approval_id: string;
  createdAt: string;
  created_at: string;
  hiq_intervention_id?: string;
  id: string;
  kind: string;
  quorum: {
    approved: number;
    distinctFamilyRequired: boolean;
    min: number;
  };
  requester: string;
  riskLevel: "low" | "medium" | "high" | "critical";
  state: ApprovalState;
  status: ApprovalState;
  subject: string;
  target_id: string;
  target_type: string;
  two_man_required: boolean;
  twoManRequired: boolean;
  stages: ApprovalStage[];
};

type HiqDto = {
  approval_id: string;
  id: string;
  intervention_id: string;
  resolution?: string;
  status: "pending_review" | "resolved";
  target_id: string;
  target_type: string;
};

type AuditEvent = {
  action: string;
  actor: string;
  correlationId: string;
  target: string;
  ts: string;
};

type BulkResultRow = {
  data?: {
    approval: ApprovalDto;
  };
  error?: {
    code: string;
    message: string;
  };
  id: string;
  ok: boolean;
};

type BulkActionResponse = {
  partial: boolean;
  results: BulkResultRow[];
  summary: {
    failed: number;
    requested: number;
    succeeded: number;
  };
};

type CommandResponse = {
  data: {
    action: string;
    approval: ApprovalDto;
    command: "ApprovalGovernanceDecision";
    commandId: string;
    command_id: string;
    hiq?: HiqDto;
    quorum: ApprovalDto["quorum"];
    receipt: {
      command_id: string;
      status: "accepted";
      trackingUrl: string;
    };
    receipt_id: string;
    status: "accepted";
    target: {
      id: string;
      type: "Approval";
    };
  };
  meta: {
    durable: true;
    idempotency: {
      idempotencyKey: string;
      replayed: false;
    };
    liveCapitalSideEffects: false;
  };
  status: "accepted";
};

type RequestRecord = {
  authorization: string | undefined;
  body: JsonRecord;
  idempotencyKey: string | undefined;
  method: string;
  path: string;
};

type BrowserResponse = {
  body: JsonRecord;
  status: number;
};

type BrowserState = {
  bulkResult: BulkActionResponse | null;
  drawerOpen: boolean;
  selected: string[];
};

class ApprovalHarness {
  private readonly server: Server;
  private commandSeq = 0;
  private readonly approvals = new Map<string, ApprovalDto>();
  private readonly hiq = new Map<string, HiqDto>();

  readonly audit: AuditEvent[] = [];
  readonly requests: RequestRecord[] = [];
  baseUrl = "";

  constructor() {
    seedApprovals().forEach((approval) => this.approvals.set(approval.id, approval));
    this.hiq.set(SINGLE_HIQ_ID, {
      approval_id: SINGLE_APPROVAL_ID,
      id: SINGLE_HIQ_ID,
      intervention_id: SINGLE_HIQ_ID,
      status: "pending_review",
      target_id: "rebalance-f12-paper",
      target_type: "rebalance_plan",
    });
    this.server = createServer((req, res) => {
      void this.handle(req, res);
    });
  }

  async start(): Promise<void> {
    await new Promise<void>((resolve) => {
      this.server.listen(0, "127.0.0.1", resolve);
    });
    const address = this.server.address() as AddressInfo;
    this.baseUrl = `http://127.0.0.1:${address.port}`;
  }

  async stop(): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      this.server.close((error) => (error ? reject(error) : resolve()));
    });
  }

  approval(id: string): ApprovalDto {
    const approval = this.approvals.get(id);
    expect(approval, `approval fixture ${id}`).toBeTruthy();
    return clone(approval as ApprovalDto);
  }

  linkedHiq(id: string): HiqDto {
    const item = this.hiq.get(id);
    expect(item, `HIQ fixture ${id}`).toBeTruthy();
    return clone(item as HiqDto);
  }

  private async handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const url = new URL(req.url ?? "/", this.baseUrl || "http://127.0.0.1");
    const path = url.pathname;

    if (path === "/" || path === "/test-shell") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end("<!doctype html><title>F12 approval governance</title><main id=\"app\"></main>");
      return;
    }

    if (req.method === "OPTIONS") {
      res.writeHead(204, corsHeaders());
      res.end();
      return;
    }

    if (path === "/bff/approvals" && req.method === "GET") {
      this.fulfillJson(res, 200, {
        items: [...this.approvals.values()].map(clone),
        meta: {
          contract: "FE-INT-GATE-C03",
          snapshot_at: SNAPSHOT_AT,
          surfaces: {
            approvals: { source: "f12-harness", status: "ok" },
          },
        },
        totalCount: this.approvals.size,
        totalCountExact: true,
      });
      return;
    }

    const approvalDetail = path.match(/^\/bff\/approvals\/([^/]+)$/);
    if (approvalDetail && req.method === "GET") {
      const approval = this.approvals.get(decodeURIComponent(approvalDetail[1]));
      this.fulfillJson(res, approval ? 200 : 404, approval ? { data: clone(approval) } : notFound(path));
      return;
    }

    const hiqDetail = path.match(/^\/bff\/v5\/interventions\/([^/]+)$/);
    if (hiqDetail && req.method === "GET") {
      const item = this.hiq.get(decodeURIComponent(hiqDetail[1]));
      this.fulfillJson(res, item ? 200 : 404, item ? { data: clone(item) } : notFound(path));
      return;
    }

    const decide = path.match(/^\/bff\/approvals\/([^/]+)\/decide$/);
    if (decide && req.method === "POST") {
      await this.handleDecide(req, res, decodeURIComponent(decide[1]), path);
      return;
    }

    const twoManSign = path.match(/^\/bff\/approvals\/([^/]+)\/two-man-sign$/);
    if (twoManSign && req.method === "POST") {
      await this.handleTwoManSign(req, res, decodeURIComponent(twoManSign[1]), path);
      return;
    }

    if (path === "/bff/approvals/batch-decide" && req.method === "POST") {
      await this.handleBatchDecide(req, res, path);
      return;
    }

    this.fulfillJson(res, 404, notFound(path));
  }

  private async handleDecide(
    req: IncomingMessage,
    res: ServerResponse,
    approvalId: string,
    path: string,
  ): Promise<void> {
    const body = await this.recordRequest(req, path);
    const decision = stringField(body.decision) === "reject" ? "reject" : "approve";
    const approval = this.applyDecision(approvalId, decision, {
      actor: OPERATOR_ID,
      correlationId: stringField(body.correlationId) || "corr-f12-single",
      memo: stringField(body.memo) || "F12 single decision",
    });
    if (!approval) {
      this.fulfillJson(res, 404, notFound(path));
      return;
    }

    const hiq = approval.hiq_intervention_id
      ? this.resolveLinkedHiq(approval.hiq_intervention_id, decision)
      : undefined;
    this.fulfillJson(
      res,
      202,
      this.commandResponse("decide", approval, header(req, "idempotency-key"), hiq),
    );
  }

  private async handleTwoManSign(
    req: IncomingMessage,
    res: ServerResponse,
    approvalId: string,
    path: string,
  ): Promise<void> {
    const body = await this.recordRequest(req, path);
    const approval = this.approvals.get(approvalId);
    if (!approval) {
      this.fulfillJson(res, 404, notFound(path));
      return;
    }

    const signerId = stringField(body.signerId ?? body.signer_id);
    const signerRoleFamily = stringField(body.roleFamily ?? body.role_family) as ApprovalStage["roleFamily"];
    const existingSignerIds = new Set(
      approval.stages
        .filter((stage) => stage.state === "approved")
        .map((stage) => stage.decidedBy)
        .filter(Boolean),
    );
    if (!signerId || existingSignerIds.has(signerId)) {
      this.fulfillJson(res, 409, {
        detail: {
          error: {
            code: "TWO_MAN_REQUIRED",
            correlationId: stringField(body.correlationId) || "corr-f12-two-man",
            details: {
              approvalId,
              kind: "two_man",
              reason: "TWO_MAN_DISTINCT_OPERATOR_REQUIRED",
            },
            i18nKey: "errors.TWO_MAN_REQUIRED",
            message: "Two-man authorization requires a distinct second operator",
            retryable: false,
            userActionable: true,
          },
        },
      });
      return;
    }

    const pendingStage = approval.stages.find(
      (stage) => stage.state === "pending" && stage.roleFamily === signerRoleFamily,
    ) ?? approval.stages.find((stage) => stage.state === "pending");
    if (pendingStage) {
      pendingStage.state = "approved";
      pendingStage.decidedBy = signerId;
      pendingStage.decidedAt = SNAPSHOT_AT;
    }
    refreshApprovalState(approval);
    this.audit.push({
      action: "approval.two_man_sign",
      actor: signerId,
      correlationId: stringField(body.correlationId) || "corr-f12-two-man",
      target: approvalId,
      ts: SNAPSHOT_AT,
    });
    this.fulfillJson(
      res,
      202,
      this.commandResponse("two-man-sign", approval, header(req, "idempotency-key")),
    );
  }

  private async handleBatchDecide(
    req: IncomingMessage,
    res: ServerResponse,
    path: string,
  ): Promise<void> {
    const body = await this.recordRequest(req, path);
    const ids = Array.isArray(body.ids) ? body.ids.map(String) : [];
    const decision = stringField(body.decision) === "reject" ? "reject" : "approve";
    const results: BulkResultRow[] = [];

    for (const id of ids) {
      if (id === BATCH_FAILED_APPROVAL_ID) {
        results.push({
          error: {
            code: "PRECONDITION_FAILED",
            message: "Approval requires a fresh validation read before deciding",
          },
          id,
          ok: false,
        });
        continue;
      }
      const approval = this.applyDecision(id, decision, {
        actor: OPERATOR_ID,
        correlationId: stringField(body.correlationId) || "corr-f12-batch",
        memo: stringField(body.memo) || "F12 batch decision",
      });
      results.push(
        approval
          ? { data: { approval: clone(approval) }, id, ok: true }
          : {
              error: {
                code: "RESOURCE_NOT_FOUND",
                message: `Unknown approval ${id}`,
              },
              id,
              ok: false,
            },
      );
    }

    const failed = results.filter((row) => !row.ok).length;
    this.fulfillJson(res, failed ? 207 : 202, {
      partial: failed > 0,
      results,
      summary: {
        failed,
        requested: ids.length,
        succeeded: results.length - failed,
      },
    } satisfies BulkActionResponse);
  }

  private applyDecision(
    approvalId: string,
    decision: Decision,
    options: { actor: string; correlationId: string; memo: string },
  ): ApprovalDto | undefined {
    const approval = this.approvals.get(approvalId);
    if (!approval) return undefined;
    const stage = approval.stages.find((item) => item.state === "pending");
    if (stage) {
      stage.state = decision === "approve" ? "approved" : "rejected";
      stage.decidedBy = options.actor;
      stage.decidedAt = SNAPSHOT_AT;
    }
    refreshApprovalState(approval);
    if (decision === "reject") approval.state = "rejected";
    this.audit.push({
      action: `approval.${decision}`,
      actor: options.actor,
      correlationId: options.correlationId,
      target: approvalId,
      ts: SNAPSHOT_AT,
    });
    return clone(approval);
  }

  private resolveLinkedHiq(hiqId: string, decision: Decision): HiqDto | undefined {
    const item = this.hiq.get(hiqId);
    if (!item) return undefined;
    item.status = "resolved";
    item.resolution = decision === "approve" ? "approved_by_governance" : "rejected_by_governance";
    return clone(item);
  }

  private commandResponse(
    action: string,
    approval: ApprovalDto,
    idempotencyKey = "",
    hiq?: HiqDto,
  ): CommandResponse {
    this.commandSeq += 1;
    const commandId = `cmd-f12-${action}-${this.commandSeq}`;
    return {
      data: {
        action,
        approval: clone(approval),
        command: "ApprovalGovernanceDecision",
        commandId,
        command_id: commandId,
        ...(hiq ? { hiq: clone(hiq) } : {}),
        quorum: approval.quorum,
        receipt: {
          command_id: commandId,
          status: "accepted",
          trackingUrl: `/api/v1/operator/commands/${commandId}`,
        },
        receipt_id: commandId,
        status: "accepted",
        target: {
          id: approval.id,
          type: "Approval",
        },
      },
      meta: {
        durable: true,
        idempotency: {
          idempotencyKey,
          replayed: false,
        },
        liveCapitalSideEffects: false,
      },
      status: "accepted",
    };
  }

  private async recordRequest(req: IncomingMessage, path: string): Promise<JsonRecord> {
    const body = await readJsonBody(req);
    this.requests.push({
      authorization: header(req, "authorization"),
      body,
      idempotencyKey: header(req, "idempotency-key"),
      method: req.method ?? "",
      path,
    });
    return body;
  }

  private fulfillJson(res: ServerResponse, status: number, body: unknown): void {
    res.writeHead(status, {
      ...corsHeaders(),
      "Content-Type": "application/json; charset=utf-8",
    });
    res.end(JSON.stringify(body));
  }
}

function seedApprovals(): ApprovalDto[] {
  return [
    makeApproval({
      hiqId: SINGLE_HIQ_ID,
      id: SINGLE_APPROVAL_ID,
      riskLevel: "high",
      stages: [
        { name: "operator", roleFamily: "operator", slaHours: 4, state: "pending" },
      ],
      subject: "Apply F12 paper rebalance",
      targetId: "rebalance-f12-paper",
      twoManRequired: false,
    }),
    makeApproval({
      id: TWO_MAN_APPROVAL_ID,
      riskLevel: "critical",
      stages: [
        {
          decidedAt: "2026-05-14T00:45:00Z",
          decidedBy: OPERATOR_ID,
          name: "operator",
          roleFamily: "operator",
          slaHours: 2,
          state: "approved",
        },
        { name: "risk", roleFamily: "risk", slaHours: 2, state: "pending" },
      ],
      subject: "Promote F12 live deployment",
      targetId: "deployment-f12-live",
      twoManRequired: true,
    }),
    makeApproval({
      id: BATCH_OK_APPROVAL_ID,
      riskLevel: "medium",
      stages: [
        { name: "operator", roleFamily: "operator", slaHours: 6, state: "pending" },
      ],
      subject: "Approve F12 batch success candidate",
      targetId: "strategy-f12-batch-ok",
      twoManRequired: false,
    }),
    makeApproval({
      id: BATCH_FAILED_APPROVAL_ID,
      riskLevel: "high",
      stages: [
        { name: "operator", roleFamily: "operator", slaHours: 6, state: "pending" },
      ],
      subject: "Approve F12 stale validation candidate",
      targetId: "strategy-f12-batch-blocked",
      twoManRequired: false,
    }),
  ];
}

function makeApproval(args: {
  hiqId?: string;
  id: string;
  riskLevel: ApprovalDto["riskLevel"];
  stages: ApprovalStage[];
  subject: string;
  targetId: string;
  twoManRequired: boolean;
}): ApprovalDto {
  const approval: ApprovalDto = {
    approval_id: args.id,
    createdAt: "2026-05-14T00:30:00Z",
    created_at: "2026-05-14T00:30:00Z",
    ...(args.hiqId ? { hiq_intervention_id: args.hiqId } : {}),
    id: args.id,
    kind: args.twoManRequired ? "deployment_live_promotion" : "rebalance_apply",
    quorum: {
      approved: 0,
      distinctFamilyRequired: args.twoManRequired,
      min: args.twoManRequired ? 2 : 1,
    },
    requester: "optimization-loop",
    riskLevel: args.riskLevel,
    state: "pending",
    status: "pending",
    stages: args.stages,
    subject: args.subject,
    target_id: args.targetId,
    target_type: args.twoManRequired ? "deployment" : "rebalance_plan",
    two_man_required: args.twoManRequired,
    twoManRequired: args.twoManRequired,
  };
  refreshApprovalState(approval);
  return approval;
}

function refreshApprovalState(approval: ApprovalDto): void {
  const approvedStages = approval.stages.filter((stage) => stage.state === "approved");
  approval.quorum = {
    approved: approvedStages.length,
    distinctFamilyRequired: approval.twoManRequired,
    min: approval.twoManRequired ? 2 : 1,
  };
  if (approval.stages.some((stage) => stage.state === "rejected")) {
    approval.state = "rejected";
  } else if (approval.quorum.approved >= approval.quorum.min) {
    approval.state = "approved";
  } else {
    approval.state = "pending";
  }
  approval.status = approval.state;
}

function corsHeaders(): Record<string, string> {
  return {
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers":
      "accept,authorization,content-type,idempotency-key,x-correlation-id,x-request-id,x-tenant-id,x-trace-id",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Expose-Headers": "x-correlation-id,x-request-id",
  };
}

function notFound(path: string): JsonRecord {
  return {
    detail: {
      error: {
        code: "RESOURCE_NOT_FOUND",
        message: `No route for ${path}`,
      },
    },
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function header(req: IncomingMessage, name: string): string | undefined {
  const value = req.headers[name.toLowerCase()];
  return Array.isArray(value) ? value[0] : value;
}

function stringField(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

async function readJsonBody(req: IncomingMessage): Promise<JsonRecord> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  const raw = Buffer.concat(chunks).toString("utf8").trim();
  if (!raw) return {};
  const parsed = JSON.parse(raw) as unknown;
  return parsed && typeof parsed === "object" && !Array.isArray(parsed)
    ? (parsed as JsonRecord)
    : {};
}

async function installApprovalQueue(page: Page, baseUrl: string): Promise<void> {
  await page.goto(`${baseUrl}/test-shell`);
  await page.evaluate(
    ({ authHeader, baseUrl: pageBaseUrl }) => {
      type ApprovalRow = ApprovalDto & { hiq?: HiqDto };

      const state = {
        bulkResult: null as BulkActionResponse | null,
        drawerOpen: false,
        rows: [] as ApprovalRow[],
        selected: [] as string[],
      };

      function escapeHtml(value: unknown): string {
        return String(value ?? "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;");
      }

      function selectedSet(): Set<string> {
        return new Set(state.selected);
      }

      function setSelected(next: Set<string>): void {
        state.selected = Array.from(next);
      }

      function queueHtml(): string {
        const rows = state.rows.map((row) => {
          const checked = selectedSet().has(row.id) ? "checked" : "";
          const disabled = row.state !== "pending" ? "disabled" : "";
          const quorum = `${row.quorum.approved}/${row.quorum.min}`;
          const families = row.quorum.distinctFamilyRequired
            ? "<span data-testid=\"quorum-family\">2-fam</span>"
            : "";
          const stages = row.stages
            .map((stage) => `<span data-testid="stage-${escapeHtml(row.id)}-${escapeHtml(stage.name)}">${escapeHtml(stage.name)} ${escapeHtml(stage.state)}</span>`)
            .join(" ");
          return [
            `<tr data-testid="approval-row-${escapeHtml(row.id)}">`,
            "<td>",
            `<input type="checkbox" aria-label="select ${escapeHtml(row.id)}" data-approval-id="${escapeHtml(row.id)}" ${checked} ${disabled} />`,
            "</td>",
            `<td class="approval-id">${escapeHtml(row.id)}</td>`,
            `<td>${escapeHtml(row.subject)}</td>`,
            `<td data-testid="approval-state-${escapeHtml(row.id)}">${escapeHtml(row.state)}</td>`,
            `<td data-testid="hiq-state-${escapeHtml(row.id)}">${escapeHtml(row.hiq?.status ?? "none")}</td>`,
            `<td data-testid="quorum-${escapeHtml(row.id)}">${quorum} ${families}</td>`,
            `<td>${stages}</td>`,
            "</tr>",
          ].join("");
        });

        const drawer = state.drawerOpen
          ? [
              "<aside role=\"dialog\" aria-label=\"BulkResultDrawer\" data-testid=\"bulk-result-drawer\">",
              "<h2>Bulk action result</h2>",
              state.bulkResult
                ? `<p>${state.bulkResult.summary.succeeded} succeeded / ${state.bulkResult.summary.failed} failed / ${state.bulkResult.summary.requested} total${state.bulkResult.partial ? " (partial)" : ""}</p>`
                : "<p>No result yet.</p>",
              "<ul>",
              ...(state.bulkResult?.results ?? []).map((row) =>
                `<li data-testid="bulk-result-${escapeHtml(row.id)}"><strong>${row.ok ? "OK" : "FAIL"}</strong> ${escapeHtml(row.id)} ${escapeHtml(row.error?.code ?? "")} ${escapeHtml(row.error?.message ?? "")}</li>`,
              ),
              "</ul>",
              "</aside>",
            ].join("")
          : "";

        return [
          "<section>",
          "<h1>F12 Approval Governance</h1>",
          `<p data-testid="selected-count">${state.selected.length} selected</p>`,
          "<button data-testid=\"batch-approve\">Batch approve</button>",
          "<table><thead><tr><th></th><th>ID</th><th>Subject</th><th>Approval</th><th>HIQ</th><th>Quorum</th><th>Stages</th></tr></thead>",
          `<tbody>${rows.join("")}</tbody></table>`,
          drawer,
          "</section>",
        ].join("");
      }

      function render(): void {
        document.body.innerHTML = queueHtml();
        document.querySelectorAll<HTMLInputElement>("input[data-approval-id]").forEach((input) => {
          input.addEventListener("change", () => {
            const next = selectedSet();
            const id = input.getAttribute("data-approval-id") ?? "";
            if (input.checked) next.add(id);
            else next.delete(id);
            setSelected(next);
            render();
          });
        });
        document.querySelector<HTMLButtonElement>("[data-testid='batch-approve']")?.addEventListener(
          "click",
          () => {
            void batchDecide("approve");
          },
        );
      }

      function upsertApproval(approval: ApprovalDto, hiq?: HiqDto): void {
        const current = state.rows.find((row) => row.id === approval.id);
        const next = { ...approval, hiq: hiq ?? current?.hiq };
        state.rows = state.rows.map((row) => (row.id === approval.id ? next : row));
      }

      async function load(): Promise<void> {
        const response = await fetch(`${pageBaseUrl}/bff/approvals`, {
          headers: { Accept: "application/json", Authorization: authHeader },
        });
        const body = (await response.json()) as { items: ApprovalDto[] };
        state.rows = body.items;
        for (const row of state.rows) {
          if (!row.hiq_intervention_id) continue;
          const hiqResponse = await fetch(`${pageBaseUrl}/bff/v5/interventions/${row.hiq_intervention_id}`, {
            headers: { Accept: "application/json", Authorization: authHeader },
          });
          if (hiqResponse.ok) {
            const hiqBody = (await hiqResponse.json()) as { data: HiqDto };
            upsertApproval(row, hiqBody.data);
          }
        }
        render();
      }

      async function postJson(path: string, body: JsonRecord): Promise<BrowserResponse> {
        const response = await fetch(`${pageBaseUrl}${path}`, {
          body: JSON.stringify(body),
          headers: {
            Accept: "application/json",
            Authorization: authHeader,
            "Content-Type": "application/json",
            "Idempotency-Key": `f12-${crypto.randomUUID()}`,
            "X-Correlation-Id": String(body.correlationId ?? "corr-f12-browser"),
            "X-Request-Id": `req-f12-${crypto.randomUUID()}`,
          },
          method: "POST",
        });
        return {
          body: (await response.json()) as JsonRecord,
          status: response.status,
        };
      }

      async function decide(id: string, decision: Decision): Promise<BrowserResponse> {
        const result = await postJson(`/bff/approvals/${id}/decide`, {
          correlationId: `corr-f12-${id}-decide`,
          decision,
          memo: `F12 ${decision}`,
        });
        if (result.status < 300) {
          const data = (result.body as CommandResponse).data;
          upsertApproval(data.approval, data.hiq);
          render();
        }
        return result;
      }

      async function twoManSign(
        id: string,
        signerId: string,
        roleFamily: string,
      ): Promise<BrowserResponse> {
        const result = await postJson(`/bff/approvals/${id}/two-man-sign`, {
          correlationId: `corr-f12-${id}-two-man`,
          roleFamily,
          signerId,
        });
        if (result.status < 300) {
          const data = (result.body as CommandResponse).data;
          upsertApproval(data.approval);
          render();
        }
        return result;
      }

      async function batchDecide(decision: Decision): Promise<BrowserResponse> {
        const result = await postJson("/bff/approvals/batch-decide", {
          correlationId: "corr-f12-batch",
          decision,
          ids: state.selected,
          memo: "F12 batch approve",
        });
        const body = result.body as unknown as BulkActionResponse;
        state.bulkResult = body;
        state.drawerOpen = body.partial || body.summary.failed > 0;
        const failed = new Set(body.results.filter((row) => !row.ok).map((row) => row.id));
        for (const row of body.results) {
          if (row.ok && row.data?.approval) upsertApproval(row.data.approval);
        }
        setSelected(failed);
        render();
        return result;
      }

      (window as unknown as Record<string, unknown>).__f12Approvals = {
        batchDecide,
        decide,
        load,
        state,
        twoManSign,
      };
    },
    { authHeader: AUTH_HEADER, baseUrl },
  );
  await page.evaluate(() => (window as any).__f12Approvals.load());
}

async function decideApproval(
  page: Page,
  id: string,
  decision: Decision,
): Promise<BrowserResponse> {
  return page.evaluate(
    ({ approvalId, nextDecision }) =>
      (window as any).__f12Approvals.decide(approvalId, nextDecision),
    { approvalId: id, nextDecision: decision },
  );
}

async function twoManSign(
  page: Page,
  id: string,
  signerId: string,
  roleFamily: string,
): Promise<BrowserResponse> {
  return page.evaluate(
    ({ approvalId, nextRoleFamily, nextSignerId }) =>
      (window as any).__f12Approvals.twoManSign(approvalId, nextSignerId, nextRoleFamily),
    { approvalId: id, nextRoleFamily: roleFamily, nextSignerId: signerId },
  );
}

async function browserState(page: Page): Promise<BrowserState> {
  return page.evaluate(() => {
    const state = (window as any).__f12Approvals.state;
    return {
      bulkResult: state.bulkResult,
      drawerOpen: state.drawerOpen,
      selected: state.selected,
    };
  });
}

async function selectApproval(page: Page, id: string): Promise<void> {
  await page.getByLabel(`select ${id}`).check();
}

test.describe("F12 approval governance", () => {
  let harness: ApprovalHarness;

  test.beforeEach(async () => {
    harness = new ApprovalHarness();
    await harness.start();
  });

  test.afterEach(async () => {
    await harness.stop();
  });

  test("single decide updates approval and linked HIQ", async ({ page }) => {
    await installApprovalQueue(page, harness.baseUrl);

    await expect(page.getByTestId(`approval-state-${SINGLE_APPROVAL_ID}`)).toHaveText("pending");
    await expect(page.getByTestId(`hiq-state-${SINGLE_APPROVAL_ID}`)).toHaveText("pending_review");

    const result = await decideApproval(page, SINGLE_APPROVAL_ID, "approve");

    expect(result.status, JSON.stringify(result.body)).toBe(202);
    expect(result.body).toMatchObject({
      data: {
        approval: {
          id: SINGLE_APPROVAL_ID,
          state: "approved",
        },
        command: "ApprovalGovernanceDecision",
        hiq: {
          id: SINGLE_HIQ_ID,
          resolution: "approved_by_governance",
          status: "resolved",
        },
        target: {
          id: SINGLE_APPROVAL_ID,
          type: "Approval",
        },
      },
      meta: {
        durable: true,
        liveCapitalSideEffects: false,
      },
      status: "accepted",
    });

    await expect(page.getByTestId(`approval-state-${SINGLE_APPROVAL_ID}`)).toHaveText("approved");
    await expect(page.getByTestId(`hiq-state-${SINGLE_APPROVAL_ID}`)).toHaveText("resolved");
    expect(harness.approval(SINGLE_APPROVAL_ID).state).toBe("approved");
    expect(harness.linkedHiq(SINGLE_HIQ_ID)).toMatchObject({
      resolution: "approved_by_governance",
      status: "resolved",
    });
    expect(harness.audit).toEqual([
      expect.objectContaining({
        action: "approval.approve",
        target: SINGLE_APPROVAL_ID,
      }),
    ]);
    expect(harness.requests[0]).toMatchObject({
      authorization: AUTH_HEADER,
      method: "POST",
      path: `/bff/approvals/${SINGLE_APPROVAL_ID}/decide`,
    });
    expect(harness.requests[0].idempotencyKey).toMatch(/^f12-/);
  });

  test("two-man sign enforces distinct signer and shows quorum progress", async ({
    page,
  }) => {
    await installApprovalQueue(page, harness.baseUrl);

    await expect(page.getByTestId(`quorum-${TWO_MAN_APPROVAL_ID}`)).toContainText("1/2");
    await expect(page.getByTestId(`quorum-${TWO_MAN_APPROVAL_ID}`)).toContainText("2-fam");

    const sameSigner = await twoManSign(
      page,
      TWO_MAN_APPROVAL_ID,
      OPERATOR_ID,
      "operator",
    );
    expect(sameSigner.status, JSON.stringify(sameSigner.body)).toBe(409);
    expect(sameSigner.body).toMatchObject({
      detail: {
        error: {
          code: "TWO_MAN_REQUIRED",
          details: {
            approvalId: TWO_MAN_APPROVAL_ID,
            reason: "TWO_MAN_DISTINCT_OPERATOR_REQUIRED",
          },
        },
      },
    });
    await expect(page.getByTestId(`quorum-${TWO_MAN_APPROVAL_ID}`)).toContainText("1/2");

    const distinctSigner = await twoManSign(
      page,
      TWO_MAN_APPROVAL_ID,
      "risk-owner-f12",
      "risk",
    );

    expect(distinctSigner.status, JSON.stringify(distinctSigner.body)).toBe(202);
    expect(distinctSigner.body).toMatchObject({
      data: {
        approval: {
          id: TWO_MAN_APPROVAL_ID,
          quorum: {
            approved: 2,
            distinctFamilyRequired: true,
            min: 2,
          },
          state: "approved",
        },
        action: "two-man-sign",
      },
    });

    await expect(page.getByTestId(`quorum-${TWO_MAN_APPROVAL_ID}`)).toContainText("2/2");
    await expect(page.getByTestId(`stage-${TWO_MAN_APPROVAL_ID}-risk`)).toContainText(
      "risk approved",
    );
    await expect(page.getByTestId(`approval-state-${TWO_MAN_APPROVAL_ID}`)).toHaveText(
      "approved",
    );
    expect(harness.approval(TWO_MAN_APPROVAL_ID).quorum).toMatchObject({
      approved: 2,
      distinctFamilyRequired: true,
      min: 2,
    });
  });

  test("batch partial failure opens BulkResultDrawer and keeps failed selected", async ({
    page,
  }) => {
    await installApprovalQueue(page, harness.baseUrl);

    await selectApproval(page, BATCH_OK_APPROVAL_ID);
    await selectApproval(page, BATCH_FAILED_APPROVAL_ID);
    await expect(page.getByTestId("selected-count")).toHaveText("2 selected");

    await page.getByTestId("batch-approve").click();

    await expect(page.getByTestId("bulk-result-drawer")).toBeVisible();
    await expect(page.getByRole("dialog", { name: "BulkResultDrawer" })).toContainText(
      "1 succeeded / 1 failed / 2 total (partial)",
    );
    await expect(page.getByTestId(`bulk-result-${BATCH_OK_APPROVAL_ID}`)).toContainText("OK");
    await expect(page.getByTestId(`bulk-result-${BATCH_FAILED_APPROVAL_ID}`)).toContainText(
      "FAIL",
    );
    await expect(page.getByTestId(`bulk-result-${BATCH_FAILED_APPROVAL_ID}`)).toContainText(
      "PRECONDITION_FAILED",
    );
    await expect(page.getByTestId(`approval-state-${BATCH_OK_APPROVAL_ID}`)).toHaveText(
      "approved",
    );
    await expect(page.getByTestId(`approval-state-${BATCH_FAILED_APPROVAL_ID}`)).toHaveText(
      "pending",
    );
    await expect(page.getByTestId("selected-count")).toHaveText("1 selected");
    await expect(page.getByLabel(`select ${BATCH_FAILED_APPROVAL_ID}`)).toBeChecked();
    await expect(page.getByLabel(`select ${BATCH_OK_APPROVAL_ID}`)).not.toBeChecked();

    const state = await browserState(page);
    expect(state.drawerOpen).toBe(true);
    expect(state.bulkResult?.partial).toBe(true);
    expect(state.selected).toEqual([BATCH_FAILED_APPROVAL_ID]);
    expect(harness.approval(BATCH_OK_APPROVAL_ID).state).toBe("approved");
    expect(harness.approval(BATCH_FAILED_APPROVAL_ID).state).toBe("pending");
  });
});
