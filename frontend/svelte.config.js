import adapter from "@sveltejs/adapter-static";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

/** @type {import('@sveltejs/kit').Config} */
export default {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter({ pages: "dist", assets: "dist" }),
    // Prerendered pages carry the policy as a <meta http-equiv> tag, with a
    // hash for SvelteKit's own inline init script. Only same-origin scripts
    // run, so a deployment sets window.API_BASE by overwriting
    // /config.js (see static/config.js), never by injecting inline script.
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
  },
};
