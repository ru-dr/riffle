"use client";

import { useEffect, useRef, useState } from "react";
import type { Heading } from "./load";

// "On this page". The current heading is the last one whose top has passed
// a line a quarter of the way down the viewport - so the highlight moves when
// a section starts to be read, not when its heading scrolls off the top.
//
// Short sections at the end of a page never reach that line, so two rules
// sit on top of it: a heading the reader picks (a click, or a #hash on
// arrival) stays highlighted until they scroll by hand, and at the bottom
// of the page the last heading on screen wins.
export function Toc({ headings }: { headings: Heading[] }) {
  const [active, setActive] = useState(headings[0]?.id ?? "");
  const pinned = useRef<string | null>(null);

  useEffect(() => {
    if (!headings.length) return;
    const els = headings.map((h) => document.getElementById(h.id)).filter(Boolean) as HTMLElement[];
    const ids = new Set(els.map((e) => e.id));

    const onScroll = () => {
      if (pinned.current) return;
      const line = window.innerHeight * 0.25;
      let current = els[0]?.id ?? "";
      for (const el of els) if (el.getBoundingClientRect().top <= line) current = el.id;
      const bottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
      if (bottom) for (const el of els) if (el.getBoundingClientRect().top < window.innerHeight) current = el.id;
      setActive(current);
    };
    const pin = (id: string) => {
      if (!ids.has(id)) return;
      pinned.current = id;
      setActive(id);
    };
    const fromHash = () => pin(decodeURIComponent(location.hash.slice(1)));
    // Only the reader's own scrolling releases a pin; the jump itself does not.
    const release = () => {
      if (!pinned.current) return;
      pinned.current = null;
      onScroll();
    };
    const onKey = (e: KeyboardEvent) => ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(e.key) && release();

    fromHash();
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("hashchange", fromHash);
    window.addEventListener("wheel", release, { passive: true });
    window.addEventListener("touchmove", release, { passive: true });
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("hashchange", fromHash);
      window.removeEventListener("wheel", release);
      window.removeEventListener("touchmove", release);
      window.removeEventListener("keydown", onKey);
    };
  }, [headings]);

  const pick = (id: string) => {
    pinned.current = id;
    setActive(id);
  };

  if (!headings.length) return null;
  return (
    <nav aria-label="On this page" className="rf-noscrollbar sticky top-0 max-h-svh overflow-y-auto py-10 pr-6 pl-6">
      <p className="mb-3 flex items-center gap-2 font-mono text-[11px] tracking-[0.08em] uppercase" style={{ color: "var(--rf-grey)" }}>
        <svg viewBox="0 0 16 16" className="size-[13px]" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden="true">
          <path d="M6 3.5h8M6 8h8M6 12.5h8" />
          <circle cx="2.5" cy="3.5" r=".9" fill="currentColor" stroke="none" />
          <circle cx="2.5" cy="8" r=".9" fill="currentColor" stroke="none" />
          <circle cx="2.5" cy="12.5" r=".9" fill="currentColor" stroke="none" />
        </svg>
        On this page
      </p>
      {/* No rail: level carries through indentation alone, and long titles
          truncate to one line so the outline keeps its rhythm. */}
      <ul className="flex flex-col">
        {headings.map((h) => (
          <li key={h.id}>
            <a
              href={`#${h.id}`}
              onClick={() => pick(h.id)}
              aria-current={active === h.id ? "location" : undefined}
              title={h.text}
              className="block truncate py-[5px] font-geist text-[13.5px] leading-[1.35] transition-colors hover:text-[var(--rf-ink)]"
              style={{
                paddingLeft: h.depth === 3 ? "1rem" : 0,
                color: active === h.id ? "var(--rf-ink)" : "var(--rf-grey)",
                fontWeight: active === h.id ? 500 : 400,
              }}
            >
              {h.text}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
