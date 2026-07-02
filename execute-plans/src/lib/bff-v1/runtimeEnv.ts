export interface BffEnv {
  MODE?: string;
  NODE_ENV?: string;
  VITE_BFF_MODE?: string;
  VITE_BFF_FALLBACK?: string;
  VITE_BFF_BASE_URL?: string;
}

export function readBffEnv(): BffEnv {
  const importEnv =
    typeof import.meta !== "undefined" && import.meta.env
      ? (import.meta.env as Record<string, unknown>)
      : {};
  const processEnv =
    typeof process !== "undefined" && process.env
      ? (process.env as Record<string, unknown>)
      : {};
  const read = (key: keyof BffEnv): string | undefined => {
    const value = importEnv[key] ?? processEnv[key];
    return typeof value === "string" ? value : undefined;
  };
  return {
    MODE: read("MODE"),
    NODE_ENV: read("NODE_ENV"),
    VITE_BFF_MODE: read("VITE_BFF_MODE"),
    VITE_BFF_FALLBACK: read("VITE_BFF_FALLBACK"),
    VITE_BFF_BASE_URL: read("VITE_BFF_BASE_URL"),
  };
}
