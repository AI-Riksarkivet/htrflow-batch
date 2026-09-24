// What the *.property.test.ts files share. Each run draws a fresh seed, and
// fast-check prints it with the counterexample when a property fails;
// FC_SEED=<seed> replays exactly that run.
import fc from "fast-check";

const seed = process.env.FC_SEED;
if (seed !== undefined && seed !== "")
  fc.configureGlobal({ seed: Number(seed) });

/** Cases per property: enough to reach the odd corners, fast enough for CI. */
export const RUNS = { numRuns: 300 };
