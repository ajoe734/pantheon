import type { DashboardRecipeV2, WidgetSpecV2 } from "./types";

export interface AgoraWidgetValidationIssue {
  code: string;
  path?: string;
  message: string;
  [key: string]: unknown;
}

export interface AgoraWidgetValidationResult {
  valid: boolean;
  errors: AgoraWidgetValidationIssue[];
  warnings: AgoraWidgetValidationIssue[];
  registry_version: string;
  schema_hash?: string;
  [key: string]: unknown;
}

function resolvedBase(baseUrl?: string): string {
  if (baseUrl) return baseUrl.replace(/\/+$/, "");
  if (typeof window !== "undefined" && window.location?.origin) {
    return window.location.origin.replace(/\/+$/, "");
  }
  return "";
}

function recordFrom(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function issuesFrom(value: unknown): AgoraWidgetValidationIssue[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    const record = recordFrom(item);
    return {
      ...record,
      code: String(record.code ?? "VALIDATION_ISSUE"),
      path: typeof record.path === "string" ? record.path : undefined,
      message: String(record.message ?? record.code ?? "Widget validation issue"),
    };
  });
}

function normalizeValidationPayload(payload: unknown): AgoraWidgetValidationResult {
  const root = recordFrom(payload);
  const data = recordFrom(root.data ?? root);
  return {
    ...data,
    valid: data.valid === true,
    errors: issuesFrom(data.errors),
    warnings: issuesFrom(data.warnings),
    registry_version: String(data.registry_version ?? "widget_registry.v1"),
    schema_hash: typeof data.schema_hash === "string" ? data.schema_hash : undefined,
  };
}

function validationResultFromErrorEnvelope(payload: unknown): AgoraWidgetValidationResult {
  const root = recordFrom(payload);
  const error = recordFrom(root.error);
  const details = recordFrom(error.details ?? error.details_extra ?? root.details);
  const errors = issuesFrom(details.errors);
  return {
    valid: false,
    errors: errors.length
      ? errors
      : [
          {
            code: String(error.code ?? "WIDGET_SPEC_INVALID"),
            message: String(error.message ?? "WidgetSpec v2 validation failed"),
          },
        ],
    warnings: issuesFrom(details.warnings),
    registry_version: String(details.registry_version ?? "widget_registry.v1"),
    schema_hash: typeof details.schema_hash === "string" ? details.schema_hash : undefined,
  };
}

async function parseJson(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { error: { message: text } };
  }
}

/** Fetch a dashboard recipe by its accepted recipe_id. Returns null when not found. */
export async function getDashboardRecipeById(
  recipeId: string,
  baseUrl?: string,
): Promise<DashboardRecipeV2 | null> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/dashboard-recipes/${encodeURIComponent(recipeId)}`;
  const res = await fetch(url, {
    method: "GET",
    credentials: "include",
    headers: { Accept: "application/json" },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    const body = await parseJson(res);
    const message = recordFrom(recordFrom(body).error).message ?? `GET ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  const body = await parseJson(res);
  const root = recordFrom(body);
  return recordFrom(root.data ?? root) as unknown as DashboardRecipeV2;
}

export async function validateAgoraWidget(
  widget: WidgetSpecV2,
  baseUrl?: string,
): Promise<AgoraWidgetValidationResult> {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/widgets/validate`;
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(widget),
  });
  const body = await parseJson(res);

  if (res.status === 422) return validationResultFromErrorEnvelope(body);
  if (!res.ok) {
    const message = recordFrom(recordFrom(body).error).message ?? `POST ${url} failed ${res.status}`;
    throw new Error(String(message));
  }
  return normalizeValidationPayload(body);
}
