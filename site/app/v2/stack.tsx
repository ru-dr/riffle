import { T } from "./tokens";

// The hero centrepiece: voidzero's isometric stack, rebuilt in SVG.
//
// Theirs is a Rive animation of translucent plates with leader lines running
// out to labelled pills, the foundation plate lit and captioned. The shape is
// doing real work — it says "these pieces sit on one base" in a way a list
// of four services cannot — so it is worth reproducing honestly rather than
// approximating with cards.
//
// Plates are drawn in SVG and labels are HTML positioned over them, so the
// text renders in the page's own fonts at the page's own weights. Geometry is
// a plain isometric rhombus per level: left/right vertices are where the
// leader lines terminate, which is why the numbers are shared between the
// two layers instead of eyeballed twice.

const CX = 500;
const HALF_W = 170; // rhombus half-width — the x of the left/right vertices
const HALF_D = 84; // half-depth, the y of the near/far vertices
const TOP_Y = 170;
const STEP = 34; // vertical gap between plates
const THICK = 20; // extrusion on the base plate only

const LEVELS = [
  { key: "app", side: "right", label: "app", lang: "TS" },
  { key: "explainer", side: "right", label: "explainer", lang: "PY" },
  { key: "scorer", side: "left", label: "scorer", lang: "PY" },
  { key: "intake", side: "left", label: "intake", lang: "GO" },
  { key: "contracts", side: "base", label: "contracts", lang: "JSON" },
] as const;

const y = (i: number) => TOP_Y + i * STEP;
const plate = (i: number) => {
  const yy = y(i);
  return `M${CX - HALF_W} ${yy} L${CX} ${yy - HALF_D} L${CX + HALF_W} ${yy} L${CX} ${yy + HALF_D} Z`;
};
const pct = (v: number, total: number) => `${(v / total) * 100}%`;

export function Stack() {
  const base = LEVELS.length - 1;

  return (
    <div
      data-anim="hero-rise"
      className="relative mx-auto w-full max-w-[60rem]"
      style={{ aspectRatio: "1000 / 560" }}
    >
      <svg
        viewBox="0 0 1000 560"
        className="absolute inset-0 h-full w-full"
        role="img"
        aria-label="Riffle's four services stacked on one set of shared contracts: app and explainer above, scorer and intake below, contracts as the foundation."
      >
        {/* Leader lines first, so plates paint over their inner ends. */}
        {LEVELS.map((l, i) =>
          l.side === "base" ? null : (
            <g key={`lead-${l.key}`} data-anim="lead">
              <line
                x1={l.side === "left" ? 200 : 800}
                y1={y(i)}
                x2={l.side === "left" ? CX - HALF_W : CX + HALF_W}
                y2={y(i)}
                stroke={T.stroke}
                strokeWidth="1"
              />
              <circle
                cx={l.side === "left" ? CX - HALF_W : CX + HALF_W}
                cy={y(i)}
                r="2.5"
                fill={T.grey}
              />
            </g>
          ),
        )}

        {/* Base plate extrusion — the lit foundation. */}
        <path
          data-anim="plate"
          d={`M${CX - HALF_W} ${y(base)} L${CX} ${y(base) + HALF_D} L${CX + HALF_W} ${y(base)} L${CX + HALF_W} ${y(base) + THICK} L${CX} ${y(base) + HALF_D + THICK} L${CX - HALF_W} ${y(base) + THICK} Z`}
          fill={T.accent}
          opacity="0.16"
        />

        {/* Plates, far to near. Opacity climbs toward the base so the stack
            reads as resting on something rather than floating. */}
        {LEVELS.map((l, i) => (
          <path
            key={l.key}
            data-anim="plate"
            d={plate(i)}
            fill={i === base ? T.accent : T.paper}
            fillOpacity={i === base ? 0.1 : 0.72}
            stroke={i === base ? T.accent : T.stroke}
            strokeWidth={i === base ? 1.25 : 1}
          />
        ))}

        {/* The mark, centred on the lit base: what all of it is for. */}
        <g data-anim="plate" transform={`translate(${CX} ${y(base)})`}>
          <circle r="5" fill={T.accent} opacity="0.18" />
          <circle r="2" fill={T.accent} />
        </g>
      </svg>

      {/* Labels as HTML so they use the page's fonts. Positions are derived
          from the same geometry as the lines above. */}
      {LEVELS.map((l, i) =>
        l.side === "base" ? null : (
          <div
            key={`label-${l.key}`}
            data-anim="lead"
            className="absolute flex items-center gap-2"
            style={{
              left: l.side === "left" ? pct(190, 1000) : pct(810, 1000),
              top: pct(y(i), 560),
              transform: l.side === "left" ? "translate(-100%, -50%)" : "translate(0, -50%)",
            }}
          >
            {l.side === "right" && <Dot />}
            <span
              className="v2-chip inline-flex items-center gap-1.5 rounded-[5px] px-2.5 py-1 font-mono text-[11px] tracking-[-0.01em] whitespace-nowrap"
              style={{ color: T.nickel }}
            >
              <span style={{ color: T.grey }}>{l.lang}</span>
              {l.label}
            </span>
            {l.side === "left" && <Dot />}
          </div>
        ),
      )}

      {/* Foundation label: dark pill plus caption, the emphasis their Oxc
          plate gets. */}
      <div
        data-anim="lead"
        className="absolute flex flex-col items-center gap-2"
        style={{ left: "50%", top: pct(y(base) + HALF_D + 34, 560), transform: "translate(-50%, 0)" }}
      >
        <span
          className="inline-flex items-center gap-1.5 rounded-[5px] px-2.5 py-1 font-mono text-[11px] tracking-[-0.01em]"
          style={{ backgroundColor: T.ink, color: T.beige }}
        >
          <span style={{ color: T.accent }}>JSON</span>
          contracts
        </span>
        <span className="font-mono text-[10.5px] tracking-[0.02em]" style={{ color: T.grey }}>
          the source of truth
        </span>
      </div>
    </div>
  );
}

function Dot() {
  return (
    <span
      aria-hidden="true"
      className="size-[3px] shrink-0 rounded-full"
      style={{ backgroundColor: T.grey }}
    />
  );
}
