"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { SECTIONS } from "./registry";

// Section navigation. Each section is a numbered group; the current page is
// a white pill with a hairline outline - the site's button language - rather
// than a rule on a tree line. Desktop: a sticky column. Phones: the same list
// behind a "Sections" toggle, closed by default.
function Item({ href, label, active, onNavigate }: { href: string; label: string; active: boolean; onNavigate?: () => void }) {
  return (
    <Link
      href={href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className="block rounded-md px-3 py-[7px] font-geist text-[14px] leading-[1.35] transition-colors hover:bg-[var(--v2-wash)]"
      style={
        active
          ? { backgroundColor: "var(--v2-surface)", outline: "1px solid var(--v2-stroke)", color: "var(--v2-ink)", fontWeight: 500 }
          : { color: "var(--v2-nickel)" }
      }
    >
      {label}
    </Link>
  );
}

function Tree({ onNavigate }: { onNavigate?: () => void }) {
  const path = usePathname();
  return (
    <nav aria-label="Docs sections" className="flex flex-col gap-6">
      <div className="-mx-3">
        <Item href="/docs" label="Overview" active={path === "/docs"} onNavigate={onNavigate} />
      </div>
      {SECTIONS.map((s, i) => (
        <div key={s.id}>
          <p className="mb-1.5 flex items-baseline gap-2 font-geist text-[13px] font-medium" style={{ color: "var(--v2-ink)" }}>
            <span className="font-mono text-[10.5px] font-normal" style={{ color: "var(--v2-grey)" }}>
              {String(i + 1).padStart(2, "0")}
            </span>
            {s.title}
          </p>
          <ul className="-mx-3 flex flex-col gap-px">
            {s.pages.map((p) => {
              const href = `/docs/${p.slug}`;
              return (
                <li key={p.slug}>
                  <Item href={href} label={p.title} active={path === href} onNavigate={onNavigate} />
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export function Sidebar() {
  return (
    <div className="v2-noscrollbar sticky top-0 max-h-svh overflow-y-auto px-7 py-10">
      <Tree />
    </div>
  );
}

export function MobileSections() {
  const [open, setOpen] = useState(false);
  return (
    <div className="border-b lg:hidden" style={{ borderColor: "var(--v2-stroke)" }}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between px-6 py-3.5 font-mono text-[12px] tracking-[0.06em] uppercase"
        style={{ color: "var(--v2-nickel)" }}
      >
        Sections
        <span aria-hidden="true" style={{ transform: `rotate(${open ? 180 : 0}deg)`, transition: "transform 200ms ease" }}>
          &#9662;
        </span>
      </button>
      {open && (
        <div className="px-6 pb-6">
          <Tree onNavigate={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}
