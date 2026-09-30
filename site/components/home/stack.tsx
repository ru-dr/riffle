"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { preconnect, preload } from "react-dom";
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

// JSON's mark, from Simple Icons (CC0). logo.dev has no real logo for
// json.org — it returns a placeholder letter — so this one is inline.
const JSON_MARK = "M12.043 23.968c.479-.004.953-.029 1.426-.094a11.805 11.805 0 003.146-.863 12.404 12.404 0 003.793-2.542 11.977 11.977 0 002.44-3.427 11.794 11.794 0 001.02-3.476c.149-1.16.135-2.346-.045-3.499a11.96 11.96 0 00-.793-2.788 11.197 11.197 0 00-.854-1.617c-1.168-1.837-2.861-3.314-4.81-4.3a12.835 12.835 0 00-2.172-.87h-.005c.119.063.24.132.345.201.12.074.239.146.351.225a8.93 8.93 0 011.559 1.33c1.063 1.145 1.797 2.548 2.218 4.041.284.982.434 1.998.495 3.017.044.743.044 1.491-.047 2.229-.149 1.27-.554 2.51-1.228 3.596a7.475 7.475 0 01-1.903 2.084c-1.244.928-2.877 1.482-4.436 1.114a3.916 3.916 0 01-.748-.258 4.692 4.692 0 01-.779-.45 6.08 6.08 0 01-1.244-1.105 6.507 6.507 0 01-1.049-1.747 7.366 7.366 0 01-.494-2.54c-.03-1.273.225-2.553.854-3.67a6.43 6.43 0 011.663-1.918c.225-.178.464-.333.704-.479l.016-.007a5.121 5.121 0 00-1.441-.12 4.963 4.963 0 00-1.228.24c-.359.12-.704.27-1.019.45a6.146 6.146 0 00-.733.494c-.211.18-.42.36-.615.555-1.123 1.153-1.768 2.682-2.022 4.256-.15.973-.15 1.96-.091 2.95.105 1.395.391 2.787.945 4.062a8.518 8.518 0 001.348 2.173 8.14 8.14 0 003.132 2.23 7.934 7.934 0 002.113.54c.074.015.149.015.209.015zm-2.934-.398a4.102 4.102 0 01-.45-.228 8.5 8.5 0 01-2.038-1.534c-1.094-1.137-1.827-2.566-2.247-4.08a15.184 15.184 0 01-.495-3.172 12.14 12.14 0 01.046-2.082c.135-1.257.495-2.501 1.124-3.58a6.889 6.889 0 011.783-2.053 6.23 6.23 0 011.633-.9 5.363 5.363 0 013.522-.045c.029 0 .029 0 .045.03.015.015.045.015.06.03.045.016.104.045.165.074.239.12.479.271.704.42a6.294 6.294 0 012.097 2.502c.42.914.615 1.934.631 2.938.014 1.079-.18 2.157-.645 3.146a6.42 6.42 0 01-2.638 2.832c.09.03.18.045.271.075.225.044.449.074.688.074 1.468.045 2.892-.66 3.94-1.647.195-.18.375-.375.54-.585.225-.27.435-.54.614-.823.239-.375.435-.75.614-1.154a8.112 8.112 0 00.509-1.664c.196-1.004.211-2.022.149-3.026-.135-2.022-.673-4.045-1.842-5.724a9.054 9.054 0 00-.555-.719 9.868 9.868 0 00-1.063-1.034 8.477 8.477 0 00-1.363-.915 9.927 9.927 0 00-1.692-.598l-.3-.06c-.209-.03-.42-.044-.634-.06a8.453 8.453 0 00-1.015.016c-.704.045-1.412.16-2.112.337C5.799 1.227 2.863 3.566 1.3 6.67A11.834 11.834 0 00.238 9.801a11.81 11.81 0 00-.104 3.775c.12 1.02.374 2.023.778 2.977.227.57.511 1.124.825 1.648 1.094 1.783 2.683 3.236 4.51 4.24.688.39 1.408.69 2.157.944.226.074.45.15.689.21z";

