import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  KeyRound,
  Loader2,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  activateAssistantControlMode,
  getAssistantControlMode,
  getAssistantProviderReauthStatus,
  getAssistantProviders,
  startAssistantProviderReauth,
  type AssistantProviderReadiness,
  type AssistantProviderReauthSession,
} from "@/lib/bff/managementAssistant";
import { cn } from "@/lib/utils";

type ControlModePayload = Record<string, unknown>;

export interface OpenClawLlmAuthApi {
  getProviders: typeof getAssistantProviders;
  getControlMode: typeof getAssistantControlMode;
  activateControlMode: typeof activateAssistantControlMode;
  startReauth: typeof startAssistantProviderReauth;
  getReauthStatus: typeof getAssistantProviderReauthStatus;
}

const defaultApi: OpenClawLlmAuthApi = {
  getProviders: getAssistantProviders,
  getControlMode: getAssistantControlMode,
  activateControlMode: activateAssistantControlMode,
  startReauth: startAssistantProviderReauth,
  getReauthStatus: getAssistantProviderReauthStatus,
};

function recordFrom(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function textFrom(...values: unknown[]): string {
  for (const value of values) {
    const text = String(value ?? "").trim();
    if (text) return text;
  }
  return "";
}

function providerId(provider: AssistantProviderReadiness | AssistantProviderReauthSession): string {
  return textFrom(provider.provider, provider.providerName, provider.provider_name, "unknown");
}

function providerLabel(provider: AssistantProviderReadiness | AssistantProviderReauthSession): string {
  return textFrom(provider.providerName, provider.provider_name, provider.provider, "unknown");
}

function providerAuthStatus(provider: AssistantProviderReadiness): string {
  return textFrom(provider.authStatus, provider.auth_status, provider.auth, provider.status, "unknown");
}

function providerReason(provider: AssistantProviderReadiness): string {
  return textFrom(provider.degradedReason, provider.degraded_reason, provider.reason, provider.message);
}

function checkedAt(provider: AssistantProviderReadiness): string {
  return textFrom(provider.checkedAt, provider.checked_at);
}

function reauthSessionId(session: AssistantProviderReauthSession): string {
  return textFrom(session.reauthSessionId, session.reauth_session_id);
}

function verificationUri(session: AssistantProviderReauthSession): string {
  return textFrom(session.verificationUriComplete, session.verification_uri_complete, session.verificationUri, session.verification_uri);
}

function userCode(session: AssistantProviderReauthSession): string {
  return textFrom(session.userCode, session.user_code);
}

function canReauth(provider: AssistantProviderReadiness): boolean {
  return ["codex", "codex_cli"].includes(providerId(provider).toLowerCase());
}

function needsReauth(provider: AssistantProviderReadiness): boolean {
  const auth = providerAuthStatus(provider).toLowerCase();
  return provider.ready !== true || ["failed", "timeout", "unavailable", "mount_unavailable"].includes(auth);
}

function isKernelReauthMode(controlMode: ControlModePayload): boolean {
  const mode = textFrom(controlMode.mode);
  return controlMode.active === true && (mode === "kernel_debug" || mode === "kernel_repair");
}

function dataPayload(value: Record<string, unknown>): Record<string, unknown> {
  const data = recordFrom(value.data);
  return Object.keys(data).length > 0 ? data : value;
}

function statusTone(status: string): string {
  const normalized = status.toLowerCase();
  if (["ready", "ok", "completed", "authorized"].includes(normalized)) {
    return "border-emerald-300 bg-emerald-50 text-emerald-800";
  }
  if (["capturing", "pending", "processing", "not_checked"].includes(normalized)) {
    return "border-blue-300 bg-blue-50 text-blue-800";
  }
  if (["degraded", "timeout", "unavailable"].includes(normalized)) {
    return "border-amber-300 bg-amber-50 text-amber-900";
  }
  if (["failed", "error", "mount_unavailable"].includes(normalized)) {
    return "border-red-300 bg-red-50 text-red-800";
  }
  return "border-slate-300 bg-slate-50 text-slate-700";
}

export function OpenClawLlmAuthPanel({ api = defaultApi }: { api?: OpenClawLlmAuthApi }) {
  const [providers, setProviders] = useState<AssistantProviderReadiness[]>([]);
  const [controlMode, setControlMode] = useState<ControlModePayload>({});
  const [loading, setLoading] = useState(false);
  const [busyProvider, setBusyProvider] = useState<string | null>(null);
  const [reauthSessions, setReauthSessions] = useState<Record<string, AssistantProviderReauthSession>>({});
  const [error, setError] = useState<string | null>(null);
  const [passphrase, setPassphrase] = useState("");
  const [reason, setReason] = useState("Refresh OpenClaw LLM provider auth");

  const activeReauthMode = isKernelReauthMode(controlMode);
  const degradedProviders = useMemo(() => providers.filter(needsReauth), [providers]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [providerPayload, controlPayload] = await Promise.all([
        api.getProviders({ authProbe: true }),
        api.getControlMode(),
      ]);
      setProviders(Array.isArray(providerPayload.data) ? providerPayload.data : []);
      setControlMode(dataPayload(controlPayload));
    } catch (err) {
      setError(err instanceof Error ? err.message : "OpenClaw LLM auth status unavailable");
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    void load();
  }, [load]);

  const activate = useCallback(async () => {
    setError(null);
    setLoading(true);
    try {
      const activated = await api.activateControlMode({
        mode: "kernel_debug",
        passphrase,
        reason,
        ttlSeconds: 900,
        idleTtlSeconds: 600,
      });
      setControlMode(dataPayload(activated));
      setPassphrase("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Control mode activation failed");
    } finally {
      setLoading(false);
    }
  }, [api, passphrase, reason]);

  const startReauth = useCallback(async (provider: AssistantProviderReadiness) => {
    const id = providerId(provider);
    setBusyProvider(id);
    setError(null);
    try {
      const started = await api.startReauth({ provider: id, reason });
      setReauthSessions((current) => ({ ...current, [id]: started.data }));
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not start ${id} reauth`);
    } finally {
      setBusyProvider(null);
    }
  }, [api, reason]);

  const refreshReauth = useCallback(async (provider: string, session: AssistantProviderReauthSession) => {
    const sessionId = reauthSessionId(session);
    if (!sessionId) return;
    setBusyProvider(provider);
    setError(null);
    try {
      const refreshed = await api.getReauthStatus(sessionId, provider);
      setReauthSessions((current) => ({ ...current, [provider]: refreshed.data }));
      if (String(refreshed.data.status ?? "").toLowerCase() === "completed") {
        void load();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not refresh ${provider} reauth`);
    } finally {
      setBusyProvider(null);
    }
  }, [api, load]);

  return (
    <section className="min-h-screen bg-slate-50 p-4 text-slate-950 sm:p-6" aria-label="OpenClaw LLM auth management">
      <div className="mx-auto flex max-w-6xl flex-col gap-4">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-normal">OpenClaw LLM Auth</h1>
            <p className="mt-1 max-w-3xl text-sm text-slate-600">
              Manage service-user CLI auth used by OpenClaw assistant providers.
            </p>
          </div>
          <button
            className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
            type="button"
            onClick={load}
            disabled={loading}
          >
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
            Refresh
          </button>
        </header>

        {error && (
          <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900" role="alert">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <section className="rounded-md border border-slate-200 bg-white p-4" aria-label="Reauth control mode">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold">Reauth Control</h2>
              <p className="mt-1 text-xs text-slate-600">
                Provider reauth requires active kernel debug or repair control mode.
              </p>
            </div>
            <Badge className={statusTone(textFrom(controlMode.state, activeReauthMode ? "active" : "inactive"))} variant="outline">
              {activeReauthMode ? (
                <ShieldCheck className="mr-1 h-3 w-3" />
              ) : (
                <KeyRound className="mr-1 h-3 w-3" />
              )}
              {activeReauthMode ? textFrom(controlMode.mode) : textFrom(controlMode.reason, "inactive")}
            </Badge>
          </div>
          {!activeReauthMode && (
            <div className="mt-3 grid gap-2 md:grid-cols-[minmax(180px,260px)_1fr_auto]">
              <input
                aria-label="Control mode passphrase"
                className="h-9 rounded-md border border-slate-300 bg-white px-3 text-sm outline-none focus:border-slate-700 focus:ring-2 focus:ring-slate-200"
                onChange={(event) => setPassphrase(event.target.value)}
                placeholder="Control passphrase"
                type="password"
                value={passphrase}
              />
              <input
                aria-label="Control mode reason"
                className="h-9 rounded-md border border-slate-300 bg-white px-3 text-sm outline-none focus:border-slate-700 focus:ring-2 focus:ring-slate-200"
                onChange={(event) => setReason(event.target.value)}
                value={reason}
              />
              <button
                className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-slate-900 bg-slate-900 px-3 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400"
                type="button"
                onClick={activate}
                disabled={loading || !passphrase.trim() || !reason.trim()}
              >
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
                Activate
              </button>
            </div>
          )}
        </section>

        <section className="grid gap-3 md:grid-cols-3" aria-label="Provider auth states">
          {providers.map((provider) => {
            const id = providerId(provider);
            const authStatus = providerAuthStatus(provider);
            const reasonText = providerReason(provider);
            const session = reauthSessions[id];
            const sessionStatus = textFrom(session?.status);
            const uri = session ? verificationUri(session) : "";
            const code = session ? userCode(session) : "";
            const supported = canReauth(provider);
            const busy = busyProvider === id;
            return (
              <article className="flex min-h-72 flex-col rounded-md border border-slate-200 bg-white p-4" key={id}>
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <h3 className="text-sm font-semibold">{providerLabel(provider)}</h3>
                    <p className="mt-1 text-xs text-slate-600">{textFrom(provider.runtime, "runtime unknown")}</p>
                  </div>
                  <Badge className={statusTone(authStatus)} variant="outline">
                    {provider.ready === true ? <CheckCircle2 className="mr-1 h-3 w-3" /> : <AlertTriangle className="mr-1 h-3 w-3" />}
                    {authStatus}
                  </Badge>
                </div>

                <dl className="mt-4 grid grid-cols-2 gap-2 text-xs">
                  <Info label="Provider" value={id} />
                  <Info label="Status" value={textFrom(provider.status, "unknown")} />
                  <Info label="Mount" value={textFrom(provider.mountMode, provider.mount_mode, "unknown")} />
                  <Info label="Version" value={textFrom(provider.version, "unknown")} />
                  <Info className="col-span-2" label="Checked" value={checkedAt(provider) || "not checked"} />
                  {reasonText && <Info className="col-span-2" label="Reason" value={reasonText} />}
                </dl>

                <div className="mt-auto pt-4">
                  {session && (
                    <div className="mb-3 rounded-md border border-blue-200 bg-blue-50 p-3 text-xs text-blue-950" data-testid={`reauth-session-${id}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold">Reauth {sessionStatus || "pending"}</span>
                        <button
                          className="inline-flex items-center gap-1 rounded border border-blue-300 bg-white px-2 py-1 font-medium text-blue-800 hover:bg-blue-100 disabled:opacity-50"
                          type="button"
                          disabled={busy}
                          onClick={() => void refreshReauth(id, session)}
                        >
                          <RefreshCw className={cn("h-3 w-3", busy && "animate-spin")} />
                          Status
                        </button>
                      </div>
                      {code && <div className="mt-2 font-mono text-sm font-semibold tracking-normal">{code}</div>}
                      {uri && (
                        <a className="mt-2 inline-flex items-center gap-1 font-medium underline" href={uri} rel="noreferrer" target="_blank">
                          Open verification
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      )}
                      {textFrom(session.errorCode, session.error_code, session.message) && (
                        <p className="mt-2 text-red-900">{textFrom(session.errorCode, session.error_code, session.message)}</p>
                      )}
                    </div>
                  )}

                  <button
                    className="inline-flex h-9 w-full items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                    type="button"
                    onClick={() => void startReauth(provider)}
                    disabled={!supported || !activeReauthMode || busy}
                  >
                    {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <KeyRound className="h-4 w-4" />}
                    {supported ? "Start reauth" : "Reauth unsupported"}
                  </button>
                  {!activeReauthMode && supported && (
                    <p className="mt-2 text-xs text-amber-800">Activate kernel debug before reauth.</p>
                  )}
                </div>
              </article>
            );
          })}
        </section>

        {!loading && providers.length === 0 && (
          <div className="rounded-md border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
            No OpenClaw assistant providers were returned by the BFF.
          </div>
        )}

        {degradedProviders.length > 0 && (
          <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
            {degradedProviders.length} provider auth state needs attention.
          </div>
        )}
      </div>
    </section>
  );
}

function Info({ label, value, className }: { label: string; value: string; className?: string }) {
  return (
    <div className={className}>
      <dt className="text-[10px] font-semibold uppercase tracking-normal text-slate-500">{label}</dt>
      <dd className="mt-0.5 break-words text-slate-800">{value}</dd>
    </div>
  );
}

export default OpenClawLlmAuthPanel;
