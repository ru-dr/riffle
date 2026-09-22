"use client";

import { useLayoutEffect } from "react";
import gsap from "gsap";

// Entrance choreography for the landing page.
//
// Side-effect only: it renders nothing and reaches for `[data-anim]` nodes so
// the page itself stays a Server Component and ships no extra markup.
//
// The order is the reading order — painting, then scrims, then lockup,
// headline, body, meta row, credit. Everything overlaps slightly, because a
// strict sequence reads as a slideshow rather than one movement.
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
        defaults: { ease: "power3.out", duration: 0.9 },
        onComplete: () => {
          // Drop the compositing hints and the blur filter entirely — a
          // persistent filter on a full-bleed image costs a layer for the
          // rest of the session.
          gsap.set("[data-anim]", { clearProps: "filter,willChange" });
          root.removeAttribute("data-motion");
        },
      });

      // The painting settles: slight overscale out, blur off. Long and slow,
      // so it is still moving underneath the type that follows.
      tl.fromTo(
        q("hero"),
        { opacity: 0, scale: 1.06, filter: "blur(18px)" },
        {
          opacity: 1,
          scale: 1,
          filter: "blur(0px)",
          duration: 1.7,
          ease: "power2.out",
        },
      );

      // Scrim, progressive blur stack, and edge falloff fade in over the
      // painting rather than with it, so the corner darkens as you watch.
      tl.fromTo(
        q("veil"),
        { opacity: 0 },
        { opacity: 1, duration: 1.2, stagger: 0.12 },
        0.25,
      );

      tl.fromTo(
        q("lockup"),
        { opacity: 0, y: 16, filter: "blur(10px)" },
        { opacity: 1, y: 0, filter: "blur(0px)", duration: 1 },
        0.55,
      );

      // Per line, not per word: the headline is two clauses and the break is
      // deliberate, so the stagger lands on the meaning.
      tl.fromTo(
        q("headline-line"),
        { opacity: 0, y: 22, filter: "blur(12px)" },
        { opacity: 1, y: 0, filter: "blur(0px)", duration: 1.05, stagger: 0.1 },
        0.7,
      );

      tl.fromTo(
        q("body"),
        { opacity: 0, y: 14, filter: "blur(8px)" },
        { opacity: 1, y: 0, filter: "blur(0px)" },
        1.0,
      );

      tl.fromTo(
        q("meta-item"),
        { opacity: 0, y: 10 },
        { opacity: 1, y: 0, duration: 0.7, stagger: 0.06 },
        1.15,
      );

      tl.fromTo(q("credit"), { opacity: 0, y: 8 }, { opacity: 1, y: 0 }, 1.45);

      // The status dot keeps breathing after the entrance: the only motion
      // that outlives the load, and the one thing on the page that is
      // genuinely live.
      gsap.to(q("status-dot"), {
        opacity: 0.35,
        scale: 0.82,
        duration: 1.6,
        delay: 2.2,
        ease: "sine.inOut",
        repeat: -1,
        yoyo: true,
        transformOrigin: "50% 50%",
      });
    });

    return () => ctx.revert();
  }, []);

  return null;
}
