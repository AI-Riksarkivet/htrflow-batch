import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [sveltekit()],
  server: { host: true }, // reachable over the LAN/tunnel, rask lesson
  test: { include: ["src/**/*.test.ts"] },
});
