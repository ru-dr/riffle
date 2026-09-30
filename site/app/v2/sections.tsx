import { Mark } from "@/components/brand";
import { LangIcon } from "./lang-icon";
import { T } from "./tokens";

// Publishable logo.dev key. It is designed to sit in client-visible image
// URLs, so NEXT_PUBLIC is correct here — but it is still read from the
// environment rather than committed, so it can be rotated without a deploy.
const LOGO_DEV_TOKEN = process.env.NEXT_PUBLIC_LOGO_DEV_TOKEN;
import {
  FOOTER_LINKS,
  INVARIANTS,
  MISSION,
  PRIOR_WORK,
  PR_EVENT,
  REPO,
  RESOURCES,
  SCORE_RESULT,
  SERVICE_PANELS,
  STATS,
  type CodeLine,
  type TokenKind,
} from "./data";

// Everything below the hero, each block built to the reference's own
// markup: a bordered .wrapper per section, ticks where rules meet the
// verticals, empty ruled spacers between blocks, and dark surfaces that
// recolour those rules to nickel.

const H2 =
  "font-geist text-[1.875rem] leading-[2.25rem] font-medium tracking-[-0.025em] text-balance md:text-[3rem] md:leading-[1.05]";
const H3 =
  "font-geist text-[1.5rem] leading-[2rem] font-medium tracking-[-0.025em] text-balance md:text-[2.5rem] md:leading-[1.1]";
const LABEL = "font-mono text-[12px] uppercase tracking-[0.05em]";

/* Their empty ruled band between blocks: a rule with ticks and nothing in
   it. It is what gives the page its measured, sectioned rhythm. */
export function Spacer({ dark = false }: { dark?: boolean }) {
  return (
    <section
      aria-hidden="true"
      data-surface={dark ? "dark" : undefined}
      className="v2-wrapper v2-ticks h-16 border-t sm:h-[7.5rem]"
      style={{ borderColor: dark ? T.nickel : T.stroke }}
    />
  );
}

export function AnnounceBar() {
  return (
    <a href={REPO} className="group relative block overflow-hidden" style={{ backgroundColor: T.darkBg }}>
      <div
        aria-hidden="true"
        className="absolute inset-y-0 right-0 w-[55%]"
        style={{
          background:
            "linear-gradient(90deg, transparent 0%, rgba(125,211,160,0.18) 40%, rgba(165,180,252,0.42) 75%, rgba(196,181,253,0.55) 100%)",
        }}
      />
      <div className="relative flex items-center gap-2.5 px-6 py-2.5">
        <span className="font-mono text-[11px] tracking-[0.06em] uppercase" style={{ color: T.darkInk }}>
          Riffle is in development
        </span>
        <span
          aria-hidden="true"
          className="inline-flex size-[18px] items-center justify-center rounded-[3px] text-[11px] transition-transform group-hover:translate-x-0.5"
          style={{ backgroundColor: T.darkInk, color: T.darkBg }}
        >
          &rarr;
        </span>
      </div>
    </a>
  );
}

/* Their customer-logo row, holding datasets. Same bracket punctuation. */
export function Provenance() {
  return (
    <section
      className="v2-wrapper v2-ticks flex flex-col items-center justify-center border-t pt-10 pb-10 md:gap-2 md:pt-14"
      style={{ borderColor: T.stroke }}
    >
      <p data-anim="rise" className="text-center font-geist text-[16px] text-balance" style={{ color: T.nickel }}>
        Trained on public defect history, then on yours
      </p>
      <div data-anim="rise" className="mt-6 flex items-center gap-6">
        <Paren />
        <div className="flex flex-wrap items-center justify-center gap-x-10 gap-y-3">
          {["AIDev", "ApacheJIT", "MSR 2020", "Your revert history"].map((src, i) => (
            <span
              key={src}
              className="font-geist text-[17px] font-medium tracking-[-0.02em]"
              style={{ color: T.ink, opacity: i === 3 ? 1 : 0.42 }}
            >
              {src}
            </span>
          ))}
        </div>
        <Paren flip />
      </div>
    </section>
  );
}

function Paren({ flip = false }: { flip?: boolean }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 8 30"
      className="h-[40px] w-[11px] shrink-0"
      fill={T.nickel}
      style={{ transform: flip ? "scaleX(-1)" : undefined }}
    >
      <path d="M4.652 30C1.48 25.427 0 20.439 0 15.035 0 9.596 1.48 4.538 4.652 0H8c-2.573 4.988-3.7 10.046-3.7 15.035 0 4.988 1.092 10.011 3.7 14.965H4.652Z" />
    </svg>
  );
}

