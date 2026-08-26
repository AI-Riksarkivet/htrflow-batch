import { sveltekit } from "@sveltejs/kit/vite";
import { svelteTesting } from "@testing-library/svelte/vite";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [sveltekit(), svelteTesting()],
  server: { host: true }, // reachable over the LAN/tunnel, rask lesson
  test: {
    include: ["src/**/*.test.ts"],
    // Component tests mount Svelte 5 components into a DOM; the pure-function
    // tests don't care which environment they run in.
    environment: "jsdom",
    setupFiles: ["src/test-setup.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,svelte}"],
      exclude: ["src/**/*.test.ts", "src/test-setup.ts", "src/app.d.ts"],
    },
  },
});
