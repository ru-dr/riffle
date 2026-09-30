import Link from "next/link";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeSlug from "rehype-slug";
import remarkGfm from "remark-gfm";
import { CopyButton } from "./copy-button";
import { Diagram } from "./diagram";
import { highlight } from "./highlight";

// Renders a docs page's markdown in the site's own vocabulary: Geist for
// prose, Geist Mono for code and table heads, hairline rules, and code
// blocks as a card in the docs' light or dark theme. Everything is a server
// component; highlighted code arrives as static HTML.

async function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  const clean = code.replace(/\n$/, "");
  const html = await highlight(clean, lang);
  return (
    <figure className="docs-code my-7 overflow-hidden rounded-md" style={{ backgroundColor: "var(--rf-code-bg)", outline: "1px solid var(--rf-code-stroke)" }}>
      {/* Header on every block: the language, and a copy button. */}
      <figcaption
        className="flex items-center justify-between border-b py-1.5 pr-2 pl-5"
        style={{ borderColor: "var(--rf-code-stroke)", backgroundColor: "var(--rf-code-head)" }}
      >
        <span className="font-mono text-[11px] tracking-[0.06em] uppercase" style={{ color: "var(--rf-grey)" }}>
          {lang && lang !== "text" ? lang : "text"}
        </span>
        <CopyButton text={clean} />
      </figcaption>
      <div className="docs-code-body overflow-x-auto px-5 py-4 font-mono text-[13px] leading-[1.75]" dangerouslySetInnerHTML={{ __html: html }} />
    </figure>
  );
}

/** Plain text of a React node, to read a callout's leading label. */
function textOf(node: unknown): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  const props = (node as { props?: { children?: unknown } }).props;
  return props ? textOf(props.children) : "";
}

const CALLOUT: Record<string, string> = {
  planned: "var(--rf-grey)",
  proposed: "#d97706",
  accepted: "#16a34a",
  draft: "#dc2626",
};

const components: Components = {
  h2: ({ children, id }) => (
    <h2
      id={id}
      className="group mt-14 scroll-mt-24 border-t pt-8 font-geist text-[1.6rem] leading-[1.2] font-medium tracking-[-0.025em] first:mt-0"
      style={{ borderColor: "var(--rf-stroke)", color: "var(--rf-ink)" }}
    >
      <a href={`#${id}`} className="no-underline">
        {children}
      </a>
    </h2>
  ),
  h3: ({ children, id }) => (
    <h3 id={id} className="mt-10 scroll-mt-24 font-geist text-[1.2rem] font-medium tracking-[-0.015em]" style={{ color: "var(--rf-ink)" }}>
      <a href={`#${id}`} className="no-underline">
        {children}
      </a>
    </h3>
  ),
  h4: ({ children, id }) => (
    <h4 id={id} className="mt-8 scroll-mt-24 font-mono text-[14px]" style={{ color: "var(--rf-ink)" }}>
      {children}
    </h4>
  ),
  p: ({ children }) => (
    <p className="my-5 font-geist text-[16px] leading-[1.75]" style={{ color: "var(--rf-nickel)" }}>
      {children}
    </p>
  ),
  strong: ({ children }) => (
    <strong className="font-medium" style={{ color: "var(--rf-ink)" }}>
      {children}
    </strong>
  ),
  a: ({ href = "", children }) =>
    href.startsWith("/") || href.startsWith("#") ? (
      <Link href={href} className="docs-link">
        {children}
      </Link>
    ) : (
      <a href={href} target="_blank" rel="noopener noreferrer" className="docs-link">
        {children}
        <span aria-hidden="true" className="ml-0.5 text-[0.8em]">
          &#8599;
        </span>
      </a>
    ),
  ul: ({ children }) => <ul className="my-5 space-y-2 pl-5 font-geist text-[16px] leading-[1.7] marker:text-[var(--rf-grey)] [list-style:disc]" style={{ color: "var(--rf-nickel)" }}>{children}</ul>,
  ol: ({ children }) => <ol className="my-5 space-y-2 pl-5 font-geist text-[16px] leading-[1.7] [list-style:decimal]" style={{ color: "var(--rf-nickel)" }}>{children}</ol>,
  li: ({ children }) => <li className="pl-1">{children}</li>,
  blockquote: ({ children }) => {
    // Callouts: a hairline box with an inset accent bar. The bar's colour
    // follows the note's leading label - Planned, Proposed, Accepted.
    const label = textOf(children).trim().split(/[.\s]/)[0].toLowerCase();
    const bar = CALLOUT[label] ?? "var(--rf-accent)";
    return (
      <aside
        className="docs-callout relative my-7 rounded-md border py-3.5 pr-5 pl-6"
        style={{ borderColor: "var(--rf-stroke)", backgroundColor: "var(--rf-wash)" }}
      >
        <span aria-hidden="true" className="absolute top-3 bottom-3 left-0 w-[3px] rounded-r-full" style={{ backgroundColor: bar }} />
        {children}
      </aside>
    );
  },
  hr: () => <hr className="my-12" style={{ borderColor: "var(--rf-stroke)" }} />,
  table: ({ children }) => (
    <div className="my-7 overflow-x-auto rounded-md" style={{ outline: `1px solid var(--rf-stroke)` }}>
      <table className="w-full border-collapse text-left">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead style={{ backgroundColor: "var(--rf-wash)" }}>{children}</thead>,
  th: ({ children }) => (
    <th className="border-b px-4 py-2.5 font-mono text-[11px] font-normal tracking-[0.06em] whitespace-nowrap uppercase" style={{ borderColor: "var(--rf-stroke)", color: "var(--rf-grey)" }}>
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b px-4 py-3 align-top font-geist text-[14px] leading-[1.55]" style={{ borderColor: "var(--rf-stroke)", color: "var(--rf-nickel)" }}>
      {children}
    </td>
  ),
  code: ({ children, className }) => {
    // Fenced blocks arrive with a language-* class and are handled by `pre`;
    // everything reaching here without one is inline code.
    if (className?.startsWith("language-")) return <code className={className}>{children}</code>;
    return (
      <code className="rounded-[4px] px-[0.35em] py-[0.1em] font-mono text-[0.86em]" style={{ backgroundColor: "var(--rf-wash)", color: "var(--rf-ink)" }}>
        {children}
      </code>
    );
  },
  pre: ({ children }) => {
    const child = Array.isArray(children) ? children[0] : children;
    const props = (child as { props?: { className?: string; children?: unknown } })?.props ?? {};
    const lang = /language-(\w+)/.exec(props.className ?? "")?.[1];
    if (lang === "mermaid") return <Diagram source={String(props.children ?? "")} />;
    return <CodeBlock code={String(props.children ?? "")} lang={lang} />;
  },
};

export function Markdown({ source }: { source: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSlug]} components={components}>
      {source}
    </ReactMarkdown>
  );
}
