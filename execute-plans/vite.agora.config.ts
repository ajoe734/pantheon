import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const authAudience = env.VITE_AUTH_AUDIENCE || "pantheon-agora";

  return {
    plugins: [react()],
    define: {
      // VITE_APP_KIND is baked in at build time — not overridable via .env
      "import.meta.env.VITE_APP_KIND": JSON.stringify("agora"),
      // VITE_AUTH_AUDIENCE defaults to pantheon-agora; override via env file or CI secret
      "import.meta.env.VITE_AUTH_AUDIENCE": JSON.stringify(authAudience),
    },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    build: {
      outDir: "dist/agora",
      rollupOptions: {
        input: {
          app: path.resolve(__dirname, "agora.html"),
        },
      },
    },
    server: {
      port: 5173,
    },
    preview: {
      port: 4173,
    },
  };
});
