import type { Metadata } from "next";
import { LockupBlack } from "@/components/brand";
import { V2Motion } from "@/components/v2-motion";
import { Stack } from "./stack";
import { T } from "./tokens";
import {
  BANDS,
  DATA,
  EVIDENCE,
  INVARIANTS,
  METRICS,
  PR_EVENT,
  REPO,
  SCORE_RESULT,
  SERVICES,
} from "./data";

// Riffle, built on voidzero.dev's layout system.
//
// What that system actually is, once you strip the content: a beige field, a
// centred column with visible vertical hairlines, one near-black ink and one
// grey, section headers set as code comments, statistics as oversized
// numerals that count up, and corner brackets used as punctuation. Sections
// stack; each is separated by a single rule rather than by whitespace.
//
// Two deliberate departures. Their typefaces are commercial, so this uses
// what the repo licenses. And their customer-logo carousel, star counts and
// investor row have no truthful equivalent here — Riffle has no users, the
// repository is private, and there is no funding — so those sections are
// absent rather than filled with invented proof.

export const metadata: Metadata = {
  title: "Riffle — review what matters first (v2)",
  robots: { index: false, follow: false },
};

const NAV = [
  ["Problem", "#problem"],
  ["Architecture", "#architecture"],
  ["Invariants", "#invariants"],
  ["Contracts", "#contracts"],
] as const;

/* Corner bracket, their signature piece of punctuation. Drawn rather than
   typed so the stroke matches the hairlines instead of the font's weight. */
function Bracket({ side }: { side: "left" | "right" }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 8 30"
      className="h-[24px] w-[6.4px] shrink-0"
      fill={T.stroke}
      style={{ transform: side === "right" ? "scaleX(-1)" : undefined }}
    >
      {/* Their exact bracket outline: a bowed parenthesis, so it reads as
          punctuation rather than a UI corner. */}
      <path d="M4.652 30C1.48 25.427 0 20.439 0 15.035 0 9.596 1.48 4.538 4.652 0H8c-2.573 4.988-3.7 10.046-3.7 15.035 0 4.988 1.092 10.011 3.7 14.965H4.652Z" />
    </svg>
  );
}

/* Section header set as a code comment — the device that makes their page
   read as documentation rather than marketing. */
function SectionLabel({ children, id }: { children: string; id?: string }) {
  return (
    <div
      id={id}
      data-anim="rise"
      className="flex items-baseline gap-2 font-mono text-[11.5px] tracking-[0.04em]"
    >
      <span style={{ color: T.grey }}>//</span>
      <span style={{ color: T.nickel }}>{children}</span>
    </div>
  );
}

function Section({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`v2-ticks border-t px-5 py-14 sm:px-8 sm:py-20 lg:px-12 lg:py-24 ${className}`}
      style={{ borderColor: T.stroke }}
    >
      <div className="mx-auto w-full max-w-[74rem]">{children}</div>
    </section>
  );
}

