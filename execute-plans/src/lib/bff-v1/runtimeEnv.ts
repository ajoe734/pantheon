export interface BffEnv {
  MODE?: string;
  NODE_ENV?: string;
  VITE_BFF_MODE?: string;
  VITE_BFF_FALLBACK?: string;
}

export function readBffEnv(): BffEnv {
  const env =
    typeof import.meta !== "undefined" && import.meta.env
      ? (import.meta.env as Record<string, unknown>)
      : {};
  return {
    MODE: typeof env.MODE === "string" ? env.MODE : undefined,
    NODE_ENV: typeof env.NODE_ENV === "string" ? env.NODE_ENV : undefined,
    VITE_BFF_MODE: typeof env.VITE_BFF_MODE === "string" ? env.VITE_BFF_MODE : undefined,
    VITE_BFF_FALLBACK: typeof env.VITE_BFF_FALLBACK === "string" ? env.VITE_BFF_FALLBACK : undefined,
  };
}
