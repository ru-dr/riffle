"use client";

import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Renders an Ask AI answer. Loaded only when a question is asked, so the
// markdown renderer never weighs on the docs' first load. Citations like
// [2] become small links to the excerpt they came from.

export type AskSource = { n: number; title: string; href: string };

export default function Answer({ text, sources, onNavigate }: { text: string; sources: AskSource[]; onNavigate: () => void }) {
  const byN = new Map(sources.map((s) => [s.n, s]));
  // [1] or [1, 3] -> one link per number, left alone if the number is unknown.
  const linked = text.replace(/\[(\d+(?:\s*,\s*\d+)*)\](?!\()/g, (m, list: string) =>
    list
      .split(",")
      .map((n) => n.trim())
      .map((n) => (byN.has(+n) ? `[${n}](#cite-${n})` : `[${n}]`))
      .join(""),
  );

  return (
    <div className="docs-answer font-geist text-[14.5px] leading-[1.7]" style={{ color: "var(--rf-nickel)" }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="my-2.5 first:mt-0">{children}</p>,
          ul: ({ children }) => <ul className="my-2.5 list-disc space-y-1 pl-5 marker:text-[var(--rf-grey)]">{children}</ul>,
          ol: ({ children }) => <ol className="my-2.5 list-decimal space-y-1 pl-5">{children}</ol>,
          strong: ({ children }) => (
            <strong className="font-medium" style={{ color: "var(--rf-ink)" }}>
              {children}
            </strong>
          ),
          code: ({ children, className }) =>
            // Fenced blocks: plain inside the <pre> card. Inline: a chip.
            className || String(children).includes("\n") ? (
              <code className="font-mono">{children}</code>
            ) : (
            <code className="rounded-[4px] px-[0.35em] py-[0.1em] font-mono text-[0.86em]" style={{ backgroundColor: "var(--rf-wash)", color: "var(--rf-ink)" }}>
              {children}
            </code>
            ),
          pre: ({ children }) => <pre className="my-3 overflow-x-auto rounded-md p-3 text-[12.5px]" style={{ backgroundColor: "var(--rf-wash)" }}>{children}</pre>,
          a: ({ href = "", children }) => {
            const cite = /^#cite-(\d+)$/.exec(href);
            if (cite) {
              const s = byN.get(+cite[1])!;
              return (
                <Link
                  href={s.href}
                  onClick={onNavigate}
                  title={s.title}
                  className="mx-[1px] inline-flex h-[17px] min-w-[17px] -translate-y-[1px] items-center justify-center rounded-[4px] px-1 align-middle font-mono text-[10px] no-underline transition-colors hover:bg-[var(--rf-ink)] hover:text-[var(--rf-bg)]"
                  style={{ backgroundColor: "var(--rf-wash)", color: "var(--rf-ink)", outline: "1px solid var(--rf-stroke)" }}
                >
                  {cite[1]}
                </Link>
              );
            }
            return href.startsWith("/") ? (
              <Link href={href} onClick={onNavigate} className="docs-link">
                {children}
              </Link>
            ) : (
              <a href={href} target="_blank" rel="noopener noreferrer" className="docs-link">
                {children}
              </a>
            );
          },
        }}
      >
        {linked}
      </ReactMarkdown>
    </div>
  );
}
