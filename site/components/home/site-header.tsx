import { LockupBlack } from "@/components/brand";
import { NavMenu } from "./nav-menu";
import { REPO } from "./data";

// The site header, shared by the landing page and the docs. Logo and links
// grouped left, the GitHub mark and the phone menu right, inside the bordered
// wrapper. `animated` tags it for the landing page's entrance; elsewhere it
// must not be tagged, or the motion gate would hide it with no timeline to
// bring it back.
export function SiteHeader({
  links,
  animated = false,
  section,
  actions,
}: {
  links: readonly (readonly [string, string])[];
  animated?: boolean;
  /** A small label after the logo, e.g. "Docs". */
  section?: string;
  /** Extra controls before the GitHub mark, e.g. the docs theme switch. */
  actions?: React.ReactNode;
}) {
  return (
    <header
      data-anim={animated ? "nav" : undefined}
      className="v2-wrapper relative flex items-center justify-between px-6 py-5 lg:py-7"
    >
      <div className="flex items-center gap-10">
        <a href="/" aria-label="Riffle home" className="flex items-center gap-3">
          <LockupBlack aria-hidden="true" className="block h-[22px] w-auto" style={{ color: "var(--v2-ink)" }} />
          {section && (
            <span
              className="rounded-[4px] px-1.5 py-0.5 font-mono text-[11px] tracking-[0.04em] uppercase"
              style={{ outline: `1px solid var(--v2-stroke)`, color: "var(--v2-grey)" }}
            >
              {section}
            </span>
          )}
        </a>
        <nav className="hidden md:block">
          <ul className="flex items-center">
            {links.map(([label, href]) => (
              <li key={href} className="inline-block px-5">
                <a
                  href={href}
                  data-scroll-to={href.startsWith("#") ? "" : undefined}
                  className="v2-link font-geist text-[16px] leading-6"
                  style={{ color: "var(--v2-ink)" }}
                >
                  {label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </div>
      <div className="flex items-center gap-2">
        {actions}
        <a href={REPO} aria-label="GitHub" className="v2-link flex size-8 items-center justify-center" style={{ color: "var(--v2-grey)" }}>
          <svg viewBox="0 0 16 16" className="size-[18px]" fill="currentColor" aria-hidden="true">
            <path d="M8 0a8 8 0 0 0-2.53 15.59c.4.07.55-.17.55-.38v-1.34c-2.23.48-2.7-1.07-2.7-1.07-.36-.93-.89-1.18-.89-1.18-.73-.5.05-.49.05-.49.8.06 1.23.83 1.23.83.72 1.23 1.88.87 2.34.67.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.03 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.28.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48v2.19c0 .21.14.46.55.38A8 8 0 0 0 8 0Z" />
          </svg>
        </a>
        <NavMenu links={links} />
      </div>
    </header>
  );
}
