import adapter from "@sveltejs/adapter-static";
import { sveltekit } from "@sveltejs/kit/vite";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";
import { svelteTesting } from "@testing-library/svelte/vite";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    sveltekit({
      preprocess: vitePreprocess(),
      adapter: adapter({ pages: "dist", assets: "dist" }),
      // Prerendered pages carry the policy as a <meta http-equiv> tag, with a
      // hash for SvelteKit's own inline init script. Only same-origin scripts
      // run, which is why the page takes its runtime configuration from
      // /config.js -- a same-origin file the read API serves from its own
      // environment (packages/web) -- and never from an injected inline
      // script, which this policy blocks.
      // A CSP header sent by the web server must not be stricter than this one
      // (the browser enforces the intersection): either send none or include
      // the same script hash.
      csp: {
        mode: "hash",
        directives: {
          "script-src": ["self"],
          "object-src": ["none"],
          "base-uri": ["self"],
        },
      },
    }),
    svelteTesting(),
  ],
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
