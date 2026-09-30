import GithubSlugger from "github-slugger";
import { ALL_PAGES } from "./registry";
import { headingText, loadMarkdown } from "./load";

// The docs search index: one record per page intro and per h2/h3 section,
// as plain text. Built once at build time and served as a static file, so
// search costs nothing until a reader opens it.

export type SearchRecord = {
  id: string;
  href: string; // /docs/slug or /docs/slug#heading
  page: string;
  section: string; // the sidebar section
  heading: string | null; // null for the page itself
  text: string;
};

/** Markdown to searchable plain text. */
function plain(md: string) {
  return md
    .replace(/```mermaid[\s\S]*?```/g, " ") // diagram source is not prose
    .replace(/```\w*\n?|```/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s*\|?\s*:?-{3,}.*$/gm, " ") // table rules
    .replace(/^\s*(?:[-+*]|\d+\.)\s+/gm, "") // list markers
    .replace(/(^|\s)_([^_\n]+)_(?=[\s.,;:]|$)/g, "$1$2") // _emphasis_, but not snake_case keys
    .replace(/[|>*`#]/g, " ")
    .replace(/\(\s+/g, "(")
    .replace(/\s+\)/g, ")")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * `raw` keeps each section's markdown (minus diagrams) instead of plain text:
 * Ask AI needs code, globs and keys exactly as written - `src/auth/**`, not
 * `src/auth/`. The browser index stays plain, which is smaller.
 */
export async function buildIndex({ raw = false } = {}): Promise<SearchRecord[]> {
  const body = raw ? (md: string) => md.replace(/```mermaid[\s\S]*?```/g, "").replace(/\n{3,}/g, "\n\n").trim() : plain;
  const out: SearchRecord[] = [];
  for (const page of ALL_PAGES) {
    const base = `/docs/${page.slug}`;
    const md = "outline" in page ? page.outline.map((l) => `- ${l}`).join("\n") : await loadMarkdown(page);

    // Split on headings with the same slugger the page uses, so anchors match.
    const slugger = new GithubSlugger();
    const parts: { heading: string | null; id: string | null; lines: string[] }[] = [{ heading: null, id: null, lines: [page.description] }];
    let fence = false;
    for (const line of md.split("\n")) {
      if (/^\s*(```|~~~)/.test(line)) fence = !fence;
      const m = fence ? null : /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
      if (!m) {
        parts[parts.length - 1].lines.push(line);
        continue;
      }
      const text = headingText(m[2]);
      const id = slugger.slug(text);
      if (m[1].length === 2 || m[1].length === 3) parts.push({ heading: text, id, lines: [] });
      else parts[parts.length - 1].lines.push(text);
    }

    for (const [i, p] of parts.entries()) {
      out.push({
        id: `${page.slug}:${i}`,
        href: p.id ? `${base}#${p.id}` : base,
        page: page.title,
        section: page.section.title,
        heading: p.heading,
        text: body(p.lines.join("\n")),
      });
    }
  }
  return out;
}
