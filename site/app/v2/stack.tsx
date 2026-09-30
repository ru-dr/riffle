"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import gsap from "gsap";
import { LangIcon, type Lang } from "./lang-icon";
import { T } from "./tokens";

// The hero stack.
//
// Sequence, taken from the reference: it opens as a single cube carrying the
// mark, then a springy wave splits the cube into the five slabs of the
// architecture, and only then does the lift start walking the stack. The
// cube is the product as one thing; the wave is the claim that it is made of
// separable layers; the walk explains each layer in turn.
//
// Each slab is solid: a rounded isometric top face, visible side thickness,
// white fill, thin ink outline. The lifted slab takes everything above it
// with it, opening a gap underneath where its textured sides show.
//
// Geometry is sampled polylines so every face and outline derives from one
// shape. Two transform layers per slab: the outer <g> belongs to the GSAP
// intro, the inner one to the lift, so the two never fight over the same
// attribute.

const CX = 500;
const W = 200;
const TAN30 = Math.tan(Math.PI / 6);
const D = 30;
const STEP = 46;
const TOP = 176;
const R = 26;
const LIFT = 28;
const CYCLE_MS = 2800;

const CUBE_W = 124; // half-width of the cube's top face
const CUBE_EDGE = CUBE_W / Math.cos(Math.PI / 6); // vertical edge = top-face edge
const CUBE_Y = 262; // y of the cube's top-face centre

type Layer = {
  key: string;
  icon: Lang;
  side: "left" | "right";
  caption: string;
  tone: readonly [string, string];
  art: string;
};

