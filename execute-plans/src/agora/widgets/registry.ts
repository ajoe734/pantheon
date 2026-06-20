import type { ChartSpecV1, WidgetSpecV2 } from "@/lib/bff-v1/agora/types";
import { AGORA_V1_CONTRACT_SNAPSHOT } from "@/lib/bff-v1/agora/types";

import widgetRegistrySource from "../../../../docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/widget_registry.v1.json";

export const WIDGET_REGISTRY_VERSION = "widget_registry.v1" as const;
export const WIDGET_REGISTRY_ENTRY_COUNT = 42 as const;

export const AGORA_WIDGET_CONTRACT_HASHES = {
  widgetRegistryV1: "add7f379f4ff1f3c0c0930a566a269897cd497fb22ef53bbdfecb2b1d85c34d4",
  widgetSpecV2: AGORA_V1_CONTRACT_SNAPSHOT.files["specs/agora/v2/widget_spec_v2.schema.json"],
  chartSpecV1: AGORA_V1_CONTRACT_SNAPSHOT.files["specs/agora/v2/chart_spec_v1.schema.json"],
  dashboardRecipeV2: AGORA_V1_CONTRACT_SNAPSHOT.files["specs/agora/v2/dashboard_recipe_v2.schema.json"],
} as const;

export const CHART_SPEC_KINDS = [
  "metric",
  "table",
  "line",
  "area",
  "bar",
  "stacked_bar",
  "heatmap",
  "scatter",
  "network",
  "timeline",
  "sankey",
  "candlestick",
  "gauge",
] as const;

export const CHART_ENCODING_CHANNELS = [
  "x",
  "y",
  "color",
  "size",
  "shape",
  "opacity",
  "row",
  "column",
  "label",
  "source",
  "target",
  "value",
  "open",
  "high",
  "low",
  "close",
  "volume",
  "time",
] as const;

export const CHART_TRANSFORM_TYPES = [
  "filter",
  "sort",
  "top_k",
  "aggregate",
  "window",
  "rolling_mean",
  "rolling_sum",
  "percent_change",
  "rank",
  "percentile",
  "normalize",
  "winsorize",
  "zscore",
  "bucket",
  "time_bucket",
  "join_by_key",
] as const;

export const WIDGET_INTERACTION_KINDS = [
  "open_candidate",
  "open_strategy",
  "open_position",
  "open_evidence",
  "open_research_run",
  "open_shadow_record",
  "filter_workspace",
  "cross_highlight",
  "add_to_monitoring",
  "remove_from_monitoring",
  "park_candidate",
  "request_more_research",
  "send_to_shadow",
  "request_widget_revision",
  "create_journal_note",
] as const;

export const BLOCKED_INTERACTION_KINDS = [
  "place_order",
  "submit_order",
  "enable_live",
  "bind_capital",
  "runtime_binding",
  "invoke_broker",
] as const;

export type ChartSpecKind = typeof CHART_SPEC_KINDS[number];
export type ChartEncodingChannel = typeof CHART_ENCODING_CHANNELS[number];
export type ChartTransformType = typeof CHART_TRANSFORM_TYPES[number];
export type WidgetInteractionKind = typeof WIDGET_INTERACTION_KINDS[number];
export type WidgetRendererMode = "builtin" | "chart_spec";
export type WidgetSensitivity = WidgetSpecV2["sensitivity"];

export interface WidgetRegistryEntry {
  widget_type: string;
  display_name: string;
  description: string;
  category: string;
  renderer: WidgetRendererMode;
  allowed_chart_kinds: ChartSpecKind[];
  allowed_data_sources: string[];
  required_fields: string[];
  optional_fields: string[];
  allowed_transforms: ChartTransformType[];
  allowed_interactions: WidgetInteractionKind[];
  sensitivity: WidgetSensitivity;
  phase: string;
  status: "active" | "inactive" | "deprecated";
}

