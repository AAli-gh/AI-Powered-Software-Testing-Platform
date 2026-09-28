import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dashboard talks to the API on the same origin: in development Vite proxies to the
// API (TESTMIND_API_URL, default http://127.0.0.1:8000); in production nginx does.
const api = process.env.TESTMIND_API_URL ?? "http://127.0.0.1:8000";
const proxy = { "/api": api, "/health": api, "/ready": api };

export default defineConfig({
  plugins: [react()],
  server: { proxy },
  preview: { proxy },
  build: { sourcemap: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/api/schema.ts", "src/main.tsx", "src/test/**", "src/**/*.test.{ts,tsx}"],
      thresholds: { lines: 85, branches: 80, functions: 85, statements: 85 },
    },
  },
});