const LAYERS: readonly Layer[] = [
  { key: "app", icon: "typescript", side: "left", caption: "the only human surface", tone: ["#e0a45e", "#8a5326"], art: "/v2/art/streak-orange.webp" },
  { key: "explainer", icon: "python", side: "right", caption: "allowed to fail", tone: ["#c4b5fd", "#6d4fb8"], art: "/v2/art/streak-violet.webp" },
  { key: "scorer", icon: "python", side: "left", caption: "one model per repository", tone: ["#a5b4fc", "#4453b5"], art: "/v2/art/streak-indigo.webp" },
  { key: "intake", icon: "go", side: "right", caption: "inside a 10s budget", tone: ["#7dd3a0", "#2f6f52"], art: "/v2/art/streak-green.webp" },
  { key: "contracts", icon: "json", side: "left", caption: "the source of truth", tone: ["#6ee7d8", "#1f7a70"], art: "/v2/art/streak-teal.webp" },
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

/** A rounded isometric box: top face, visible sides, outline. */
function Box({
  y,
  w,
  depth,
  r,
  sideFill,
  stroke,
  ghost = false,
}: {
  y: number;
  w: number;
  depth: number;
  r: number;
  sideFill: string;
  stroke: string;
  /** Wireframe: no fill, dashed thin outline — how inactive layers read
   *  while another one is live. */
  ghost?: boolean;
}) {
  const top = roundedDiamond(CX, y, w, r);
  const upper = lowerHalf(CX, y, w, r);
  const lower = lowerHalf(CX, y + depth, w, r);
  const [lx] = upper[upper.length - 1];
  const [rx] = upper[0];
  const line = {
    stroke: ghost ? T.grey : stroke,
    strokeWidth: ghost ? 1 : 1.4,
    strokeDasharray: ghost ? "2 3" : undefined,
    strokeOpacity: ghost ? 0.75 : 1,
    style: { transition: "stroke 400ms ease, stroke-opacity 400ms ease" },
  } as const;
  const faceOpacity = ghost ? 0 : 1;
  return (
    <>
      <path
        d={path([...upper, ...[...lower].reverse()], true)}
        fill={sideFill}
        fillOpacity={faceOpacity}
        style={{ transition: "fill 400ms ease, fill-opacity 400ms ease" }}
      />
      <path d={path(lower)} fill="none" strokeLinejoin="round" {...line} />
      <line x1={lx} y1={y} x2={lx} y2={y + depth} {...line} />
      <line x1={rx} y1={y} x2={rx} y2={y + depth} {...line} />
      <path
        d={path(top.pts, true)}
        fill="#ffffff"
        fillOpacity={ghost ? 0.35 : 1}
        strokeLinejoin="round"
        {...line}
        style={{ transition: "fill-opacity 400ms ease, stroke 400ms ease" }}
      />
    </>
  );
}

export function Stack() {
  const root = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState(-1); // -1: nothing lifted yet
  const [ready, setReady] = useState(false); // intro finished, labels may show

  // Intro. Runs before the page's motion gate is dropped (this component
  // sits earlier in the tree than V2Motion), so its start state is written
  // inline before anything can paint the finished stack.
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    const motion = document.documentElement.dataset.motion === "on";
    if (!motion) {
      gsap.set(el.querySelector("[data-cube]"), { opacity: 0 });
      setReady(true);
      return;
    }

    const ctx = gsap.context(() => {
      const slabs = gsap.utils.toArray<SVGGElement>("[data-slab]", el);
      // Every slab starts folded into the cube's volume: pulled to the
      // cube's centre line and shrunk to its footprint.
      slabs.forEach((s, i) => {
        const rest = TOP + i * STEP + D / 2;
        gsap.set(s, {
          opacity: 0,
          y: CUBE_Y + CUBE_EDGE / 2 - rest,
          scale: CUBE_W / W,
          transformOrigin: `${CX}px ${rest}px`,
        });
      });
      gsap.set("[data-cube]", { opacity: 1, scale: 1, transformOrigin: `${CX}px ${CUBE_Y + CUBE_EDGE / 2}px` });
      gsap.set("[data-leads]", { opacity: 0 });

      gsap
        .timeline({ delay: 0.5 })
        // A breath on the cube before it opens.
        .to("[data-cube]", { y: -6, duration: 0.5, ease: "sine.inOut", yoyo: true, repeat: 1 })
        .to("[data-cube]", { opacity: 0, scale: 0.96, duration: 0.25, ease: "power1.in" }, ">-0.05")
        // The wave: slabs spring out top to bottom, overshoot, settle.
        .to(
          slabs,
          {
            opacity: 1,
            y: 0,
            scale: 1,
            duration: 1.05,
            ease: "back.out(2.1)",
            stagger: { each: 0.075, from: "start" },
          },
          "<",
        )
        .to("[data-leads]", { opacity: 1, duration: 0.5, ease: "power1.out" }, "-=0.35")
        .add(() => {
          setReady(true);
          setActive(0);
        });
    }, el);

    return () => ctx.revert();
  }, []);

  // The walk. Starts only once the intro has handed over.
  useEffect(() => {
    if (!ready) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const id = setInterval(() => setActive((a) => (a + 1) % LAYERS.length), CYCLE_MS);
    return () => clearInterval(id);
  }, [ready]);

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
          {LAYERS.map((l) => (
            <pattern key={l.key} id={`tex-${l.key}`} patternUnits="objectBoundingBox" width="1" height="1">
              <image href={l.art} width="420" height="140" preserveAspectRatio="xMidYMid slice" />
            </pattern>
          ))}
        </defs>

        {/* Leader lines: dotted at rest, a hairline of the layer's colour when
            live. Drawn first so slabs paint over their inner ends. */}
        <g data-leads>
          {LAYERS.map((l, i) => {
            const live = i === active;
            const left = l.side === "left";
            return (
              <line
                key={l.key}
                x1={left ? 196 : 804}
                x2={left ? CX - W - 8 : CX + W + 8}
                y1={edgeY(i)}
                y2={edgeY(i)}
                stroke={live ? l.tone[1] : T.grey}
                strokeWidth={live ? 1.3 : 1}
                strokeDasharray={live ? undefined : "1.5 3.5"}
                strokeLinecap="round"
                style={{ transition: "all 600ms cubic-bezier(.2,.8,.2,1)", opacity: live ? 1 : 0.55 }}
              />
            );
          })}
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
                  stroke={live ? l.tone[1] : T.ink}
                  ghost={active >= 0 && !live}
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

        {/* The opening cube: one volume, the mark on its left face. */}
        <g data-cube>
          <Box y={CUBE_Y} w={CUBE_W} depth={CUBE_EDGE} r={14} sideFill="#ffffff" stroke={T.ink} />
          {/* Left-face plane: x along the L→B edge, y straight down. */}
          <text
            transform={`matrix(0.866 0.5 0 1 ${CX - CUBE_W + 26} ${CUBE_Y + CUBE_EDGE * 0.62})`}
            className="font-mono"
            fontSize="26"
            fontWeight="600"
            fill={T.ink}
          >
            (riffle)
          </text>
        </g>
      </svg>

      {/* Pills: HTML for real fonts, centred exactly on their line's y. The
          caption hangs off the pill absolutely, so it never shifts the pill
          off the line — the misalignment the first version had. */}
      {LAYERS.map((l, i) => {
        const live = i === active;
        const left = l.side === "left";
        return (
          <div
            key={l.key}
            className="absolute"
            style={{
              left: left ? "19.6%" : "80.4%",
              top: `${(edgeY(i) / 600) * 100}%`,
              transform: left ? "translate(-100%, -50%)" : "translate(0, -50%)",
              opacity: ready ? 1 : 0,
              transition: "top 700ms cubic-bezier(.2,.8,.2,1), opacity 500ms ease",
            }}
          >
            <span
              className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-[3px] font-mono text-[10.5px] leading-none tracking-[0.1em] whitespace-nowrap uppercase"
              style={{
                outline: `1px solid ${live ? "rgba(22,23,29,0.18)" : T.stroke}`,
                backgroundColor: "rgba(255,255,255,0.7)",
                color: live ? T.ink : T.grey,
                transition: "color 400ms ease, outline-color 400ms ease",
              }}
            >
              <span
                className="flex items-center"
                style={{ filter: live ? "none" : "grayscale(1)", opacity: live ? 1 : 0.6, transition: "filter 400ms ease, opacity 400ms ease" }}
              >
                (<LangIcon lang={l.icon} className="mx-px size-[10px]" />)
              </span>
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
