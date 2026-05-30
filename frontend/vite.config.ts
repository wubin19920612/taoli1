/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv, type UserConfig } from "vite";
import type { InlineConfig } from "vitest";

type ViteConfig = UserConfig & { test: InlineConfig };

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const proxyTarget = env.VITE_PROXY_TARGET ?? "http://127.0.0.1:8000";
  return {
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: proxyTarget,
        changeOrigin: true
      }
    }
  },
  build: {
    chunkSizeWarningLimit: 1200
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    css: true
  }
} as ViteConfig;
});
