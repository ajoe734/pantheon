export const DEFAULT_FE_OPERATOR_ID = "op-fe-gate";
export const DEFAULT_FE_TENANT_ID = "tenant-dev";
export const DEFAULT_FE_AUTH_ROLES = ["operator", "reviewer", "approver"] as const;
export const DEFAULT_DEV_AUTH_TOKEN = `${DEFAULT_FE_OPERATOR_ID}:${DEFAULT_FE_AUTH_ROLES.join(
  ",",
)}:mfa`;

export const BFF_AUTH_STORAGE_KEYS = {
  bearerToken: "pantheon.bff.bearerToken",
  legacyBearerToken: "pantheon_operator_token",
  tenantId: "pantheon.bff.tenantId",
  legacyTenantId: "pantheon_tenant_id",
  devOidcSession: "pantheon.e2e.devOidcSession",
} as const;

export type HeaderMap = Record<string, string>;

export type E2ePage = {
  addInitScript<Arg>(
    script: (arg: Arg) => unknown | Promise<unknown>,
    arg: Arg,
  ): Promise<void>;
  evaluate<Result>(script: () => Result | Promise<Result>): Promise<Result>;
  evaluate<Result, Arg>(
    script: (arg: Arg) => Result | Promise<Result>,
    arg: Arg,
  ): Promise<Result>;
  goto(url: string): Promise<unknown>;
};

export type DevLoginSession = {
  authorization: string;
  operatorId: string;
  roles: string[];
  tenantId: string;
  token: string;
};

export type AuthHeaderOptions = {
  accept?: string;
  contentType?: string;
  env?: Record<string, string | undefined>;
  extra?: HeaderMap;
  includeContentType?: boolean;
  tenantId?: string;
  token?: string;
};

export type DevLoginOptions = {
  goto?: string | false;
  operatorId?: string;
  pageBaseUrl?: string;
  roles?: string[];
  storage?: "session" | "local" | "both";
  tenantId?: string;
  token?: string;
};

function envValue(env: Record<string, string | undefined>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = env[key]?.trim();
    if (value) return value;
  }
  return undefined;
}

function defaultEnv(): Record<string, string | undefined> {
  return typeof process === "undefined" ? {} : process.env;
}

export function normalizeBearerToken(token: string): string {
  const trimmed = token.trim();
  return trimmed.toLowerCase().startsWith("bearer ")
    ? trimmed.slice("bearer ".length).trim()
    : trimmed;
}

export function makeDevAuthToken(options: {
  operatorId?: string;
  roles?: readonly string[];
  mfa?: boolean;
} = {}): string {
  const operatorId = options.operatorId ?? DEFAULT_FE_OPERATOR_ID;
  const roles = options.roles ?? DEFAULT_FE_AUTH_ROLES;
  const suffix = options.mfa === false ? "" : ":mfa";
  return `${operatorId}:${roles.join(",")}${suffix}`;
}

export function authToken(options: AuthHeaderOptions = {}): string {
  const env = options.env ?? defaultEnv();
  return normalizeBearerToken(
    options.token ??
      envValue(env, [
        "BFF_AUTH_TOKEN",
        "PANTHEON_BFF_SMOKE_BEARER_TOKEN",
        "PANTHEON_BFF_SMOKE_TOKEN",
        "VITE_BFF_DEV_BEARER_TOKEN",
      ]) ??
      DEFAULT_DEV_AUTH_TOKEN,
  );
}

export function bearerHeader(token?: string): string {
  return `Bearer ${normalizeBearerToken(token ?? authToken())}`;
}

export function withBearer<T extends HeaderMap>(
  headers: T,
  token?: string,
): T & { Authorization: string } {
  return {
    ...headers,
    Authorization: bearerHeader(token),
  };
}

