import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

import { paths } from "../paths";
import { ERROR_CODES } from "@/lib/v4/errorCodes";
import { ACTION_COMMAND_STATUSES, EVIDENCE_CAPABILITY_MAP } from "../dto";
import { SSE_CHANNELS } from "../sse/channels";

const repoRoot = process.cwd();

function readIfExists(rel: string): string {
  const p = path.join(repoRoot, rel);
  return fs.existsSync(p) ? fs.readFileSync(p, "utf8") : "";
}

const openApi = readIfExists(".lovable/feedback/2026-05-07-final/Pantheon_BFF_OpenAPI_3_1.yaml");
const asyncApi = readIfExists(".lovable/feedback/2026-05-07-final/Pantheon_BFF_AsyncAPI_SSE.md")
  || readIfExists(".lovable/spec/bff/2026-05-07-H/Pantheon_BFF_AsyncAPI_SSE.md");

describe("BFF v1 contract drift", () => {
  it("canonical frontend path builders are reflected in OpenAPI text", () => {
    expect(openApi.length, "OpenAPI artifact must be present").toBeGreaterThan(1000);

    const canonicalPaths = [
      paths.me(),
      paths.authRefresh(),
      paths.logout(),
      paths.strategies(),
      paths.personas(),
      paths.capitalPools(),
      paths.rebalances(),
      paths.deployments(),
      paths.evolutionPrograms(),
      paths.researchExperiments(),
      paths.artifacts(),
      paths.jobs(),
      paths.approvals(),
      paths.approvalDecide("{id}").replace("%7Bid%7D", "{id}"),
      paths.alerts(),
      paths.incidents(),
      paths.audit(),
      paths.runtimes(),
      paths.mcpServers(),
      paths.mcpServerImportTools("{id}").replace("%7Bid%7D", "{id}"),
      paths.mcpTools(),
      paths.skills(),
      paths.channels(),
      paths.tools(),
      paths.rankingFormulas(),
      paths.sse(),
      paths.agoraSignals(),
      paths.agoraInbox(),
      paths.agoraJournal(),
      paths.agoraPostmortems(),
      paths.agoraAskSessions(),
      paths.v5LoopRuns(),
      paths.v5SentinelFindings(),
      paths.v5Interventions(),
      paths.v5InterventionDecide("{id}").replace("%7Bid%7D", "{id}"),
      paths.v5ExecutionPersonaHealth(),
    ];

    for (const p of canonicalPaths) {
      expect(openApi, `OpenAPI must include ${p}`).toContain(p);
    }
  });

  it("ActionCommandStatus is a named schema and does not contain requires_* success states", () => {
    expect(ACTION_COMMAND_STATUSES).toEqual(["accepted", "queued", "completed"]);
    expect(openApi).toMatch(/ActionCommandStatus:/);
    expect(openApi).not.toMatch(/requires_confirm_token.*enum|requires_approval.*enum|requires_two_man.*enum/s);
  });

  it("ErrorCode runtime list is represented in OpenAPI or final docs", () => {
    const finalDoc = readIfExists(".lovable/feedback/2026-05-07-final/Pantheon_BFF_Contract_Spec_2026-05-07_Final.md");
    const corpus = `${openApi}\n${finalDoc}`;
    for (const code of ERROR_CODES) {
      expect(corpus, `contract docs must include ErrorCode ${code}`).toContain(code);
    }
  });

  it("SSE channel runtime list is represented in AsyncAPI or final docs", () => {
    const finalDoc = readIfExists(".lovable/feedback/2026-05-07-final/Pantheon_BFF_Contract_Spec_2026-05-07_Final.md");
    const corpus = `${asyncApi}\n${finalDoc}`;
    for (const channel of SSE_CHANNELS) {
      expect(corpus, `SSE contract docs must include channel ${channel}`).toContain(channel);
    }
  });

  it("Evidence capability map has a capability for every accepted evidence kind", () => {
    for (const [kind, cap] of Object.entries(EVIDENCE_CAPABILITY_MAP)) {
      expect(kind.length).toBeGreaterThan(0);
      expect(cap.length).toBeGreaterThan(0);
      expect(cap).toMatch(/^([a-z0-9_]+|\*)(\.[a-z0-9_*]+)*$/);
    }
  });
});