/* Dark product block, to the reference's structure: a short label row with
   the rule beneath it (no rule above — the block starts flush), a tall
   header band, then a rail with its own right border beside the rows. */
export function ServicesBlock() {
  return (
    <div data-surface="dark" style={{ backgroundColor: T.darkBg }}>
      <section className="v2-wrapper px-6 py-5 md:px-10">
        <p className="font-geist text-[16px] font-medium" style={{ color: T.darkInk }}>
          Services
        </p>
      </section>

      <section
        id="architecture"
        className="v2-wrapper v2-ticks border-t px-6 py-16 md:px-10 md:py-24"
        style={{ borderColor: T.nickel }}
      >
        <h2 data-anim="rise" className={H2} style={{ color: T.darkInk }}>
          Four services
        </h2>
        <p
          data-anim="rise"
          className="mt-6 max-w-[25rem] font-geist text-[18px] leading-[1.55] text-pretty"
          style={{ color: T.darkNickel }}
        >
          Each boundary is a place where the failure mode changes. Intake must
          never be slow, scoring must never be lost, and explanation is allowed
          to fail.
        </p>
      </section>

      <section className="v2-wrapper v2-ticks flex flex-col border-t md:flex-row" style={{ borderColor: T.nickel }}>
        <div className="hidden shrink-0 border-r px-6 py-10 md:block md:w-[15.5rem] md:px-10" style={{ borderColor: T.nickel }}>
          <ul className="sticky top-10 flex flex-col gap-4">
            {SERVICE_PANELS.map((s, i) => (
              <li key={s.name} data-rail={s.name} data-active={i === 0 ? "" : undefined}>
                <a
                  href={`#svc-${s.name}`}
                  className="flex items-center gap-3 font-geist text-[16px]"
                  style={{ color: T.darkInk }}
                >
                  <span className="flex items-center font-mono text-[15px]" style={{ color: T.darkGrey }}>
                    (
                    <LangIcon
                      lang={s.icon}
                      className="mx-[3px] size-[15px]"
                      color={s.icon === "python" ? "#4b8bbe" : undefined}
                    />
                    )
                  </span>
                  {s.name}
                </a>
              </li>
            ))}
          </ul>
        </div>

        <div className="w-full">
          {SERVICE_PANELS.map((s, i) => (
            <div
              key={s.name}
              id={`svc-${s.name}`}
              data-project={s.name}
              className="grid w-full lg:grid-cols-2"
              style={{ borderTop: i === 0 ? undefined : `1px solid ${T.nickel}` }}
            >
              <div data-anim="rise" className="flex flex-col justify-between gap-10 p-6 md:p-10 lg:gap-20">
                <div className="flex max-w-[22rem] flex-col gap-5">
                  <span className="font-mono text-[14px] tracking-[0.08em] uppercase" style={{ color: T.darkGrey }}>
                    {s.name}
                  </span>
                  <h4
                    className="font-geist text-[1.875rem] leading-[1.15] font-medium tracking-[-0.025em] text-pretty"
                    style={{ color: T.darkInk }}
                  >
                    {s.title}
                  </h4>
                  <p className="font-geist text-[16px] leading-[1.55] text-pretty" style={{ color: T.darkNickel }}>
                    {s.body}
                  </p>
                  <a href={`${REPO}#architecture`} className="v2-btn v2-btn--sm mt-5 w-fit">
                    Explore {s.name}
                  </a>
                </div>
                <div
                  className="flex w-fit items-center gap-3 rounded-sm px-3 py-1 font-mono text-[12px]"
                  style={{ backgroundColor: "rgba(255,255,255,0.06)", color: T.darkNickel }}
                >
                  <span style={{ color: T.darkInk }}>{s.budget}</span>
                  <span style={{ color: T.darkGrey }}>{s.budgetLabel}</span>
                </div>
              </div>

              <Artwork tone={s.tone} code={s.code} art={s.art} />
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

/* Right-hand artwork: texture flush to the cell, terminal card vertically
   centred and running off the right edge. The card has the reference's dual
   edge — a thin bright outer rim over a dark inner border — and highlighted
   output rather than flat grey text. */
const TOKEN: Record<TokenKind, React.CSSProperties> = {
  cmd: { color: "#ffffff", fontWeight: 600 },
  key: { color: "#e7e6ea" },
  dim: { color: "#8b8993" },
  val: { color: "#8ab4ff" },
  str: { color: "#6ee7d8" },
  ok: { color: "#3fb950" },
  warn: { color: "#f0b35a" },
};

function Artwork({ tone, code, art }: { tone: readonly string[]; code: readonly CodeLine[]; art: string }) {
  return (
    <div
      data-anim="rise"
      className="relative min-h-[22rem] overflow-hidden lg:min-h-[29rem]"
      style={{
        backgroundColor: tone[1],
        backgroundImage: `url(${art})`,
        backgroundSize: "cover",
        backgroundPosition: "center",
      }}
    >
      {/* Dual edge, as on the reference: the dark card has no border of its
          own; around it runs a 1px transparent gap where the texture shows
          through; around that, a thin light rim with a soft glow. */}
      <div
        className="absolute top-1/2 right-0 left-6 -translate-y-1/2 rounded-l-[9px] p-px md:left-10"
        style={{
          border: "1px solid rgba(255,255,255,0.55)",
          borderRight: "none",
          boxShadow: "0 0 14px rgba(255,255,255,0.22), 0 24px 50px -20px rgba(0,0,0,0.6)",
        }}
      >
        <div className="rounded-l-[7px] px-7 py-7" style={{ backgroundColor: "#1a1b20" }}>
          <pre className="overflow-hidden font-mono text-[13px] leading-[2]">
            {code.map((line, i) => (
              <div key={i}>
                {line.length === 0
                  ? "\u00a0"
                  : line.map(([text, kind], j) => (
                      <span key={j} style={TOKEN[kind]}>
                        {text}
                      </span>
                    ))}
              </div>
            ))}
          </pre>
        </div>
      </div>
    </div>
  );
}

/* Statistics: heading, then a 4/6 split of big figure and chart, then three
   cells — each divided by hairlines, as theirs are. */
export function StatsBand() {
  return (
    <section className="v2-wrapper v2-ticks border-t" style={{ borderColor: T.stroke }}>
      <div className="px-6 pt-10 pb-10 md:px-10 md:pt-14">
        <h3 data-anim="rise" className={`${H3} max-w-[28rem]`}>
          {STATS.headline}
        </h3>
      </div>
      <div className="grid border-t md:grid-cols-10 md:divide-x" style={{ borderColor: T.stroke }}>
        <div data-anim="rise" className="flex flex-col justify-between gap-16 p-6 md:col-span-4 md:p-10" style={{ borderColor: T.stroke }}>
          <p className="font-geist text-[15px]" style={{ color: T.nickel }}>
            Bootstrap corpus
          </p>
          <div>
            <p className="font-geist text-[2.75rem] leading-none font-medium tracking-[-0.04em] md:text-[3.5rem]">
              <span data-count={STATS.big}>{STATS.big.toLocaleString("en-US")}</span>
            </p>
            <p className="mt-3 font-geist text-[14px]" style={{ color: T.grey }}>
              {STATS.bigLabel}
            </p>
          </div>
        </div>
        <div data-anim="rise" className="flex flex-col justify-between gap-8 border-t p-6 md:col-span-6 md:border-t-0 md:p-10" style={{ borderColor: T.stroke }}>
          <p className="w-fit rounded px-2 py-1 font-mono text-[12px]" style={{ outline: `1px solid ${T.stroke}`, color: T.nickel }}>
            ApacheJIT · labelled share
          </p>
          <div>
            <div className="flex h-24 items-end gap-2">
              <div className="h-full w-[26.5%] rounded-sm" style={{ backgroundColor: T.live }} />
              <div className="h-full flex-1 rounded-sm" style={{ backgroundColor: T.stroke }} />
            </div>
            <div className="mt-3 flex justify-between font-mono text-[12px]">
              <span style={{ color: T.accent }}>26.5% bug-inducing</span>
              <span style={{ color: T.grey }}>73.5% clean</span>
            </div>
          </div>
        </div>
      </div>
      <div className="grid border-t sm:grid-cols-3 sm:divide-x" style={{ borderColor: T.stroke }}>
        {STATS.cells.map(([value, label]) => (
          <div key={label} data-anim="rise" className="p-6 md:p-10" style={{ borderColor: T.stroke }}>
            <p className="font-geist text-[2rem] leading-none font-medium tracking-[-0.03em]">{value}</p>
            <p className="mt-3 font-geist text-[14px]" style={{ color: T.grey }}>
              {label}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}

export function MissionPanel() {
  return (
    <>
      <section
        className="v2-wrapper v2-ticks flex flex-col items-center gap-8 border-t px-6 py-20 text-center md:py-28"
        style={{ borderColor: T.stroke, backgroundColor: T.beige }}
      >
        <h3 data-anim="rise" className={`${H3} max-w-[44rem] md:text-[2.25rem]`}>
          {MISSION}
        </h3>
        <a data-anim="rise" href={`${REPO}#what-riffle-does`} className="v2-btn v2-btn--sm">
          Learn more
        </a>
      </section>
      {/* Their "backed by" row, holding the sources Riffle's approach cites.
          Logos come from logo.dev when a publishable token is configured;
          without one each cell falls back to the name, so the row never
          renders broken images. Labelled as research, not backing, because
          none of these organisations endorse Riffle. */}
      <section className="v2-wrapper v2-ticks grid border-t md:grid-cols-6 md:divide-x" style={{ borderColor: T.stroke }}>
        <p className="flex items-center p-6 font-geist text-[14px] md:col-span-1 md:p-8" style={{ color: T.nickel }}>
          Research this builds on
        </p>
        {PRIOR_WORK.map(({ name, domain }) => (
          <div key={name} className="flex items-center justify-center gap-2.5 p-6 md:p-8">
            {LOGO_DEV_TOKEN && domain ? (
              <>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={`https://img.logo.dev/${domain}?token=${LOGO_DEV_TOKEN}&size=64&format=png&greyscale=true&retina=true`}
                  alt=""
                  width={22}
                  height={22}
                  loading="lazy"
                  className="rounded-[4px] opacity-70"
                />
                <span className="font-geist text-[17px] font-medium tracking-[-0.02em]" style={{ color: T.ink, opacity: 0.6 }}>
                  {name}
                </span>
              </>
            ) : (
              <span className="font-geist text-[18px] font-medium tracking-[-0.02em]" style={{ color: T.ink, opacity: 0.55 }}>
                {name}
              </span>
            )}
          </div>
        ))}
      </section>
    </>
  );
}

export function InvariantsSection() {
  return (
    <section id="invariants" className="v2-wrapper v2-ticks border-t" style={{ borderColor: T.stroke }}>
      <div className="px-6 pt-10 pb-10 md:px-10 md:pt-14">
        <p data-anim="rise" className={LABEL} style={{ color: T.grey }}>
          Invariants
        </p>
        <h3 data-anim="rise" className={`${H3} mt-6 max-w-[34rem]`}>
          Breaking one is a bug even if the tests pass
        </h3>
      </div>
      <ol className="grid border-t md:grid-cols-3" style={{ borderColor: T.stroke }}>
        {INVARIANTS.map(([rule, detail], i) => (
          <li
            key={rule}
            data-anim="rise"
            className="flex flex-col gap-3 border-b p-6 md:p-10 [&:nth-child(3n+2)]:md:border-x"
            style={{ borderColor: T.stroke }}
          >
            <span className="font-mono text-[12px]" style={{ color: T.grey }}>
              {String(i + 1).padStart(2, "0")}
            </span>
            <span className="font-geist text-[17px] font-medium tracking-[-0.015em]">{rule}</span>
            <span className="font-geist text-[14.5px] leading-[1.55]" style={{ color: T.nickel }}>
              {detail}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ContractsSection() {
  return (
    <section id="contracts" className="v2-wrapper v2-ticks border-t" style={{ borderColor: T.stroke }}>
      <div className="px-6 pt-10 pb-10 md:px-10 md:pt-14">
        <p data-anim="rise" className={LABEL} style={{ color: T.grey }}>
          Contracts
        </p>
        <h3 data-anim="rise" className={`${H3} mt-6 max-w-[34rem]`}>
          Two shapes cross every boundary
        </h3>
      </div>
      <div className="grid border-t md:grid-cols-2 md:divide-x" style={{ borderColor: T.stroke }}>
        {[
          ["intake → scorer", PR_EVENT],
          ["scorer → app", SCORE_RESULT],
        ].map(([caption, code]) => (
          <CodeBlock key={caption} tag="json" caption={caption} code={code} />
        ))}
      </div>
    </section>
  );
}

/* Their code device: a small language tag pinned top-left on a faint beige
   chip, then monospace lines at 12px. */
function CodeBlock({ tag, caption, code }: { tag: string; caption: string; code: string }) {
  return (
    <figure data-anim="rise" className="relative flex flex-col gap-4 px-6 pt-16 pb-8 md:px-10" style={{ borderColor: T.stroke }}>
      <span
        className="absolute top-3 left-3 rounded p-2 font-mono text-[12px]"
        style={{ backgroundColor: "rgba(244,243,236,0.4)", color: T.ink }}
      >
        {tag}
      </span>
      <figcaption className="font-mono text-[12px]" style={{ color: T.grey }}>
        {caption}
      </figcaption>
      <pre className="overflow-x-auto font-mono text-[12px] leading-[1.7]" style={{ color: T.ink }}>
        {code}
      </pre>
    </figure>
  );
}

/* Resources: their 4/6 split. Left column is heading-plus-button over the
   .filter() device; right column is the card row. */
export function ResourcesGrid() {
  return (
    <section
      className="v2-wrapper v2-ticks grid grid-cols-1 border-t md:grid-cols-10 md:divide-x"
      style={{ borderColor: T.stroke }}
    >
      <div className="flex flex-col divide-y md:col-span-4" style={{ borderColor: T.stroke }}>
        <div className="flex flex-col justify-center gap-6 p-5 md:h-72 md:justify-start md:gap-10 md:p-10" style={{ borderColor: T.stroke }}>
          <h3 data-anim="rise" className={H3}>
            Reference &amp; internals
          </h3>
          <a data-anim="rise" href={`${REPO}#readme`} className="v2-btn w-fit">
            All docs
          </a>
        </div>
        <div data-anim="rise" className="relative flex flex-col gap-4 px-6 pt-16 pb-8 md:px-10" style={{ borderColor: T.stroke }}>
          <span
            className="absolute top-3 left-3 rounded p-2 font-mono text-[12px]"
            style={{ backgroundColor: "rgba(244,243,236,0.4)", color: T.ink }}
          >
            ts
          </span>
          <div className="flex flex-col font-mono text-[12px] leading-[1.7]">
            <span style={{ color: T.grey }}>docs</span>
            {[
              ["d.settled ===", "true"],
              ["d.source ===", '"README"'],
              ["d.sections.length ===", "3"],
            ].map(([arg, value]) => (
              <span key={arg} style={{ color: T.ink }}>
                {" "}.filter<span style={{ color: T.grey }}>(d =&gt; {arg}</span> {value}
                <span style={{ color: T.grey }}>)</span>
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="grid border-t sm:grid-cols-3 md:col-span-6 md:border-t-0" style={{ borderColor: T.stroke }}>
        {RESOURCES.map((r) => (
          <a key={r.kind} data-anim="rise" href={r.href} className="group flex flex-col gap-4 p-5 md:p-6">
            <div
              className="aspect-[16/10] w-full overflow-hidden rounded-sm transition-transform duration-300 group-hover:scale-[1.02]"
              style={{
                backgroundColor: r.tone[1],
                backgroundImage: `url(${r.art})`,
                backgroundSize: "cover",
                backgroundPosition: "center",
              }}
            />
            <div className="flex flex-col gap-1.5">
              <span className="font-mono text-[12px] uppercase tracking-[0.05em]" style={{ color: T.grey }}>
                // {r.kind}
              </span>
              <span className="font-geist text-[16px] leading-[1.35]" style={{ color: T.ink }}>
                {r.title}
              </span>
            </div>
          </a>
        ))}
      </div>
    </section>
  );
}

/* Dark footer, to the reference render: a two-column CTA row, a banner flush
   to the wrapper's edges with the mark centred, a compact cluster of link
   columns with Social pushed right, deliberate empty space, then a ticked
   rule over the copyright line.

   Their right-hand column is a newsletter form. Riffle has no list, so the
   same bordered field-plus-button shape holds the repository address and a
   Watch button — it looks like theirs and says something true. */
export function ClosingBlock() {
  return (
    // A full viewport tall, as the reference's is: the link section takes the
    // slack so the copyright rule always lands on the bottom edge.
    <footer data-surface="dark" className="flex min-h-dvh flex-col" style={{ backgroundColor: T.darkBg }}>
      <section className="v2-wrapper grid w-full gap-10 px-6 pt-14 pb-20 md:grid-cols-2 md:px-6 md:pt-16 md:pb-24 lg:px-6">
        <h3
          data-anim="rise"
          className="max-w-[28rem] font-geist text-[1.875rem] leading-[1.12] font-medium tracking-[-0.025em] md:text-[2.25rem]"
          style={{ color: T.darkInk }}
        >
          Interested in where your repository breaks?
        </h3>
        <div data-anim="rise" className="flex flex-col gap-4 md:items-end md:pt-1">
          <p className="w-full max-w-[17.5rem] font-geist text-[17px] md:max-w-[17.5rem] md:self-end lg:max-w-[17.5rem]" style={{ color: T.darkInk }}>
            Follow the build on GitHub
          </p>
          <div
            className="flex w-full max-w-[17.5rem] items-center justify-between gap-3 rounded-[4px] py-1.5 pr-1.5 pl-3 md:w-[17.5rem] lg:w-[17.5rem]"
            style={{ outline: `1px solid ${T.nickel}` }}
          >
            <span className="truncate font-geist text-[14px]" style={{ color: T.darkGrey }}>
              github.com/ru-dr/riffle
            </span>
            <a
              href={REPO}
              className="rounded-[3px] px-3 py-1.5 font-geist text-[14px] font-medium whitespace-nowrap transition-transform hover:scale-105"
              style={{ backgroundColor: "#fff", color: T.ink }}
            >
              Watch repo
            </a>
          </div>
        </div>
      </section>

      <section className="v2-wrapper w-full">
        <div
          data-anim="rise"
          className="flex h-36 items-center justify-center md:h-[8.75rem]"
          style={{
            backgroundColor: "#3a1e8c",
            backgroundImage: "url(/v2/art/rays-purple.webp)",
            backgroundSize: "cover",
            backgroundPosition: "center",
          }}
        >
          <span
            className="flex size-12 items-center justify-center rounded-[10px]"
            style={{ backgroundColor: "#0d0c10", boxShadow: "0 10px 26px -8px rgba(0,0,0,0.6)" }}
          >
            <Mark aria-hidden="true" className="h-auto w-7" style={{ color: T.darkInk }} />
          </span>
        </div>
      </section>

      <section className="v2-wrapper flex w-full flex-1 flex-col justify-between gap-12 px-6 pt-11 pb-24 sm:flex-row sm:items-start sm:px-14 md:pb-28">
        <div className="flex flex-wrap gap-x-12 gap-y-10 sm:gap-x-12">
          {FOOTER_LINKS.map(([heading, links]) => (
            <div key={heading}>
              <p className="font-mono text-[12px] tracking-[0.06em] uppercase" style={{ color: T.darkGrey }}>
                {heading}
              </p>
              <ul className="mt-7 flex flex-col gap-4">
                {links.map(([label, href]) => (
                  <li key={label}>
                    <a href={href} className="v2-link font-geist text-[14px]" style={{ color: T.darkInk }}>
                      {label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="sm:pr-8">
          <p className="font-mono text-[12px] tracking-[0.06em] uppercase" style={{ color: T.darkGrey }}>
            Social
          </p>
          <ul className="mt-7 flex flex-col gap-3">
            <li>
              <a href={REPO} className="v2-link flex items-center gap-2 font-geist text-[14px]" style={{ color: T.darkInk }}>
                <svg viewBox="0 0 16 16" className="size-[15px]" fill="currentColor" aria-hidden="true">
                  <path d="M8 0a8 8 0 0 0-2.53 15.59c.4.07.55-.17.55-.38v-1.34c-2.23.48-2.7-1.07-2.7-1.07-.36-.93-.89-1.18-.89-1.18-.73-.5.05-.49.05-.49.8.06 1.23.83 1.23.83.72 1.23 1.88.87 2.34.67.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.03 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.28.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48v2.19c0 .21.14.46.55.38A8 8 0 0 0 8 0Z" />
                </svg>
                GitHub
              </a>
            </li>
          </ul>
        </div>
      </section>

      <section className="v2-wrapper v2-ticks w-full border-t px-6 py-5 sm:px-14" style={{ borderColor: T.nickel }}>
        <p className="font-geist text-[13px]" style={{ color: T.darkGrey }}>
          &copy; 2026 Riffle contributors. Apache-2.0.
        </p>
      </section>
    </footer>
  );
}
