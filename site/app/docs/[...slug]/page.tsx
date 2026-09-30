import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { ALL_PAGES, findPage } from "@/components/docs/registry";
import { loadMarkdown, outline } from "@/components/docs/load";
import { Draft } from "@/components/docs/draft";
import { Markdown } from "@/components/docs/markdown";
import { StatusTag } from "@/components/docs/status";
import { Toc } from "@/components/docs/toc";

// One docs page. Every page in the registry is rendered at build time, and
// anything else 404s rather than rendering on demand.
export const dynamicParams = false;

export function generateStaticParams() {
  return ALL_PAGES.map((p) => ({ slug: p.slug.split("/") }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string[] }> }): Promise<Metadata> {
  const found = findPage((await params).slug.join("/"));
  if (!found) return {};
  return { title: `${found.page.title} — Riffle docs`, description: found.page.description };
}

export default async function DocPage({ params }: { params: Promise<{ slug: string[] }> }) {
  const found = findPage((await params).slug.join("/"));
  if (!found) notFound();
  const { page, prev, next } = found;
  const md = await loadMarkdown(page);
  const headings = outline(md);

  return (
    <>
      <main className="min-w-0 px-6 pt-10 pb-16 md:px-10 lg:px-14 lg:pt-14">
        <article className="mx-auto max-w-[64rem]">
          <p className="flex items-center gap-2 font-mono text-[11px] tracking-[0.08em] uppercase" style={{ color: "var(--rf-grey)" }}>
            <Link href="/docs" className="rf-link">
              Docs
            </Link>
            <span aria-hidden="true">/</span>
            <span>{page.section.title}</span>
          </p>
          <h1 className="mt-5 font-geist text-[2.1rem] leading-[1.1] font-medium tracking-[-0.03em] md:text-[2.6rem]">{page.title}</h1>
          <p className="mt-4 font-geist text-[18px] leading-[1.55]" style={{ color: "var(--rf-nickel)" }}>
            {page.description}
          </p>
          {page.status && (
            <div className="mt-5">
              <StatusTag status={page.status} />
            </div>
          )}
          <div className="mt-10">{"outline" in page ? <Draft outline={page.outline} /> : <Markdown source={md} />}</div>
        </article>

        {/* Pager: the previous and next pages, as two rounded buttons. */}
        <nav aria-label="Pager" className="mx-auto mt-16 grid max-w-[64rem] gap-3 sm:grid-cols-2">
          {[prev, next].map((p, i) =>
            p ? (
              <Link
                key={p.slug}
                href={`/docs/${p.slug}`}
                className={`group flex flex-col gap-1 rounded-lg border px-5 py-4 transition-colors hover:border-[var(--rf-grey)] hover:bg-[var(--rf-surface)] ${i === 1 ? "sm:col-start-2 sm:items-end sm:text-right" : ""}`}
                style={{ borderColor: "var(--rf-stroke)" }}
              >
                <span className="font-mono text-[11px] tracking-[0.06em] uppercase" style={{ color: "var(--rf-grey)" }}>
                  {i === 0 ? "\u2190 Previous" : "Next \u2192"}
                </span>
                <span className="font-geist text-[15px] font-medium">{p.title}</span>
              </Link>
            ) : null,
          )}
        </nav>
      </main>
      <aside className="hidden border-l xl:block" style={{ borderColor: "var(--rf-stroke)" }}>
        <Toc headings={headings} />
      </aside>
    </>
  );
}
