"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import gsap from "gsap";
import { logoUrl } from "./logo";
import { T } from "./tokens";

// The hero stack.
//
// The lift walks the stack from the moment it renders. The live layer is
// solid with textured sides; the rest become fully transparent wireframes, so
// a live layer underneath shows at full strength straight through them.
//
// Micro-motion, all of it cheap (transforms, opacity, dash offsets):
//   - dotted leader lines flow toward the stack
//   - the live leader draws itself in from the pill, and a spark runs it
//   - the live slab's texture drifts; its outline carries a soft glow
//   - the live pill springs up; hovering any pill takes over the walk


const CX = 500;
const W = 200;
const TAN30 = Math.tan(Math.PI / 6);
const D = 30;
const STEP = 46;
const TOP = 176;
const R = 26;
const LIFT = 28;
const CYCLE_MS = 2800;


type Layer = {
  key: string;
  domain: string | null; // logo.dev domain for the language mark
  side: "left" | "right";
  caption: string;
  tone: readonly [string, string];
  art: string;
};

const LAYERS: readonly Layer[] = [
  { key: "app", domain: "typescriptlang.org", side: "left", caption: "the only human surface", tone: ["#e0a45e", "#8a5326"], art: "/v2/art/streak-orange.webp" },
  { key: "explainer", domain: "python.org", side: "right", caption: "allowed to fail", tone: ["#c4b5fd", "#6d4fb8"], art: "/v2/art/streak-violet.webp" },
  { key: "scorer", domain: "python.org", side: "left", caption: "one model per repository", tone: ["#a5b4fc", "#4453b5"], art: "/v2/art/streak-indigo.webp" },
  { key: "intake", domain: "go.dev", side: "right", caption: "inside a 10s budget", tone: ["#7dd3a0", "#2f6f52"], art: "/v2/art/streak-green.webp" },
  { key: "contracts", domain: null, side: "left", caption: "the source of truth", tone: ["#6ee7d8", "#1f7a70"], art: "/v2/art/streak-teal.webp" },
];

type Pt = [number, number];

function roundedDiamond(cx: number, y: number, w: number, r: number) {
  const h = w * TAN30;
  const V: Pt[] = [
    [cx - w, y],
    [cx, y - h],
    [cx + w, y],
    [cx, y + h],
  ];
  const t = r / Math.hypot(w, h);
  const SAMPLES = 10;
  const pts: Pt[] = [];
  const mids: number[] = [];
  for (let i = 0; i < 4; i++) {
    const v = V[i];
    const prev = V[(i + 3) % 4];
    const next = V[(i + 1) % 4];
    const a: Pt = [v[0] + (prev[0] - v[0]) * t, v[1] + (prev[1] - v[1]) * t];
    const b: Pt = [v[0] + (next[0] - v[0]) * t, v[1] + (next[1] - v[1]) * t];
    for (let s = 0; s <= SAMPLES; s++) {
      const u = s / SAMPLES;
      if (s === SAMPLES / 2) mids.push(pts.length);
      pts.push([
        (1 - u) ** 2 * a[0] + 2 * (1 - u) * u * v[0] + u ** 2 * b[0],
        (1 - u) ** 2 * a[1] + 2 * (1 - u) * u * v[1] + u ** 2 * b[1],
      ]);
    }
  }
  return { pts, leftMid: mids[0], rightMid: mids[2] };
}

const path = (pts: Pt[], close = false) =>
  pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ") + (close ? " Z" : "");

function lowerHalf(cx: number, y: number, w: number, r: number): Pt[] {
  const { pts, leftMid, rightMid } = roundedDiamond(cx, y, w, r);
  return [...pts.slice(rightMid), ...pts.slice(0, leftMid + 1)];
}

function Box({
  y,
  w,
  depth,
  r,
  sideFill,
  stroke,
  ghost = false,
  glow = false,
}: {
  y: number;
  w: number;
  depth: number;
  r: number;
  sideFill: string;
  stroke: string;
  ghost?: boolean;
  glow?: boolean;
}) {
  const top = roundedDiamond(CX, y, w, r);
  const upper = lowerHalf(CX, y, w, r);
  const lower = lowerHalf(CX, y + depth, w, r);
  const [lx] = upper[upper.length - 1];
  const [rx] = upper[0];
  const line = {
    stroke: ghost ? T.grey : stroke,
    strokeWidth: ghost ? 1 : glow ? 2.2 : 1.4,
    strokeDasharray: ghost ? "2 3" : undefined,
    strokeOpacity: ghost ? 0.7 : 1,
    filter: glow ? "url(#glow)" : undefined,
    style: { transition: "stroke 400ms ease, stroke-opacity 400ms ease" },
  } as const;
  return (
    <>
      {/* Ghosts carry no fill at all, so whatever is live beneath them shows
          at full strength — they are outlines, not panes of frosted glass. */}
      <path
        d={path([...upper, ...[...lower].reverse()], true)}
        fill={sideFill}
        fillOpacity={ghost ? 0 : 1}
        style={{ transition: "fill-opacity 400ms ease" }}
      />
      <path d={path(lower)} fill="none" strokeLinejoin="round" {...line} />
      <line x1={lx} y1={y} x2={lx} y2={y + depth} {...line} />
      <line x1={rx} y1={y} x2={rx} y2={y + depth} {...line} />
      <path
        d={path(top.pts, true)}
        fill="#ffffff"
        fillOpacity={ghost ? 0 : 1}
        strokeLinejoin="round"
        {...line}
        style={{ transition: "fill-opacity 400ms ease, stroke 400ms ease" }}
      />
    </>
  );
}

