"use client";

import { useEffect, useState } from "react";

// Phone navigation. Below md the inline links are hidden, and until now
// nothing replaced them: a phone reader had no way to the sections except
// scrolling. A two-bar button, as the reference uses, opening a panel with
// the same links. It closes on a link tap and on Escape.
export function NavMenu({ links, until = "md" }: { links: readonly (readonly [string, string])[]; until?: "md" | "lg" }) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className={until === "lg" ? "lg:hidden" : "md:hidden"}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls="rf-mobile-menu"
        aria-label={open ? "Close navigation menu" : "Open navigation menu"}
        onClick={() => setOpen((o) => !o)}
        className="-mr-2 flex size-10 items-center justify-center"
      >
        {/* Square box so the close state is a true X; the 18x8 hamburger box
            flattened it into a "><". */}
        <svg viewBox="0 0 18 18" className="size-[18px]" aria-hidden="true" stroke={"var(--rf-ink)"} strokeWidth="1.5">
          {open ? (
            <>
              <path d="M3.5 3.5l11 11" />
              <path d="M14.5 3.5l-11 11" />
            </>
          ) : (
            <>
              <path d="M0 6h18" />
              <path d="M0 12h18" />
            </>
          )}
        </svg>
      </button>
      <nav
        id="rf-mobile-menu"
        className="absolute inset-x-0 top-full z-50 border-t border-b px-6 py-4"
        style={{
          backgroundColor: "var(--rf-bg)",
          borderColor: "var(--rf-stroke)",
          visibility: open ? "visible" : "hidden",
          opacity: open ? 1 : 0,
          transform: `translateY(${open ? 0 : -6}px)`,
          transition: "opacity 180ms ease, transform 180ms ease, visibility 180ms",
        }}
      >
        <ul className="flex flex-col">
          {links.map(([label, href]) => (
            <li key={href} className="border-b last:border-b-0" style={{ borderColor: "var(--rf-stroke)" }}>
              <a
                href={href}
                data-scroll-to={href.startsWith("#") ? "" : undefined}
                onClick={() => setOpen(false)}
                className="block py-3.5 font-geist text-[17px]"
                style={{ color: "var(--rf-ink)" }}
              >
                {label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}
