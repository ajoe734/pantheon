import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  FileStack,
  RefreshCw,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/empty-state";
import { managementClient } from "@/lib/bff/client";
import type {
  ManagementEvidenceItem,
  ManagementEvidenceResponse,
} from "@/lib/bff-v1/management";
import { ManagementDenseTable } from "@/management/components/dense-table";
import { cn } from "@/lib/utils";

type LoadState = "loading" | "ready" | "error";

export interface LiveEvidenceManifestFile {
  path: string;
  bytes: number;
  currentRunAllowed: boolean;
  forbiddenAuditScope: boolean;
  oversized: boolean;
}

export interface LiveEvidenceCriterionView {
  key: string;
  label: string;
  status: string;
  note: string;
}

export interface LiveEvidenceInvalidInputView {
  name: string;
  reason: string;
}

export interface LiveEvidenceRemediationView {
  githubEnvironment: string;
  repository: string;
  requiredSecretNames: string[];
  missingSecretNames: string[];
  missingWorkflowInputs: string[];
  invalidInputs: LiveEvidenceInvalidInputView[];
  secretSetCommands: string[];
  workflow: {
    recommendedWorkflow: string;
    mode: string;
    environment: string;
    runCommandTemplate: string;
  };
  notes: string[];
}

export interface LiveEvidenceManifestView {
  id: string;
  title: string;
  status: string;
  fileCount: number;
  totalBytes: number;
  maxFiles: number;
  maxTotalBytes: number;
  maxFileBytes: number;
  files: LiveEvidenceManifestFile[];
  criteria: LiveEvidenceCriterionView[];
  remediation: LiveEvidenceRemediationView | null;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function asNumber(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function stringListFrom(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item ?? "").trim()).filter(Boolean)
    : [];
}

function textFrom(value: unknown, fallback = "-"): string {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function labelFrom(value: unknown, fallback = "unknown"): string {
  return textFrom(value, fallback).replace(/_/g, " ");
}

interface CoverageToken {
  key: string;
  value: string;
  tone: "success" | "warning" | "neutral";
}

function isCompleteRatio(value: string): boolean {
  const match = value.match(/^(\d+)\/(\d+)$/);
  if (!match) return false;
  const current = Number(match[1]);
  const expected = Number(match[2]);
  return expected > 0 && current === expected;
}

function isIncompleteRatio(value: string): boolean {
  const match = value.match(/^(\d+)\/(\d+)$/);
  if (!match) return false;
  const current = Number(match[1]);
  const expected = Number(match[2]);
  return expected > 0 && current < expected;
}

function coverageTokenTone(key: string, value: string): CoverageToken["tone"] {
  const normalizedValue = value.toLowerCase();
  const normalizedKey = key.toLowerCase();
  if (["true", "ok", "pass", "ready", "none"].includes(normalizedValue) || isCompleteRatio(value)) {
    return "success";
  }
  if (/^(duplicate|duplicates|duplicatewinners)$/.test(normalizedKey) && normalizedValue === "false") {
    return "success";
  }
  if (["false", "fail", "error", "blocked", "reported"].includes(normalizedValue) || isIncompleteRatio(value)) {
    return "warning";
  }
  if (
    /^(duplicate|duplicates|missing|missingreplay|replayed)$/.test(normalizedKey)
    && Number(value) === 0
  ) {
    return "success";
  }
  if (
    /^(duplicate|duplicates|missing|missingreplay|replayed)$/.test(normalizedKey)
    && Number(value) > 0
  ) {
    return "warning";
  }
  return "neutral";
}

function coverageTokensFromNote(note: string): CoverageToken[] {
  return note
    .split(/\s+/)
    .map((part) => part.trim())
    .map((part) => part.match(/^([A-Za-z][A-Za-z0-9_-]*):([^\s]+)$/))
    .filter((match): match is RegExpMatchArray => match !== null)
    .map((match) => {
      const key = match[1];
      const value = match[2].replace(/[;,]$/, "");
      return { key, value, tone: coverageTokenTone(key, value) };
    });
}

function coverageTokenClass(tone: CoverageToken["tone"]): string {
  if (tone === "success") return "bg-status-success/15 text-status-success border-status-success/30";
  if (tone === "warning") return "bg-status-warning/15 text-status-warning border-status-warning/30";
  return "bg-muted text-muted-foreground border-border";
}

function testIdPart(value: string): string {
  return value.replace(/[^A-Za-z0-9_-]+/g, "-");
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 B";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(2)} MB`;
}

function booleanFrom(value: unknown): boolean {
  return value === true || String(value ?? "").toLowerCase() === "true";
}

function normalizeManifestFile(value: unknown, fallbackPath = ""): LiveEvidenceManifestFile | null {
  const record = asRecord(value);
  const path = textFrom(record.path ?? record.name ?? record.storage_ref ?? fallbackPath, "");
  if (!path) return null;
  return {
    path,
    bytes: asNumber(record.bytes ?? record.size_bytes ?? record.size, 0),
    currentRunAllowed: booleanFrom(record.current_run_allowed ?? record.currentRunAllowed),
    forbiddenAuditScope: booleanFrom(record.forbidden_audit_scope ?? record.forbiddenAuditScope),
    oversized: booleanFrom(record.oversized),
  };
}

function normalizeManifestFiles(value: unknown): LiveEvidenceManifestFile[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => normalizeManifestFile(item))
      .filter((item): item is LiveEvidenceManifestFile => item !== null);
  }
  const record = asRecord(value);
  return Object.entries(record)
    .map(([name, item]) => {
      if (typeof item === "string") {
        return normalizeManifestFile({ path: item }, name);
      }
      return normalizeManifestFile(item, name);
    })
    .filter((item): item is LiveEvidenceManifestFile => item !== null);
}

