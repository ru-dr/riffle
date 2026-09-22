import { Lockup } from "@/components/brand";
import { HeroMotion } from "@/components/hero-motion";

// Riffle — the whole site. One screen, no scroll.
//
// Composition: full-bleed painting with the type block anchored bottom-left.
// A single asymmetric vignette darkens that corner and leaves the painting's
// bright chaos exposed top-right, so image and type never compete for the
// same tonal zone. Poster logic, not centred-web-page logic.

const REPO = "https://github.com/ru-dr/riffle";

const HERO = "/heroes/hero-desktop.jpg";

// Colors are picked for this painting rather than a brand palette: the
// image is warm (smoke, firelight, stone), so the type runs cool and
// near-neutral to sit against it instead of blending in.
const INK = "#f4f5f7"; // primary type — slate-tinted white, not pure #fff
const INK_DIM = "rgba(244,245,247,0.68)"; // body copy
const INK_FAINT = "rgba(244,245,247,0.5)"; // meta row
const INK_GHOST = "rgba(244,245,247,0.28)"; // brackets, separators
const BASE = "#14100c"; // page base — warm near-black, keyed to the smoke
const SCRIM = "20,16,12"; // scrim rgb, same hue as BASE

const HERO_ALT =
  "Thomas Cole's painting 'The Course of Empire: Destruction': an ancient harbour city under sack, its colonnade burning, a storm of smoke sweeping the sky above a broken bridge crowded with fighting figures.";

function Dot() {
  return (
    <span
      aria-hidden="true"
      data-anim="meta-item"
      className="select-none"
      style={{ color: INK_GHOST }}
    >
      /
    </span>
  );
}

