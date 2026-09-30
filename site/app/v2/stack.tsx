"use client";

import { useEffect, useState } from "react";
import { LangIcon, type Lang } from "./lang-icon";
import { T } from "./tokens";

// The hero stack, rebuilt from the reference render rather than guessed at.
//
// Each layer is a solid slab: a rounded isometric top face, two visible side
// faces giving it thickness, white fill, thin ink outline. They rest one on
// another with a small gap. One slab at a time is lifted out of the stack —
// everything above it rises with it, opening a gap underneath — and its side
// faces fill with that service's colour. The lift walks the stack on a timer,
// so the diagram explains the architecture one layer at a time instead of all
// at once.
//
// Geometry is computed as sampled polylines so the top face, the visible lower
// half of the side faces, and the outlines all come from one shape and cannot
// drift apart.

const CX = 500;
const W = 200; // half-width of the top face
const H = W * Math.tan(Math.PI / 6); // isometric half-depth
const D = 30; // slab thickness
const STEP = 46; // top-to-top distance between resting slabs
const TOP = 176;
const R = 26; // corner radius along each edge
const LIFT = 42;
const CYCLE_MS = 2800;

const LAYERS = [
  { key: "app", icon: "typescript" as Lang, art: "/v2/art/streak-orange.webp", lang: "TS", side: "left", caption: "the only human surface", tone: ["#7dd3a0", "#2f6f52"] },
  { key: "explainer", icon: "python" as Lang, art: "/v2/art/streak-violet.webp", lang: "PY", side: "right", caption: "allowed to fail", tone: ["#c4b5fd", "#6d4fb8"] },
  { key: "scorer", icon: "python" as Lang, art: "/v2/art/streak-indigo.webp", lang: "PY", side: "left", caption: "one model per repository", tone: ["#a5b4fc", "#4453b5"] },
  { key: "intake", icon: "go" as Lang, art: "/v2/art/streak-green.webp", lang: "GO", side: "right", caption: "inside a 10s budget", tone: ["#e0a45e", "#8a5326"] },
  { key: "contracts", icon: "json" as Lang, art: "/v2/art/streak-teal.webp", lang: "{}", side: "left", caption: "the source of truth", tone: ["#7dd3a0", "#2f6f52"] },
] as const;

type Pt = [number, number];

/** Rounded isometric diamond, clockwise from the left vertex, as points. */
function roundedDiamond(y: number): { pts: Pt[]; leftMid: number; rightMid: number } {
  const V: Pt[] = [
    [CX - W, y],
    [CX, y - H],
    [CX + W, y],
    [CX, y + H],
  ];
  const edge = Math.hypot(W, H);
  const t = R / edge;
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
      const x = (1 - u) ** 2 * a[0] + 2 * (1 - u) * u * v[0] + u ** 2 * b[0];
      const yy = (1 - u) ** 2 * a[1] + 2 * (1 - u) * u * v[1] + u ** 2 * b[1];
      if (s === SAMPLES / 2) mids.push(pts.length);
      pts.push([x, yy]);
    }
  }
  return { pts, leftMid: mids[0], rightMid: mids[2] };
}

const d = (pts: Pt[], close = false) =>
  pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ") + (close ? " Z" : "");

/** The part of the outline visible from the front: right extreme → bottom → left extreme. */
function lowerHalf(y: number): Pt[] {
  const { pts, leftMid, rightMid } = roundedDiamond(y);
  return [...pts.slice(rightMid), ...pts.slice(0, leftMid + 1)];
}

function Slab({ y, active, tone, id, label, art }: { y: number; active: boolean; tone: readonly string[]; id: string; label: string; art: string }) {
  const top = roundedDiamond(y);
  const upper = lowerHalf(y);
  const lower = lowerHalf(y + D);
  const side = [...upper, ...[...lower].reverse()];
  const [lx] = upper[upper.length - 1];
  const [rx] = upper[0];
  const stroke = active ? tone[1] : T.ink;

  return (
    <g>
      <defs>
        {/* Streaked fill for the lifted slab's sides, drawn from the service
            colour: bands at uneven offsets read as brushed light. */}
        <pattern id={`tex-${id}`} patternUnits="objectBoundingBox" width="1" height="1">
          <image href={art} width="420" height="140" preserveAspectRatio="xMidYMid slice" />
        </pattern>
        <linearGradient id={`side-${id}`} x1="0" y1="0" x2="1" y2="0.35">
          <stop offset="0" stopColor={tone[1]} />
          <stop offset="0.18" stopColor={tone[0]} />
          <stop offset="0.27" stopColor={tone[1]} />
          <stop offset="0.46" stopColor="#ffffff" stopOpacity="0.85" />
          <stop offset="0.52" stopColor={tone[0]} />
          <stop offset="0.71" stopColor={tone[1]} />
          <stop offset="0.82" stopColor={tone[0]} />
          <stop offset="1" stopColor={tone[1]} />
        </linearGradient>
      </defs>
      <path
        d={d(side, true)}
        fill={active ? `url(#tex-${id})` : "#ffffff"}
        style={{ transition: "fill 400ms ease" }}
      />
      <path d={d(lower)} fill="none" stroke={stroke} strokeWidth="1.4" strokeLinejoin="round" />
      <line x1={lx} y1={y} x2={lx} y2={y + D} stroke={stroke} strokeWidth="1.4" />
      <line x1={rx} y1={y} x2={rx} y2={y + D} stroke={stroke} strokeWidth="1.4" />
      <path d={d(top.pts, true)} fill="#ffffff" stroke={stroke} strokeWidth="1.4" strokeLinejoin="round" />
      {/* Service name laid flat on the top face via the isometric matrix. */}
      <text
        transform={`matrix(0.866 0.5 -0.866 0.5 ${CX} ${y})`}
        textAnchor="middle"
        dominantBaseline="middle"
        className="font-mono"
        fontSize="22"
        letterSpacing="1"
        fill={active ? tone[1] : T.grey}
        style={{ opacity: active ? 1 : 0.55, transition: "opacity 400ms ease, fill 400ms ease" }}
      >
        {label}
      </text>
    </g>
  );
}