export function authHeaders(options: AuthHeaderOptions = {}): HeaderMap {
  const headers: HeaderMap = {
    Accept: options.accept ?? "application/json",
    Authorization: bearerHeader(authToken(options)),
    ...(options.extra ?? {}),
  };
  const tenantId = options.tenantId ?? envValue(options.env ?? defaultEnv(), [
    "BFF_TENANT_ID",
    "PANTHEON_TENANT_ID",
  ]);
  if (tenantId) headers["X-Tenant-Id"] = tenantId;
  if (options.includeContentType || options.contentType) {
    headers["Content-Type"] = options.contentType ?? "application/json";
  }
  return headers;
}

export function mutationAuthHeaders(options: AuthHeaderOptions = {}): HeaderMap {
  return authHeaders({ ...options, includeContentType: true });
}

export function actorFromAuthorization(value: string | undefined): string {
  if (!value) return "";
  const token = normalizeBearerToken(value);
  return token.split(":")[0] ?? "";
}

export function devLoginSession(options: DevLoginOptions = {}): DevLoginSession {
  const roles = options.roles ?? [...DEFAULT_FE_AUTH_ROLES];
  const token = authToken({
    token: options.token ?? makeDevAuthToken({ operatorId: options.operatorId, roles }),
  });
  return {
    authorization: bearerHeader(token),
    operatorId: (options.operatorId ?? actorFromAuthorization(token)) || DEFAULT_FE_OPERATOR_ID,
    roles,
    tenantId: options.tenantId ?? DEFAULT_FE_TENANT_ID,
    token,
  };
}

export async function installOidcDevLogin(
  page: E2ePage,
  options: DevLoginOptions = {},
): Promise<DevLoginSession> {
  const session = devLoginSession(options);
  const storage = options.storage ?? "both";

  await page.addInitScript(
    ({ keys, session: nextSession, storageMode }) => {
      const write = (target: Storage | undefined) => {
        if (!target) return;
        target.setItem(keys.bearerToken, nextSession.token);
        target.setItem(keys.legacyBearerToken, nextSession.token);
        target.setItem(keys.tenantId, nextSession.tenantId);
        target.setItem(keys.legacyTenantId, nextSession.tenantId);
        target.setItem(
          keys.devOidcSession,
          JSON.stringify({
            aud: "pantheon-bff",
            auth_time: Math.floor(Date.now() / 1000),
            iss: "pantheon-e2e-dev-login",
            roles: nextSession.roles,
            sub: nextSession.operatorId,
            tenant_id: nextSession.tenantId,
          }),
        );
      };
      try {
        if (storageMode === "session" || storageMode === "both") write(window.sessionStorage);
        if (storageMode === "local" || storageMode === "both") write(window.localStorage);
      } catch {
        // Init scripts can run before the page has a durable origin.
      }
    },
    { keys: BFF_AUTH_STORAGE_KEYS, session, storageMode: storage },
  );

  await page
    .evaluate(
      ({ keys, session: nextSession, storageMode }) => {
        const write = (target: Storage | undefined) => {
          if (!target) return;
          target.setItem(keys.bearerToken, nextSession.token);
          target.setItem(keys.legacyBearerToken, nextSession.token);
          target.setItem(keys.tenantId, nextSession.tenantId);
          target.setItem(keys.legacyTenantId, nextSession.tenantId);
          target.setItem(
            keys.devOidcSession,
            JSON.stringify({
              aud: "pantheon-bff",
              auth_time: Math.floor(Date.now() / 1000),
              iss: "pantheon-e2e-dev-login",
              roles: nextSession.roles,
              sub: nextSession.operatorId,
              tenant_id: nextSession.tenantId,
            }),
          );
        };
        if (storageMode === "session" || storageMode === "both") write(window.sessionStorage);
        if (storageMode === "local" || storageMode === "both") write(window.localStorage);
      },
      { keys: BFF_AUTH_STORAGE_KEYS, session, storageMode: storage },
    )
    .catch(() => undefined);

  if (options.goto !== false && options.goto) {
    const base = options.pageBaseUrl?.replace(/\/$/, "");
    await page.goto(base ? `${base}${options.goto}` : options.goto);
  }

  return session;
}

export const installDevOidcLogin = installOidcDevLogin;
export const devLogin = installOidcDevLogin;
