import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        configure(proxy) {
          proxy.on("error", (error, request) => {
            console.error("[market-proxy] proxy_error", {
              requestId: request.headers["x-request-id"] ?? null,
              kind: request.headers["x-market-request-kind"] ?? null,
              method: request.method,
              url: request.url,
              errorType: error.name,
              error: error.message,
            });
          });
          proxy.on("proxyRes", (response, request) => {
            if (!request.url?.startsWith("/api/v1/market/bars")) return;
            console.info("[market-proxy] response", {
              requestId: response.headers["x-request-id"] ?? request.headers["x-request-id"] ?? null,
              kind: request.headers["x-market-request-kind"] ?? null,
              method: request.method,
              url: request.url,
              status: response.statusCode,
              bars: response.headers["x-market-bar-count"] ?? null,
            });
          });
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
});
