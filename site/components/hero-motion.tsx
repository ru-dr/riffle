"use client";

import { useLayoutEffect } from "react";
import gsap from "gsap";

// Entrance choreography for the landing page.
//
// Side-effect only: it renders nothing and reaches for `[data-anim]` nodes so
// the page itself stays a Server Component and ships no extra markup.
//
// Everything here animates `opacity` and `transform` and nothing else. The
// first cut animated `filter: blur()` on a full-bleed 2560px painting while
// cross-fading three `backdrop-filter` layers, which asks the compositor to
// re-blur the viewport every frame — it dropped frames on a fast laptop. The
// progressive-blur stack is now left alone entirely (see `data-anim="veil"`
// only on the cheap gradient layers) and depth is carried by scale instead.
//
// Initial hidden states live in CSS behind `html[data-motion="on"]`, which the
// blocking script in `layout.tsx` sets only when motion is wanted. Without JS,
// or with reduced motion, nothing here runs and nothing is ever hidden.
export function HeroMotion() {
  useLayoutEffect(() => {
    const root = document.documentElement;
    if (root.dataset.motion !== "on") return;

    // Tells the failsafe in layout.tsx that motion took over in time.
    root.dataset.motionReady = "true";

    const ctx = gsap.context(() => {
      const q = (name: string) => `[data-anim="${name}"]`;
      const tl = gsap.timeline({
        defaults: { ease: "power3.out", duration: 0.85, force3D: true },
        onComplete: () => {
          // Release the compositor layers — holding promoted layers for the
          // rest of the session costs memory and buys nothing once still.
          gsap.set("[data-anim]", { clearProps: "willChange,transform" });
          root.removeAttribute("data-motion");
        },
      });

      // The painting settles out of an overscale. Transform only, so this is
      // one composited layer moving rather than a repaint of the viewport.
      tl.fromTo(
        q("hero"),
        { opacity: 0, scale: 1.045 },
        { opacity: 1, scale: 1, duration: 1.5, ease: "power2.out" },
      );

      // Only the gradient scrims fade — the backdrop-filter stack underneath
      // them is static, because animating its opacity re-runs the blur.
      tl.fromTo(q("veil"), { opacity: 0 }, { opacity: 1, duration: 1.1, stagger: 0.1 }, 0.2);

      tl.fromTo(q("lockup"), { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 0.95 }, 0.45);

      // Per line, not per word: the headline is two clauses and the break is
      // deliberate, so the stagger lands on the meaning.
      tl.fromTo(
        q("headline-line"),
        { opacity: 0, y: 20 },
        { opacity: 1, y: 0, duration: 1, stagger: 0.09 },
        0.58,
      );

      tl.fromTo(q("body"), { opacity: 0, y: 12 }, { opacity: 1, y: 0 }, 0.85);

      tl.fromTo(
        q("meta-item"),
        { opacity: 0, y: 8 },
        { opacity: 1, y: 0, duration: 0.65, stagger: 0.05 },
        1.0,
      );

      tl.fromTo(q("credit"), { opacity: 0, y: 6 }, { opacity: 1, y: 0 }, 1.28);

      // The brackets around the status keep breathing after the entrance:
      // the only motion that outlives the load, on the one line that is
      // genuinely live. Opacity only — scaling type forces a re-raster.
      gsap.to(q("status-bracket"), {
        opacity: 0.3,
        duration: 1.7,
        delay: 2,
        ease: "sine.inOut",
        repeat: -1,
        yoyo: true,
      });
    });

    return () => ctx.revert();
  }, []);

  return null;
}