function findArtifactManifest(value: unknown): Record<string, unknown> | null {
  const record = asRecord(value);
  for (const key of ["artifact_manifest", "artifactManifest"]) {
    const manifest = asRecord(record[key]);
    if (Object.keys(manifest).length > 0) return manifest;
  }
  for (const key of ["payload", "data", "metadata", "meta", "details", "resolvedLink", "resolved_link"]) {
    const nested = findArtifactManifest(record[key]);
    if (nested) return nested;
  }
  return null;
}

function findVerificationCriteria(value: unknown): unknown {
  const record = asRecord(value);
  for (const key of ["criteria", "verificationCriteria"]) {
    const candidate = record[key];
    if (Array.isArray(candidate) && candidate.length > 0) return candidate;
    const criteria = asRecord(candidate);
    if (Object.keys(criteria).length > 0) return criteria;
  }
  for (const key of ["payload", "data", "metadata", "meta", "details", "resolvedLink", "resolved_link"]) {
    const nested = findVerificationCriteria(record[key]);
    if (nested) return nested;
  }
  return null;
}

function findOperatorRemediation(value: unknown): unknown {
  const record = asRecord(value);
  for (const key of ["operator_remediation", "operatorRemediation"]) {
    const remediation = asRecord(record[key]);
    if (Object.keys(remediation).length > 0) return remediation;
  }
  for (const key of ["payload", "data", "metadata", "meta", "details", "resolvedLink", "resolved_link"]) {
    const nested = findOperatorRemediation(record[key]);
    if (nested) return nested;
  }
  return null;
}

function normalizeInvalidInputs(value: unknown): LiveEvidenceInvalidInputView[] {
  return Array.isArray(value)
    ? value
        .map((item) => {
          const record = asRecord(item);
          return {
            name: textFrom(record.name, ""),
            reason: textFrom(record.reason, ""),
          };
        })
        .filter((item) => item.name || item.reason)
    : [];
}

