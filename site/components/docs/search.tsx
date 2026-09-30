"use client";

import MiniSearch, { type SearchResult } from "minisearch";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AskSource } from "./answer";
import type { SearchRecord } from "./search-index";
import { topical } from "./stopwords";

// The answer renderer (react-markdown) loads only when someone asks.
const Answer = dynamic(() => import("./answer"), { ssr: false });

type Ask = {
  question: string;
  sources: AskSource[];
  text: string;
  status: "loading" | "streaming" | "done" | "error";
  error?: string;
};

// Docs search, and Ask AI. The trigger sits in the header; ⌘K, Ctrl+K or "/" opens it
// anywhere in the docs. The index is fetched on first open, never on page
// load, and searched in the browser: prefix and light fuzzy matching, with
// headings and page titles weighted above body text.

type Hit = SearchRecord & { terms: string[] };

// Shown before anyone types, so Ask AI is visible without having to guess
// it exists. Each is answerable from the docs as they stand.
const EXAMPLES = ["How do I make src/auth always go to senior review?", "What happens when the explainer is down?", "Does Riffle store my code?"];

const SUGGEST: { href: string; label: string }[] = [
  { href: "/docs/getting-started/introduction", label: "Introduction" },
  { href: "/docs/getting-started/how-it-works", label: "How it works" },
  { href: "/docs/concepts/rank-bands", label: "Rank bands" },
  { href: "/docs/configuration/reference", label: "Configuration reference" },
];

let cached: Promise<MiniSearch<SearchRecord>> | null = null;
function loadIndex() {
  cached ??= fetch("/docs/search.json")
    .then((r) => r.json() as Promise<SearchRecord[]>)
    .then((docs) => {
      const ms = new MiniSearch<SearchRecord>({
        fields: ["page", "heading", "text"],
        storeFields: ["href", "page", "section", "heading", "text"],
        searchOptions: {
          boost: { heading: 3, page: 2 },
          prefix: true,
          fuzzy: 0.2,
        },
      });
      ms.addAll(docs);
      return ms;
    })
    .catch((err) => {
      cached = null; // let the next open retry
      throw err;
    });
  return cached;
}

function search(ms: MiniSearch<SearchRecord>, q: string): Hit[] {
  // Every word must match; if nothing does, fall back to any word.
  const words = topical(q);
  let res: SearchResult[] = ms.search(words, { combineWith: "AND" });
  if (!res.length) res = ms.search(words, { combineWith: "OR" });
  return res.slice(0, 12).map((r) => ({ ...(r as unknown as SearchRecord), terms: r.terms }));
}

/** A short excerpt around the first matched term, with matches marked. */
function Snippet({ text, terms }: { text: string; terms: string[] }) {
  if (!text) return null;
  const lower = text.toLowerCase();
  const at =
    terms
      .map((t) => lower.indexOf(t.toLowerCase()))
      .filter((i) => i >= 0)
      .sort((a, b) => a - b)[0] ?? 0;
  const start = Math.max(0, at - 48);
  const end = Math.min(text.length, start + 150);
  const slice = (start > 0 ? "…" : "") + text.slice(start, end) + (end < text.length ? "…" : "");
  // Mark whole-word starts only; a 2-letter term inside "criticality" is noise.
  const escaped = terms.filter((t) => t.length > 2).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const parts = escaped.length ? slice.split(new RegExp(`(?<![\\p{L}\\p{N}])(${escaped.join("|")})`, "giu")) : [slice];
  return (
    <span className="line-clamp-2 font-geist text-[13px] leading-[1.55]" style={{ color: "var(--rf-grey)" }}>
      {parts.map((p, i) =>
        i % 2 ? (
          <mark
            key={i}
            className="rounded-[2px] bg-transparent font-medium"
            style={{
              color: "var(--rf-ink)",
              boxShadow: "inset 0 -0.45em 0 color-mix(in srgb, var(--rf-accent) 22%, transparent)",
            }}
          >
            {p}
          </mark>
        ) : (
          <Fragment key={i}>{p}</Fragment>
        ),
      )}
    </span>
  );
}

