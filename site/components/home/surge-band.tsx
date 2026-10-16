"use client";

import { useEffect, useId, useRef, useState } from "react";
import { SURGE } from "./data";
import { H3, StatusTag } from "./sections";
import { T } from "./tokens";

// The volume problem, built to voidzero's "Trusted by millions" band: a
// heading, then a figure beside a chart, then three cells. Their chart is a
// canvas filling its whole 5:3.5 cell edge to edge — one thick line over a
// soft fill, no dots, no axis, a project dropdown top left and the date span
// in mono at the bottom corners. This draws the same thing in SVG.
//
// Every number is GitHub's own published data and is tagged "reported", so
// none of it reads as Riffle's measurement.

const GREEN = "#00b442";
// The plot in viewBox units (1000 x 700, the cell's 5:3.5). Measured off
// voidzero's at 1440: the fill's floor sits 84% down the cell, with the
// date labels on plain white beneath it, and the peak 5% from the top.
const VW = 1000;
const VH = 700;
const FLOOR = 588;
const PEAK = 38;

// Each series fills the chart edge to edge over its own range: first period
// at the left edge, latest at the right, as voidzero's does. The labels
// under it name that range.
function shape(s: { values: readonly number[] }) {
  const n = s.values.length;
  const max = Math.max(...s.values);
  const pts = s.values.map((v, i) => {
    return [(i / (n - 1)) * VW, FLOOR - (v / max) * (FLOOR - PEAK)] as const;
  });
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const [fx] = pts[0];
  const [lx] = pts[n - 1];
  return { line, area: `${line} L${lx.toFixed(1)} ${FLOOR} L${fx.toFixed(1)} ${FLOOR} Z` };
}

function GitIcon() {
  // A commit on a branch: the mark for "code pushed".
  return (
    <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke={GREEN} strokeWidth="2" strokeLinecap="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3.5" />
      <path d="M2 12h6.5M15.5 12H22" />
    </svg>
  );
}