function normalizeRemediation(value: unknown): LiveEvidenceRemediationView | null {
  const record = asRecord(value);
  if (Object.keys(record).length === 0) return null;
  const workflow = asRecord(record.workflow_dispatch ?? record.workflowDispatch);
  return {
    githubEnvironment: textFrom(record.github_environment ?? record.githubEnvironment, ""),
    repository: textFrom(record.repository, ""),
    requiredSecretNames: stringListFrom(record.required_secret_names ?? record.requiredSecretNames),
    missingSecretNames: stringListFrom(record.missing_secret_names ?? record.missingSecretNames),
    missingWorkflowInputs: stringListFrom(record.missing_workflow_inputs ?? record.missingWorkflowInputs),
    invalidInputs: normalizeInvalidInputs(record.invalid_inputs ?? record.invalidInputs),
    secretSetCommands: stringListFrom(record.secret_set_commands ?? record.secretSetCommands),
    workflow: {
      recommendedWorkflow: textFrom(workflow.recommended_workflow ?? workflow.recommendedWorkflow, ""),
      mode: textFrom(workflow.mode, ""),
      environment: textFrom(workflow.environment, ""),
      runCommandTemplate: textFrom(workflow.run_command_template ?? workflow.runCommandTemplate, ""),
    },
    notes: stringListFrom(record.notes),
  };
}

function normalizeCriterion(key: string, value: unknown): LiveEvidenceCriterionView | null {
  if (typeof value === "string") {
    return {
      key,
      label: labelFrom(key),
      status: textFrom(value, "unknown"),
      note: "",
    };
  }
  const record = asRecord(value);
  const normalizedKey = textFrom(record.key ?? record.id ?? key, key);
  if (!normalizedKey && Object.keys(record).length === 0) return null;
  return {
    key: normalizedKey,
    label: textFrom(record.label ?? record.title ?? labelFrom(normalizedKey), labelFrom(normalizedKey)),
    status: textFrom(record.status ?? record.state ?? record.outcome, "unknown"),
    note: textFrom(record.note ?? record.reason ?? record.message ?? record.detail ?? record.details, ""),
  };
}

function normalizeCriteria(value: unknown): LiveEvidenceCriterionView[] {
  if (Array.isArray(value)) {
    return value
      .map((item, index) => normalizeCriterion(`criterion-${index}`, item))
      .filter((item): item is LiveEvidenceCriterionView => item !== null);
  }
  return Object.entries(asRecord(value))
    .map(([key, item]) => normalizeCriterion(key, item))
    .filter((item): item is LiveEvidenceCriterionView => item !== null);
}

function manifestTitle(item: ManagementEvidenceItem, fallbackId: string): string {
  return textFrom(
    item.title ?? item.display_label ?? item.ref_id ?? item.id,
    fallbackId,
  );
}

function normalizeManifestView(item: ManagementEvidenceItem, index: number): LiveEvidenceManifestView | null {
  const manifest = findArtifactManifest(item);
  if (!manifest) return null;
  const files = normalizeManifestFiles(manifest.files);
  const limits = asRecord(manifest.limits);
  const itemPayload = asRecord(item.payload);
  const criteria = normalizeCriteria(findVerificationCriteria(item));
  const remediation = normalizeRemediation(findOperatorRemediation(item));
  const id = textFrom(item.ref_id ?? item.id, `live-evidence-${index}`);
  return {
    id,
    title: manifestTitle(item, id),
    status: textFrom(item.status ?? item.overall ?? itemPayload.overall, "unknown"),
    fileCount: asNumber(manifest.file_count ?? manifest.fileCount, files.length),
    totalBytes: asNumber(manifest.total_bytes ?? manifest.totalBytes, files.reduce((sum, file) => sum + file.bytes, 0)),
    maxFiles: asNumber(limits.max_files ?? manifest.max_files ?? manifest.maxFiles, 0),
    maxTotalBytes: asNumber(limits.max_total_bytes ?? manifest.max_total_bytes ?? manifest.maxTotalBytes, 0),
    maxFileBytes: asNumber(limits.max_file_bytes ?? manifest.max_file_bytes ?? manifest.maxFileBytes, 0),
    files,
    criteria,
    remediation,
  };
}

export function collectLiveEvidenceManifests(
  response: ManagementEvidenceResponse,
): LiveEvidenceManifestView[] {
  return response.data.items
    .map((item, index) => normalizeManifestView(item, index))
    .filter((item): item is LiveEvidenceManifestView => item !== null);
}

function statusTone(status: string): string {
  if (status === "pass" || status === "ok" || status === "ready") {
    return "bg-status-success/15 text-status-success border-status-success/30";
  }
  if (status === "fail" || status === "blocked" || status === "error") {
    return "bg-status-warning/15 text-status-warning border-status-warning/30";
  }
  return "bg-muted text-muted-foreground border-border";
}

