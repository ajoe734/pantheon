export type AssistantUiContextVersion = "assistant_ui_context.v1";

export interface AssistantRouteHint {
  path: string;
  name?: string;
  params?: Record<string, string>;
  query?: Record<string, string>;
}

export interface AssistantVisibleSurfaceHint {
  workbench?: string;
  screenId?: string;
  componentId?: string;
  heading?: string;
}

export interface AssistantSelectedEntityHint {
  entityType: string;
  entityId: string;
  label?: string;
  href?: string;
}

export interface AssistantFormFieldHint {
  name: string;
  label: string;
  value?: unknown;
  valueState: "present" | "empty" | "redacted" | "unavailable";
  dirty?: boolean;
  disabled?: boolean;
  required?: boolean;
  validatorRefs: Array<{
    type: "required" | "enum" | "min" | "max" | "regex" | "custom";
    message?: string;
    params?: Record<string, unknown>;
  }>;
  optionSet?: Array<{ value: string; label: string; disabled?: boolean }>;
}

export interface AssistantFormRegistryHint {
  formId: string;
  action: {
    kind: "bff_command" | "bff_route" | "frontend_patch_only";
    method?: "POST" | "PUT" | "PATCH";
    href?: string;
    command?: string;
    idempotencyRequired?: boolean;
    submitAuthority: "bff";
  };
  fields: AssistantFormFieldHint[];
  dirty: boolean;
  errors: Array<{ field?: string; message: string; code?: string }>;
}

export interface AssistantTableContextHint {
  tableId: string;
  filters: Array<{ key: string; operator: string; value: unknown; label?: string }>;
  sort?: Array<{ key: string; direction: "asc" | "desc" }>;
  selectedRows: Array<{ id: string; entityType?: string; label?: string }>;
  visibleColumns?: string[];
}

export interface AssistantAttachmentHint {
  attachmentId: string;
  kind: "image" | "csv" | "json" | "text" | "other";
  name: string;
  sizeBytes?: number;
  proxyHref?: string;
}

export interface AssistantVisibleErrorHint {
  message: string;
  source?: string;
  code?: string;
}

export interface AssistantContextRefHint {
  sourceId?: string;
  href: string;
  label?: string;
}

export interface AssistantUiContextV1 {
  version: AssistantUiContextVersion;
  capturedAt: string;
  route: AssistantRouteHint;
  visibleSurface?: AssistantVisibleSurfaceHint;
  selectedEntity?: AssistantSelectedEntityHint;
  formRegistry?: AssistantFormRegistryHint;
  tableContext?: AssistantTableContextHint;
  attachments?: AssistantAttachmentHint[];
  visibleErrors: AssistantVisibleErrorHint[];
  contextRefs: AssistantContextRefHint[];
}

export interface BuildAssistantUiContextOptions {
  now?: () => Date;
  route?: Partial<AssistantRouteHint>;
  visibleSurface?: AssistantVisibleSurfaceHint;
  selectedEntity?: AssistantSelectedEntityHint;
  formRegistry?: AssistantFormRegistryHint;
  tableContext?: AssistantTableContextHint;
  attachments?: AssistantAttachmentHint[];
  visibleErrors?: AssistantVisibleErrorHint[];
  contextRefs?: AssistantContextRefHint[];
}

const SENSITIVE_FIELD_PATTERN =
  /(authorization|bearer|cookie|credential|jwt|key|passphrase|password|provider|secret|session|token)/i;

function currentPath(): string {
  if (typeof window === "undefined" || !window.location) return "/";
  return `${window.location.pathname || "/"}${window.location.search || ""}${window.location.hash || ""}`;
}

function currentQuery(): Record<string, string> {
  if (typeof window === "undefined" || !window.location) return {};
  return Object.fromEntries(new URLSearchParams(window.location.search || "").entries());
}

function sanitizeField(field: AssistantFormFieldHint): AssistantFormFieldHint {
  const sensitive = SENSITIVE_FIELD_PATTERN.test(`${field.name} ${field.label}`);
  if (!sensitive && field.valueState !== "redacted") return field;
  const { value: _value, ...rest } = field;
  return {
    ...rest,
    valueState: "redacted",
  };
}

function sanitizeFormRegistry(registry?: AssistantFormRegistryHint): AssistantFormRegistryHint | undefined {
  if (!registry) return undefined;
  return {
    ...registry,
    fields: registry.fields.map(sanitizeField),
  };
}

export function buildAssistantUiContext(options: BuildAssistantUiContextOptions = {}): AssistantUiContextV1 {
  const capturedAt = (options.now?.() ?? new Date()).toISOString();
  const routePath = options.route?.path || currentPath();
  return {
    version: "assistant_ui_context.v1",
    capturedAt,
    route: {
      path: routePath,
      ...(options.route?.name ? { name: options.route.name } : {}),
      ...(options.route?.params ? { params: options.route.params } : {}),
      query: options.route?.query ?? currentQuery(),
    },
    ...(options.visibleSurface ? { visibleSurface: options.visibleSurface } : {}),
    ...(options.selectedEntity ? { selectedEntity: options.selectedEntity } : {}),
    ...(options.formRegistry ? { formRegistry: sanitizeFormRegistry(options.formRegistry) } : {}),
    ...(options.tableContext ? { tableContext: options.tableContext } : {}),
    ...(options.attachments ? { attachments: options.attachments } : {}),
    visibleErrors: options.visibleErrors ?? [],
    contextRefs: options.contextRefs ?? [],
  };
}

export function assistantUiContextMetadata(
  context: AssistantUiContextV1,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    ...extra,
    assistant_context_version: context.version,
    assistant_ui_context: context,
  };
}
