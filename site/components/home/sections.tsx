import { Mark } from "@/components/brand";
import { CorpusCarousel } from "./corpus-carousel";
import { logoUrl } from "./logo";
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

/* Their customer-logo carousel slot, holding the training corpus. */
export function Provenance() {
  return (
    <section
      className="v2-wrapper v2-ticks flex flex-col items-center justify-center gap-6 border-t pt-10 pb-10 md:pt-14"
      style={{ borderColor: T.stroke }}
    >
      <p data-anim="rise" className="text-center font-geist text-[16px] text-balance" style={{ color: T.nickel }}>
        Planned training corpus: 50 public repositories, then yours
      </p>
      <div data-anim="rise" className="w-full max-w-[60rem]">
        <CorpusCarousel />
      </div>
    </section>
  );
}

/* Dark product block, to the reference's structure: a short label row with
   the rule beneath it (no rule above — the block starts flush), a tall
   header band, then a rail with its own right border beside the rows. */
// Language marks come from logo.dev by each language's home domain.
const LANG_DOMAIN: Record<string, string> = {
  go: "go.dev",
  python: "python.org",
  typescript: "typescriptlang.org",
};

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
                  data-scroll-to
                  className="flex items-center gap-3 font-geist text-[16px]"
                  style={{ color: T.darkInk }}
                >
                  <span className="flex items-center gap-[3px] font-mono text-[15px]" style={{ color: T.darkGrey }}>
                    (
                    {logoUrl(LANG_DOMAIN[s.icon], { size: 40 }) && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img
                        src={logoUrl(LANG_DOMAIN[s.icon], { size: 40 })!}
                        alt=""
                        width={16}
                        height={16}
                        className="rounded-[3px]"
                      />
                    )}
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
              className="grid w-full grid-cols-[minmax(0,1fr)] lg:grid-cols-2"
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

