"use client";

import { useEffect, useRef, useState } from "react";
import { STATS } from "./data";
import { T } from "./tokens";

// The corpus by category as one stacked bar, with no legend. Hovering,
// focusing or tapping a block widens it to a fixed share of the bar and
// writes its category, PR count and share inside it; the other blocks shrink
// in proportion and widen back when it is released. A row of eleven labels
// under the bar read as clutter at every width; this keeps the detail inside
// the thing it describes. Left alone, it walks the blocks on its own, one
// every CYCLE_MS, so the detail shows without anyone needing to find it; any
// interaction takes over and leaving hands back to the walk.
//
// One colour family per corpus version: the first fifty repositories in the
// ink greys, the next fifty in the greens, so the bar reads as two halves
// before it reads as eleven categories.
const VERSION_COLORS = [
  ["#16171d", "#3b3440", "#5e5666", "#867e8e", "#b9b3bf"],
  ["#00702a", "#00913a", "#00b442", "#3fcb72", "#7ddca0", "#b5ebc9"],
] as const;
// Ink text on the pale blocks, white on the rest.
const PALE = new Set(["#b9b3bf", "#3fcb72", "#7ddca0", "#b5ebc9"]);
const CATEGORIES = STATS.versions.flatMap((v, vi) =>
  v.categories.map(([name, n], ci) => ({ name, n, color: VERSION_COLORS[vi][ci] })),
);
const TOTAL = CATEGORIES.reduce((n, c) => n + c.n, 0);
// The opened block's share of the bar: wide enough for its text on a phone.
const OPEN = 0.45;
// In-out, matching the hero stack: a block hands its width to the next
// in one continuous move rather than snapping open.
const EASE = "cubic-bezier(.45,0,.2,1)";
const MOVE_MS = 750;
const CYCLE_MS = 2600;

export function CategoryBar() {
  const [active, setActive] = useState<number | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const engaged = useRef(false);

  // The walk: only while the bar is on screen and nobody is interacting,
  // and never under reduced motion.
  useEffect(() => {
    const el = root.current;
    if (!el || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    let id: number | undefined;
    const io = new IntersectionObserver(([e]) => {
      clearInterval(id);
      if (!e.isIntersecting) return;
      // Open the first block as the bar arrives, rather than after a beat.
      setActive((a) => (a === null ? 0 : a));
      id = window.setInterval(() => {
        if (!engaged.current) setActive((a) => (a === null ? 0 : (a + 1) % CATEGORIES.length));
      }, CYCLE_MS);
    });
    io.observe(el);
    return () => {
      io.disconnect();
      clearInterval(id);
    };
  }, []);

  const take = (i: number) => {
    engaged.current = true;
    setActive(i);
  };
  const release = () => {
    engaged.current = false;
  };

  // flex-grow is the block's weight. Opening one sets its weight so it takes
  // OPEN of the bar against the others' unchanged weights.
  const grow = (i: number) => {
    if (active !== i) return CATEGORIES[i].n;
    const rest = TOTAL - CATEGORIES[i].n;
    return (OPEN / (1 - OPEN)) * rest;
  };

  return (
    <div ref={root} onPointerLeave={release}>
      {/* An inline-size container, so a block's text can be laid out at the
          opened width (OPEN of the bar, in cqw) whatever the block's width
          mid-move: it is revealed by the widening block, never re-wrapped
          frame by frame. */}
      <div className="flex h-24 w-full gap-[3px] [container-type:inline-size]" role="list" aria-label="Pull requests by category">
        {CATEGORIES.map((c, i) => {
          const open = active === i;
          const share = Math.round((c.n / TOTAL) * 100);
          return (
            <button
              key={c.name}
              type="button"
              role="listitem"
              aria-label={`${c.name}: ${c.n.toLocaleString("en-US")} pull requests, ${share}%`}
              onPointerEnter={(e) => e.pointerType === "mouse" && take(i)}
              onFocus={() => take(i)}
              onBlur={release}
              // A tap fires no pointerleave, so it holds the block for two
              // beats and then hands back to the walk, which resumes from it.
              onClick={() => take(i)}
              onPointerUp={(e) => e.pointerType !== "mouse" && window.setTimeout(release, CYCLE_MS * 2)}
              className="relative h-full min-w-0 cursor-default overflow-hidden rounded-[3px] text-left outline-offset-2"
              style={{
                flex: `${grow(i)} 1 0`,
                backgroundColor: c.color,
                color: PALE.has(c.color) ? T.ink : "#ffffff",
                transition: `flex-grow ${MOVE_MS}ms ${EASE}`,
              }}
            >
              {/* The outgoing block's text fades while the incoming one's
                  fades in a little behind it, overlapping, so the read moves
                  across with the width. */}
              <span
                aria-hidden="true"
                className="absolute inset-y-0 left-0 flex flex-col justify-between p-3 font-mono text-[11px] leading-[1.35] md:p-4 md:text-[12px]"
                style={{
                  width: `${OPEN * 100}cqw`,
                  opacity: open ? 1 : 0,
                  transform: `translateY(${open ? 0 : 3}px)`,
                  transition: open
                    ? `opacity 420ms ease 260ms, transform 520ms ${EASE} 220ms`
                    : `opacity 260ms ease, transform 260ms ease`,
                }}
              >
                <span className="font-medium">{c.name}</span>
                <span className="tabular-nums opacity-80">
                  {c.n.toLocaleString("en-US")} PRs · {share}%
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