export interface WidgetRegistry {
  registry_version: typeof WIDGET_REGISTRY_VERSION;
  schema_version: string;
  created_at: string;
  entries: WidgetRegistryEntry[];
}

export interface WidgetRegistryValidationFailure {
  code:
    | "INVALID_SPEC_VERSION"
    | "INVALID_REGISTRY_VERSION"
    | "UNKNOWN_WIDGET_TYPE"
    | "INACTIVE_WIDGET_TYPE"
    | "UNAPPROVED_DATA_SOURCE"
    | "UNAPPROVED_CHART_KIND"
    | "UNAPPROVED_TRANSFORM"
    | "UNAPPROVED_INTERACTION"
    | "SENSITIVITY_DOWNGRADE";
  message: string;
}

export type WidgetRegistryValidationResult =
  | { ok: true; entry: WidgetRegistryEntry }
  | ({ ok: false } & WidgetRegistryValidationFailure);

export const WIDGET_REGISTRY = widgetRegistrySource as WidgetRegistry;
export const WIDGET_REGISTRY_ENTRIES = WIDGET_REGISTRY.entries;

const entryByWidgetType = new Map(WIDGET_REGISTRY_ENTRIES.map((entry) => [entry.widget_type, entry]));
const chartKindSet = new Set<string>(CHART_SPEC_KINDS);
const encodingSet = new Set<string>(CHART_ENCODING_CHANNELS);
const transformSet = new Set<string>(CHART_TRANSFORM_TYPES);
const interactionSet = new Set<string>(WIDGET_INTERACTION_KINDS);
const blockedInteractionSet = new Set<string>(BLOCKED_INTERACTION_KINDS);

const sensitivityRank: Record<WidgetSensitivity, number> = {
  public_market: 0,
  user_private: 1,
  broker_sensitive: 2,
  restricted: 3,
};

export function getWidgetRegistryEntry(widgetType: string): WidgetRegistryEntry | undefined {
  return entryByWidgetType.get(widgetType);
}

export function getActiveWidgetTypes(): string[] {
  return WIDGET_REGISTRY_ENTRIES.filter((entry) => entry.status === "active").map((entry) => entry.widget_type);
}

export function isActiveWidgetType(widgetType: string): boolean {
  return getWidgetRegistryEntry(widgetType)?.status === "active";
}

export function isChartSpecKind(value: unknown): value is ChartSpecKind {
  return typeof value === "string" && chartKindSet.has(value);
}

export function isChartEncodingChannel(value: unknown): value is ChartEncodingChannel {
  return typeof value === "string" && encodingSet.has(value);
}

export function isChartTransformType(value: unknown): value is ChartTransformType {
  return typeof value === "string" && transformSet.has(value);
}

export function isWidgetInteractionKind(value: unknown): value is WidgetInteractionKind {
  return typeof value === "string" && interactionSet.has(value);
}

export function isBlockedInteractionKind(value: unknown): boolean {
  return typeof value === "string" && blockedInteractionSet.has(value);
}

export function chartRendererForKind(kind: ChartSpecKind): "builtin" | "echarts" | "recharts" {
  if (kind === "metric" || kind === "line" || kind === "area" || kind === "bar") {
    return "recharts";
  }
  if (kind === "table" || kind === "stacked_bar" || kind === "timeline") {
    return "builtin";
  }
  return "echarts";
}