const LAYERS: readonly Layer[] = [
  { key: "app", domain: "typescriptlang.org", side: "left", caption: "the only human surface", tone: ["#e0a45e", "#8a5326"], art: "/art/streak-orange.webp" },
  { key: "explainer", domain: "python.org", side: "right", caption: "allowed to fail", tone: ["#c4b5fd", "#6d4fb8"], art: "/art/streak-violet.webp" },
  { key: "scorer", domain: "python.org", side: "left", caption: "one model per repository", tone: ["#a5b4fc", "#4453b5"], art: "/art/streak-indigo.webp" },
  { key: "intake", domain: "go.dev", side: "right", caption: "inside a 10s budget", tone: ["#7dd3a0", "#2f6f52"], art: "/art/streak-green.webp" },
  { key: "contracts", domain: null, side: "left", caption: "the source of truth", tone: ["#6ee7d8", "#1f7a70"], art: "/art/streak-teal.webp" },
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
  // Warm the cache before the walk needs it. Each slab's texture is only
  // requested the first time that layer goes live, so the first lift of
  // each layer used to flash untextured; and the pills' marks come from
  // logo.dev over a cold connection. React hoists these into <head>.
  for (const l of LAYERS) preload(l.art, { as: "image", fetchPriority: "low" });
  preconnect("https://img.logo.dev", { crossOrigin: "anonymous" });

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
    <>
    {/* Below sm the side labels have no room, so the container crops to the
        stack itself (x 280-720, y 20-520 of the 1000x600 drawing) and the
        live layer's label moves underneath. CSS sizing only: the server
        render is already correct on a phone, with no jump on hydration. */}
    <div ref={root} className="v2-stack relative mx-auto w-full max-w-[60rem] overflow-hidden sm:overflow-visible">
      <svg
        viewBox="0 0 1000 600"
        className="v2-stack-svg absolute overflow-visible"
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
                  href={l.art}
                  x="-40"
                  width={2 * W + 88}
                  height={2 * W * TAN30 + D + 8}
                  preserveAspectRatio="xMidYMid slice"
                />
              </pattern>
            );
          })}
        </defs>

        <g>

          <g data-leads className="max-sm:hidden">
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
            <circle data-spark r="2.5" cx="0" cy="0" fill={active >= 0 ? LAYERS[active].tone[1] : T.grey} opacity="0" />
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
          // Each pill rides a full-size layer that translates by the lift, in
          // percent of its own (= the drawing's) height. Moving it with `top`
          // cost a layout on every frame of every lift; a transform does not.
          <div
            key={l.key}
            aria-hidden={!live}
            className="pointer-events-none absolute inset-0 max-sm:hidden"
            style={{
              transform: `translateY(${(offset(i) / 600) * 100}%)`,
              transition: "transform 700ms cubic-bezier(.2,.8,.2,1)",
            }}
          >
          <div
            className="pointer-events-auto absolute"
            onMouseEnter={() => take(i)}
            onMouseLeave={release}
            style={{
              // Lines end at x=196 / 804 (of 1000); pills sit 6 units short
              // of that so there is air between line and pill.
              left: left ? "19%" : "81%",
              top: `${((restY(i) + D / 2) / 600) * 100}%`,
              transform: left ? "translate(-100%, -50%)" : "translate(0, -50%)",
              opacity: ready ? 1 : 0,
              transition: "opacity 500ms ease",
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
                  onLoad={(e) => e.currentTarget.classList.add("is-loaded")}
                  className="v2-fade rounded-[2px]"
                  style={{ filter: live ? "none" : "grayscale(1)", opacity: live ? 1 : 0.6, transition: "filter 400ms ease, opacity 400ms ease" }}
                />
              ) : (
                <svg
                  viewBox="0 0 24 24"
                  width={12}
                  height={12}
                  fill={live ? T.ink : T.grey}
                  aria-hidden="true"
                  style={{ opacity: live ? 1 : 0.6, transition: "fill 400ms ease, opacity 400ms ease" }}
                >
                  <path d={JSON_MARK} />
                </svg>
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
          </div>
        );
      })}
    </div>

    {/* Phone label: the live layer's pill and caption, centred under the
        cropped stack. */}
    {active >= 0 && (
      <div className="mt-4 flex flex-col items-center gap-2 sm:hidden" aria-live="polite">
        <span
          className="inline-flex items-center gap-[7px] rounded-full py-[5px] pr-3 pl-2.5 font-mono text-[10px] leading-none tracking-[0.1em] uppercase"
          style={{ backgroundColor: "#fff", outline: "1px solid rgba(22,23,29,0.24)", color: T.ink }}
        >
          {LAYERS[active].domain && logoUrl(LAYERS[active].domain!, { size: 32 }) ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={logoUrl(LAYERS[active].domain!, { size: 32 })!} alt="" width={12} height={12} className="rounded-[2px]" />
          ) : (
            <svg viewBox="0 0 24 24" width={12} height={12} fill={T.ink} aria-hidden="true">
              <path d={JSON_MARK} />
            </svg>
          )}
          {LAYERS[active].key}
        </span>
        <span className="font-geist text-[13px]" style={{ color: T.grey }}>
          {LAYERS[active].caption}
        </span>
      </div>
    )}
    </>
  );
}