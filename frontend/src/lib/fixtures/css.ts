// A component's <style>, read the way the Svelte compiler parses it. jsdom
// applies none of a component's scoped styles, so a test that promises
// something about layout reads it from the rules themselves -- every rule,
// media queries included, in source order -- rather than by regexes that
// only ever saw the first rule of a name at one indentation.
import { parse, type AST } from "svelte/compiler";

export type CssRule = {
  selectors: string[];
  media: string | null;
  decls: [string, string][];
};

export const squash = (text: string) => text.replace(/\s+/g, " ").trim();

/** Every rule in `source`'s <style>, with the media query it sits in. */
export function cssRules(source: string): CssRule[] {
  const rules: CssRule[] = [];
  const walk = (
    nodes: (AST.CSS.Rule | AST.CSS.Atrule | AST.CSS.Declaration)[],
    media: string | null,
  ) => {
    for (const node of nodes) {
      if (node.type === "Atrule" && node.name === "media" && node.block)
        walk(node.block.children, squash(node.prelude));
      if (node.type !== "Rule") continue;
      rules.push({
        selectors: node.prelude.children.map((c) =>
          squash(source.slice(c.start, c.end)),
        ),
        media,
        decls: node.block.children.flatMap((d) =>
          d.type === "Declaration"
            ? [[d.property, squash(d.value)] as [string, string]]
            : [],
        ),
      });
    }
  };
  walk(parse(source, { modern: true }).css?.children ?? [], null);
  return rules;
}

/**
 * What `selector` (exactly that selector) ends up with, at full width or at
 * `media`: the top-level rules and that media query's, the later one of two
 * winning as in the cascade.
 */
export function cssOf(
  rules: CssRule[],
  selector: string,
  media: string | null = null,
) {
  const out = new Map<string, string>();
  for (const rule of rules)
    if (
      (rule.media === null || rule.media === media) &&
      rule.selectors.includes(selector)
    )
      for (const [property, value] of rule.decls) out.set(property, value);
  return out;
}