function RemediationSection({ manifest }: { manifest: LiveEvidenceManifestView }) {
  const remediation = manifest.remediation;
  if (!remediation) return null;
  const hasRequiredSecrets = remediation.requiredSecretNames.length > 0;
  const hasMissingSecrets = remediation.missingSecretNames.length > 0;
  const hasMissingInputs = remediation.missingWorkflowInputs.length > 0;
  const hasInvalidInputs = remediation.invalidInputs.length > 0;

  return (
    <section className="mt-3 border-t border-border pt-3 text-xs" data-testid={`live-evidence-remediation-${manifest.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <AlertTriangle className="h-3.5 w-3.5 text-status-warning" />
        <h4 className="text-sm font-semibold">Operator remediation</h4>
        {remediation.githubEnvironment ? (
          <Badge variant="outline" className="font-mono text-[10px]" data-testid={`live-evidence-remediation-env-${manifest.id}`}>
            env:{remediation.githubEnvironment}
          </Badge>
        ) : null}
        {remediation.repository ? (
          <Badge variant="outline" className="font-mono text-[10px]" data-testid={`live-evidence-remediation-repo-${manifest.id}`}>
            repo:{remediation.repository}
          </Badge>
        ) : null}
      </div>

      <div className="mt-2 grid gap-2 lg:grid-cols-3">
        <div>
          <div className="text-muted-foreground">Required secrets</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {hasRequiredSecrets ? remediation.requiredSecretNames.map((name) => (
              <Badge
                key={name}
                variant="outline"
                className="font-mono text-[10px]"
                data-testid={`live-evidence-remediation-required-secret-${manifest.id}-${testIdPart(name)}`}
              >
                {name}
              </Badge>
            )) : <span className="text-muted-foreground">-</span>}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground">Missing secrets</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {hasMissingSecrets ? remediation.missingSecretNames.map((name) => (
              <Badge
                key={name}
                variant="outline"
                className="font-mono text-[10px] bg-status-warning/15 text-status-warning border-status-warning/30"
                data-testid={`live-evidence-remediation-missing-secret-${manifest.id}-${testIdPart(name)}`}
              >
                {name}
              </Badge>
            )) : <span className="text-muted-foreground">-</span>}
          </div>
        </div>
        <div>
          <div className="text-muted-foreground">Workflow inputs</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {hasMissingInputs ? remediation.missingWorkflowInputs.map((name) => (
              <Badge
                key={name}
                variant="outline"
                className="font-mono text-[10px] bg-status-warning/15 text-status-warning border-status-warning/30"
                data-testid={`live-evidence-remediation-missing-input-${manifest.id}-${testIdPart(name)}`}
              >
                {name}
              </Badge>
            )) : <span className="text-muted-foreground">-</span>}
          </div>
        </div>
      </div>

      {hasInvalidInputs ? (
        <div className="mt-2">
          <div className="text-muted-foreground">Invalid inputs</div>
          <ul className="mt-1 list-disc pl-4">
            {remediation.invalidInputs.map((item) => (
              <li key={`${item.name}:${item.reason}`}>{item.name ? `${item.name}: ` : ""}{item.reason}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {remediation.secretSetCommands.length > 0 ? (
        <div className="mt-2">
          <div className="text-muted-foreground">Secret commands</div>
          <div className="mt-1 grid gap-1">
            {remediation.secretSetCommands.map((command, index) => (
              <code
                key={command}
                className="block break-all rounded bg-muted px-2 py-1 font-mono text-[10px] text-foreground"
                data-testid={`live-evidence-remediation-secret-command-${manifest.id}-${index}`}
              >
                {command}
              </code>
            ))}
          </div>
        </div>
      ) : null}

      {remediation.workflow.runCommandTemplate ? (
        <div className="mt-2">
          <div className="text-muted-foreground">Workflow dispatch</div>
          <code
            className="mt-1 block break-all rounded bg-muted px-2 py-1 font-mono text-[10px] text-foreground"
            data-testid={`live-evidence-remediation-workflow-command-${manifest.id}`}
          >
            {remediation.workflow.runCommandTemplate}
          </code>
        </div>
      ) : null}

      {remediation.notes.length > 0 ? (
        <ul className="mt-2 list-disc pl-4 text-muted-foreground">
          {remediation.notes.map((note) => <li key={note}>{note}</li>)}
        </ul>
      ) : null}
    </section>
  );
}


function ManifestRow({ manifest }: { manifest: LiveEvidenceManifestView }) {
  const allowedCount = manifest.files.filter((file) => file.currentRunAllowed).length;
  const forbiddenCount = manifest.files.filter((file) => file.forbiddenAuditScope).length;
  const oversizedCount = manifest.files.filter((file) => file.oversized).length;
  const largest = manifest.files.reduce<LiveEvidenceManifestFile | null>(
    (current, file) => (!current || file.bytes > current.bytes ? file : current),
    null,
  );

  return (
    <article
      className="rounded-md border border-border bg-background p-3"
      data-testid={`live-evidence-manifest-${manifest.id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold leading-5">{manifest.title}</h3>
            <Badge variant="outline" className="font-mono text-[11px]">
              {manifest.id}
            </Badge>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Files: {manifest.fileCount}/{manifest.maxFiles || "-"}</span>
            <span>Total: {formatBytes(manifest.totalBytes)}</span>
            <span>Limit: {manifest.maxTotalBytes ? formatBytes(manifest.maxTotalBytes) : "-"}</span>
            <span>Largest: {largest ? `${largest.path} ${formatBytes(largest.bytes)}` : "-"}</span>
          </div>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Badge variant="outline" className={cn("capitalize", statusTone(manifest.status))}>
            {labelFrom(manifest.status)}
          </Badge>
          <Badge
            variant="outline"
            className={cn(
              allowedCount === manifest.files.length
                ? "bg-status-success/15 text-status-success border-status-success/30"
                : "bg-status-warning/15 text-status-warning border-status-warning/30",
            )}
            data-testid={`live-evidence-current-run-${manifest.id}`}
          >
            {allowedCount === manifest.files.length ? <CheckCircle2 className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
            Current-run {allowedCount}/{manifest.files.length}
          </Badge>
        </div>
      </div>

      <div className="mt-3 grid gap-2 text-xs sm:grid-cols-3">
        <div>
          <div className="text-muted-foreground">Forbidden scope</div>
          <div className="font-medium">{forbiddenCount}</div>
        </div>
        <div>
          <div className="text-muted-foreground">Oversized files</div>
          <div className="font-medium">{oversizedCount}</div>
        </div>
        <div>
          <div className="text-muted-foreground">File limit</div>
          <div className="font-medium">{manifest.maxFileBytes ? formatBytes(manifest.maxFileBytes) : "-"}</div>
        </div>
      </div>

      {manifest.criteria.length > 0 ? (
        <ManagementDenseTable className="mt-3" testId={`live-evidence-criteria-${manifest.id}`}>
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 font-medium">Criterion</th>
                <th className="py-1 pr-3 font-medium">Status</th>
                <th className="py-1 pr-3 font-medium">Note</th>
              </tr>
            </thead>
            <tbody>
              {manifest.criteria.map((criterion) => {
                const tokens = coverageTokensFromNote(criterion.note);
                return (
                  <tr key={criterion.key} className="border-t border-border">
                    <td className="py-1.5 pr-3 font-medium">{criterion.label}</td>
                    <td className="py-1.5 pr-3">
                      <Badge variant="outline" className={cn("capitalize", statusTone(criterion.status))}>
                        {labelFrom(criterion.status)}
                      </Badge>
                    </td>
                    <td className="py-1.5 pr-3 text-muted-foreground break-words">
                      <div>{criterion.note || "-"}</div>
                      {tokens.length > 0 ? (
                        <div className="mt-1.5 flex flex-wrap gap-1" data-testid={`live-evidence-tokens-${manifest.id}-${criterion.key}`}>
                          {tokens.map((token) => (
                            <Badge
                              key={`${token.key}:${token.value}`}
                              variant="outline"
                              className={cn("font-mono text-[10px]", coverageTokenClass(token.tone))}
                              data-testid={`live-evidence-token-${manifest.id}-${criterion.key}-${testIdPart(token.key)}`}
                            >
                              {token.key}:{token.value}
                            </Badge>
                          ))}
                        </div>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </ManagementDenseTable>
      ) : null}

      <RemediationSection manifest={manifest} />

      {manifest.files.length > 0 ? (
        <ManagementDenseTable className="mt-3" testId={`live-evidence-files-${manifest.id}`}>
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 pr-3 font-medium">Path</th>
                <th className="py-1 pr-3 font-medium">Bytes</th>
                <th className="py-1 pr-3 font-medium">Scope</th>
                <th className="py-1 pr-3 font-medium">Flags</th>
              </tr>
            </thead>
            <tbody>
              {manifest.files.map((file) => (
                <tr key={file.path} className="border-t border-border">
                  <td className="py-1.5 pr-3 font-mono text-[11px]">{file.path}</td>
                  <td className="py-1.5 pr-3">{formatBytes(file.bytes)}</td>
                  <td className="py-1.5 pr-3">{file.currentRunAllowed ? "current-run" : "out-of-scope"}</td>
                  <td className="py-1.5 pr-3">
                    {file.forbiddenAuditScope || file.oversized
                      ? [
                          file.forbiddenAuditScope ? "forbidden" : "",
                          file.oversized ? "oversized" : "",
                        ].filter(Boolean).join(", ")
                      : "clean"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </ManagementDenseTable>
      ) : null}
    </article>
  );
}

export function LiveEvidenceManifestPanel() {
  const [state, setState] = useState<LoadState>("loading");
  const [response, setResponse] = useState<ManagementEvidenceResponse | undefined>();
  const [error, setError] = useState<string | undefined>();

  async function load() {
    setState("loading");
    setError(undefined);
    try {
      const next = await managementClient.evidenceExplorer.list({ page_size: 25 });
      setResponse(next);
      setState("ready");
    } catch (err) {
      setResponse(undefined);
      setError(err instanceof Error ? err.message : "Live evidence unavailable");
      setState("error");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const manifests = useMemo(
    () => (response ? collectLiveEvidenceManifests(response) : []),
    [response],
  );
  const surface = asRecord(response?.meta?.surfaces?.management_evidence);
  const surfaceStatus = labelFrom(surface.status, "unknown");
  const surfaceSource = textFrom(surface.source, "unknown");

  return (
    <section className="flex flex-col gap-3" data-testid="live-evidence-manifest-panel">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <FileStack className="h-4 w-4 text-primary" />
            <h2 className="text-base font-semibold">BFF Live Evidence</h2>
            <Badge variant="outline" className="capitalize">
              {surfaceStatus}
            </Badge>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>Source: {surfaceSource}</span>
            <span>Manifests: {manifests.length}</span>
            <span>Evidence: {response?.data.summary.visible_evidence ?? 0}/{response?.data.summary.total_evidence ?? 0}</span>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex h-8 items-center gap-2 rounded-md border border-border px-3 text-xs font-medium hover:bg-muted"
        >
          <RefreshCw className={cn("h-3.5 w-3.5", state === "loading" ? "animate-spin" : "")} />
          Refresh
        </button>
      </header>

      {state === "loading" ? (
        <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground" role="status">
          <RefreshCw className="h-4 w-4 animate-spin" />
          Loading live evidence
        </div>
      ) : null}

      {state === "error" ? (
        <div className="pt-3">
          <EmptyState
            icon={<AlertTriangle className="h-8 w-8" />}
            title="Live evidence unavailable"
            description={error}
            cta={{ label: "Retry", onClick: load }}
          />
        </div>
      ) : null}

      {state === "ready" && manifests.length === 0 ? (
        <div className="pt-3">
          <EmptyState
            icon={<FileStack className="h-8 w-8" />}
            title="No live evidence manifests"
            description="The BFF returned no artifact manifest entries."
            cta={{ label: "Refresh", onClick: load }}
          />
        </div>
      ) : null}

      {state === "ready" && manifests.length > 0 ? (
        <div className="mt-3 grid gap-3">
          {manifests.map((manifest) => (
            <ManifestRow key={manifest.id} manifest={manifest} />
          ))}
        </div>
      ) : null}
    </section>
  );
}
