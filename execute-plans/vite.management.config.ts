import { defineConfig, loadEnv, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

function isManagementShellPath(pathname: string): boolean {
  return pathname === "/management" || pathname.startsWith("/management/");
}

function rewriteToManagementHtml(url: string | undefined): string | undefined {
  if (!url) return url;
  const [pathname, query = ""] = url.split("?", 2);
  if (!isManagementShellPath(pathname)) return url;
  return query ? `/management.html?${query}` : "/management.html";
}

function managementShellFallback(): Plugin {
  return {
    name: "pantheon-management-shell-fallback",
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        req.url = rewriteToManagementHtml(req.url);
        next();
      });
    },
    configurePreviewServer(server) {
      server.middlewares.use((req, _res, next) => {
        req.url = rewriteToManagementHtml(req.url);
        next();
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const authAudience = env.VITE_AUTH_AUDIENCE || "pantheon-management";

  return {
    plugins: [managementShellFallback(), react()],
    define: {
      // VITE_APP_KIND is baked in at build time — not overridable via .env
      "import.meta.env.VITE_APP_KIND": JSON.stringify("management"),
      // VITE_AUTH_AUDIENCE defaults to pantheon-management; override via env file or CI secret
      "import.meta.env.VITE_AUTH_AUDIENCE": JSON.stringify(authAudience),
    },
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    build: {
      outDir: "dist/management",
      rollupOptions: {
        input: {
          app: path.resolve(__dirname, "management.html"),
        },
      },
    },
    server: {
      port: 5174,
    },
    preview: {
      port: 4174,
    },
  };
});