export function Stack() {
  const root = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(-1);
  const [ready, setReady] = useState(false);
  const hovering = useRef(false);

  // On mount: first layer live, labels in. The stack itself stays still.
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    setReady(true);
    setActive(0);
  }, []);

  // The walk. Hovering a pill takes over; leaving hands back to the timer.
  useEffect(() => {
    if (!ready) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const id = setInterval(() => {
      if (!hovering.current) setActive((a) => (a + 1) % LAYERS.length);
    }, CYCLE_MS);
    return () => clearInterval(id);
  }, [ready]);

  // A spark runs the live leader from the pill into the slab.
  useEffect(() => {
    if (active < 0 || !root.current) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const spark = root.current.querySelector<SVGCircleElement>("[data-spark]");
    const l = LAYERS[active];
    if (!spark) return;
    const from = l.side === "left" ? 196 : 804;
    const to = l.side === "left" ? CX - W - 8 : CX + W + 8;
    const y = TOP + active * STEP - LIFT + D / 2;
    gsap.fromTo(
      spark,
      { attr: { cx: from, cy: y }, opacity: 0 },
      { attr: { cx: to }, opacity: 1, duration: 0.7, ease: "power2.inOut", onComplete: () => void gsap.to(spark, { opacity: 0, duration: 0.25 }) },
    );
  }, [active]);

  const take = useCallback((i: number) => {
    hovering.current = true;
    setActive(i);
  }, []);
  const release = useCallback(() => {
    hovering.current = false;
  }, []);

  const offset = (i: number) => (active >= 0 && i <= active ? -LIFT : 0);
  const restY = (i: number) => TOP + i * STEP;
  const edgeY = (i: number) => restY(i) + offset(i) + D / 2;

  return (
    <div ref={root} className="relative mx-auto w-full max-w-[60rem]" style={{ aspectRatio: "1000 / 600" }}>
      <svg
        viewBox="0 0 1000 600"
        className="absolute inset-0 h-full w-full overflow-visible"
        role="img"
        aria-label="Riffle's architecture as a stack: app, explainer, scorer and intake resting on shared contracts."
      >
        <defs>
          {/* One texture per layer in user-space units, sized to that slab.
              Bounding-box units would fail on the vertical edge lines (zero
              width), and the texture has to paint strokes as well as sides:
              the live slab's outline is the image itself, not a colour. The
              pattern sits inside the slab's transformed group, so it rides
              along when the slab lifts. */}
          {LAYERS.map((l, i) => {
            const y0 = TOP + i * STEP - W * TAN30 - 4;
            return (
              <pattern
                key={l.key}
                id={`tex-${l.key}`}
                patternUnits="userSpaceOnUse"
                x={CX - W - 4}
                y={y0}
                width={2 * W + 8}
                height={2 * W * TAN30 + D + 8}
              >
                <image
                  className="v2-drift"
                  href={l.art}
                  x="-40"
                  width={2 * W + 88}
                  height={2 * W * TAN30 + D + 8}
                  preserveAspectRatio="xMidYMid slice"
                />
              </pattern>
            );
          })}
          {/* User-space region. The default sizes the filter to each
              element's bounding box, and a vertical <line> has zero width —
              so the live slab's side edges were filtered out of existence. */}
          <filter id="glow" filterUnits="userSpaceOnUse" x="0" y="0" width="1000" height="600">
            <feGaussianBlur stdDeviation="2.2" result="b" />
            <feMerge>
              <feMergeNode in="b" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <g>

          <g data-leads>
            {LAYERS.map((l, i) => {
              const live = i === active;
              const left = l.side === "left";
              const x1 = left ? 196 : 804;
              const x2 = left ? CX - W - 8 : CX + W + 8;
              const len = Math.abs(x2 - x1);
              return (
                <g key={l.key}>
                  {/* Dotted rest line, flowing toward the stack. */}
                  <line
                    className={left ? "v2-flow-r" : "v2-flow-l"}
                    x1={x1}
                    x2={x2}
                    y1={edgeY(i)}
                    y2={edgeY(i)}
                    stroke={T.grey}
                    strokeWidth="1"
                    strokeDasharray="1.5 4"
                    strokeLinecap="round"
                    style={{ opacity: live ? 0 : 0.55, transition: "opacity 400ms ease, all 700ms cubic-bezier(.2,.8,.2,1)" }}
                  />
                  {/* Live line: draws itself in from the pill. */}
                  <line
                    x1={x1}
                    x2={x2}
                    y1={edgeY(i)}
                    y2={edgeY(i)}
                    stroke={l.tone[1]}
                    strokeWidth="1.4"
                    strokeLinecap="round"
                    strokeDasharray={len}
                    strokeDashoffset={live ? 0 : len}
                    style={{ transition: "stroke-dashoffset 650ms cubic-bezier(.65,0,.35,1), all 700ms cubic-bezier(.2,.8,.2,1)" }}
                  />
                </g>
              );
            })}
            <circle data-spark r="3" cx="0" cy="0" fill={active >= 0 ? LAYERS[active].tone[0] : T.grey} opacity="0" filter="url(#glow)" />
          </g>

          {[...LAYERS.keys()].reverse().map((i) => {
            const l = LAYERS[i];
            const live = i === active;
            return (
              <g key={l.key} data-slab>
                <g style={{ transform: `translateY(${offset(i)}px)`, transition: "transform 700ms cubic-bezier(.2,.8,.2,1)" }}>
                  <Box
                    y={restY(i)}
                    w={W}
                    depth={D}
                    r={R}
                    sideFill={live ? `url(#tex-${l.key})` : "#ffffff"}
                    stroke={live ? `url(#tex-${l.key})` : T.ink}
                    ghost={active >= 0 && !live}
                    glow={live}
                  />
                  <text
                    transform={`matrix(0.866 0.5 -0.866 0.5 ${CX} ${restY(i)})`}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    className="font-mono"
                    fontSize="20"
                    letterSpacing="1"
                    fill={live ? l.tone[1] : T.grey}
                    style={{ opacity: live ? 1 : active >= 0 ? 0 : 0.5, transition: "opacity 400ms ease, fill 400ms ease" }}
                  >
                    {l.key}
                  </text>
                </g>
              </g>
            );
          })}

        </g>
      </svg>

      {/* Pills: minimal, no brackets, centred on their line's y; caption
          hangs off the pill so it can't push it off the line. Hover takes
          over the walk. */}
      {LAYERS.map((l, i) => {
        const live = i === active;
        const left = l.side === "left";
        const src = l.domain ? logoUrl(l.domain, { size: 32 }) : null;
        return (
          <div
            key={l.key}
            className="absolute"
            onMouseEnter={() => take(i)}
            onMouseLeave={release}
            style={{
              // Lines end at x=196 / 804 (of 1000); pills sit 6 units short
              // of that so there is air between line and pill.
              left: left ? "19%" : "81%",
              top: `${(edgeY(i) / 600) * 100}%`,
              transform: left ? "translate(-100%, -50%)" : "translate(0, -50%)",
              transformOrigin: left ? "right center" : "left center",
              opacity: ready ? 1 : 0,
              transition:
                "top 700ms cubic-bezier(.2,.8,.2,1), opacity 500ms ease, transform 450ms cubic-bezier(.34,1.56,.64,1)",
            }}
          >
            {/* A proper pill: white fill, 1px hairline, fully rounded, with
                even padding around a mark and a word. Live darkens the
                outline and the ink; the drawn leader does the rest. */}
            <span
              className="inline-flex cursor-default items-center gap-[7px] rounded-full py-[5px] pr-3 pl-2.5 font-mono text-[10px] leading-none tracking-[0.1em] whitespace-nowrap uppercase"
              style={{
                backgroundColor: "#ffffff",
                outline: `1px solid ${live ? "rgba(22,23,29,0.24)" : T.stroke}`,
                boxShadow: live ? "0 2px 6px -2px rgba(22,23,29,0.12)" : "none",
                color: live ? T.ink : T.grey,
                transition: "color 400ms ease, outline-color 400ms ease, box-shadow 400ms ease",
              }}
            >
              {src ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={src}
                  alt=""
                  width={12}
                  height={12}
                  className="rounded-[2px]"
                  style={{ filter: live ? "none" : "grayscale(1)", opacity: live ? 1 : 0.6, transition: "filter 400ms ease, opacity 400ms ease" }}
                />
              ) : (
                <span style={{ color: live ? l.tone[1] : T.grey }}>{"{}"}</span>
              )}
              {l.key}
            </span>
            <span
              className="absolute top-full mt-2 font-geist text-[13px] whitespace-nowrap"
              style={{
                [left ? "right" : "left"]: 0,
                color: T.grey,
                opacity: live ? 1 : 0,
                transform: `translateY(${live ? 0 : -3}px)`,
                transition: "opacity 400ms ease, transform 400ms ease",
              }}
            >
              {l.caption}
            </span>
          </div>
        );
      })}
    </div>
  );
}
