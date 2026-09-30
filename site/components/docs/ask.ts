import MiniSearch from "minisearch";
import { buildIndex, type SearchRecord } from "./search-index";
import { topical } from "./stopwords";

// Retrieval for "Ask AI": the same records the search dialog uses, searched
// on the server, so the model only ever sees what the docs actually say.

export type Source = { n: number; title: string; href: string };

let ready: Promise<MiniSearch<SearchRecord>> | null = null;
function index() {
  ready ??= buildIndex({ raw: true }).then((docs) => {
    const ms = new MiniSearch<SearchRecord>({
      fields: ["page", "heading", "text"],
      storeFields: ["href", "page", "section", "heading", "text"],
      searchOptions: { boost: { heading: 3, page: 2 }, prefix: true, fuzzy: 0.2, combineWith: "OR" },
    });
    ms.addAll(docs);
    return ms;
  });
  return ready;
}

/** The sections most relevant to a question, numbered for citation. */
export async function retrieve(question: string, limit = 8) {
  const ms = await index();
  const hits = ms.search(topical(question)).slice(0, limit) as unknown as SearchRecord[];
  const sources: Source[] = hits.map((h, i) => ({
    n: i + 1,
    title: h.heading ? `${h.page} › ${h.heading}` : h.page,
    href: h.href,
  }));
  // A shared budget rather than a fixed cut per excerpt: a long section (the
  // table of 50 repositories) gets room when the others are short, and the
  // whole context stays near 12,000 characters, so a question stays cheap.
  // Link targets are dropped - the model needs the text, not the URLs.
  let budget = 12_000;
  const context = hits
    .map((h, i) => {
      const text = h.text.replace(/\[([^\]]+)\]\([^)]*\)/g, "$1");
      const take = Math.max(400, Math.min(6000, budget, text.length));
      budget -= Math.min(take, text.length);
      return `[${i + 1}] ${sources[i].title} (${h.href})\n${text.slice(0, take)}`;
    })
    .join("\n\n");
  return { sources, context };
}

export function prompt(question: string, context: string) {
  return `You answer questions about Riffle using only its documentation, given below as numbered excerpts.

Riffle is a GitHub App that orders a pull request queue by risk. It is in development: the design is settled, the implementation is in progress.

Rules:
- Use only the excerpts. If they do not answer the question, say the docs do not cover it and suggest the closest page. Never guess or add outside knowledge.
- Cite the excerpts you use with their number in brackets, like [2], right after the claim.
- Be brief: a short paragraph or a few bullets. Use Markdown. Put configuration keys and values in backticks.
- Keep the docs' status words: if something is planned or proposed, say so.

Excerpts:
${context || "(no matching excerpts)"}

Question: ${question}`;
}
