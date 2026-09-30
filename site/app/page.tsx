import type { Viewport } from "next";
import { V2Motion } from "@/components/v2-motion";
import { T } from "@/components/home/tokens";
import { SiteHeader } from "@/components/home/site-header";
import { Stack } from "@/components/home/stack";
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
} from "@/components/home/sections";

// Riffle's landing page, on voidzero.dev's layout system, rebuilt from their
// markup. The earlier poster version lives on at /v1.
//
// Structure matches theirs element for element where Riffle has something
// true to put in the slot: every section is its own bordered .wrapper (the
// vertical hairlines are those borders), empty ruled spacers separate the
// blocks, and the dark product block and dark footer bracket the light ones.
//
// Slots with no honest equivalent are substituted, never faked: dataset
// provenance where their customer logos sit, prior work where their
// investors sit, the repository where their newsletter sits.

// Title, description and social card come from the root layout. This page
// only sets the browser chrome colour to its own light surface; the layout's
// default is the dark poster page's.
export const viewport: Viewport = { themeColor: "#fbfaf7" };

const NAV = [
  ["Architecture", "#architecture"],
  ["Invariants", "#invariants"],
  ["Contracts", "#contracts"],
  ["Docs", "/docs"],
] as const;

export default function Page() {
  return (
    <div data-riffle-light style={{ backgroundColor: T.beige, color: T.ink }}>
      {/* Their header: logo and links grouped left with a 2.5rem gap, icon
          links right. Part of the wrapper, not a sticky bar — it scrolls away
          with the page. Links are sans at 16px in full ink, not mono grey. */}
      <SiteHeader links={NAV} animated />

      {/* Hero: chip, headline, one line of lede, then the diagram. Their hero
          has no buttons, so neither does this one — the CTA lives in the
          dark blocks and the footer. */}
      <div className="v2-wrapper v2-hero flex flex-col items-center pt-6 pb-10">
        <div className="flex w-full flex-col items-center px-5 sm:w-[46rem] sm:px-0">
          <div
            data-anim="chip"
            className="v2-chip v2-hero-chip flex items-center gap-1.5 rounded px-3 py-1 font-mono text-[13px] font-medium tracking-[-0.03em]"
          >
            <span className="inline-flex rounded-sm p-0.5" style={{ backgroundColor: T.accentSoft }}>
              <span
                aria-hidden="true"
                className="v2-pulse block size-1.5 rounded-[1.1px]"
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
            className="v2-shine v2-hero-title px-[0.08em] pb-1 text-center font-geist text-[2.25rem] leading-[2.6rem] font-medium tracking-[-0.05em] text-balance"
          >
            Every repo
            <br />
            breaks differently
          </h1>

          <p
            data-anim="lede"
            className="v2-hero-lede self-stretch text-center font-geist text-[16px] leading-[1.6] text-balance"
            style={{ color: T.nickel }}
          >
            Riffle ranks your pull request queue by risk, trained on your
            repository&rsquo;s own revert history.
          </p>
        </div>

        <div data-anim="stackin" className="v2-hero-stack w-full px-5">
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
      <ResourcesGrid />
      <Spacer />
      <ClosingBlock />

      <V2Motion />
    </div>
  );
}
