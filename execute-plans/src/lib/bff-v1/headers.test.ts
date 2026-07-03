// AG-DYNUI-LIVE-AUTH-003 — shared BFF auth header builder.
//
// Coverage:
//   * buildHeaders always sends Accept
//   * buildHeaders sends no Authorization/X-Tenant-Id when no auth is available
//   * setAuthProvider injects Authorization / X-Tenant-Id
//   * extra headers pass through and win on conflicts
//   * setAuthProvider(undefined) resets to the default (storage/env) provider

import { describe, it, expect, afterEach } from "vitest";
import { buildHeaders, setAuthProvider } from "./headers";

afterEach(() => {
  setAuthProvider(undefined);
  window.localStorage.clear();
});

describe("buildHeaders — no auth available", () => {
  it("sends only Accept when no token or tenant is set", () => {
    const headers = buildHeaders({ method: "GET" });
    expect(headers.Accept).toBe("application/json");
    expect(headers.Authorization).toBeUndefined();
    expect(headers["X-Tenant-Id"]).toBeUndefined();
  });
});

describe("buildHeaders — setAuthProvider", () => {
  it("sends Authorization: Bearer <token> when a bearer token is provided", () => {
    setAuthProvider(() => ({ bearerToken: "test-bearer-token" }));

    const headers = buildHeaders({ method: "GET" });

    expect(headers.Authorization).toBe("Bearer test-bearer-token");
  });

  it("sends X-Tenant-Id when a tenant is provided", () => {
    setAuthProvider(() => ({ bearerToken: "test-bearer-token", tenantId: "tenant-42" }));

    const headers = buildHeaders({ method: "GET" });

    expect(headers["X-Tenant-Id"]).toBe("tenant-42");
  });

  it("resets to no auth after setAuthProvider(undefined)", () => {
    setAuthProvider(() => ({ bearerToken: "test-bearer-token" }));
    setAuthProvider(undefined);

    const headers = buildHeaders({ method: "GET" });

    expect(headers.Authorization).toBeUndefined();
  });
});

describe("buildHeaders — browser storage fallback", () => {
  it("reads the bearer token from pantheon.bff.bearerToken", () => {
    window.localStorage.setItem("pantheon.bff.bearerToken", "storage-token");

    const headers = buildHeaders({ method: "GET" });

    expect(headers.Authorization).toBe("Bearer storage-token");
  });

  it("falls back to the legacy pantheon_operator_token key", () => {
    window.localStorage.setItem("pantheon_operator_token", "legacy-token");

    const headers = buildHeaders({ method: "GET" });

    expect(headers.Authorization).toBe("Bearer legacy-token");
  });
});

describe("buildHeaders — extra headers", () => {
  it("merges caller-supplied extra headers", () => {
    setAuthProvider(() => ({ bearerToken: "test-bearer-token" }));

    const headers = buildHeaders({
      method: "POST",
      extra: { "Content-Type": "application/json", "If-Match": '"etag-1"' },
    });

    expect(headers.Authorization).toBe("Bearer test-bearer-token");
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers["If-Match"]).toBe('"etag-1"');
  });

  it("lets extra headers win on conflicts", () => {
    setAuthProvider(() => ({ bearerToken: "test-bearer-token" }));

    const headers = buildHeaders({ method: "GET", extra: { Accept: "text/plain" } });

    expect(headers.Accept).toBe("text/plain");
  });
});
