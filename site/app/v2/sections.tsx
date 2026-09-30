import { T } from "./tokens";
import {
  FOOTER_LINKS,
  MISSION,
  PRIOR_WORK,
  RESOURCES,
  SERVICE_PANELS,
  STATS,
  REPO,
} from "./data";

// The blocks below the hero, in the reference page's order: a full-bleed dark
// product block, a statistics band, a mission panel, a prior-work row, a
// resources grid, and a dark closing CTA.
//
// Two substitutions, both because the honest version is not available: there
// is no investor row (no funding), and no newsletter (no mailing list). The
// slots hold the papers the approach rests on, and the repository, instead.

/* Gradient code panel — their vivid right-hand column. The gradient is two
   stops from the service's own tone, so four panels differ without four
   different art directions. */
function Panel({
  tone,
  code,
  label,
}: {
  tone: readonly [string, string] | readonly string[];
  code: string;
  label: string;
}) {
  return (
    <div
      className="relative flex h-full min-h-[210px] flex-col justify-end overflow-hidden p-4"
      style={{
        background: `linear-gradient(140deg, ${tone[0]} 0%, ${tone[1]} 52%, ${tone[0]} 100%)`,
      }}
    >
      {/* The code sits on a dark card over the gradient rather than directly
          on it — their panels are a screenshot floating on artwork, and that
          separation is what keeps the type readable at this saturation. */}
      <div
        className="flex h-full flex-col justify-between rounded-[3px] px-4 py-3.5"
        style={{
          backgroundColor: "rgba(12,9,7,0.86)",
          border: "1px solid rgba(251,250,247,0.12)",
          boxShadow: "0 12px 28px -10px rgba(0,0,0,0.55)",
        }}
      >
        <pre
          className="overflow-hidden font-mono text-[10.5px] leading-[1.75] whitespace-pre"
          style={{ color: "rgba(251,250,247,0.8)" }}
        >
          {code}
        </pre>
        <p
          className="mt-3 font-mono text-[10px] tracking-[0.08em] uppercase"
          style={{ color: tone[0] }}
        >
          {label}
        </p>
      </div>
    </div>
  );
}

