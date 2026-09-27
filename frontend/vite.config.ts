/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// Development-only settings are read from the repository-root .env (see .env.example).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "VERITAS_");
  const apiTarget = env.VERITAS_API_PROXY_TARGET || "http://127.0.0.1:8000";
  const allowedHosts = (env.VERITAS_DEV_ALLOWED_HOSTS || "")
    .split(",")
    .map((host) => host.trim())
    .filter(Boolean);

  // The browser only ever talks to this origin; the dev server proxies API calls.
  // changeOrigin rewrites Host to the API target, so the backend's trusted-host list
  // needs only its own address, whatever host the dev server is reached through.
  const proxy = Object.fromEntries(
    ["/api", "/health", "/ready"].map((path) => [path, { target: apiTarget, changeOrigin: true }]),
  );

  return {
    plugins: [react(), tailwindcss()],
    server: { host: "0.0.0.0", port: 5173, strictPort: true, allowedHosts, proxy },
    preview: { host: "0.0.0.0", port: 4173, strictPort: true, allowedHosts, proxy },
    // No inlined data: assets, so the production CSP can stay strict (font-src/img-src self).
    build: { sourcemap: false, target: "es2022", chunkSizeWarningLimit: 600, assetsInlineLimit: 0 },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
      // CSS is not processed in tests, except raw token reads (see src/test/contrast.test.ts).
      css: { include: [/styles\.css\?raw$/] },
      restoreMocks: true,
    },
  };
});
