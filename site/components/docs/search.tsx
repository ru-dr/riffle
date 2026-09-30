"use client";

import MiniSearch, { type SearchResult } from "minisearch";
import { useRouter } from "next/navigation";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { SearchRecord } from "./search-index";

// Docs search. The trigger sits in the header; ⌘K, Ctrl+K or "/" opens it
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
  let res: SearchResult[] = ms.search(q, { combineWith: "AND" });
  if (!res.length) res = ms.search(q, { combineWith: "OR" });
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
  const escaped = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).filter(Boolean);
  const parts = escaped.length ? slice.split(new RegExp(`(${escaped.join("|")})`, "gi")) : [slice];
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

  const open = useCallback(() => {
    const d = dialog.current;
    if (!d || d.open) return;
    d.showModal();
    input.current?.select();
    loadIndex().then(setIndex, () => setFailed(true));
  }, []);
  const close = useCallback(() => dialog.current?.close(), []);

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

  const onInputKey = (e: React.KeyboardEvent) => {
    const n = q.trim() ? hits.length : SUGGEST.length;
    if (!n) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel((s) => (s + 1) % n);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel((s) => (s - 1 + n) % n);
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(q.trim() ? hits[sel].href : SUGGEST[sel].href);
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
        className="flex h-8 items-center gap-2 rounded-md border px-2 transition-colors hover:border-[var(--rf-grey)] sm:w-52 sm:px-2.5"
        style={{ borderColor: "var(--rf-stroke)", color: "var(--rf-grey)", backgroundColor: "var(--rf-surface)" }}
      >
        <svg viewBox="0 0 24 24" className="size-[15px] shrink-0" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
          <circle cx="11" cy="11" r="6.5" />
          <path d="m20 20-4.2-4.2" />
        </svg>
        <span className="hidden font-geist text-[13px] sm:inline">Search docs</span>
        <kbd className="ml-auto hidden rounded-[4px] border px-1.5 font-mono text-[10.5px] leading-[18px] sm:inline" style={{ borderColor: "var(--rf-stroke)" }}>
          {mac ? "⌘K" : "Ctrl K"}
        </kbd>
      </button>

      <dialog
        ref={dialog}
        aria-label="Search docs"
        onClick={(e) => e.target === dialog.current && close()}
        onClose={() => setQ("")}
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
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onInputKey}
            placeholder="Search the docs"
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
          ) : failed ? (
            <p className="px-3 py-6 text-center font-geist text-[14px]" style={{ color: "var(--rf-grey)" }}>
              Search could not load. Check your connection and try again.
            </p>
          ) : !index ? (
            <p className="px-3 py-6 text-center font-geist text-[14px]" style={{ color: "var(--rf-grey)" }}>
              Loading…
            </p>
          ) : hits.length ? (
            <ul ref={list} id="docs-search-results">
              {hits.map((h, i) => (
                <li key={h.id}>
                  <button type="button" data-i={i} onMouseMove={() => setSel(i)} onClick={() => go(h.href)} className={row(i)}>
                    <span className="font-mono text-[10.5px] tracking-[0.06em] uppercase" style={{ color: "var(--rf-grey)" }}>
                      {h.section} / {h.page}
                    </span>
                    <span className="font-geist text-[14.5px] font-medium">{h.heading ?? h.page}</span>
                    <Snippet text={h.text} terms={h.terms} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-3 py-6 text-center font-geist text-[14px]" style={{ color: "var(--rf-grey)" }}>
              No results for &ldquo;{q.trim()}&rdquo;
            </p>
          )}
        </div>

        <div className="flex items-center gap-4 border-t px-4 py-2 font-mono text-[10.5px] tracking-[0.04em]" style={{ borderColor: "var(--rf-stroke)", color: "var(--rf-grey)" }}>
          <span>↑↓ to move</span>
          <span>↵ to open</span>
          <span className="ml-auto">{hits.length ? `${hits.length} result${hits.length === 1 ? "" : "s"}` : ""}</span>
        </div>
      </dialog>
    </>
  );
}
