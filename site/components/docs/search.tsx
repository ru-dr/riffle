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

type Ask = { question: string; sources: AskSource[]; text: string; status: "loading" | "streaming" | "done" | "error"; error?: string };

// Docs search, and Ask AI. The trigger sits in the header; ⌘K, Ctrl+K or "/" opens it
// anywhere in the docs. The index is fetched on first open, never on page
// load, and searched in the browser: prefix and light fuzzy matching, with
// headings and page titles weighted above body text.

type Hit = SearchRecord & { terms: string[] };

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
        searchOptions: { boost: { heading: 3, page: 2 }, prefix: true, fuzzy: 0.2 },
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
  const at = terms.map((t) => lower.indexOf(t.toLowerCase())).filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? 0;
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
          <mark key={i} className="rounded-[2px] bg-transparent font-medium" style={{ color: "var(--rf-ink)", boxShadow: "inset 0 -0.45em 0 color-mix(in srgb, var(--rf-accent) 22%, transparent)" }}>
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
        const msg = await res.json().then((j) => j.error as string, () => "Ask AI is unavailable right now.");
        setAsk({ question, sources: [], text: "", status: "error", error: msg });
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
      setAsk({ question, sources: [], text: "", status: "error", error: "Ask AI is unavailable right now." });
    }
  }, []);

  const open = useCallback(() => {
    const d = dialog.current;
    if (!d || d.open) return;
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
        open();
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

  // With a query, row 0 is "Ask AI" and the search hits follow it.
  const query = q.trim();
  const onInputKey = (e: React.KeyboardEvent) => {
    if (ask) {
      if (e.key === "Enter" && query && query !== ask.question) {
        e.preventDefault();
        startAsk(query);
      }
      return;
    }
    const n = query ? hits.length + 1 : SUGGEST.length;
    if (!n) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel((s) => (s + 1) % n);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel((s) => (s - 1 + n) % n);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (!query) go(SUGGEST[sel].href);
      else if (sel === 0) startAsk(query);
      else go(hits[sel - 1].href);
    }
  };

  const row = (i: number) =>
    `flex w-full flex-col gap-0.5 rounded-md px-3 py-2.5 text-left transition-colors ${i === sel ? "bg-[var(--rf-wash)]" : ""}`;

  return (
    <>
      <button
        type="button"
        onClick={open}
        aria-label="Search docs"
        aria-keyshortcuts="Meta+K Control+K /"
        className="flex h-8 shrink-0 items-center gap-2 rounded-md border px-2 whitespace-nowrap transition-colors hover:border-[var(--rf-grey)] xl:w-52 xl:px-2.5"
        style={{ borderColor: "var(--rf-stroke)", color: "var(--rf-grey)", backgroundColor: "var(--rf-surface)" }}
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
              style={{ borderColor: "var(--rf-stroke)", backgroundColor: "var(--rf-bg)" }}
            >
              {k}
            </kbd>
          ))}
        </span>
      </button>

      <dialog
        ref={dialog}
        aria-label="Search docs"
        onClick={(e) => e.target === dialog.current && close()}
        onClose={() => {
          setQ("");
          reset();
        }}
        className="docs-search m-0 mx-auto mt-[10vh] w-[calc(100vw-2rem)] max-w-[40rem] overflow-hidden rounded-lg p-0"
        style={{ backgroundColor: "var(--rf-bg)", color: "var(--rf-ink)", outline: "1px solid var(--rf-stroke)", border: "none" }}
      >
        <div className="flex items-center gap-3 border-b px-4" style={{ borderColor: "var(--rf-stroke)" }}>
          <svg viewBox="0 0 24 24" className="size-[17px] shrink-0" fill="none" stroke="var(--rf-grey)" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <circle cx="11" cy="11" r="6.5" />
            <path d="m20 20-4.2-4.2" />
          </svg>
          <input
            ref={input}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              if (ask && ask.status !== "loading" && ask.status !== "streaming") reset();
            }}
            onKeyDown={onInputKey}
            placeholder="Search, or ask a question"
            aria-label="Search the docs"
            aria-controls="docs-search-results"
            autoComplete="off"
            spellCheck={false}
            className="h-13 min-w-0 flex-1 bg-transparent font-geist text-[16px] outline-none placeholder:text-[var(--rf-grey)]"
          />
          <kbd className="rounded-[4px] border px-1.5 font-mono text-[10.5px] leading-[18px]" style={{ borderColor: "var(--rf-stroke)", color: "var(--rf-grey)" }}>
            Esc
          </kbd>
        </div>

        <div className="max-h-[min(28rem,60vh)] overflow-y-auto p-2">
          {!q.trim() ? (
            <>
              <p className="px-3 pt-2 pb-1 font-mono text-[11px] tracking-[0.06em] uppercase" style={{ color: "var(--rf-grey)" }}>
                Start here
              </p>
              <ul ref={list} id="docs-search-results">
                {SUGGEST.map((s, i) => (
                  <li key={s.href}>
                    <button type="button" data-i={i} onMouseMove={() => setSel(i)} onClick={() => go(s.href)} className={row(i)}>
                      <span className="font-geist text-[14.5px]">{s.label}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : ask ? (
            <AskView ask={ask} onNavigate={close} onBack={reset} />
          ) : failed ? (
            <p className="px-3 py-6 text-center font-geist text-[14px]" style={{ color: "var(--rf-grey)" }}>
              Search could not load. Check your connection and try again.
            </p>
          ) : !index ? (
            <p className="px-3 py-6 text-center font-geist text-[14px]" style={{ color: "var(--rf-grey)" }}>
              Loading…
            </p>
          ) : (
            <ul ref={list} id="docs-search-results">
              <li>
                <button type="button" data-i={0} onMouseMove={() => setSel(0)} onClick={() => startAsk(query)} className={`${row(0)} !flex-row items-center gap-3`}>
                  <Spark />
                  <span className="min-w-0 flex-1 truncate font-geist text-[14.5px]">
                    Ask AI <span style={{ color: "var(--rf-grey)" }}>&ldquo;{query}&rdquo;</span>
                  </span>
                  <span className="font-mono text-[10.5px] tracking-[0.04em]" style={{ color: "var(--rf-grey)" }}>
                    ↵
                  </span>
                </button>
              </li>
              {hits.map((h, i) => (
                <li key={h.id}>
                  <button type="button" data-i={i + 1} onMouseMove={() => setSel(i + 1)} onClick={() => go(h.href)} className={row(i + 1)}>
                    <span className="font-mono text-[10.5px] tracking-[0.06em] uppercase" style={{ color: "var(--rf-grey)" }}>
                      {h.section} / {h.page}
                    </span>
                    <span className="font-geist text-[14.5px] font-medium">{h.heading ?? h.page}</span>
                    <Snippet text={h.text} terms={h.terms} />
                  </button>
                </li>
              ))}
              {!hits.length && (
                <li className="px-3 py-5 text-center font-geist text-[14px]" style={{ color: "var(--rf-grey)" }}>
                  No pages match &ldquo;{query}&rdquo;. Ask AI may still find it.
                </li>
              )}
            </ul>
          )}
        </div>

        <div className="flex items-center gap-4 border-t px-4 py-2 font-mono text-[10.5px] tracking-[0.04em]" style={{ borderColor: "var(--rf-stroke)", color: "var(--rf-grey)" }}>
          {ask ? (
            <span>AI answers come from these docs and can still be wrong. Check the sources.</span>
          ) : (
            <>
              <span>↑↓ to move</span>
              <span>↵ to open</span>
              <span className="ml-auto">{hits.length ? `${hits.length} result${hits.length === 1 ? "" : "s"}` : ""}</span>
            </>
          )}
        </div>
      </dialog>
    </>
  );
}

function Spark() {
  return (
    <svg viewBox="0 0 24 24" className="size-[16px] shrink-0" fill="none" stroke="var(--rf-accent)" strokeWidth="1.6" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9L12 3.5Z" />
      <path d="M18.5 16.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8Z" />
    </svg>
  );
}

/** The Ask AI panel: the question, the streamed answer, and its sources. */
function AskView({ ask, onNavigate, onBack }: { ask: Ask; onNavigate: () => void; onBack: () => void }) {
  return (
    <div className="px-3 py-2" aria-live="polite" aria-busy={ask.status === "loading" || ask.status === "streaming"}>
      <div className="flex items-center justify-between gap-3">
        <p className="flex min-w-0 items-center gap-2 font-mono text-[11px] tracking-[0.06em] uppercase" style={{ color: "var(--rf-grey)" }}>
          <Spark />
          <span className="truncate">Ask AI</span>
        </p>
        <button type="button" onClick={onBack} className="rf-link font-mono text-[11px] tracking-[0.04em]" style={{ color: "var(--rf-grey)" }}>
          &larr; Back to results
        </button>
      </div>
      <p className="mt-3 font-geist text-[15px] font-medium" style={{ color: "var(--rf-ink)" }}>
        {ask.question}
      </p>
      <div className="mt-3">
        {ask.status === "error" ? (
          <p className="font-geist text-[14px]" style={{ color: "var(--rf-grey)" }}>
            {ask.error}
          </p>
        ) : ask.text ? (
          <Answer text={ask.text} sources={ask.sources} onNavigate={onNavigate} />
        ) : (
          <p className="docs-thinking font-geist text-[14px]" style={{ color: "var(--rf-grey)" }}>
            Reading the docs…
          </p>
        )}
      </div>
      {ask.sources.length > 0 && ask.status !== "loading" && (
        <div className="mt-4 border-t pt-3" style={{ borderColor: "var(--rf-stroke)" }}>
          <p className="mb-1.5 font-mono text-[11px] tracking-[0.06em] uppercase" style={{ color: "var(--rf-grey)" }}>
            Sources
          </p>
          <ol className="flex flex-col gap-0.5">
            {ask.sources.map((s) => (
              <li key={s.n}>
                <Link href={s.href} onClick={onNavigate} className="group flex items-baseline gap-2 rounded-md px-1 py-1 font-geist text-[13.5px] hover:bg-[var(--rf-wash)]">
                  <span className="font-mono text-[10.5px]" style={{ color: "var(--rf-grey)" }}>
                    {s.n}
                  </span>
                  <span className="truncate" style={{ color: "var(--rf-nickel)" }}>
                    {s.title}
                  </span>
                </Link>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}