export function validateChartSpecGrammar(chartSpec: ChartSpecV1): WidgetRegistryValidationFailure | null {
  if (chartSpec.spec_version !== "1.0") {
    return {
      code: "UNAPPROVED_CHART_KIND",
      message: "ChartSpec must use spec_version 1.0.",
    };
  }
  if (!isChartSpecKind(chartSpec.kind)) {
    return {
      code: "UNAPPROVED_CHART_KIND",
      message: `Chart kind is not in the ChartSpec v1 allowlist: ${String(chartSpec.kind)}`,
    };
  }
  for (const channel of Object.keys(chartSpec.encodings ?? {})) {
    if (!isChartEncodingChannel(channel)) {
      return {
        code: "UNAPPROVED_CHART_KIND",
        message: `Encoding channel is not in the ChartSpec v1 allowlist: ${channel}`,
      };
    }
  }
  for (const transform of chartSpec.transforms ?? []) {
    if (!isChartTransformType(transform.type)) {
      return {
        code: "UNAPPROVED_TRANSFORM",
        message: `Transform is not in the ChartSpec v1 allowlist: ${String(transform.type)}`,
      };
    }
  }
  if (chartSpec.click_action) {
    if (!isWidgetInteractionKind(chartSpec.click_action.kind) || isBlockedInteractionKind(chartSpec.click_action.kind)) {
      return {
        code: "UNAPPROVED_INTERACTION",
        message: `Interaction is not in the ChartSpec v1 allowlist: ${String(chartSpec.click_action.kind)}`,
      };
    }
  }
  return null;
}

export function validateWidgetSpecAgainstRegistry(widget: WidgetSpecV2): WidgetRegistryValidationResult {
  if (widget.spec_version !== "2.0") {
    return { ok: false, code: "INVALID_SPEC_VERSION", message: "WidgetSpec must use spec_version 2.0." };
  }
  if (widget.registry_version !== WIDGET_REGISTRY_VERSION) {
    return {
      ok: false,
      code: "INVALID_REGISTRY_VERSION",
      message: `WidgetSpec must use registry_version ${WIDGET_REGISTRY_VERSION}.`,
    };
  }

  const entry = getWidgetRegistryEntry(widget.widget_type);
  if (!entry) {
    return {
      ok: false,
      code: "UNKNOWN_WIDGET_TYPE",
      message: `Widget type is not registered: ${widget.widget_type}`,
    };
  }
  if (entry.status !== "active") {
    return {
      ok: false,
      code: "INACTIVE_WIDGET_TYPE",
      message: `Widget type is not active: ${widget.widget_type}`,
    };
  }
  if (!entry.allowed_data_sources.includes(widget.data_source_id)) {
    return {
      ok: false,
      code: "UNAPPROVED_DATA_SOURCE",
      message: `Data source is not allowed for ${widget.widget_type}: ${widget.data_source_id}`,
    };
  }

  const grammarFailure = validateChartSpecGrammar(widget.chart_spec);
  if (grammarFailure) {
    return { ok: false, ...grammarFailure };
  }
  if (!entry.allowed_chart_kinds.includes(widget.chart_spec.kind)) {
    return {
      ok: false,
      code: "UNAPPROVED_CHART_KIND",
      message: `Chart kind ${widget.chart_spec.kind} is not allowed for ${widget.widget_type}.`,
    };
  }
  for (const transform of widget.chart_spec.transforms ?? []) {
    if (!entry.allowed_transforms.includes(transform.type)) {
      return {
        ok: false,
        code: "UNAPPROVED_TRANSFORM",
        message: `Transform ${transform.type} is not allowed for ${widget.widget_type}.`,
      };
    }
  }

  const interactions = [...(widget.interactions ?? []), widget.chart_spec.click_action].filter(Boolean);
  for (const interaction of interactions) {
    const kind = interaction?.kind;
    if (!isWidgetInteractionKind(kind) || isBlockedInteractionKind(kind) || !entry.allowed_interactions.includes(kind)) {
      return {
        ok: false,
        code: "UNAPPROVED_INTERACTION",
        message: `Interaction ${String(kind)} is not allowed for ${widget.widget_type}.`,
      };
    }
  }

  if (sensitivityRank[widget.sensitivity] < sensitivityRank[entry.sensitivity]) {
    return {
      ok: false,
      code: "SENSITIVITY_DOWNGRADE",
      message: `Widget sensitivity ${widget.sensitivity} is less restrictive than registry sensitivity ${entry.sensitivity}.`,
    };
  }

  return { ok: true, entry };
}
