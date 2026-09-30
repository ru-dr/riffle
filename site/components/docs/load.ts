import GithubSlugger from "github-slugger";
import { DOCS } from "./content.generated";
import type { DocPage } from "./registry";

// A page's markdown, from the module scripts/build-docs.mjs compiles before
// every dev and build: authored pages from site/content/docs, and repository
// files (the configuration reference) under their repo-relative path.

export async function loadMarkdown(page: DocPage): Promise<string> {
  if ("outline" in page) return ""; // drafts render their outline, not markdown
  const key = "file" in page ? page.file : page.source;
  let md = DOCS[key];
  if (md === undefined) throw new Error(`docs: no compiled content for ${key} - run \`bun run docs\``);
  if ("source" in page) {
    // The page renders its own title; drop the file's H1 and its
    // "generated, do not edit" line, which are notes for the repository.
    md = md.replace(/^# .+\n+/, "").replace(/^Generated from the schemas[^\n]*\n+/, "");
  }
  return md;
}

export type Heading = { depth: 2 | 3; text: string; id: string };

/**
 * The page's h2/h3 outline, for "On this page". Slugs are made with the same
 * github-slugger rehype-slug uses, over every heading in document order, so
 * duplicate-heading suffixes (-1, -2) line up with the rendered ids.
 */
export function outline(md: string): Heading[] {
  const slugger = new GithubSlugger();
  const out: Heading[] = [];
  let inFence = false;
  for (const line of md.split("\n")) {
    if (/^\s*(```|~~~)/.test(line)) inFence = !inFence;
    if (inFence) continue;
    const m = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!m) continue;
    const text = m[2]
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/[`*_]/g, "")
      .trim();
    const id = slugger.slug(text);
    const depth = m[1].length;
    if (depth === 2 || depth === 3) out.push({ depth, text, id });
  }
  return out;
}
