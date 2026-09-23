// What a zod parse dropped: zod strips keys a schema has never heard of, at
// any depth and without a word, so a field renamed on the producing side
// parses fine and vanishes. The contract tests compare a fixture of real
// output with its parse through this.

/** Every key of `raw` missing from `parsed`, as a path with `[]` for arrays. */
export function dropped(raw: unknown, parsed: unknown, path = ""): string[] {
  if (Array.isArray(raw) && Array.isArray(parsed))
    return [
      ...new Set(raw.flatMap((r, i) => dropped(r, parsed[i], `${path}[]`))),
    ];
  if (!isObject(raw) || !isObject(parsed)) return [];
  return Object.keys(raw)
    .flatMap((k) => {
      const at = path === "" ? k : `${path}.${k}`;
      return k in parsed ? dropped(raw[k], parsed[k], at) : [at];
    })
    .sort();
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
