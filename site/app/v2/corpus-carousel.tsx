"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { logoUrl } from "./logo";
import { T } from "./tokens";

// The corpus row, as the reference's logo carousel: repositories slide past a
// pair of brackets, and whichever one sits between them is the only one at
// full strength. The brackets measure the repository they frame and resize to
// hug it, in step with the slide — names run from rails/rails to
// huggingface/transformers, and a fixed gap either gaped or collided. Named by repository and shown with the project's own mark
// — they are the training corpus, not customers.

const REPOS: { repo: string; domain: string }[] = [
  { repo: "kubernetes/kubernetes", domain: "kubernetes.io" },
  { repo: "python/cpython", domain: "python.org" },
  { repo: "llvm/llvm-project", domain: "llvm.org" },
  { repo: "facebook/react", domain: "react.dev" },
  { repo: "apache/spark", domain: "spark.apache.org" },
  { repo: "pytorch/pytorch", domain: "pytorch.org" },
  { repo: "rust-lang/cargo", domain: "rust-lang.org" },
  { repo: "nodejs/node", domain: "nodejs.org" },
  { repo: "django/django", domain: "djangoproject.com" },
  { repo: "rails/rails", domain: "rubyonrails.org" },
  { repo: "ClickHouse/ClickHouse", domain: "clickhouse.com" },
  { repo: "grafana/grafana", domain: "grafana.com" },
  { repo: "huggingface/transformers", domain: "huggingface.co" },
  { repo: "godotengine/godot", domain: "godotengine.org" },
  { repo: "home-assistant/core", domain: "home-assistant.io" },
  { repo: "elastic/elasticsearch", domain: "elastic.co" },
];

const ITEM_W = 300; // px per slot, wide enough that the longest name never meets its neighbours
const BRACKET_PAD = 18; // space between a bracket and the name it frames
const STEP_MS = 2200;

export function CorpusCarousel() {
  // Three copies so the track can always slide left without running out;
  // after passing one full copy it snaps back invisibly by one copy's width.
  const items = [...REPOS, ...REPOS, ...REPOS];
  const [index, setIndex] = useState(REPOS.length);
  const [animate, setAnimate] = useState(true);
  const track = useRef<HTMLUListElement>(null);
  const [frame, setFrame] = useState(220);

  // Measure the framed repository after each move. The snap-back lands on
  // the same repository one copy earlier, so the width does not change.
  useLayoutEffect(() => {
    const content = track.current?.children[index]?.querySelector<HTMLElement>("[data-content]");
    if (content) setFrame(content.getBoundingClientRect().width + BRACKET_PAD * 2 + 22);
  }, [index]);

  useEffect(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const id = setInterval(() => {
      setAnimate(true);
      setIndex((i) => i + 1);
    }, STEP_MS);
    return () => clearInterval(id);
  }, []);

  // Snap back a copy once the middle copy is exhausted, with no transition,
  // so the loop never visibly rewinds.
  useEffect(() => {
    if (index < REPOS.length * 2) return;
    const t = setTimeout(() => {
      setAnimate(false);
      setIndex((i) => i - REPOS.length);
    }, 650);
    return () => clearTimeout(t);
  }, [index]);

  return (
    <div className="relative w-full overflow-hidden" style={{ height: 64 }}>
      {/* Fixed brackets around the centre slot. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute top-1/2 left-1/2 z-10 flex -translate-x-1/2 -translate-y-1/2 items-center justify-between"
        style={{ width: frame, transition: "width 600ms cubic-bezier(.65,0,.35,1)" }}
      >
        <Paren />
        <Paren flip />
      </div>

      {/* Edge fades so repositories arrive and leave rather than clip. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 z-10"
        style={{
          background: `linear-gradient(90deg, ${T.beige} 0%, transparent 18%, transparent 82%, ${T.beige} 100%)`,
        }}
      />

      <ul
        ref={track}
        className="absolute top-0 left-1/2 flex h-full items-center"
        style={{
          transform: `translateX(${-(index + 0.5) * ITEM_W}px)`,
          transition: animate ? "transform 600ms cubic-bezier(.65,0,.35,1)" : "none",
        }}
        aria-label="Repositories in the planned training corpus"
      >
        {items.map(({ repo, domain }, i) => {
          const live = i === index;
          const src = logoUrl(domain, { size: 48 });
          return (
            <li
              key={`${repo}-${i}`}
              aria-hidden={i < REPOS.length || i >= REPOS.length * 2 ? true : undefined}
              className="flex shrink-0 items-center justify-center"
              style={{
                width: ITEM_W,
                opacity: live ? 1 : 0.35,
                filter: live ? "none" : "grayscale(1)",
                transition: "opacity 500ms ease, filter 500ms ease",
              }}
            >
              <span data-content className="flex items-center gap-2.5">
                {src && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={src} alt="" width={20} height={20} loading="lazy" className="rounded-[4px]" />
                )}
                <span className="font-mono text-[13px] whitespace-nowrap" style={{ color: T.ink }}>
                  {repo}
                </span>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function Paren({ flip = false }: { flip?: boolean }) {
  return (
    <svg
      viewBox="0 0 8 30"
      className="h-[40px] w-[11px] shrink-0"
      fill={T.nickel}
      style={{ transform: flip ? "scaleX(-1)" : undefined }}
    >
      <path d="M4.652 30C1.48 25.427 0 20.439 0 15.035 0 9.596 1.48 4.538 4.652 0H8c-2.573 4.988-3.7 10.046-3.7 15.035 0 4.988 1.092 10.011 3.7 14.965H4.652Z" />
    </svg>
  );
}