export function SurgeBand() {
  const [which, setWhich] = useState(0);
  const [open, setOpen] = useState(false);
  const menu = useRef<HTMLDivElement>(null);
  const listId = useId();
  const series = SURGE.series[which];
  const { line, area } = shape(series);
  const figure = series.figure.toLocaleString("en-US");

  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !menu.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  // Series differ in point count, so a path cannot morph between them; each
  // switch remounts the paths and fades the new one in.
  const enter = { animation: "rf-chart-in 600ms cubic-bezier(.2,.8,.2,1) both" };

  return (
    // No top rule: coming out of the dark block the page goes straight into
    // this heading, and the first rule sits beneath it — as the reference does.
    <section className="rf-wrapper">
      <div className="px-6 pt-10 pb-10 md:px-10 md:pt-14">
        <h3 data-anim="rise" className={`${H3} max-w-[36rem]`}>
          {SURGE.headline} <span style={{ color: T.grey }}>{SURGE.headlineCause}</span>
        </h3>
      </div>

      <div className="grid border-t lg:grid-cols-2 lg:divide-x" style={{ borderColor: T.stroke }}>
        <div data-anim="rise" className="flex flex-col justify-between gap-16 p-6 md:p-10">
          <div className="flex items-center gap-3">
            <p className="font-geist text-[17px] md:text-[20px]" style={{ color: T.ink }}>
              {series.figureLabel}
            </p>
            <StatusTag status="reported" />
          </div>
          <div>
            {/* npm's twelve-digit monthly total would overrun the cell at the
                full size, so long figures step down. */}
            <p
              className={`font-geist leading-none font-medium tracking-[-0.045em] tabular-nums ${
                figure.length > 11 ? "text-[2.25rem] md:text-[3.75rem]" : "text-[3rem] md:text-[5rem]"
              }`}
            >
              <span key={series.key} data-count={series.figure}>
                {figure}
              </span>
            </p>
            <a
              href={series.source}
              target="_blank"
              rel="noopener noreferrer"
              className="rf-link mt-4 inline-block font-geist text-[14px] underline decoration-[#e5e4e7] underline-offset-[3px]"
              style={{ color: T.grey }}
            >
              {series.cite}&nbsp;&#8599;
            </a>
          </div>
        </div>

        <div data-anim="rise" className="relative aspect-[5/3.5] w-full overflow-hidden border-t lg:border-t-0" style={{ borderColor: T.stroke }}>
          <svg viewBox={`0 0 ${VW} ${VH}`} preserveAspectRatio="none" className="absolute inset-0 h-full w-full" aria-hidden="true">
            <path key={`a-${series.key}`} d={area} fill={GREEN} fillOpacity="0.1" style={enter} />
            <path
              key={`l-${series.key}`}
              d={line}
              fill="none"
              stroke={GREEN}
              strokeWidth="3"
              strokeLinejoin="round"
              strokeLinecap="round"
              vectorEffect="non-scaling-stroke"
              style={enter}
            />
          </svg>

          <div ref={menu} className="absolute top-4 left-4 z-10 md:top-6 md:left-6">
            <button
              type="button"
              aria-haspopup="listbox"
              aria-expanded={open}
              aria-controls={listId}
              aria-label={`Select series: ${series.label}`}
              onClick={() => setOpen((o) => !o)}
              className="rf-chip flex cursor-pointer items-center gap-2 rounded-lg px-4 py-2 font-geist text-[15px] md:text-[16px]"
              style={{ color: T.ink }}
            >
              <GitIcon />
              {series.label}
              <svg viewBox="0 0 12 7" className="ml-1 size-3" fill={T.ink} aria-hidden="true" style={{ transform: open ? "rotate(180deg)" : undefined, transition: "transform 200ms ease" }}>
                <path d="M1.41 0L6 4.58 10.59 0 12 1.42l-6 6-6-6z" />
              </svg>
            </button>
            {open && (
              <ul id={listId} role="listbox" className="rf-chip mt-2 overflow-hidden rounded-lg py-1 font-geist text-[15px]">
                {SURGE.series.map((s, i) => (
                  <li
                    key={s.key}
                    role="option"
                    aria-selected={i === which}
                    tabIndex={0}
                    onClick={() => {
                      setWhich(i);
                      setOpen(false);
                    }}
                    onKeyDown={(e) => {
                      if (e.key !== "Enter" && e.key !== " ") return;
                      e.preventDefault();
                      setWhich(i);
                      setOpen(false);
                    }}
                    className="cursor-pointer px-4 py-2 whitespace-nowrap hover:bg-[#f4f3f5]"
                    style={{ color: i === which ? T.ink : T.nickel }}
                  >
                    {s.label}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="absolute inset-x-6 bottom-6 flex justify-between font-mono text-[13px] md:inset-x-10 md:bottom-10 md:text-[14px]" style={{ color: T.nickel }}>
            <span>{series.from}</span>
            <span>{series.to}</span>
          </div>
        </div>
      </div>

      {/* Two rows of three. Each cell draws its own top and left rules, so
          the grid stays ruled however it wraps. */}
      <div className="grid border-t sm:grid-cols-2 lg:grid-cols-3" style={{ borderColor: T.stroke }}>
        {SURGE.cells.map(([value, label, href, cite], i) => (
          <div
            key={label}
            data-anim="rise"
            className={`flex flex-col gap-3 p-6 md:p-10 ${i ? "border-t" : ""} ${i % 2 ? "sm:border-l" : ""} ${i === 1 ? "sm:border-t-0" : ""} lg:border-l lg:border-t ${i % 3 === 0 ? "lg:border-l-0" : ""} ${i < 3 ? "lg:border-t-0" : ""}`}
            style={{ borderColor: T.stroke }}
          >
            <p className="font-geist text-[2.5rem] leading-none font-medium tracking-[-0.04em] whitespace-nowrap md:text-[3.5rem]">{value}</p>
            <p className="font-geist text-[15px] md:text-[17px]" style={{ color: T.grey }}>
              {label}.{" "}
              <a href={href} target="_blank" rel="noopener noreferrer" className="rf-link underline decoration-[#e5e4e7] underline-offset-[3px] whitespace-nowrap">
                {cite}&nbsp;&#8599;
              </a>
            </p>
          </div>
        ))}
      </div>

    </section>
  );
}
