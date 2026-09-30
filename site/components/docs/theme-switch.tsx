"use client";

import { useEffect, useState } from "react";

// Light / dark switch for the docs. Until the reader chooses, the docs follow
// the OS (the CSS handles that with prefers-color-scheme). A choice is stored
// and applied as data-theme on the docs root, which the CSS gives priority.
const KEY = "riffle-docs-theme";
type Theme = "light" | "dark";

function current(): Theme {
  const set = document.querySelector<HTMLElement>("[data-docs]")?.dataset.theme as Theme | undefined;
  if (set) return set;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeSwitch() {
  const [theme, setTheme] = useState<Theme | null>(null);

  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(KEY);
    } catch {}
    const root = document.querySelector<HTMLElement>("[data-docs]");
    if (root && (saved === "light" || saved === "dark")) root.dataset.theme = saved;
    setTheme(current());
  }, []);

  const toggle = () => {
    const next: Theme = current() === "dark" ? "light" : "dark";
    const root = document.querySelector<HTMLElement>("[data-docs]");
    if (root) root.dataset.theme = next;
    try {
      localStorage.setItem(KEY, next);
    } catch {}
    setTheme(next);
  };

  const dark = theme === "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      title={dark ? "Light theme" : "Dark theme"}
      className="rf-link flex size-8 items-center justify-center rounded-md"
      style={{ color: "var(--rf-grey)" }}
    >
      <svg viewBox="0 0 24 24" className="size-[17px]" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
        {dark ? (
          <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </>
        ) : (
          <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
        )}
      </svg>
    </button>
  );
}