export function DocsSearch() {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [index, setIndex] = useState<MiniSearch<SearchRecord> | null>(null);
  const [failed, setFailed] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const [mac, setMac] = useState(true);
  // Opened from "Ask AI": the input invites a question instead of a search.
  const [asking, setAsking] = useState(false);
  const [ask, setAsk] = useState<Ask | null>(null);
  const abort = useRef<AbortController | null>(null);

  // Ask AI: POST the question; the reply's first line is the sources as
  // JSON, the rest is the answer, streamed.
  const startAsk = useCallback(async (question: string) => {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setAsk({ question, sources: [], text: "", status: "loading" });
    try {
      const res = await fetch("/docs/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question }),
        signal: ctl.signal,
      });
      if (!res.ok || !res.body) {
        const msg = await res.json().then(
          (j) => j.error as string,
          () => "Ask AI is unavailable right now.",
        );
        setAsk({
          question,
          sources: [],
          text: "",
          status: "error",
          error: msg,
        });
        return;
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      let sources: AskSource[] | null = null;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        if (!sources) {
          const nl = buf.indexOf("\n");
          if (nl < 0) continue;
          sources = JSON.parse(buf.slice(0, nl)) as AskSource[];
          buf = buf.slice(nl + 1);
        }
        const text = buf;
        const src = sources;
        setAsk({ question, sources: src, text, status: "streaming" });
      }
      setAsk({ question, sources: sources ?? [], text: buf, status: "done" });
    } catch (err) {
      if (ctl.signal.aborted) return;
      setAsk({
        question,
        sources: [],
        text: "",
        status: "error",
        error: "Ask AI is unavailable right now.",
      });
    }
  }, []);

  const open = useCallback((mode: "search" | "ask" = "search") => {
    const d = dialog.current;
    if (!d || d.open) return;
    setAsking(mode === "ask");
    d.showModal();
    input.current?.select();
    loadIndex().then(setIndex, () => setFailed(true));
  }, []);
  const close = useCallback(() => dialog.current?.close(), []);
  const reset = useCallback(() => {
    abort.current?.abort();
    setAsk(null);
  }, []);

  // Shortcuts: ⌘K / Ctrl+K from anywhere, "/" when not already typing.
  useEffect(() => {
    setMac(/Mac|iPhone|iPad/.test(navigator.userAgent));
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && (e.target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName));
      if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing)) {
        e.preventDefault();
        open("search");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const hits = useMemo(() => (index && q.trim() ? search(index, q.trim()) : []), [index, q]);
  useEffect(() => setSel(0), [q]);
  useEffect(() => {
    list.current?.querySelector(`[data-i="${sel}"]`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  const go = (href: string) => {
    close();
    router.push(href);
  };

  const query = q.trim();
  const mode: "search" | "ask" = asking ? "ask" : "search";

  // Every selectable row for the current state, in order. Keyboard and
  // mouse both index into this, so they can never disagree.
  type Row = { kind: "example"; text: string } | { kind: "page"; href: string; label: string } | { kind: "ask" } | { kind: "hit"; hit: Hit };
  const rows: Row[] = useMemo(() => {
    const examples = EXAMPLES.map((text) => ({
      kind: "example" as const,
      text,
    }));
    if (mode === "ask") return query ? [{ kind: "ask" }] : examples;
    if (!query) return [...SUGGEST.map((s) => ({ kind: "page" as const, ...s })), ...examples];
    return [{ kind: "ask" }, ...hits.map((hit) => ({ kind: "hit" as const, hit }))];
  }, [mode, query, hits]);

  const askExample = (question: string) => {
    setQ(question);
    startAsk(question);
  };
  const run = (r: Row) => {
    if (r.kind === "example") askExample(r.text);
    else if (r.kind === "ask") startAsk(query);
    else go(r.kind === "page" ? r.href : r.hit.href);
  };
  const switchMode = (m: "search" | "ask") => {
    reset();
    setAsking(m === "ask");
    setSel(0);
    input.current?.focus();
  };

  const onInputKey = (e: React.KeyboardEvent) => {
    if (ask) {
      if (e.key === "Enter" && query && query !== ask.question) {
        e.preventDefault();
        startAsk(query);
      }
      return;
    }
    if (!rows.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel((s) => (s + 1) % rows.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel((s) => (s - 1 + rows.length) % rows.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      run(rows[Math.min(sel, rows.length - 1)]);
    }
  };

  const row = (i: number) => `flex w-full flex-col gap-1 rounded-lg px-3.5 py-3 text-left transition-colors ${i === sel ? "bg-[var(--rf-wash)]" : ""}`;
  const label = "px-3.5 pt-3 pb-1.5 font-mono text-[11px] tracking-[0.06em] uppercase";

  return (
    <>
      <button
        type="button"
        // Hidden on phones, where the header has no room; the dialog opens
        // with Ask AI's examples first, so it stays one tap away.
        onClick={() => open("ask")}
        aria-label="Ask AI about the docs"
        title="Ask AI about the docs"
        className="hidden h-8 shrink-0 items-center gap-1.5 rounded-md border px-2 whitespace-nowrap transition-colors hover:border-[var(--rf-grey)] sm:flex xl:px-2.5"
        style={{
          borderColor: "var(--rf-stroke)",
          color: "var(--rf-ink)",
          backgroundColor: "var(--rf-surface)",
        }}
      >
        <Spark />
        <span className="hidden font-geist text-[13px] leading-none xl:inline">Ask AI</span>
      </button>
      <button
        type="button"
        onClick={() => open("search")}
        aria-label="Search docs"
        aria-keyshortcuts="Meta+K Control+K /"
        className="flex h-8 shrink-0 items-center gap-2 rounded-md border px-2 whitespace-nowrap transition-colors hover:border-[var(--rf-grey)] xl:w-52 xl:px-2.5"
        style={{
          borderColor: "var(--rf-stroke)",
          color: "var(--rf-grey)",
          backgroundColor: "var(--rf-surface)",
        }}
      >
        <svg viewBox="0 0 24 24" className="size-[15px] shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          <circle cx="11" cy="11" r="6.5" />
          <path d="m20 20-4.2-4.2" />
        </svg>
        <span className="hidden font-geist text-[13px] leading-none xl:inline">Search docs</span>
        {/* The shortcut as two keycaps: the modifier's symbol, then K. */}
        <span className="ml-auto hidden shrink-0 items-center gap-0.5 xl:flex" aria-hidden="true">
          {[mac ? "⌘" : "⌃", "K"].map((k) => (
            <kbd
              key={k}
              className="flex size-[18px] items-center justify-center rounded-[4px] border font-geist text-[11px] leading-none"
              style={{
                borderColor: "var(--rf-stroke)",
                backgroundColor: "var(--rf-bg)",
              }}
            >
              {k}
            </kbd>
          ))}
        </span>
      </button>

      <dialog
        ref={dialog}
        aria-label="Search docs or ask AI"
        onClick={(e) => e.target === dialog.current && close()}
        onClose={() => {
          setQ("");
          reset();
        }}
        className="docs-search m-0 mx-auto mt-[7vh] w-[calc(100vw-1.5rem)] max-w-[46rem] overflow-hidden rounded-xl p-0"
        style={{
          backgroundColor: "var(--rf-bg)",
          color: "var(--rf-ink)",
          outline: "1px solid var(--rf-stroke)",
          border: "none",
        }}
      >
        {/* Input, then the two modes as tabs. The icon and prompt follow the mode. */}
        <div className="flex items-center gap-3 px-5">
          {mode === "ask" ? (
            <Spark size={19} />
          ) : (
            <svg viewBox="0 0 24 24" className="size-[19px] shrink-0" fill="none" stroke="var(--rf-grey)" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
              <circle cx="11" cy="11" r="6.5" />
              <path d="m20 20-4.2-4.2" />
            </svg>
          )}
          <input
            ref={input}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              if (ask && ask.status !== "loading" && ask.status !== "streaming") reset();
            }}
            onKeyDown={onInputKey}
            placeholder={mode === "ask" ? "Ask a question about Riffle…" : "Search the docs"}
            aria-label={mode === "ask" ? "Ask a question about Riffle" : "Search the docs"}
            aria-controls="docs-search-results"
            autoComplete="off"
            spellCheck={false}
            className="h-16 min-w-0 flex-1 bg-transparent font-geist text-[18px] outline-none placeholder:text-[var(--rf-grey)]"
          />
          <kbd className="rounded-[5px] border px-1.5 font-mono text-[11px] leading-[20px]" style={{ borderColor: "var(--rf-stroke)", color: "var(--rf-grey)" }}>
            Esc
          </kbd>
        </div>
        <div role="tablist" aria-label="Mode" className="flex items-center gap-1 border-b px-4" style={{ borderColor: "var(--rf-stroke)" }}>
          {(["search", "ask"] as const).map((m) => (
            <button
              key={m}
              type="button"
              role="tab"
              aria-selected={mode === m}
              onClick={() => switchMode(m)}
              className="relative flex items-center gap-1.5 px-2.5 pt-1 pb-2.5 font-geist text-[13.5px] transition-colors"
              style={{ color: mode === m ? "var(--rf-ink)" : "var(--rf-grey)" }}
            >
              {m === "ask" ? <Spark size={14} /> : null}
              {m === "ask" ? "Ask AI" : "Search"}
              <span
                aria-hidden="true"
                className="absolute inset-x-2 -bottom-px h-[2px] rounded-full transition-opacity"
                style={{
                  backgroundColor: "var(--rf-ink)",
                  opacity: mode === m ? 1 : 0,
                }}
              />
            </button>
          ))}
          <span className="ml-auto pb-1.5 font-mono text-[10.5px] tracking-[0.04em]" style={{ color: "var(--rf-grey)" }}>
            {mode === "ask" ? "Answers come only from these docs" : index && query ? `${hits.length} result${hits.length === 1 ? "" : "s"}` : ""}
          </span>
        </div>

        <div className="max-h-[min(40rem,68vh)] min-h-[18rem] overflow-y-auto p-2.5">
          {ask ? (
            <AskView ask={ask} onNavigate={close} onBack={reset} backLabel={mode === "ask" ? "New question" : "Back to results"} />
          ) : mode === "search" && query && failed ? (
            <p className="px-3 py-10 text-center font-geist text-[14.5px]" style={{ color: "var(--rf-grey)" }}>
              Search could not load. Check your connection and try again.
            </p>
          ) : mode === "search" && query && !index ? (
            <p className="px-3 py-10 text-center font-geist text-[14.5px]" style={{ color: "var(--rf-grey)" }}>
              Loading…
            </p>
          ) : (
            <ul ref={list} id="docs-search-results" role="listbox" aria-label={mode === "ask" ? "Questions" : "Results"}>
              {rows.map((r, i) => {
                const prev = rows[i - 1]?.kind;
                const head = r.kind !== prev ? (r.kind === "page" ? "Start here" : r.kind === "example" ? (mode === "ask" ? "Try asking" : "Or ask AI") : r.kind === "hit" ? "Pages" : null) : null;
                const on = {
                  "data-i": i,
                  onMouseMove: () => setSel(i),
                  onClick: () => run(r),
                  role: "option",
                  "aria-selected": i === sel,
                } as const;
                return (
                  <Fragment key={r.kind === "hit" ? r.hit.id : r.kind === "page" ? r.href : r.kind === "example" ? r.text : "ask"}>
                    {head && (
                      <li role="presentation" className={`${label} ${i ? "mt-2" : ""}`} style={{ color: "var(--rf-grey)" }}>
                        {head}
                      </li>
                    )}
                    <li role="presentation">
                      {r.kind === "ask" ? (
                        <button type="button" {...on} className={`${row(i)} !flex-row items-center gap-3`}>
                          <span className="flex size-8 shrink-0 items-center justify-center rounded-md" style={{ backgroundColor: "var(--rf-wash)" }}>
                            <Spark size={16} />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block font-geist text-[15px] font-medium">Ask AI</span>
                            <span className="block truncate font-geist text-[13.5px]" style={{ color: "var(--rf-grey)" }}>
                              &ldquo;{query}&rdquo;
                            </span>
                          </span>
                          <Key>↵</Key>
                        </button>
                      ) : r.kind === "example" ? (
                        <button type="button" {...on} className={`${row(i)} !flex-row items-center gap-3`}>
                          <Spark size={15} />
                          <span className="min-w-0 flex-1 truncate font-geist text-[15px]">{r.text}</span>
                          {i === sel && <Key>↵</Key>}
                        </button>
                      ) : r.kind === "page" ? (
                        <button type="button" {...on} className={`${row(i)} !flex-row items-center gap-3`}>
                          <span className="min-w-0 flex-1 font-geist text-[15px]">{r.label}</span>
                          {i === sel && <Key>↵</Key>}
                        </button>
                      ) : (
                        <button type="button" {...on} className={row(i)}>
                          <span className="font-mono text-[10.5px] tracking-[0.06em] uppercase" style={{ color: "var(--rf-grey)" }}>
                            {r.hit.section} / {r.hit.page}
                          </span>
                          <span className="font-geist text-[15.5px] font-medium">{r.hit.heading ?? r.hit.page}</span>
                          <Snippet text={r.hit.text} terms={r.hit.terms} />
                        </button>
                      )}
                    </li>
                  </Fragment>
                );
              })}
              {mode === "search" && query && index && !hits.length && (
                <li role="presentation" className="px-3 py-8 text-center font-geist text-[14.5px]" style={{ color: "var(--rf-grey)" }}>
                  No pages match &ldquo;{query}&rdquo;. Ask AI may still find it.
                </li>
              )}
              {mode === "ask" && query && (
                <li role="presentation" className="px-3.5 pt-3 font-geist text-[13px]" style={{ color: "var(--rf-grey)" }}>
                  Riffle&rsquo;s docs are searched for the passages that answer it, and the answer cites them.
                </li>
              )}
            </ul>
          )}
        </div>

        <div className="flex items-center gap-4 border-t px-5 py-2.5 font-mono text-[10.5px] tracking-[0.04em]" style={{ borderColor: "var(--rf-stroke)", color: "var(--rf-grey)" }}>
          {ask ? (
            <span>AI answers can be wrong. Check the sources.</span>
          ) : (
            <>
              <span className="flex items-center gap-1.5">
                <Key>↑</Key>
                <Key>↓</Key> to move
              </span>
              <span className="flex items-center gap-1.5">
                <Key>↵</Key> {mode === "ask" ? "to ask" : "to open"}
              </span>
            </>
          )}
          <span className="ml-auto flex items-center gap-1.5">
            <Key>Esc</Key> to close
          </span>
        </div>
      </dialog>
    </>
  );
}

function Key({ children }: { children: React.ReactNode }) {
  return (
    <kbd
      className="inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-[4px] border px-1 font-geist text-[10.5px] leading-none"
      style={{
        borderColor: "var(--rf-stroke)",
        backgroundColor: "var(--rf-surface)",
        color: "var(--rf-grey)",
      }}
    >
      {children}
    </kbd>
  );
}

function Spark({ size = 16 }: { size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} className="shrink-0" fill="none" stroke="var(--rf-accent)" strokeWidth="1.6" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9L12 3.5Z" />
      <path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8Z" />
    </svg>
  );
}

/** The Ask AI panel: the question, the streamed answer, and its sources. */
function AskView({ ask, onNavigate, onBack, backLabel }: { ask: Ask; onNavigate: () => void; onBack: () => void; backLabel: string }) {
  const busy = ask.status === "loading" || ask.status === "streaming";
  // Lead with the sources the answer cites; the rest were searched but not used.
  const citedNs = new Set([...ask.text.matchAll(/\[(\d+(?:\s*,\s*\d+)*)\]/g)].flatMap((m) => m[1].split(",").map((n) => +n.trim())));
  const cited = ask.sources.filter((s) => citedNs.has(s.n));
  const others = ask.sources.filter((s) => !citedNs.has(s.n));
  const lead = busy || !cited.length ? ask.sources : cited;
  return (
    <div className="px-4 py-3" aria-live="polite" aria-busy={busy}>
      <div className="flex items-start justify-between gap-4">
        <h2 className="font-geist text-[19px] leading-[1.35] font-medium tracking-[-0.01em]" style={{ color: "var(--rf-ink)" }}>
          {ask.question}
        </h2>
        <button type="button" onClick={onBack} className="rf-link mt-1 shrink-0 font-mono text-[11px] tracking-[0.04em]" style={{ color: "var(--rf-grey)" }}>
          &larr; {backLabel}
        </button>
      </div>
      <p className="mt-2 flex items-center gap-2 font-mono text-[10.5px] tracking-[0.06em] uppercase" style={{ color: "var(--rf-grey)" }}>
        <Spark size={13} />
        {ask.status === "loading" ? "Reading the docs…" : busy ? "Answering…" : ask.status === "error" ? "No answer" : "Answer"}
      </p>
      <div className="mt-3 text-[15.5px]">
        {ask.status === "error" ? (
          <p className="font-geist text-[15px]" style={{ color: "var(--rf-grey)" }}>
            {ask.error}
          </p>
        ) : ask.text ? (
          <Answer text={ask.text} sources={ask.sources} onNavigate={onNavigate} />
        ) : (
          <div className="docs-thinking space-y-2.5 pt-1" aria-hidden="true">
            {[92, 78, 85, 40].map((w) => (
              <div key={w} className="h-3 rounded-full" style={{ width: `${w}%`, backgroundColor: "var(--rf-wash)" }} />
            ))}
          </div>
        )}
      </div>
      {ask.sources.length > 0 && ask.status !== "loading" && (
        <div className="mt-6 border-t pt-4" style={{ borderColor: "var(--rf-stroke)" }}>
          <p className="mb-2.5 font-mono text-[11px] tracking-[0.06em] uppercase" style={{ color: "var(--rf-grey)" }}>
            {lead === cited ? "Cited" : "Sources"}
          </p>
          <SourceCards sources={lead} onNavigate={onNavigate} />
          {lead === cited && others.length > 0 && (
            <details className="group mt-3">
              <summary
                className="flex cursor-pointer list-none items-center gap-2 font-mono text-[11px] tracking-[0.06em] uppercase select-none [&::-webkit-details-marker]:hidden"
                style={{ color: "var(--rf-grey)" }}
              >
                <span aria-hidden="true" className="transition-transform group-open:rotate-90">
                  &#8250;
                </span>
                Also searched ({others.length})
              </summary>
              <div className="mt-2.5">
                <SourceCards sources={others} onNavigate={onNavigate} />
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

function SourceCards({ sources, onNavigate }: { sources: AskSource[]; onNavigate: () => void }) {
  return (
    <ol className="grid gap-2 sm:grid-cols-2">
      {sources.map((s) => {
        const [page, heading] = s.title.split(" › ");
        return (
          <li key={s.n}>
            <Link
              href={s.href}
              onClick={onNavigate}
              className="flex h-full items-start gap-2.5 rounded-lg border px-3 py-2.5 transition-colors hover:border-[var(--rf-grey)] hover:bg-[var(--rf-wash)]"
              style={{ borderColor: "var(--rf-stroke)" }}
            >
              <span
                className="mt-px flex size-[18px] shrink-0 items-center justify-center rounded-[4px] font-mono text-[10px]"
                style={{
                  backgroundColor: "var(--rf-wash)",
                  color: "var(--rf-ink)",
                }}
              >
                {s.n}
              </span>
              <span className="min-w-0">
                <span className="block truncate font-geist text-[13.5px] font-medium" style={{ color: "var(--rf-ink)" }}>
                  {heading ?? page}
                </span>
                {heading && (
                  <span className="block truncate font-geist text-[12.5px]" style={{ color: "var(--rf-grey)" }}>
                    {page}
                  </span>
                )}
              </span>
            </Link>
          </li>
        );
      })}
    </ol>
  );
}