function Artwork({
  tone,
  code,
  art,
  dense = false,
}: {
  tone: readonly string[];
  code: readonly CodeLine[];
  art: string;
  /** Long schema lines: smaller type below xl, and a horizontal scroll as the
   *  last resort, so code is never clipped mid-line. */
  dense?: boolean;
}) {
  if (dense) {
    return (
      <div
        data-anim="rise"
        className="flex min-h-[20rem] flex-col pt-10 pl-6 md:pl-10"
        style={{
          backgroundColor: tone[1],
          backgroundImage: `url(${art})`,
          backgroundSize: "cover",
          backgroundPosition: "center",
        }}
      >
        {/* Anchored to the bottom-right corner: flush with the row's floor
            and the wall, with the same 40px of texture showing above and to
            the left. It stretches to fill the row, so two contracts of
            different lengths still read as the same object. Rim on the top
            and left only — the two edges that face the texture. */}
        <div
          className="flex flex-1 flex-col rounded-tl-[9px] pt-px pl-px"
          style={{
            borderTop: "1px solid rgba(255,255,255,0.55)",
            borderLeft: "1px solid rgba(255,255,255,0.55)",
            boxShadow: "-10px -10px 24px -14px rgba(255,255,255,0.28)",
          }}
        >
          <div className="flex-1 rounded-tl-[7px] px-5 py-7 xl:px-7" style={{ backgroundColor: "#1a1b20" }}>
            {/* On phones long lines scroll sideways; the fade at the right
                edge says so, where a hard cut would read as a clipping bug. */}
            <pre className="v2-noscrollbar overflow-x-auto font-mono text-[11px] leading-[1.9] max-sm:[mask-image:linear-gradient(90deg,#000_82%,transparent)] xl:text-[13px] xl:leading-[2]">
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
        className="absolute top-1/2 right-0 left-6 -translate-y-1/2 rounded-l-[9px] py-px pr-0 pl-px md:left-10"
        style={{
          // No right rim and no right gap: the card runs straight into the
          // section's wall, so no sliver of texture shows at that edge.
          border: "1px solid rgba(255,255,255,0.55)",
          borderRight: "none",
          boxShadow: "0 0 14px rgba(255,255,255,0.22), 0 24px 50px -20px rgba(0,0,0,0.6)",
        }}
      >
        <div className="rounded-l-[7px] px-7 py-7" style={{ backgroundColor: "#1a1b20" }}>
          <pre
            className={
              dense
                ? "v2-noscrollbar overflow-x-auto font-mono text-[11px] leading-[1.9] xl:text-[13px] xl:leading-[2]"
                : "overflow-hidden font-mono text-[13px] leading-[2]"
            }
          >
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

/* Statistics: heading, a 4/6 split of headline figure and a chart, then
   three cells. Every value shows its status from the dataset plan. */
function StatusTag({ status }: { status: string }) {
  return (
    <span
      className="rounded px-1.5 py-0.5 font-mono text-[10.5px] tracking-[0.06em] uppercase"
      style={{ outline: `1px solid ${T.stroke}`, color: T.grey }}
    >
      {status}
    </span>
  );
}

const DOMAIN_COLORS = ["#16171d", "#3b3440", "#867e8e", "#b9b3bf", "#00b442"];

export function StatsBand() {
  const total = STATS.domains.reduce((n, [, v]) => n + v, 0);
  // No top rule: coming out of the dark block the page goes straight into
  // this heading, and the first rule sits beneath it — as the reference does.
  return (
    <section className="v2-wrapper">
      <div className="px-6 pt-10 pb-10 md:px-10 md:pt-14">
        <h3 data-anim="rise" className={`${H3} max-w-[28rem]`}>
          {STATS.headline}
        </h3>
      </div>
      {/* Thirds, not 4/6: the figure takes one third and the chart two, so
          this row's divider lands on the first divider of the three cells
          below and the verticals run unbroken through the band. */}
      <div className="grid border-t lg:grid-cols-3 lg:divide-x" style={{ borderColor: T.stroke }}>
        <div data-anim="rise" className="flex flex-col justify-between gap-16 p-6 md:p-10 lg:col-span-1">
          <div className="flex items-center gap-3">
            <p className="font-geist text-[15px]" style={{ color: T.nickel }}>
              Pull requests in the corpus
            </p>
            <StatusTag status={STATS.bigStatus} />
          </div>
          <div>
            <p className="font-geist text-[2.75rem] leading-none font-medium tracking-[-0.04em] md:text-[3.5rem]">
              {/* Approximate: PR tabs were read over July-September and the
                  counts keep moving. Outside the counting span so the
                  count-up, which rewrites that span's text, cannot drop it. */}
              <span aria-label="approximately" className="mr-1" style={{ color: T.grey }}>
                ~
              </span>
              <span data-count={STATS.big}>{STATS.big.toLocaleString("en-US")}</span>
            </p>
            <p className="mt-3 max-w-[20rem] font-geist text-[14px] leading-[1.5]" style={{ color: T.grey }}>
              {STATS.bigLabel}
            </p>
          </div>
        </div>

        {/* By domain, to scale — the measured subtotals as one stacked bar.
            The legend runs in segment order, and each entry (swatch, name,
            share) is one unbreakable unit that wraps whole: in a fixed grid,
            long names split across lines and their percentages were pushed
            to the far edge of the cell, away from the name they belong to. */}
        <div
          data-anim="rise"
          className="flex flex-col justify-between gap-8 border-t p-6 md:p-10 lg:col-span-2 lg:border-t-0"
          style={{ borderColor: T.stroke }}
        >
          <div className="flex items-center gap-3">
            <p className="w-fit rounded px-2 py-1 font-mono text-[12px]" style={{ outline: `1px solid ${T.stroke}`, color: T.nickel }}>
              By domain
            </p>
            <StatusTag status="measured" />
          </div>
          <div>
            <div className="flex h-24 w-full gap-[3px]" role="img" aria-label="Pull requests by domain">
              {STATS.domains.map(([name, v], i) => (
                <div
                  key={name}
                  className="h-full rounded-[3px] transition-opacity duration-150 hover:opacity-85"
                  style={{ width: `${(v / total) * 100}%`, backgroundColor: DOMAIN_COLORS[i] }}
                  title={`${name}: ${v.toLocaleString("en-US")} PRs`}
                />
              ))}
            </div>
            <ul className="mt-5 flex flex-wrap gap-x-7 gap-y-2.5">
              {STATS.domains.map(([name, v], i) => (
                <li key={name} className="flex items-center gap-2 font-mono text-[12px] whitespace-nowrap">
                  <span className="size-2 shrink-0 rounded-[2px]" style={{ backgroundColor: DOMAIN_COLORS[i] }} />
                  <span style={{ color: T.nickel }}>{name}</span>
                  <span className="tabular-nums" style={{ color: T.grey }}>
                    {Math.round((v / total) * 100)}%
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
      <div className="grid border-t sm:grid-cols-3 sm:divide-x" style={{ borderColor: T.stroke }}>
        {STATS.cells.map(([value, label, status]) => (
          <div key={label} data-anim="rise" className="flex flex-col gap-3 p-6 md:p-10">
            {/* The value never breaks (100–300 split at its dash in a narrow
                cell); the tag wraps beneath it instead. */}
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <p className="font-geist text-[2rem] leading-none font-medium tracking-[-0.03em] whitespace-nowrap">{value}</p>
              <StatusTag status={status} />
            </div>
            <p className="font-geist text-[14px]" style={{ color: T.grey }}>
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
      <section className="v2-wrapper v2-ticks grid grid-cols-2 border-t md:grid-cols-3 lg:grid-cols-6 lg:divide-x" style={{ borderColor: T.stroke }}>
        <p className="col-span-full flex items-center p-6 font-geist text-[14px] lg:col-span-1 lg:p-8" style={{ color: T.nickel }}>
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

/* JSON source to highlighted lines, for the contract cards. A quoted string
   followed by a colon is a key; any other quoted string is a value; numbers
   are values; structure is dim. Enough for schema examples, which is all it
   has to read. */
function jsonLines(src: string): CodeLine[] {
  const re = /("(?:[^"\\]|\\.)*")(\s*:)?|(-?\d+(?:\.\d+)?)|([{}\[\],])|(\s+)|([^\s"{}\[\],]+)/g;
  return src.split("\n").map((line) => {
    const out: [string, TokenKind][] = [];
    for (const m of line.matchAll(re)) {
      if (m[1] && m[2]) {
        out.push([m[1], "key"]);
        out.push([m[2], "dim"]);
      } else if (m[1]) out.push([m[1], "str"]);
      else if (m[3]) out.push([m[3], "val"]);
      else if (m[4]) out.push([m[4], "dim"]);
      else out.push([m[0], "dim"]);
    }
    return out;
  });
}

const CONTRACTS = [
  {
    id: "pr-event",
    route: "intake → scorer",
    name: "PrEvent",
    body: "Published by intake the moment a webhook verifies — one per GitHub delivery. Its delivery_id is the idempotency key for the entire pipeline.",
    facts: ["7 fields, all required", "delivery_id dedupes"],
    code: PR_EVENT,
    tone: ["#6ee7d8", "#1f7a70"] as const,
    art: "/art/streak-teal.webp",
  },
  {
    id: "score-result",
    route: "scorer → app",
    name: "ScoreResult",
    body: "Written by scorer, read by the app. The model version is pinned per request, and the feature vector travels with the score so every rank can be audited.",
    facts: ["explanation is nullable", "model_version pinned"],
    code: SCORE_RESULT,
    tone: ["#a5b4fc", "#4453b5"] as const,
    art: "/art/streak-indigo.webp",
  },
] as const;

/* Contracts, as a second dark block built exactly like services: starts
   flush, a short label row with the rule beneath it, a tall header band,
   then a two-up row per contract — copy on the left, the schema itself on
   the right as a highlighted card anchored into its texture. */
export function ContractsSection() {
  return (
    <div data-surface="dark" style={{ backgroundColor: T.darkBg }}>
      <section id="contracts" className="v2-wrapper px-6 py-5 md:px-10">
        <p className="font-geist text-[16px] font-medium" style={{ color: T.darkInk }}>
          Contracts
        </p>
      </section>

      <section className="v2-wrapper v2-ticks border-t px-6 py-16 md:px-10 md:py-24" style={{ borderColor: T.nickel }}>
        <h2 data-anim="rise" className={H2} style={{ color: T.darkInk }}>
          Two shapes cross every boundary
        </h2>
        <p
          data-anim="rise"
          className="mt-6 max-w-[27rem] font-geist text-[18px] leading-[1.55] text-pretty"
          style={{ color: T.darkNickel }}
        >
          Three languages read these schemas and nothing else defines the wire
          format. Any change to one is a breaking change.
        </p>
      </section>

      <section className="v2-wrapper v2-ticks border-t" style={{ borderColor: T.nickel }}>
        {CONTRACTS.map((c, i) => (
          <div
            key={c.id}
            id={c.id}
            className="grid w-full grid-cols-[minmax(0,1fr)] lg:grid-cols-2 lg:divide-x"
            style={{ borderTop: i === 0 ? undefined : `1px solid ${T.nickel}` }}
          >
            <div data-anim="rise" className="flex flex-col justify-between gap-10 p-6 md:p-10 lg:gap-20">
              <div className="flex max-w-[24rem] flex-col gap-5">
                <span className="font-mono text-[14px] tracking-[0.08em] uppercase" style={{ color: T.darkGrey }}>
                  {c.route}
                </span>
                <h4
                  className="font-mono text-[1.75rem] leading-[1.15] font-medium tracking-[-0.03em]"
                  style={{ color: T.darkInk }}
                >
                  {c.name}
                </h4>
                <p className="font-geist text-[16px] leading-[1.55] text-pretty" style={{ color: T.darkNickel }}>
                  {c.body}
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {c.facts.map((f) => (
                  <span
                    key={f}
                    className="rounded-sm px-3 py-1 font-mono text-[12px]"
                    style={{ backgroundColor: "rgba(255,255,255,0.06)", color: T.darkNickel }}
                  >
                    {f}
                  </span>
                ))}
              </div>
            </div>
            <Artwork tone={c.tone} code={jsonLines(c.code)} art={c.art} dense />
          </div>
        ))}
      </section>
    </div>
  );
}

/* Resources: their 4/6 split. Left column is heading-plus-button over the
   .filter() device; right column is the card row. */
export function ResourcesGrid() {
  return (
    // Follows the dark contracts block, so no rule at the boundary — the edge
    // of the dark surface is the break, as after services.
    <section className="v2-wrapper grid grid-cols-1 lg:grid-cols-10 lg:divide-x">
      <div className="flex flex-col divide-y lg:col-span-4" style={{ borderColor: T.stroke }}>
        <div className="flex flex-col justify-center gap-6 p-5 md:p-10 lg:h-72 lg:justify-start lg:gap-10" style={{ borderColor: T.stroke }}>
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

      <div className="grid border-t sm:grid-cols-3 lg:col-span-6 lg:border-t-0" style={{ borderColor: T.stroke }}>
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

/* Dark footer, to the reference's markup: one wrapper holding the CTA grid,
   the banner at a 6.4:1 aspect, and the link row; then a ticked wrapper for
   the copyright. Its screen-filling height comes from the padding (py-30
   around the CTA, pb-40 under the links), not from a viewport unit.

   Their right-hand column is a newsletter form. Riffle has no list, so the
   same bordered box holds the repository address and a button that goes
   there — the shape of theirs, saying something true. */
const GITHUB_PATH =
  "M8 0a8 8 0 0 0-2.53 15.59c.4.07.55-.17.55-.38v-1.34c-2.23.48-2.7-1.07-2.7-1.07-.36-.93-.89-1.18-.89-1.18-.73-.5.05-.49.05-.49.8.06 1.23.83 1.23.83.72 1.23 1.88.87 2.34.67.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.03 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.28.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48v2.19c0 .21.14.46.55.38A8 8 0 0 0 8 0Z";

export function ClosingBlock() {
  return (
    // At least a full viewport: the link row grows to take the slack, so the
    // copyright rule always sits on the bottom edge on any screen height.
    <footer data-surface="dark" className="flex min-h-dvh flex-col" style={{ backgroundColor: T.darkBg }}>
      <section className="v2-wrapper flex w-full flex-1 flex-col">
        <div className="grid grid-cols-1 items-center gap-10 px-5 py-10 text-center md:px-10 md:py-[7.5rem] lg:grid-cols-2 lg:items-start lg:gap-16 lg:text-left">
          <div className="mx-auto max-w-xl lg:mx-0">
            {/* Two lines on a fixed 60px rhythm (lg), matching the two rows on
                the right: line one sits level with the label, line two with
                the field. */}
            <h2 data-anim="rise" className={`${H2} text-center lg:text-left lg:!leading-[3.75rem]`} style={{ color: "#fff" }}>
              <span className="lg:block">Interested in where </span>
              <span className="lg:block">your repository breaks?</span>
            </h2>
          </div>
          <div
            data-anim="rise"
            className="mx-auto flex w-full max-w-md flex-col items-center gap-4 lg:mx-0 lg:gap-0 lg:items-stretch lg:justify-self-end"
          >
            <h3
              className="text-center font-geist text-[22px] font-normal md:text-[28px] lg:flex lg:h-[3.75rem] lg:items-center lg:text-left"
              style={{ color: "#fff" }}
            >
              Follow the build on GitHub
            </h3>
            {/* Exactly one 60px row, so it sits level with heading line two. */}
            <div
              className="relative flex items-center overflow-hidden rounded border transition-colors hover:bg-white/10 lg:h-[3.75rem]"
              style={{ borderColor: "rgba(255,255,255,0.2)" }}
            >
              <span
                className="flex-1 truncate px-3 py-3 text-left font-geist text-[15px] sm:px-5 sm:py-4 sm:text-[18px]"
                style={{ color: "rgba(255,255,255,0.55)" }}
              >
                github.com/ru-dr/riffle
              </span>
              <a
                href={REPO}
                className="mr-1.5 shrink-0 rounded px-3 py-2 font-geist text-[15px] font-medium whitespace-nowrap transition-colors hover:bg-white/90 sm:mr-2 sm:px-6 sm:py-2.5 sm:text-[16px]"
                style={{ backgroundColor: "#fff", color: T.ink }}
              >
                Watch repo
              </a>
            </div>
          </div>
        </div>

        <div
          data-anim="rise"
          className="relative flex aspect-[4] w-full items-center justify-center md:aspect-[6.4]"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/art/rays-purple.webp"
            alt=""
            loading="lazy"
            className="absolute inset-0 z-0 h-full w-full object-cover"
          />
          {/* The mark tile, edged like the terminal cards: the tile has no
              border of its own; 1px out, a gap where the banner shows
              through; 1px beyond that, a rim lit at two opposite corners and
              dark between them, with a blurred copy underneath as the glow. */}
          <span className="relative flex size-12 items-center justify-center md:size-20">
            <span aria-hidden="true" className="v2-rim v2-rim-glow absolute -inset-[2px] rounded-[14px] md:rounded-[20px]" />
            <span aria-hidden="true" className="v2-rim absolute -inset-[2px] rounded-[14px] md:rounded-[20px]" />
            <span
              className="relative flex size-full items-center justify-center rounded-[12px] md:rounded-[18px]"
              style={{ backgroundColor: "#0d0c10", boxShadow: "0 12px 30px -8px rgba(0,0,0,0.6)" }}
            >
              <Mark aria-hidden="true" className="h-auto w-7 md:w-11" style={{ color: "#fff" }} />
            </span>
          </span>
        </div>

        <div className="flex flex-1 flex-col items-center justify-center gap-10 px-5 pt-10 pb-16 text-center md:flex-row md:items-start md:justify-between md:gap-0 md:px-24 md:pt-16 md:pb-24 md:text-left">
          <div className="flex flex-col items-center gap-10 md:flex-row md:items-start md:gap-20">
            {FOOTER_LINKS.map(([heading, links]) => (
              <div key={heading}>
                <p className="mb-8 font-mono text-[13px] tracking-[0.03em] uppercase" style={{ color: T.grey }}>
                  {heading}
                </p>
                <ul className="flex flex-col gap-4">
                  {links.map(([label, href]) => (
                    <li key={label}>
                      <a href={href} className="v2-link font-geist text-[18px]" style={{ color: "#fff" }}>
                        {label}
                      </a>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
          <div className="flex flex-col items-center md:items-start">
            <p className="mb-8 font-mono text-[13px] tracking-[0.03em] uppercase" style={{ color: T.grey }}>
              Social
            </p>
            <ul className="flex flex-col gap-4">
              <li>
                <a href={REPO} className="v2-link flex items-center gap-3 font-geist text-[18px]" style={{ color: "#fff" }}>
                  <svg viewBox="0 0 16 16" className="size-[18px]" fill="currentColor" aria-hidden="true">
                    <path d={GITHUB_PATH} />
                  </svg>
                  GitHub
                </a>
              </li>
            </ul>
          </div>
        </div>
      </section>

      <section
        // w-full: inside the footer's flex column an auto-margined wrapper
        // shrinks to its text, which cut the rule and ticks down to a stub.
        className="v2-wrapper v2-ticks flex w-full flex-col items-center justify-between gap-3 border-t px-5 py-5 text-center md:flex-row md:gap-0 md:px-24 md:text-left"
        style={{ borderColor: T.nickel }}
      >
        <p className="font-geist text-[15px]" style={{ color: T.grey }}>
          &copy; 2026 Riffle contributors. <span className="hidden sm:inline">Apache-2.0.</span>
        </p>
      </section>
    </footer>
  );
}
