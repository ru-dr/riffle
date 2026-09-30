import type { Metadata } from "next";
import { LockupBlack } from "@/components/brand";
import { V2Motion } from "@/components/v2-motion";
import { T } from "./tokens";
import { REPO } from "./data";
import { Stack } from "./stack";
import {
  ClosingBlock,
  ContractsSection,
  InvariantsSection,
  MissionPanel,
  Provenance,
  ResourcesGrid,
  ServicesBlock,
  Spacer,
  StatsBand,
} from "./sections";

// Riffle on voidzero.dev's layout system, rebuilt from their markup.
//
// Structure matches theirs element for element where Riffle has something
// true to put in the slot: every section is its own bordered .wrapper (the
// vertical hairlines are those borders), empty ruled spacers separate the
// blocks, and the dark product block and dark footer bracket the light ones.
//
// Slots with no honest equivalent are substituted, never faked: dataset
// provenance where their customer logos sit, prior work where their
// investors sit, the repository where their newsletter sits.

export const metadata: Metadata = {
  title: "Riffle — review what matters first (v2)",
  robots: { index: false, follow: false },
};

const NAV = [
  ["Architecture", "#architecture"],
  ["Invariants", "#invariants"],
  ["Contracts", "#contracts"],
  ["Docs", `${REPO}#readme`],
] as const;

export default function Page() {
  return (
    <div data-riffle-light style={{ backgroundColor: T.beige, color: T.ink }}>
      {/* Their header: logo and links grouped left with a 2.5rem gap, icon
          links right. Part of the wrapper, not a sticky bar — it scrolls away
          with the page. Links are sans at 16px in full ink, not mono grey. */}
      <header className="v2-wrapper flex items-center justify-between px-6 py-5 lg:py-7">
        <div className="flex items-center gap-10">
          <a href="/v2" aria-label="Riffle home">
            <LockupBlack aria-hidden="true" className="block h-[22px] w-auto" style={{ color: T.ink }} />
          </a>
          <nav className="hidden md:block">
            <ul className="flex items-center">
              {NAV.map(([label, href]) => (
                <li key={href} className="inline-block px-5">
                  <a
                    href={href}
                    data-scroll-to={href.startsWith("#") ? "" : undefined}
                    className="font-geist text-[16px] leading-6"
                    style={{ color: T.ink }}
                  >
                    {label}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        </div>
        <div className="flex items-center gap-1">
          <a
            href={REPO}
            aria-label="GitHub"
            className="v2-link flex size-8 items-center justify-center"
            style={{ color: T.grey }}
          >
            <svg viewBox="0 0 16 16" className="size-[18px]" fill="currentColor" aria-hidden="true">
              <path d="M8 0a8 8 0 0 0-2.53 15.59c.4.07.55-.17.55-.38v-1.34c-2.23.48-2.7-1.07-2.7-1.07-.36-.93-.89-1.18-.89-1.18-.73-.5.05-.49.05-.49.8.06 1.23.83 1.23.83.72 1.23 1.88.87 2.34.67.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.03 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.28.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48v2.19c0 .21.14.46.55.38A8 8 0 0 0 8 0Z" />
            </svg>
          </a>
        </div>
      </header>

      {/* Hero: chip, headline, one line of lede, then the diagram. Their hero
          has no buttons, so neither does this one — the CTA lives in the
          dark blocks and the footer. */}
      <div className="v2-wrapper flex flex-col items-center gap-6 pt-6 pb-10 md:pt-20">
        <div className="flex w-full flex-col items-center gap-4 px-5 sm:w-[42rem] sm:px-0">
          <div
            data-anim="chip"
            className="v2-chip mb-3 flex items-center gap-1.5 rounded px-3 py-1 font-mono text-[14px] font-medium tracking-[-0.03em]"
          >
            <span className="inline-flex rounded-sm p-0.5" style={{ backgroundColor: T.accentSoft }}>
              <span
                aria-hidden="true"
                data-anim="pulse"
                className="block size-1.5 rounded-[1.1px]"
                style={{ backgroundColor: T.live }}
              />
            </span>
            <span style={{ color: T.grey }}>main</span>
            <span style={{ color: T.grey }}>/</span>
            <span style={{ color: T.nickel }}>in development</span>
          </div>

          {/* One element, so the shine clips across both lines at once and
              the entrance can move it without breaking the clip. */}
          <h1
            data-anim="title"
            className="v2-shine pb-1 text-center font-geist text-[2.25rem] leading-[2.6rem] font-medium tracking-[-0.05em] text-balance md:text-[3rem] md:leading-[3.4rem] lg:text-[3.75rem] lg:leading-[4.2rem]"
          >
            Every repo breaks differently
          </h1>

          <p
            data-anim="lede"
            className="self-stretch text-center font-geist text-[16px] leading-[1.6] text-balance md:text-[17px]"
            style={{ color: T.nickel }}
          >
            Riffle ranks your pull request queue by risk, trained on your
            repository&rsquo;s own revert history.
          </p>
        </div>

        <div className="w-full px-5 pt-6 md:pt-0">
          <Stack />
        </div>
      </div>

      <Provenance />
      <ServicesBlock />
      <StatsBand />
      <Spacer />
      <MissionPanel />
      <Spacer />
      <InvariantsSection />
      <ContractsSection />
      <Spacer />
      <ResourcesGrid />
      <Spacer />
      <ClosingBlock />

      <V2Motion />
    </div>
  );
}
