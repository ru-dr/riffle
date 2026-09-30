"use client";

import { useEffect, useState } from "react";

// Back to top, bottom right. Hidden until the reader is a screen down, so
// it never covers the start of a page; smooth unless motion is reduced.
export function ToTop() {
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const onScroll = () => setShown(window.scrollY > window.innerHeight);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const top = () => {
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
    history.replaceState(null, "", location.pathname + location.search);
  };

  return (
    <button
      type="button"
      onClick={top}
      aria-label="Back to top"
      title="Back to top"
      tabIndex={shown ? 0 : -1}
      aria-hidden={!shown}
      className={`fixed right-4 bottom-4 z-40 flex size-10 items-center justify-center rounded-lg border transition-[opacity,transform,border-color] duration-200 hover:border-[var(--rf-grey)] md:right-6 md:bottom-6 ${shown ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-2 opacity-0"}`}
      style={{ borderColor: "var(--rf-stroke)", backgroundColor: "var(--rf-surface)", color: "var(--rf-ink)", boxShadow: "0 4px 16px -6px rgba(0,0,0,0.18)" }}
    >
      <svg viewBox="0 0 24 24" className="size-[17px]" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 19V5M5.5 11.5 12 5l6.5 6.5" />
      </svg>
    </button>
  );
}
