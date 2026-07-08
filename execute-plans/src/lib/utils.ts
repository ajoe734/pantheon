import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import type { ManagementSurfaceRef } from "@/lib/bff-v1/management";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export type ManagementDataConfidenceState =
  | "formal"
  | "partial"
  | "fallback"
  | "degraded"
  | "unavailable";

export const DATA_CONFIDENCE_LABELS: Record<ManagementDataConfidenceState, string> = {
  formal: "Formal source",
  partial: "Partial source",
  fallback: "Fallback source",
  degraded: "Degraded source",
  unavailable: "Unavailable source",
};

export const DATA_CONFIDENCE_EMPTY_COPY: Record<ManagementDataConfidenceState, string> = {
  formal: "The formal source returned successfully, but no rows matched this view.",
  partial: "Partial source data is available; missing joins remain explicit diagnostics.",
  fallback: "Fallback diagnostic data is shown because formal source rows are absent.",
  degraded: "The source is degraded; loaded rows remain visible with diagnostics.",
  unavailable: "The source is unavailable; no operator metric is inferred from missing data.",
};

export const DATA_CONFIDENCE_TONE: Record<ManagementDataConfidenceState, string> = {
  formal: "bg-status-success/15 text-status-success border-status-success/30",
  partial: "bg-status-warning/15 text-status-warning border-status-warning/30",
  fallback: "bg-status-warning/15 text-status-warning border-status-warning/30",
  degraded: "bg-status-warning/15 text-status-warning border-status-warning/30",
  unavailable: "bg-status-failed/15 text-status-failed border-status-failed/30",
};

const MISSING_TEXT = new Set(["nan", "undefined", "null", "none"]);

function toSnakeCase(value: string): string {
  return value.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`);
}

function toCamelCase(value: string): string {
  return value.replace(/_([a-zA-Z0-9])/g, (_, letter: string) => letter.toUpperCase());
}

function fieldVariants(key: string): string[] {
  const variants = [key];
  if (key.includes("_")) variants.push(toCamelCase(key));
  else variants.push(toSnakeCase(key));
  return [...new Set(variants)];
}

export function asManagementRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function managementField(record: unknown, keys: string | string[]): unknown {
  const source = asManagementRecord(record);
  const requestedKeys = Array.isArray(keys) ? keys : [keys];
  for (const key of requestedKeys) {
    for (const variant of fieldVariants(key)) {
      if (Object.prototype.hasOwnProperty.call(source, variant)) return source[variant];
    }
  }
  return undefined;
}

export function isMissingDisplayValue(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "number") return !Number.isFinite(value);
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    return !normalized || MISSING_TEXT.has(normalized);
  }
  return false;
}

export function displayText(value: unknown, fallback = "-"): string {
  if (isMissingDisplayValue(value)) return fallback;
  return String(value).trim() || fallback;
}

export function displayLabel(value: unknown, fallback = "unknown"): string {
  return displayText(value, fallback).replace(/_/g, " ");
}

export function displayTitle(value: unknown, fallback = "Unknown"): string {
  return displayLabel(value, fallback).replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function finiteNumber(value: unknown): number | null {
  if (isMissingDisplayValue(value)) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function displayInteger(value: unknown): string {
  const parsed = finiteNumber(value);
  if (parsed === null) return "-";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(parsed);
}

export function displayNumber(value: unknown, maximumFractionDigits = 2): string {
  const parsed = finiteNumber(value);
  if (parsed === null) return "-";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits }).format(parsed);
}

export function displayMoney(value: unknown): string {
  const parsed = finiteNumber(value);
  if (parsed === null) return "-";
  const sign = parsed < 0 ? "-" : "";
  return `${sign}$${new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(Math.abs(parsed))}`;
}

export function displayPercent(value: unknown): string {
  const parsed = finiteNumber(value);
  if (parsed === null) return "-";
  const normalized = Math.abs(parsed) <= 1 ? parsed * 100 : parsed;
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(normalized)}%`;
}

export function displayBps(value: unknown): string {
  const parsed = finiteNumber(value);
  if (parsed === null) return "-";
  return `${new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 }).format(parsed)} bps`;
}

export function displayTime(value: unknown): string {
  const text = displayText(value, "-");
  if (text === "-") return text;
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return text;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  }).format(parsed);
}

export function dataConfidenceFromSurface(surface?: ManagementSurfaceRef | null): ManagementDataConfidenceState {
  const status = displayText(surface?.status, "").toLowerCase();
  const source = displayText(surface?.source, "").toLowerCase();
  const note = displayText(surface?.note ?? surface?.message ?? surface?.reason, "").toLowerCase();
  const combined = `${status} ${source} ${note}`;

  if (/\b(unavailable|missing|not_found|offline|failed|error)\b/.test(combined)) return "unavailable";
  if (/\b(fallback|fixture|mock|seed|snapshot_fallback)\b/.test(combined)) return "fallback";
  if (/\b(degraded|stale|unknown|warning|warn)\b/.test(combined)) return "degraded";
  if (/\b(partial|incomplete|limited)\b/.test(combined)) return "partial";
  if (/\b(ok|ready|healthy|formal|bff_composed|live)\b/.test(combined)) return "formal";
  return surface ? "partial" : "unavailable";
}

export function aggregateDataConfidence(states: ManagementDataConfidenceState[]): ManagementDataConfidenceState {
  if (states.includes("unavailable")) return "unavailable";
  if (states.includes("degraded")) return "degraded";
  if (states.includes("fallback")) return "fallback";
  if (states.includes("partial")) return "partial";
  return "formal";
}

export function surfaceIssueMessage(surface?: ManagementSurfaceRef | null): string {
  return displayText(surface?.message ?? surface?.note ?? surface?.reason ?? surface?.source ?? surface?.status, "degraded");
}

export function firstSurface(refs: unknown): ManagementSurfaceRef | undefined {
  const surfaces = asManagementRecord(refs);
  return Object.values(surfaces).find((surface): surface is ManagementSurfaceRef =>
    Boolean(surface && typeof surface === "object" && !Array.isArray(surface)),
  );
}

export function firstSurfaceSource(refs: unknown, fallback = "BFF"): string {
  const surface = firstSurface(refs);
  return displayLabel(surface?.source, fallback);
}

export interface PerformanceAttributionHrefInput {
  personaId?: unknown;
  runtimeId?: unknown;
  period?: unknown;
  sourceHint?: unknown;
  sourceConfidence?: ManagementDataConfidenceState;
  diagnostic?: boolean;
}

export function buildPerformanceAttributionHref(input: PerformanceAttributionHrefInput): string {
  const params = new URLSearchParams();
  params.set("dimension", "persona");
  params.set("period", displayText(input.period, "quarter"));

  const personaId = displayText(input.personaId, "");
  const runtimeId = displayText(input.runtimeId, "");
  const sourceHint = displayText(input.sourceHint, "");
  if (personaId) params.set("persona_id", personaId);
  if (runtimeId) params.set("runtime_id", runtimeId);
  if (sourceHint) params.set("source_hint", sourceHint);
  if (input.sourceConfidence) params.set("source_confidence", input.sourceConfidence);
  if (input.diagnostic) params.set("mode", "fallback-diagnostic");

  return `/management/performance-attribution?${params.toString()}`;
}
