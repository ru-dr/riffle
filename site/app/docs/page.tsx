import type { Metadata } from "next";
import Link from "next/link";
import { T } from "@/components/home/tokens";
import { SECTIONS } from "@/components/docs/registry";
import { StatusTag } from "@/components/docs/status";

export const metadata: Metadata = {
  title: "Docs — Riffle",
  description: "How Riffle ranks a pull request queue by risk: concepts, configuration, architecture and operations.",
};

// The docs landing: every section gets its own cell with its blurb and its
// pages, so the whole documentation is visible - and reachable - from here.
const START = [
  ["Introduction", "/docs/getting-started/introduction", "What Riffle does and what it guarantees"],
  ["How it works", "/docs/getting-started/how-it-works", "One pull request, end to end"],
  ["Configuration reference", "/docs/configuration/reference", "Every key, value and default"],
] as const;

export default function DocsHome() {
  return (
    <main className="min-w-0 xl:col-span-2">
      <div className="px-6 pt-12 pb-10 md:px-10 lg:pt-16">
        <p className="font-mono text-[11px] tracking-[0.08em] uppercase" style={{ color: "var(--v2-grey)" }}>
          Documentation
        </p>
        <h1 className="mt-5 font-geist text-[2.25rem] leading-[1.08] font-medium tracking-[-0.035em] md:text-[3rem]">
          Riffle docs
        </h1>
        <p className="mt-5 max-w-[38rem] font-geist text-[17px] leading-[1.6]" style={{ color: "var(--v2-nickel)" }}>
          Riffle orders a pull request queue by risk, learned from each
          repository&rsquo;s own history. These pages cover the concepts it rests
          on, how to configure it, how its services fit together, and how to
          run it yourself.
        </p>
      </div>

      {/* Start here: three entry points in a hairline row. */}
      <div className="grid border-t sm:grid-cols-3 sm:divide-x" style={{ borderColor: "var(--v2-stroke)" }}>
        {START.map(([title, href, note]) => (
          <Link key={href} href={href} className="group flex flex-col gap-1.5 px-6 py-6 transition-colors hover:bg-[var(--v2-surface)] md:px-10" style={{ borderColor: "var(--v2-stroke)" }}>
            <span className="font-mono text-[11px] tracking-[0.06em] uppercase" style={{ color: "var(--v2-grey)" }}>
              Start here
            </span>
            <span className="flex items-center gap-2 font-geist text-[16px] font-medium">
              {title}
              <span aria-hidden="true" className="transition-transform group-hover:translate-x-0.5" style={{ color: "var(--v2-grey)" }}>
                &rarr;
              </span>
            </span>
            <span className="font-geist text-[14px]" style={{ color: "var(--v2-nickel)" }}>
              {note}
            </span>
          </Link>
        ))}
      </div>

      {/* Every section, with its pages. */}
      <div className="grid gap-px border-t md:grid-cols-2 xl:grid-cols-3" style={{ borderColor: "var(--v2-stroke)", backgroundColor: "var(--v2-stroke)" }}>
        {SECTIONS.map((s, i) => (
          <section key={s.id} className="flex flex-col gap-5 px-6 py-8 md:px-10" style={{ backgroundColor: "var(--v2-bg)" }}>
            <div className="flex items-baseline justify-between gap-4">
              <span className="font-mono text-[11px] tracking-[0.06em]" style={{ color: "var(--v2-grey)" }}>
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="font-mono text-[11px] tracking-[0.04em]" style={{ color: "var(--v2-grey)" }}>
                {s.pages.length} {s.pages.length === 1 ? "page" : "pages"}
              </span>
            </div>
            <div>
              <h2 className="font-geist text-[1.35rem] leading-[1.2] font-medium tracking-[-0.02em]">{s.title}</h2>
              <p className="mt-2 font-geist text-[14px] leading-[1.55]" style={{ color: "var(--v2-nickel)" }}>
                {s.blurb}
              </p>
            </div>
            <ul className="mt-auto flex flex-col border-t" style={{ borderColor: "var(--v2-stroke)" }}>
              {s.pages.map((p) => (
                <li key={p.slug} className="border-b" style={{ borderColor: "var(--v2-stroke)" }}>
                  <Link href={`/docs/${p.slug}`} className="group flex items-center justify-between gap-3 py-2.5">
                    <span className="font-geist text-[14.5px] transition-colors group-hover:underline group-hover:decoration-[var(--v2-grey)] group-hover:underline-offset-4">
                      {p.title}
                    </span>
                    {p.status && <StatusTag status={p.status} />}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </main>
  );
}