export default function Page() {
  return (
    <main
      data-riffle-lock
      style={{ backgroundColor: BASE }}
      className="relative isolate h-dvh w-full overflow-hidden"
    >
      {/* Per-class crops, selected by aspect ratio first and width second.
          Portrait tablets (~0.71, e.g. OnePlus Pad Go 2 at 860x1204) get
          their own art rather than a landscape frame cropped to fit. */}
      <picture>
        {/* Tall portrait: phones. */}
        <source
          media="(max-aspect-ratio: 0.62)"
          srcSet="/heroes/hero-phone.jpg 1170w"
          width={1170}
          height={2532}
        />
        {/* Portrait tablets. */}
        <source
          media="(max-aspect-ratio: 0.88)"
          srcSet="/heroes/hero-tablet-portrait.jpg 1240w"
          width={1240}
          height={1740}
        />
        {/* Landscape tablets (4:3-ish). */}
        <source
          media="(max-aspect-ratio: 1.45)"
          srcSet="/heroes/hero-tablet.jpg 1536w"
          width={1536}
          height={1152}
        />
        {/* Laptops (16:10-ish). */}
        <source
          media="(max-aspect-ratio: 1.7)"
          srcSet="/heroes/hero-laptop.jpg 1920w"
          width={1920}
          height={1200}
        />
        <img
          src={HERO}
          alt={HERO_ALT}
          data-anim="hero"
          className="absolute inset-0 z-0 h-full w-full object-cover object-center"
          fetchPriority="high"
          decoding="async"
          width={2560}
          height={1440}
        />
      </picture>

      {/* One asymmetric scrim, not a flat wash: dense at the bottom-left
          where the type lives, clearing entirely toward the top-right so the
          burning colonnade stays fully visible. */}
      <div
        aria-hidden="true"
        data-anim="veil"
        className="pointer-events-none absolute inset-0 z-0"
        style={{
          background:
            `radial-gradient(120% 115% at 0% 100%, rgba(${SCRIM},0.94) 0%, rgba(${SCRIM},0.8) 26%, rgba(${SCRIM},0.42) 52%, rgba(${SCRIM},0.07) 76%, transparent 100%)`,
        }}
      />

      {/* Progressive blur on the same bottom-left axis as the scrim above:
          masked with matching radial geometry so the softening tracks the
          type block and clears toward the top-right.

          Deliberately not animated: fading a stack of backdrop-filter layers
          re-runs three viewport blurs every frame. It is present from the
          first paint and the gradient scrims move over it instead. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 z-0"
      >
        {[
          { radius: 3, start: "30%", stop: "62%" },
          { radius: 9, start: "22%", stop: "48%" },
          { radius: 18, start: "12%", stop: "34%" },
        ].map(({ radius, start, stop }) => {
          const mask = `radial-gradient(105% 100% at 0% 100%, black 0%, rgba(0,0,0,0.9) ${start}, transparent ${stop})`;
          return (
            <div
              key={radius}
              className="absolute inset-0"
              style={{
                backdropFilter: `blur(${radius}px)`,
                WebkitBackdropFilter: `blur(${radius}px)`,
                maskImage: mask,
                WebkitMaskImage: mask,
              }}
            />
          );
        })}
      </div>

      {/* Thin top-edge and bottom-edge falloff: frames the image and keeps
          the meta row and rule from floating on raw painting. */}
      <div
        aria-hidden="true"
        data-anim="veil"
        className="pointer-events-none absolute inset-0 z-0"
        style={{
          background:
            `linear-gradient(to bottom, rgba(${SCRIM},0.45) 0%, transparent 18%, transparent 72%, rgba(${SCRIM},0.58) 100%)`,
        }}
      />

      {/* Image credit, bottom-right. The trigger names the painting and
          year; the tooltip carries the artist, medium, and why this image is
          the one behind a product about predictable failure. */}
      <aside data-anim="credit" className="group absolute right-6 bottom-8 z-20 hidden sm:right-10 sm:bottom-14 lg:right-16 lg:bottom-20 xl:right-20 [@media_(min-width:640px)_and_(min-aspect-ratio:1.1)]:block">
        <div
          role="tooltip"
          id="hero-credit"
          className="pointer-events-none absolute right-0 bottom-full mb-3 w-[20rem] origin-bottom-right translate-y-1 scale-[0.98] rounded-md border p-4 text-left opacity-0 backdrop-blur-md transition-[opacity,transform] duration-200 ease-out group-hover:translate-y-0 group-hover:scale-100 group-hover:opacity-100 group-focus-within:translate-y-0 group-focus-within:scale-100 group-focus-within:opacity-100 group-active:translate-y-0 group-active:scale-100 group-active:opacity-100 motion-reduce:transition-none"
          style={{
            backgroundColor: "rgba(20,16,12,0.9)",
            borderColor: "rgba(244,245,247,0.14)",
            boxShadow: "0 16px 40px -12px rgba(0,0,0,0.7)",
          }}
        >
          <p
            className="font-mono text-[10.5px] tracking-[0.06em] uppercase"
            style={{ color: INK_FAINT }}
          >
            Thomas Cole &middot; 1836 &middot; oil on canvas
          </p>
          <p
            className="mt-2.5 border-t pt-2.5 font-geist text-[12.5px] leading-[1.55]"
            style={{ color: INK_DIM, borderColor: "rgba(244,245,247,0.12)" }}
          >
            Fourth of five paintings tracking one city from wilderness to
            ruin. Cole&rsquo;s argument is that the collapse was legible in
            the earlier canvases — it arrives along the seams the city was
            built with, not out of nowhere.
            <span className="mt-2 block" style={{ color: INK_FAINT }}>
              That is Riffle&rsquo;s claim about a codebase. The next
              incident follows the fault lines your repository has already
              broken along, and its own history is where they are written.
            </span>
          </p>
        </div>

        <button
          type="button"
          aria-describedby="hero-credit"
          className="cursor-help touch-manipulation font-mono text-[11.5px] tracking-[0.01em] whitespace-nowrap transition-colors duration-200 group-hover:text-[color:var(--ink-dim)] group-focus-within:text-[color:var(--ink-dim)] group-active:text-[color:var(--ink-dim)] focus-visible:outline-none"
          style={{ color: INK_GHOST }}
        >
          <span className="italic">The Course of Empire: Destruction</span>
          <span className="not-italic">, 1836</span>
        </button>
      </aside>

      {/* Type block, bottom-left. Everything on one axis, bottom-aligned so
          the negative space sits above it rather than around it. */}
      <div className="relative z-10 flex h-full flex-col justify-end px-7 pb-12 sm:px-10 sm:pb-14 md:px-14 lg:px-16 xl:px-20 md:pb-16 lg:pb-20">
        <div className="w-full max-w-184">
          <Lockup data-anim="lockup" style={{ color: INK }} className="h-auto w-26 sm:w-40 [@media_(min-height:561px)_and_(max-height:820px)]:w-33 [@media_(max-height:560px)]:w-27" />

          <h1 style={{ color: INK }} className="mt-6 font-tagline text-[clamp(1.9rem,10.5vw,2.75rem)] leading-[1.08] font-normal tracking-[-0.018em] sm:mt-9 sm:text-[clamp(2.4rem,7vw,6rem)] [@media_(min-height:561px)_and_(max-height:820px)]:mt-6 [@media_(min-height:561px)_and_(max-height:820px)]:text-[clamp(1.9rem,6.3vw,4.875rem)] [@media_(max-height:560px)]:mt-4 [@media_(max-height:560px)]:text-[clamp(1.6rem,5vw,3.25rem)]">
            <span data-anim="headline-line" className="block">
              Every repo
            </span>
            <span data-anim="headline-line" className="block">
              breaks differently.
            </span>
          </h1>

          <p data-anim="body" style={{ color: INK_DIM }} className="mt-5 max-w-[21rem] font-geist text-[14px] leading-[1.6] font-normal sm:mt-7 sm:max-w-[38rem] sm:text-[15px] sm:leading-[1.65] [@media_(min-height:561px)_and_(max-height:820px)]:mt-5 [@media_(min-height:561px)_and_(max-height:820px)]:max-w-[31rem] [@media_(min-height:561px)_and_(max-height:820px)]:text-[13.5px] [@media_(min-height:561px)_and_(max-height:820px)]:leading-[1.6] [@media_(max-height:560px)]:mt-3.5 [@media_(max-height:560px)]:max-w-[26rem] [@media_(max-height:560px)]:text-[11px] [@media_(max-height:560px)]:leading-[1.55]">
            Riffle ranks your pull request queue by risk — trained on your
            repository&rsquo;s own revert history, not one vendor&rsquo;s rules.
            Self-hosted, and nothing skips review.
          </p>

          {/* Meta row reads as one monospace line, slashes as separators. */}
          <div style={{ color: INK_FAINT }} className="mt-5 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 font-mono text-[12px] tracking-[0.01em] sm:mt-7 sm:gap-x-3 sm:gap-y-2 sm:text-[14px] [@media_(min-height:561px)_and_(max-height:820px)]:mt-5 [@media_(min-height:561px)_and_(max-height:820px)]:text-[12px] [@media_(max-height:560px)]:mt-3.5 [@media_(max-height:560px)]:gap-x-2 [@media_(max-height:560px)]:text-[10px]">
            {/* Status reads like build output: bracketed, monospace. The
                brackets pulse rather than a status dot — the punctuation is
                already there, so nothing extra has to be drawn to say live. */}
            <span data-anim="meta-item" className="inline-flex items-center" style={{ color: INK_DIM }}>
              <span data-anim="status-bracket" style={{ color: INK_FAINT }}>
                [
              </span>
              in development
              <span data-anim="status-bracket" style={{ color: INK_FAINT }}>
                ]
              </span>
            </span>
            <Dot />
            <a
              data-anim="meta-item"
              href={REPO}
              className="riffle-link rounded-sm underline decoration-1 underline-offset-[5px] transition-colors focus-visible:ring-2 focus-visible:ring-offset-4 focus-visible:outline-none"
            >
              GitHub
            </a>
            <Dot />
            <span data-anim="meta-item">Self-hosted</span>
            <Dot />
            <span data-anim="meta-item">Open source</span>
            <Dot />
            <span data-anim="meta-item">Apache-2.0</span>
          </div>
        </div>
      </div>

      <HeroMotion />
    </main>
  );
}
