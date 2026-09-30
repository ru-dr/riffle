import type { Viewport } from "next";
import { SiteHeader } from "@/components/home/site-header";
import { MobileSections, Sidebar } from "@/components/docs/sidebar";
import { DocsSearch } from "@/components/docs/search";
import { ThemeSwitch } from "@/components/docs/theme-switch";

// The docs frame: the site header, then one bordered wrapper split by
// hairlines into section nav | article | "On this page". The article and its
// outline are supplied by each page, so the layout only owns the sidebar.

export const viewport: Viewport = { themeColor: "#fbfaf7" };

const DOCS_NAV = [
  ["Home", "/"],
  ["Concepts", "/docs/concepts/rank-bands"],
  ["Configuration", "/docs/configuration/overview"],
  ["Architecture", "/docs/architecture/services"],
] as const;

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div data-riffle-light data-docs className="min-h-dvh" style={{ backgroundColor: "var(--rf-bg)", color: "var(--rf-ink)" }}>
      <SiteHeader links={DOCS_NAV} section="Docs" navFrom="lg" actions={
          <>
            <DocsSearch />
            <ThemeSwitch />
          </>
        } />
      <div
        className="rf-wrapper rf-ticks grid border-t lg:grid-cols-[16rem_minmax(0,1fr)] xl:grid-cols-[16rem_minmax(0,1fr)_15rem]"
        style={{ borderColor: "var(--rf-stroke)" }}
      >
        <aside className="hidden border-r lg:block" style={{ borderColor: "var(--rf-stroke)" }}>
          <Sidebar />
        </aside>
        <div className="contents">
          <div className="lg:hidden">
            <MobileSections />
          </div>
          {children}
        </div>
      </div>
      <footer className="rf-wrapper rf-ticks flex flex-wrap items-center justify-between gap-3 border-t px-6 py-6 md:px-10" style={{ borderColor: "var(--rf-stroke)" }}>
        <p className="font-geist text-[13px]" style={{ color: "var(--rf-grey)" }}>
          &copy; 2026 Riffle contributors. Apache-2.0.
        </p>
        <p className="font-mono text-[11px] tracking-[0.04em]" style={{ color: "var(--rf-grey)" }}>
          Riffle is in development. These docs track the settled design.
        </p>
      </footer>
    </div>
  );
}
