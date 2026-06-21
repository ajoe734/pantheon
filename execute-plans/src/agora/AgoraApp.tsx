import React, { useEffect, useState } from "react";
import { agoraIdentityClient } from "@/lib/bff-v1/agora/identity";
import { agoraServantClient } from "@/lib/bff-v1/agora/servant";
import type { AgoraCapabilityName, AgoraUserScope, ServantProfile } from "@/lib/bff-v1/agora/types";

type BootPhase = "init" | "identity" | "servant" | "ready" | "error";

export default function AgoraApp(): JSX.Element {
  const [phase, setPhase] = useState<BootPhase>("init");
  const [identity, setIdentity] = useState<AgoraUserScope | null>(null);
  const [capabilities, setCapabilities] = useState<AgoraCapabilityName[]>([]);
  const [servant, setServant] = useState<ServantProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [command, setCommand] = useState("");

  useEffect(() => {
    let cancelled = false;

    async function bootstrap() {
      // 1. Resolve identity and capabilities before provisioning servant
      setPhase("identity");
      const [scope, caps] = await Promise.all([
        agoraIdentityClient.getMe(),
        agoraIdentityClient.getCapabilities(),
      ]);
      if (cancelled) return;
      setIdentity(scope);
      setCapabilities(caps);

      // 2. Provision servant only after identity resolves (strict, no-fallback)
      setPhase("servant");
      const profile = await agoraServantClient.ensure();
      if (cancelled) return;
      setServant(profile);
      setPhase("ready");
    }

    bootstrap().catch((err) => {
      if (!cancelled) {
        setError(String(err));
        setPhase("error");
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  if (phase === "init" || phase === "identity") {
    return (
      <div data-testid="agora-loading" role="status">
        Loading Agora identity...
      </div>
    );
  }

  if (phase === "servant") {
    return (
      <div data-testid="agora-provisioning" role="status">
        Provisioning Agora servant...
      </div>
    );
  }

  if (phase === "error") {
    return (
      <div data-testid="agora-error" role="alert">
        <strong>Agora Error:</strong> {error}
      </div>
    );
  }

  return (
    <div data-testid="agora-ready">
      <h1>Agora</h1>
      <div data-testid="agora-identity">
        <strong>Identity:</strong>{" "}
        {identity
          ? `${identity.user_id} (tenant: ${identity.tenant_id})`
          : "—"}
      </div>
      {capabilities.length > 0 && (
        <div data-testid="agora-capabilities">
          <strong>Capabilities:</strong> {capabilities.join(", ")}
        </div>
      )}
      <div data-testid="agora-servant-status">
        <strong>Servant:</strong>{" "}
        {servant
          ? `${servant.display_name} — ${servant.status}`
          : "not provisioned"}
      </div>
      <div data-testid="agora-command-bar">
        <input
          type="text"
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          placeholder="Enter command"
          aria-label="Agora command"
          data-testid="agora-command-input"
        />
        <button
          data-testid="agora-command-send"
          onClick={() => setCommand("")}
          disabled={!command.trim() || servant === null}
        >
          Send
        </button>
      </div>
    </div>
  );
}