export function Stack() {
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const id = setInterval(() => setActive((a) => (a + 1) % LAYERS.length), CYCLE_MS);
    return () => clearInterval(id);
  }, []);

  // Everything at or above the active layer rises by LIFT, opening a gap
  // beneath it so its lit sides show.
  const offset = (i: number) => (i <= active ? -LIFT : 0);
  const restY = (i: number) => TOP + i * STEP;
  const edgeY = (i: number) => restY(i) + offset(i) + D / 2;

  return (
    <div className="relative mx-auto w-full max-w-[60rem]" style={{ aspectRatio: "1000 / 600" }}>
      <svg
        viewBox="0 0 1000 600"
        className="absolute inset-0 h-full w-full overflow-visible"
        role="img"
        aria-label="Riffle's architecture as a stack: app, explainer, scorer and intake resting on shared contracts."
      >
        {/* Leader lines under the slabs. Dotted at rest, solid colour when live. */}
        {LAYERS.map((l, i) => {
          const live = i === active;
          const x1 = l.side === "left" ? 190 : 810;
          const x2 = l.side === "left" ? CX - W - 6 : CX + W + 6;
          return (
            <g key={`lead-${l.key}`} data-anim="lead">
              <defs>
                <linearGradient id={`lead-${l.key}`} x1={l.side === "left" ? "0" : "1"} x2={l.side === "left" ? "1" : "0"}>
                  <stop offset="0" stopColor={l.tone[0]} />
                  <stop offset="1" stopColor={l.tone[1]} />
                </linearGradient>
              </defs>
              <line
                x1={x1}
                x2={x2}
                y1={edgeY(i)}
                y2={edgeY(i)}
                stroke={live ? `url(#lead-${l.key})` : T.grey}
                strokeWidth={live ? 1.6 : 1.1}
                strokeDasharray={live ? undefined : "2 4"}
                style={{ transition: "all 500ms cubic-bezier(.2,.8,.2,1)", opacity: live ? 1 : 0.7 }}
              />
            </g>
          );
        })}

        {/* Paint bottom slab first so each one above occludes the one below. */}
        {[...LAYERS.keys()].reverse().map((i) => (
          <g
            key={LAYERS[i].key}
            data-anim="plate"
            style={{
              transform: `translateY(${offset(i)}px)`,
              transition: "transform 700ms cubic-bezier(.2,.8,.2,1)",
            }}
          >
            <Slab y={restY(i)} active={i === active} tone={LAYERS[i].tone} id={LAYERS[i].key} label={LAYERS[i].key} art={LAYERS[i].art} />
          </g>
        ))}
      </svg>

      {/* Pills as HTML so they use the page's fonts; positioned from the
          same geometry as the leader lines. */}
      {LAYERS.map((l, i) => {
        const live = i === active;
        const left = l.side === "left";
        return (
          <div
            key={`pill-${l.key}`}
            data-anim="lead"
            className="absolute flex flex-col gap-2"
            style={{
              left: left ? "19%" : "81%",
              top: `${(edgeY(i) / 600) * 100}%`,
              transform: left ? "translate(-100%, -50%)" : "translate(0, -50%)",
              alignItems: left ? "flex-end" : "flex-start",
              transition: "top 700ms cubic-bezier(.2,.8,.2,1)",
            }}
          >
            <span
              className="inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 font-mono text-[12px] tracking-[0.12em] whitespace-nowrap uppercase"
              style={{
                backgroundColor: "#fff",
                outline: `1px solid ${live ? l.tone[1] : T.stroke}`,
                color: live ? T.ink : T.nickel,
                transition: "outline-color 400ms ease, color 400ms ease",
              }}
            >
              <span
                className="flex items-center"
                style={{
                  color: T.grey,
                  filter: live ? "none" : "grayscale(1)",
                  opacity: live ? 1 : 0.7,
                  transition: "filter 400ms ease, opacity 400ms ease",
                }}
              >
                (<LangIcon lang={l.icon} className="mx-[2px] size-[13px]" />)
              </span>
              {l.key}
            </span>
            <span
              className="font-geist text-[14px] whitespace-nowrap"
              style={{
                color: T.grey,
                opacity: live ? 1 : 0,
                transform: `translateY(${live ? 0 : -4}px)`,
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
