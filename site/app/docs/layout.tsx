import type { Viewport } from "next";
import { SiteHeader } from "@/components/home/site-header";
import { MobileSections, Sidebar } from "@/components/docs/sidebar";
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
    <div data-riffle-light data-docs className="min-h-dvh" style={{ backgroundColor: "var(--v2-bg)", color: "var(--v2-ink)" }}>
      <SiteHeader links={DOCS_NAV} section="Docs" actions={<ThemeSwitch />} />
      <div
        className="v2-wrapper v2-ticks grid border-t lg:grid-cols-[16rem_minmax(0,1fr)] xl:grid-cols-[16rem_minmax(0,1fr)_15rem]"
        style={{ borderColor: "var(--v2-stroke)" }}
      >
        <aside className="hidden border-r lg:block" style={{ borderColor: "var(--v2-stroke)" }}>
          <Sidebar />
        </aside>
        <div className="contents">
          <div className="lg:hidden">
            <MobileSections />
          </div>
          {children}
        </div>
      </div>
      <footer className="v2-wrapper v2-ticks flex flex-wrap items-center justify-between gap-3 border-t px-6 py-6 md:px-10" style={{ borderColor: "var(--v2-stroke)" }}>
        <p className="font-geist text-[13px]" style={{ color: "var(--v2-grey)" }}>
          &copy; 2026 Riffle contributors. Apache-2.0.
        </p>
        <p className="font-mono text-[11px] tracking-[0.04em]" style={{ color: "var(--v2-grey)" }}>
          Riffle is in development. These docs track the settled design.
        </p>
      </footer>
    </div>
  );
}
