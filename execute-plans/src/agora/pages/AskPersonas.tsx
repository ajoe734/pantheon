import React, { useEffect, useRef, useState } from "react";
import { postAsk, openAskSse, getAskSession } from "@/lib/bff/agora";

type AssistantMode = "user" | "kernel_observe" | "kernel_debug" | "kernel_repair";

interface SourceCitation {
  source_id: string;
  href: string;
  snapshot_at: string;
  status: string;
}

function isUserMode(mode: AssistantMode | null): boolean {
  return mode === null || mode === "user";
}

export default function AskPersonas(): JSX.Element {
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<Array<{ role: string; content: string }>>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [mode, setMode] = useState<AssistantMode | null>(null);
  const [sourceCitations, setSourceCitations] = useState<SourceCitation[]>([]);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    return () => {
      if (esRef.current) {
        esRef.current.close();
      }
    };
  }, []);

  const appendDelta = (delta: string, role = "assistant") => {
    setMessages((m) => [...m, { role, content: delta }]);
  };

  const handleSend = async () => {
    if (!prompt.trim()) return;
    setMessages([{ role: "operator", content: prompt }]);
    setStatus("sending");
    setSourceCitations([]);

    try {
      const body = {
        prompt,
        persona_ids: [],
        metadata: { source: "ask-personas-ui" },
      };
      const res = await postAsk(body);
      // res may be envelope { data: { session_id, mode } }
      const data = res?.data ?? res;
      const id = (data?.session_id ?? res?.session_id) as string | undefined;
      if (!id) throw new Error("no session id returned");
      setSessionId(id);
      const sessionMode = (data?.mode ?? null) as AssistantMode | null;
      setMode(sessionMode);
      setStatus("active");

      // subscribe to SSE deltas
      if (esRef.current) esRef.current.close();
      const es = openAskSse((ev) => {
        try {
          const payload = JSON.parse(ev.data);
          // expected shape: { event: 'delta'|'completed', session_id, delta, transcript?, sources? }
          if (payload.session_id !== id) return;
          if (payload.type === "delta" || payload.event === "delta") {
            appendDelta(String(payload.delta ?? payload.content ?? ""));
          }
          if (payload.type === "completed" || payload.event === "completed") {
            setStatus("completed");
            // extract source citations from SSE completion event
            if (Array.isArray(payload.sources)) {
              setSourceCitations(payload.sources as SourceCitation[]);
            }
            // fetch final transcript
            void (async () => {
              try {
                const s = await getAskSession(id);
                const final = (s.transcript ?? s.messages ?? []).map((it: any) => ({
                  role: it.role ?? "assistant",
                  content: it.content ?? "",
                }));
                setMessages((m) => [...m, ...final]);
                // also pick up source citations from transcript response
                if (Array.isArray(s.sources) && s.sources.length > 0) {
                  setSourceCitations(s.sources as SourceCitation[]);
                }
              } catch (e) {
                // ignore
              }
            })();
          }
        } catch (e) {
          // ignore parse errors
        }
      });
      esRef.current = es;
    } catch (err) {
      setStatus("error");
      appendDelta(`Error: ${String(err)}`, "assistant");
    }
  };

  const handleResync = async () => {
    if (!sessionId) return;
    try {
      const s = await getAskSession(sessionId);
      const final = (s.transcript ?? s.messages ?? []).map((it: any) => ({
        role: it.role ?? "assistant",
        content: it.content ?? "",
      }));
      setMessages(final);
      setStatus(s.status ?? null);
      if (Array.isArray(s.sources)) {
        setSourceCitations(s.sources as SourceCitation[]);
      }
    } catch (e) {
      appendDelta(`Resync failed: ${String(e)}`, "assistant");
    }
  };

  return (
    <div>
      <h2>Ask Personas</h2>
      <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6} cols={80} />
      <div>
        <button onClick={handleSend} disabled={status === "sending" || !prompt.trim()}>
          Ask
        </button>
        <button onClick={handleResync} disabled={!sessionId}>
          Resync Transcript
        </button>
      </div>
      <div>
        <strong>Status:</strong> {status ?? "idle"} {sessionId ? `(session ${sessionId})` : null}
        {/* Mode badge: only shown for non-user (kernel) sessions; user mode shows nothing here */}
        {mode !== null && !isUserMode(mode) && (
          <span style={{ marginLeft: 8, color: "#b45309", fontWeight: "bold" }}>
            [{mode}]
          </span>
        )}
      </div>
      <div>
        <h3>Messages</h3>
        <div style={{ whiteSpace: "pre-wrap", border: "1px solid #ddd", padding: 8 }}>
          {messages.map((m, i) => (
            <div key={i}><strong>{m.role}:</strong> {m.content}</div>
          ))}
        </div>
      </div>
      {/* Source citations: shown in both user and kernel modes */}
      {sourceCitations.length > 0 && (
        <div>
          <h4>Sources</h4>
          <ul>
            {sourceCitations.map((cite, i) => (
              <li key={i}>
                <a href={cite.href} target="_blank" rel="noopener noreferrer">
                  {cite.source_id}
                </a>{" "}
                <span style={{ fontSize: "0.85em", color: "#555" }}>
                  ({cite.status}, {cite.snapshot_at})
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {/* Kernel-only controls: not rendered for user mode */}
    </div>
  );
}
