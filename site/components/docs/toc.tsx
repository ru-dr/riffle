"use client";

import { useEffect, useState } from "react";
import type { Heading } from "./load";

// "On this page". The current heading is the last one whose top has passed
// a line a quarter of the way down the viewport - so the highlight moves when
// a section starts to be read, not when its heading scrolls off the top.
export function Toc({ headings }: { headings: Heading[] }) {
  const [active, setActive] = useState(headings[0]?.id ?? "");

  useEffect(() => {
    if (!headings.length) return;
    const els = headings.map((h) => document.getElementById(h.id)).filter(Boolean) as HTMLElement[];
    const onScroll = () => {
      const line = window.innerHeight * 0.25;
      let current = els[0]?.id ?? "";
      for (const el of els) if (el.getBoundingClientRect().top <= line) current = el.id;
      setActive(current);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [headings]);

  if (!headings.length) return null;
  return (
    <nav aria-label="On this page" className="v2-noscrollbar sticky top-0 max-h-svh overflow-y-auto py-10 pr-6 pl-6">
      <p className="mb-3 font-mono text-[11px] tracking-[0.08em] uppercase" style={{ color: "var(--v2-grey)" }}>
        On this page
      </p>
      <ul className="flex flex-col gap-2 border-l" style={{ borderColor: "var(--v2-stroke)" }}>
        {headings.map((h) => (
          <li key={h.id}>
            <a
              href={`#${h.id}`}
              className="-ml-px block border-l font-geist text-[13px] leading-[1.4] transition-colors"
              style={{
                paddingLeft: h.depth === 3 ? "1.75rem" : "1rem",
                borderColor: active === h.id ? "var(--v2-ink)" : "transparent",
                color: active === h.id ? "var(--v2-ink)" : "var(--v2-grey)",
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