export default function Page() {
  return (
    <div data-riffle-light style={{ backgroundColor: T.beige, color: T.ink }}>
      <div className="v2-wrapper">
      {/* Sticky rail: mark, anchors, live status. The status chip is the one
          place on their page where monospace is doing signalling work rather
          than labelling, so it keeps the brackets. */}
      <header
        className="sticky top-0 z-50 border-b backdrop-blur-md"
        style={{ borderColor: T.stroke, backgroundColor: "rgba(244,243,236,0.86)" }}
      >
        <div className="mx-auto flex w-full max-w-[74rem] items-center justify-between px-5 py-3.5 sm:px-8 lg:px-12">
          <LockupBlack
            aria-hidden="true"
            className="h-auto w-[84px] shrink-0"
            style={{ color: T.ink }}
          />

          <nav className="hidden items-center gap-7 md:flex">
            {NAV.map(([label, href]) => (
              <a
                key={href}
                href={href}
                className="v2-navlink font-mono text-[12px] tracking-[0.01em]"
                style={{ color: T.grey }}
              >
                {label}
              </a>
            ))}
          </nav>

          {/* Icon links, the way their rail ends. The status chip belongs to
              the hero — carrying it in both places said the same thing twice,
              and the CTA lives in the hero too. */}
          <div className="flex items-center gap-4">
            <a
              href={REPO}
              aria-label="Riffle on GitHub"
              className="v2-navlink"
              style={{ color: T.grey }}
            >
              <svg viewBox="0 0 16 16" className="size-[17px]" fill="currentColor" aria-hidden="true">
                <path d="M8 0a8 8 0 0 0-2.53 15.59c.4.07.55-.17.55-.38v-1.34c-2.23.48-2.7-1.07-2.7-1.07-.36-.93-.89-1.18-.89-1.18-.73-.5.05-.49.05-.49.8.06 1.23.83 1.23.83.72 1.23 1.88.87 2.34.67.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.03 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.28.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48v2.19c0 .21.14.46.55.38A8 8 0 0 0 8 0Z" />
              </svg>
            </a>
          </div>
        </div>
      </header>

      {/* Hero. Centred column at 42rem, chip above the headline, diagram
          below it at 60rem — their proportions, measured off the real page.
          The headline is two inline-block spans so the shine sweeps across
          both lines as one surface rather than restarting per line. */}
      <section className="flex flex-col items-center gap-10 px-5 pt-10 pb-12 sm:px-8 sm:pt-20 sm:pb-16">
        <div className="flex w-full max-w-[42rem] flex-col items-center gap-4">
          <div
            data-anim="hero-rise"
            className="v2-chip mb-2 flex items-center gap-1.5 rounded-[5px] px-3 py-1 font-mono text-[13px] font-medium tracking-[-0.02em]"
          >
            <span
              className="inline-flex items-center rounded-[3px] p-[2px]"
              style={{ backgroundColor: T.accentSoft }}
            >
              <span
                aria-hidden="true"
                data-anim="pulse"
                className="block size-[6px] rounded-[1.1px]"
                style={{ backgroundColor: T.accent }}
              />
            </span>
            <span style={{ color: T.grey }}>main</span>
            <span style={{ color: T.grey }}>/</span>
            <span style={{ color: T.nickel }}>in development</span>
          </div>

          <h1 className="v2-shine text-balance text-center font-geist text-[clamp(2.3rem,7vw,4.5rem)] leading-[1.06] font-medium tracking-[-0.04em]">
            <span data-anim="line" className="inline-block">
              Every repo breaks
            </span>{" "}
            <span data-anim="line" className="inline-block">
              differently.
            </span>
          </h1>

          <p
            data-anim="hero-rise"
            className="text-balance text-center font-geist text-[15px] leading-[1.6] sm:text-[16.5px]"
            style={{ color: T.nickel }}
          >
            Riffle ranks your pull request queue by risk &mdash; trained on your
            repository&rsquo;s own revert history, not one vendor&rsquo;s rules.
          </p>

          <div data-anim="hero-rise" className="mt-2 flex flex-wrap items-center justify-center gap-3">
            <a
              href={REPO}
              className="riffle-btn inline-flex items-center gap-2 rounded-[8px] px-4 py-2.5 font-mono text-[12.5px] transition-transform"
              style={{ backgroundColor: T.ink, color: T.beige }}
            >
              GitHub repo
            </a>
            <a
              href="#architecture"
              className="riffle-btn-ghost inline-flex items-center gap-1.5 rounded-[8px] px-4 py-2.5 font-mono text-[12.5px]"
              style={{ color: T.ink, border: `1px solid ${T.stroke}` }}
            >
              Read the architecture <span aria-hidden="true">&darr;</span>
            </a>
          </div>
        </div>

        <Stack />
      </section>

      {/* Provenance, in the slot their customer-logo carousel occupies. It
          holds datasets rather than logos because Riffle has no users yet,
          and inventing a wall of trusted-by marks is the one thing a page
          like this must not do. Brackets flank it the way theirs do. */}
      <section className="v2-ticks border-t px-5 pt-10 pb-12 sm:px-8 sm:pt-14" style={{ borderColor: T.stroke }}>
        <div className="mx-auto w-full max-w-[74rem]">
          <p
            data-anim="rise"
            className="text-center font-geist text-[14.5px]"
            style={{ color: T.nickel }}
          >
            Trained on public defect history, then on yours
          </p>
          <div data-anim="rise" className="mt-6 flex items-center justify-center gap-4">
            <Bracket side="left" />
            <div className="flex flex-wrap items-center justify-center gap-x-7 gap-y-3">
              {["AIDev", "ApacheJIT", "MSR 2020", "your revert history"].map((src) => (
                <span
                  key={src}
                  className="font-mono text-[12px] tracking-[0.01em]"
                  style={{ color: T.grey }}
                >
                  {src}
                </span>
              ))}
            </div>
            <Bracket side="right" />
          </div>
        </div>
      </section>

      {/* The output, stated once: three bands and what each one means. */}
      <Section>
        <SectionLabel>the output</SectionLabel>
        <h2
          data-anim="rise"
          className="mt-5 max-w-[34rem] font-geist text-[clamp(1.5rem,3.4vw,2.35rem)] leading-[1.15] font-medium tracking-[-0.025em]"
        >
          Every pull request lands in one of three bands.
        </h2>
        <div className="mt-11 grid gap-px sm:grid-cols-3" style={{ backgroundColor: T.stroke }}>
          {BANDS.map(([band, meaning]) => (
            <div
              key={band}
              data-anim="rise"
              className="px-5 py-6"
              style={{ backgroundColor: T.beige }}
            >
              <p className="font-mono text-[12.5px]" style={{ color: T.accent }}>
                {band}
              </p>
              <p
                className="mt-2.5 font-geist text-[13.5px] leading-[1.5]"
                style={{ color: T.nickel }}
              >
                {meaning}
              </p>
            </div>
          ))}
        </div>
        <p data-anim="rise" className="mt-7 font-geist text-[14px]" style={{ color: T.grey }}>
          Nothing is auto-approved and nothing is hidden. Riffle reorders the
          queue; it never merges, and it never removes a PR from review.
        </p>
      </Section>

      {/* Evidence. Oversized numerals that count up on entry — the one piece
          of motion their page spends real attention on. */}
      <Section>
        <SectionLabel id="problem">the problem</SectionLabel>
        <h2
          data-anim="rise"
          className="mt-5 max-w-[34rem] font-geist text-[clamp(1.5rem,3.4vw,2.35rem)] leading-[1.15] font-medium tracking-[-0.025em]"
        >
          Writing code got cheap. Reviewing it did not.
        </h2>
        <div
          className="mt-12 grid gap-px lg:grid-cols-3"
          style={{ backgroundColor: T.stroke }}
        >
          {EVIDENCE.map((e) => (
            <div
              key={e.source}
              data-anim="rise"
              className="px-6 py-8"
              style={{ backgroundColor: T.beige }}
            >
              <p className="font-geist text-[clamp(2.2rem,5vw,3.1rem)] leading-none font-medium tracking-[-0.04em]">
                <span data-count={e.stat}>{e.stat}</span>
                <span style={{ color: T.accent }}>{e.unit}</span>
              </p>
              <p className="mt-3 font-geist text-[14px] leading-[1.45]" style={{ color: T.ink }}>
                {e.claim}
              </p>
              <p
                className="mt-3 font-geist text-[13px] leading-[1.5]"
                style={{ color: T.nickel }}
              >
                {e.detail}
              </p>
              <p className="mt-4 font-mono text-[10.5px] tracking-[0.04em] uppercase" style={{ color: T.grey }}>
                {e.source}
              </p>
            </div>
          ))}
        </div>
      </Section>

      {/* Services. Their product sections are one row each: name, a line of
          what it is, and the numbers that matter. Here the numbers are
          replaced by the failure mode, which is what actually distinguishes
          these four from each other. */}
      <Section>
        <SectionLabel id="architecture">architecture</SectionLabel>
        <h2
          data-anim="rise"
          className="mt-5 max-w-[38rem] font-geist text-[clamp(1.5rem,3.4vw,2.35rem)] leading-[1.15] font-medium tracking-[-0.025em]"
        >
          Four services. Each boundary is a place where the failure mode
          changes.
        </h2>
        <div className="mt-12" style={{ borderTop: `1px solid ${T.stroke}` }}>
          {SERVICES.map((s) => (
            <div
              key={s.name}
              data-anim="rise"
              className="grid grid-cols-1 gap-3 border-b py-7 sm:grid-cols-12 sm:gap-6"
              style={{ borderColor: T.stroke }}
            >
              <div className="sm:col-span-3">
                <p className="font-mono text-[15px]" style={{ color: T.ink }}>
                  {s.name}
                </p>
                <p className="mt-1.5 font-mono text-[11px] tracking-[0.03em]" style={{ color: T.grey }}>
                  {s.lang}
                </p>
              </div>
              <p
                className="font-geist text-[14px] leading-[1.55] sm:col-span-6"
                style={{ color: T.nickel }}
              >
                {s.job}
              </p>
              <div className="sm:col-span-3">
                <p
                  className="inline-flex rounded-[5px] px-2 py-1 font-mono text-[11px]"
                  style={{
                    color: s.constraint === "Allowed to fail" ? T.accent : T.nickel,
                    backgroundColor:
                      s.constraint === "Allowed to fail" ? T.accentSoft : "rgba(22,23,29,0.045)",
                  }}
                >
                  {s.constraint}
                </p>
                <p className="mt-2 font-geist text-[12.5px] leading-[1.45]" style={{ color: T.grey }}>
                  {s.reason}
                </p>
              </div>
            </div>
          ))}
        </div>
        <p
          data-anim="rise"
          className="mt-8 font-geist text-[15px]"
          style={{ color: T.ink }}
        >
          The LLM is never on the correctness path.
        </p>
      </Section>

      {/* Invariants: a numbered hairline list. No cards, no icons. */}
      <Section>
        <SectionLabel id="invariants">invariants</SectionLabel>
        <h2
          data-anim="rise"
          className="mt-5 max-w-[34rem] font-geist text-[clamp(1.5rem,3.4vw,2.35rem)] leading-[1.15] font-medium tracking-[-0.025em]"
        >
          Not preferences. Breaking one is a bug even if the tests pass.
        </h2>
        <ol className="mt-12" style={{ borderTop: `1px solid ${T.stroke}` }}>
          {INVARIANTS.map(([rule, detail], i) => (
            <li
              key={rule}
              data-anim="rise"
              className="grid grid-cols-[2rem_1fr] gap-x-4 gap-y-1 border-b py-5 sm:grid-cols-[3rem_16rem_1fr] sm:gap-x-6"
              style={{ borderColor: T.stroke }}
            >
              <span className="font-mono text-[11.5px] pt-0.5" style={{ color: T.grey }}>
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="font-geist text-[14.5px]" style={{ color: T.ink }}>
                {rule}
              </span>
              <span
                className="col-start-2 font-geist text-[13.5px] leading-[1.5] sm:col-start-3"
                style={{ color: T.nickel }}
              >
                {detail}
              </span>
            </li>
          ))}
        </ol>
      </Section>

      {/* Contracts: the two schemas, side by side, as code. Their page uses
          the same trick — real source as the visual, not a screenshot. */}
      <Section>
        <SectionLabel id="contracts">contracts</SectionLabel>
        <h2
          data-anim="rise"
          className="mt-5 max-w-[36rem] font-geist text-[clamp(1.5rem,3.4vw,2.35rem)] leading-[1.15] font-medium tracking-[-0.025em]"
        >
          Two shapes cross every service boundary. Both are the source of
          truth.
        </h2>
        <div className="mt-12 grid gap-px lg:grid-cols-2" style={{ backgroundColor: T.stroke }}>
          {[
            ["intake → scorer", PR_EVENT],
            ["scorer → app", SCORE_RESULT],
          ].map(([caption, code]) => (
            <figure
              key={caption}
              data-anim="rise"
              className="flex flex-col"
              style={{ backgroundColor: T.paper }}
            >
              <figcaption
                className="border-b px-5 py-3 font-mono text-[11.5px] tracking-[0.02em]"
                style={{ borderColor: T.stroke, color: T.grey }}
              >
                {caption}
              </figcaption>
              <pre className="overflow-x-auto px-5 py-5 font-mono text-[11.5px] leading-[1.7]" style={{ color: T.nickel }}>
                {code}
              </pre>
            </figure>
          ))}
        </div>
        <p data-anim="rise" className="mt-6 font-geist text-[13.5px]" style={{ color: T.grey }}>
          <code className="font-mono">explanation</code> is nullable by design. A
          null means the explainer was slow or down &mdash; the rank is still
          valid.
        </p>
      </Section>

      {/* Data and observability: two plain tables. Density without ornament,
          which is what their lower sections do once the pitch is over. */}
      <Section>
        <div className="grid gap-14 lg:grid-cols-2 lg:gap-16">
          <div>
            <SectionLabel>training data</SectionLabel>
            <dl className="mt-7" style={{ borderTop: `1px solid ${T.stroke}` }}>
              {DATA.map(([name, use]) => (
                <div
                  key={name}
                  data-anim="rise"
                  className="border-b py-4"
                  style={{ borderColor: T.stroke }}
                >
                  <dt className="font-geist text-[14px]" style={{ color: T.ink }}>
                    {name}
                  </dt>
                  <dd className="mt-1 font-geist text-[13px] leading-[1.5]" style={{ color: T.nickel }}>
                    {use}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
          <div>
            <SectionLabel>observability</SectionLabel>
            <dl className="mt-7" style={{ borderTop: `1px solid ${T.stroke}` }}>
              {METRICS.map(([metric, meaning]) => (
                <div
                  key={metric}
                  data-anim="rise"
                  className="border-b py-4"
                  style={{ borderColor: T.stroke }}
                >
                  <dt className="font-mono text-[12px]" style={{ color: T.ink }}>
                    {metric}
                  </dt>
                  <dd className="mt-1 font-geist text-[13px] leading-[1.5]" style={{ color: T.nickel }}>
                    {meaning}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </Section>

      <footer
        className="border-t px-5 py-10 sm:px-8 lg:px-12"
        style={{ borderColor: T.stroke }}
      >
        <div className="mx-auto flex w-full max-w-[74rem] flex-wrap items-center justify-between gap-5">
          <LockupBlack
            aria-hidden="true"
            className="h-auto w-[76px]"
            style={{ color: T.ink }}
          />
          <p
            className="flex flex-wrap items-center gap-x-3 gap-y-1.5 font-mono text-[11.5px]"
            style={{ color: T.grey }}
          >
            <a href={REPO} className="v2-navlink">
              github.com/ru-dr/riffle
            </a>
            <span aria-hidden="true">/</span>
            <span>Apache-2.0</span>
            <span aria-hidden="true">/</span>
            <span>Self-hosted</span>
          </p>
        </div>
      </footer>

      </div>
      <V2Motion />
    </div>
  );
}