export function ServicesBlock() {
  return (
    <section
      id="architecture"
      className="v2-ticks border-t"
      style={{ backgroundColor: T.darkBg, borderColor: T.stroke }}
    >
      <div className="px-5 pt-12 pb-4 sm:px-8 sm:pt-16 lg:px-12">
        <div className="mx-auto w-full max-w-[74rem]">
          <p
            data-anim="rise"
            className="font-mono text-[10.5px] tracking-[0.14em] uppercase"
            style={{ color: T.darkGrey }}
          >
            Services
          </p>
          <h2
            data-anim="rise"
            className="mt-6 font-geist text-[clamp(1.7rem,4vw,2.6rem)] leading-[1.1] font-medium tracking-[-0.03em]"
            style={{ color: T.darkInk }}
          >
            Four services
          </h2>
          <p
            data-anim="rise"
            className="mt-4 max-w-[30rem] font-geist text-[14px] leading-[1.6]"
            style={{ color: T.darkNickel }}
          >
            Each boundary is a place where the failure mode changes. Intake must
            never be slow, scoring must never be lost, and explanation is
            allowed to fail.
          </p>
        </div>
      </div>

      <div className="mx-auto w-full max-w-[74rem] px-5 pb-14 sm:px-8 lg:px-12 lg:pb-20">
        <div style={{ borderTop: `1px solid ${T.darkStroke}` }}>
          {SERVICE_PANELS.map((s) => (
            <div
              key={s.name}
              data-anim="rise"
              className="grid grid-cols-1 items-stretch gap-y-5 border-b py-8 lg:grid-cols-[7rem_minmax(0,1fr)_minmax(0,1.05fr)] lg:gap-x-10"
              style={{ borderColor: T.darkStroke }}
            >
              {/* Rail: the name, dim, the way their product list runs. */}
              <div className="flex items-start gap-2 lg:flex-col lg:gap-1">
                <p className="font-mono text-[12px]" style={{ color: T.darkInk }}>
                  {s.name}
                </p>
                <p className="font-mono text-[10.5px] tracking-[0.04em] uppercase" style={{ color: T.darkGrey }}>
                  {s.lang}
                </p>
              </div>

              <div className="flex flex-col">
                <h3
                  className="font-geist text-[19px] leading-[1.25] font-medium tracking-[-0.02em]"
                  style={{ color: T.darkInk }}
                >
                  {s.title}
                </h3>
                <p
                  className="mt-3 max-w-[26rem] font-geist text-[13.5px] leading-[1.6]"
                  style={{ color: T.darkNickel }}
                >
                  {s.body}
                </p>
                <div className="mt-5 flex items-center gap-5">
                  <a
                    href={`${REPO}#architecture`}
                    className="riffle-btn-ghost inline-flex items-center gap-1.5 rounded-[7px] px-3.5 py-2 font-mono text-[11.5px]"
                    style={{ color: T.darkInk, border: `1px solid ${T.darkStroke}` }}
                  >
                    Explore {s.name}
                  </a>
                  {/* Their rows end in stars and contributors. Riffle has
                      neither yet, so the row ends in the constraint that
                      actually distinguishes the service. */}
                  <p className="font-mono text-[11px]" style={{ color: T.darkGrey }}>
                    <span style={{ color: T.mint }}>{s.budget}</span> {s.budgetLabel}
                  </p>
                </div>
              </div>

              <Panel tone={s.tone} code={s.code} label={`${s.name} · live shape`} />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function StatsBand() {
  return (
    <section className="v2-ticks border-t px-5 py-14 sm:px-8 sm:py-20 lg:px-12" style={{ borderColor: T.stroke }}>
      <div className="mx-auto w-full max-w-[74rem]">
        <h2
          data-anim="rise"
          className="max-w-[26rem] font-geist text-[clamp(1.6rem,3.6vw,2.4rem)] leading-[1.12] font-medium tracking-[-0.03em]"
        >
          {STATS.headline}
        </h2>

        <div className="mt-12 grid items-end gap-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          <div data-anim="rise">
            <p className="font-mono text-[11px] tracking-[0.04em]" style={{ color: T.grey }}>
              Bootstrap corpus
            </p>
            <p className="mt-4 font-geist text-[clamp(2.4rem,6vw,3.6rem)] leading-none font-medium tracking-[-0.04em]">
              <span data-count={STATS.big} data-format="int">
                {STATS.big.toLocaleString("en-US")}
              </span>
            </p>
            <p className="mt-3 max-w-[20rem] font-geist text-[13.5px] leading-[1.5]" style={{ color: T.nickel }}>
              {STATS.bigLabel}
            </p>
          </div>

          {/* Their chart plots real download history. Riffle has no time
              series to plot, so this is the label distribution instead — the
              one proportion in the corpus that matters, drawn to scale. */}
          <div data-anim="rise" style={{ border: `1px solid ${T.stroke}`, backgroundColor: T.paper }}>
            <p
              className="border-b px-5 py-3 font-mono text-[11px]"
              style={{ borderColor: T.stroke, color: T.grey }}
            >
              ApacheJIT · labelled share
            </p>
            <div className="px-5 py-7">
              <div className="flex h-3 w-full overflow-hidden rounded-[2px]" style={{ backgroundColor: T.stroke }}>
                <div style={{ width: "26.5%", backgroundColor: T.accent }} />
              </div>
              <div className="mt-4 flex items-baseline justify-between font-mono text-[11px]">
                <span style={{ color: T.accent }}>26.5% bug-inducing</span>
                <span style={{ color: T.grey }}>73.5% clean</span>
              </div>
              <p className="mt-5 font-geist text-[12.5px] leading-[1.5]" style={{ color: T.nickel }}>
                Enough signal to start a model, nowhere near enough to be your
                repository. That is what per-tenant training is for.
              </p>
            </div>
          </div>
        </div>

        <div className="mt-12 grid gap-px sm:grid-cols-3" style={{ backgroundColor: T.stroke }}>
          {STATS.cells.map(([value, label]) => (
            <div key={label} data-anim="rise" className="px-5 py-6" style={{ backgroundColor: T.beige }}>
              <p className="font-geist text-[clamp(1.4rem,3vw,1.9rem)] leading-none font-medium tracking-[-0.03em]">
                {value}
              </p>
              <p className="mt-2.5 font-geist text-[13px] leading-[1.45]" style={{ color: T.nickel }}>
                {label}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

export function MissionPanel() {
  return (
    <section className="v2-ticks border-t px-5 py-14 sm:px-8 sm:py-16 lg:px-12" style={{ borderColor: T.stroke }}>
      <div
        data-anim="rise"
        className="mx-auto flex w-full max-w-[74rem] flex-col items-center gap-7 px-6 py-16 text-center sm:px-12"
        style={{ backgroundColor: T.paper, border: `1px solid ${T.stroke}` }}
      >
        <p className="max-w-[40rem] text-balance font-geist text-[clamp(1.15rem,2.6vw,1.6rem)] leading-[1.4] font-medium tracking-[-0.02em]">
          {MISSION}
        </p>
        <a
          href={`${REPO}#what-riffle-does`}
          className="riffle-btn-ghost inline-flex items-center gap-1.5 rounded-[8px] px-4 py-2.5 font-mono text-[12px]"
          style={{ color: T.ink, border: `1px solid ${T.stroke}` }}
        >
          Learn more
        </a>
      </div>

      {/* Their "backed by" row, holding the work this rests on instead. */}
      <div className="mx-auto mt-12 w-full max-w-[74rem]">
        <div
          data-anim="rise"
          className="flex flex-wrap items-center justify-between gap-x-10 gap-y-4 border-t pt-7"
          style={{ borderColor: T.stroke }}
        >
          <p className="font-geist text-[13px]" style={{ color: T.nickel }}>
            Built on prior work in defect prediction and review analytics.
          </p>
          <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
            {PRIOR_WORK.map((name) => (
              <span key={name} className="font-mono text-[12px]" style={{ color: T.grey }}>
                {name}
              </span>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

export function ResourcesGrid() {
  return (
    <section className="v2-ticks border-t px-5 py-14 sm:px-8 sm:py-20 lg:px-12" style={{ borderColor: T.stroke }}>
      <div className="mx-auto w-full max-w-[74rem]">
        <div className="flex flex-wrap items-end justify-between gap-5">
          <h2
            data-anim="rise"
            className="max-w-[20rem] font-geist text-[clamp(1.5rem,3.4vw,2.2rem)] leading-[1.14] font-medium tracking-[-0.03em]"
          >
            Reference &amp; internals
          </h2>
          <a
            data-anim="rise"
            href={REPO}
            className="riffle-btn-ghost inline-flex items-center gap-1.5 rounded-[8px] px-4 py-2.5 font-mono text-[12px]"
            style={{ color: T.ink, border: `1px solid ${T.stroke}` }}
          >
            All docs
          </a>
        </div>

        <div className="mt-10 grid gap-8 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,2fr)]">
          {/* Their `js resources .filter(...)` block: documentation written as
              the query that produced it. */}
          <pre
            data-anim="rise"
            className="overflow-x-auto px-5 py-5 font-mono text-[11px] leading-[1.85]"
            style={{ backgroundColor: T.paper, border: `1px solid ${T.stroke}`, color: T.nickel }}
          >
            <span style={{ color: T.grey }}>ts</span>
            {"\n"}docs{"\n"}
            <span style={{ color: T.accent }}>  .filter</span>
            {"(d => d.settled === "}
            <span style={{ color: T.accent }}>true</span>
            {")"}
            {"\n"}
            <span style={{ color: T.accent }}>  .filter</span>
            {"(d => d.source === "}
            <span style={{ color: T.accent }}>&quot;README&quot;</span>
            {")"}
            {"\n"}
            <span style={{ color: T.accent }}>  .filter</span>
            {"(d => d.sections.length === "}
            <span style={{ color: T.accent }}>3</span>
            {")"}
          </pre>

          <div className="grid gap-px sm:grid-cols-3" style={{ backgroundColor: T.stroke }}>
            {RESOURCES.map((r) => (
              <a
                key={r.kind}
                data-anim="rise"
                href={r.href}
                className="group flex flex-col"
                style={{ backgroundColor: T.beige }}
              >
                <div
                  className="relative h-[116px] overflow-hidden"
                  style={{
                    background: `linear-gradient(140deg, ${r.tone[0]} 0%, ${r.tone[1]} 55%, ${r.tone[0]} 100%)`,
                  }}
                />
                <div className="px-4 py-4">
                  <p className="font-mono text-[10.5px] tracking-[0.06em] uppercase" style={{ color: T.grey }}>
                    // {r.kind}
                  </p>
                  <p className="mt-2 font-geist text-[14px] leading-[1.35]" style={{ color: T.ink }}>
                    {r.title}
                  </p>
                </div>
              </a>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

/* Their page opens on a dark strip with a gradient bleeding in from the
   right, carrying one announcement. Riffle's only true announcement is its
   own status, so that is what it carries. */
export function AnnounceBar() {
  return (
    <a
      href={REPO}
      className="group relative block overflow-hidden"
      style={{ backgroundColor: T.darkBg }}
    >
      <div
        aria-hidden="true"
        className="absolute inset-y-0 right-0 w-[52%]"
        style={{
          background: `linear-gradient(90deg, transparent 0%, rgba(125,211,160,0.14) 45%, rgba(165,180,252,0.3) 100%)`,
        }}
      />
      <div className="relative mx-auto flex w-full max-w-[74rem] items-center gap-2.5 px-5 py-2 sm:px-8 lg:px-12">
        <span className="font-mono text-[10.5px] tracking-[0.1em] uppercase" style={{ color: T.mint }}>
          Riffle
        </span>
        <span
          className="font-mono text-[10.5px] tracking-[0.1em] uppercase"
          style={{ color: "rgba(251,250,247,0.78)" }}
        >
          is in development &mdash; architecture and contracts are settled
        </span>
        <span
          aria-hidden="true"
          className="ml-1 inline-flex size-4 items-center justify-center rounded-full text-[9px] transition-transform group-hover:translate-x-0.5"
          style={{ backgroundColor: "rgba(251,250,247,0.14)", color: T.darkInk }}
        >
          &rarr;
        </span>
      </div>
    </a>
  );
}

/* Closing block: their dark CTA plus gradient banner plus link columns. The
   newsletter is replaced by the repository, because there is no list to
   subscribe anyone to. */
export function ClosingBlock() {
  return (
    <footer style={{ backgroundColor: T.darkBg }}>
      <div className="v2-ticks border-t" style={{ borderColor: T.stroke }}>
        <div className="mx-auto w-full max-w-[74rem] px-5 py-14 sm:px-8 sm:py-20 lg:px-12">
          <div className="flex flex-wrap items-end justify-between gap-8">
            <h2
              data-anim="rise"
              className="max-w-[24rem] font-geist text-[clamp(1.6rem,3.6vw,2.4rem)] leading-[1.12] font-medium tracking-[-0.03em]"
              style={{ color: T.darkInk }}
            >
              Interested in where your repository breaks?
            </h2>
            <div data-anim="rise" className="flex flex-wrap items-center gap-3">
              <a
                href={REPO}
                className="riffle-btn inline-flex items-center gap-2 rounded-[8px] px-4 py-2.5 font-mono text-[12.5px] transition-transform"
                style={{ backgroundColor: T.darkInk, color: T.darkBg }}
              >
                Watch the repo
              </a>
              <a
                href={`${REPO}/issues`}
                className="riffle-btn-ghost inline-flex items-center gap-1.5 rounded-[8px] px-4 py-2.5 font-mono text-[12.5px]"
                style={{ color: T.darkInk, border: `1px solid ${T.darkStroke}` }}
              >
                Open an issue
              </a>
            </div>
          </div>

          {/* Their gradient banner with the mark centred in it. */}
          <div
            data-anim="rise"
            className="relative mt-12 flex h-[140px] items-center justify-center overflow-hidden rounded-[4px] sm:h-[180px]"
            style={{
              background:
                "linear-gradient(115deg, #1f1a14 0%, #2b3a30 35%, #7dd3a0 68%, #a5b4fc 100%)",
            }}
          >
            <span
              className="flex size-11 items-center justify-center rounded-[8px] font-mono text-[15px]"
              style={{ backgroundColor: T.darkBg, color: T.darkInk }}
            >
              R
            </span>
          </div>
        </div>
      </div>

      <div className="border-t" style={{ borderColor: T.darkStroke }}>
        <div className="mx-auto grid w-full max-w-[74rem] gap-10 px-5 py-12 sm:grid-cols-3 sm:px-8 lg:px-12">
          {FOOTER_LINKS.map(([heading, links]) => (
            <div key={heading}>
              <p
                className="font-mono text-[10.5px] tracking-[0.12em] uppercase"
                style={{ color: T.darkGrey }}
              >
                {heading}
              </p>
              <ul className="mt-4 flex flex-col gap-2.5">
                {links.map(([label, href]) => (
                  <li key={label}>
                    <a
                      href={href}
                      className="v2-navlink-dark font-geist text-[13.5px]"
                      style={{ color: T.darkNickel }}
                    >
                      {label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div
          className="mx-auto w-full max-w-[74rem] border-t px-5 py-6 sm:px-8 lg:px-12"
          style={{ borderColor: T.darkStroke }}
        >
          <p className="font-mono text-[11px]" style={{ color: T.darkGrey }}>
            &copy; 2026 Riffle contributors. Apache-2.0.
          </p>
        </div>
      </div>
    </footer>
  );
}
