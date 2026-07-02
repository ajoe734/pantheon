import { describe, expect, it } from "vitest";
import { normalizeLiveListResponse } from "./lists";

describe("normalizeLiveListResponse", () => {
  it("reads canonical data.items list envelopes", () => {
    const envelope = normalizeLiveListResponse<{ id: string }>(
      {
        data: {
          items: [{ id: "persona-alpha" }],
          summary: {
            total: 1,
            returned_count: 1,
          },
        },
        page_info: { total: 1 },
        meta: {
          surfaces: {
            persona_league: { status: "ok", source: "service_store" },
          },
        },
      },
      "personaLeague",
    );

    expect(envelope.items).toEqual([{ id: "persona-alpha" }]);
    expect(envelope.cursor).toEqual({ total: 1 });
    expect(envelope.estimatedTotal).toBe(1);
    expect(envelope.meta?.surfaces).toHaveProperty("persona_league");
  });
});
