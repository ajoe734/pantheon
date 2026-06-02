import React, { useEffect, useRef, useState } from "react";
import { postAsk, openAskSse, getAskSession } from "@/lib/bff/agora";

export default function AskPersonas(): JSX.Element {
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState<Array<{ role: string; content: string }>>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
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

    try {
      const body = {
        prompt,
        persona_ids: [],
        metadata: { source: "ask-personas-ui" },
      };
      const res = await postAsk(body);
      // res may be envelope { data: { session_id } }
      const id = (res?.data?.session_id ?? res?.session_id) as string | undefined;
      if (!id) throw new Error("no session id returned");
      setSessionId(id);
      setStatus("active");

      // subscribe to SSE deltas
      if (esRef.current) esRef.current.close();
      const es = openAskSse((ev) => {
        try {
          const payload = JSON.parse(ev.data);
          // expected shape: { event: 'delta'|'completed', session_id, delta, transcript? }
          if (payload.session_id !== id) return;
          if (payload.type === "delta" || payload.event === "delta") {
            appendDelta(String(payload.delta ?? payload.content ?? ""));
          }
          if (payload.type === "completed" || payload.event === "completed") {
            setStatus("completed");
            // fetch final transcript
            void (async () => {
              try {
                const s = await getAskSession(id);
                const final = (s.transcript ?? s.messages ?? []).map((it: any) => ({ role: it.role ?? "assistant", content: it.content ?? "" }));
                setMessages((m) => [...m, ...final]);
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
      const final = (s.transcript ?? s.messages ?? []).map((it: any) => ({ role: it.role ?? "assistant", content: it.content ?? "" }));
      setMessages(final);
      setStatus(s.status ?? null);
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
      </div>
      <div>
        <h3>Messages</h3>
        <div style={{ whiteSpace: "pre-wrap", border: "1px solid #ddd", padding: 8 }}>
          {messages.map((m, i) => (
            <div key={i}><strong>{m.role}:</strong> {m.content}</div>
          ))}
        </div>
      </div>
    </div>
  );
}
