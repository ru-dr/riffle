"use client";

import { useLayoutEffect } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

// Motion for the /v2 documentation layout.
//
// Two behaviours, both taken from the reference: sections resolve as they
// come into view, and the statistics count up once when their card first
// lands. Nothing parallaxes and nothing repeats on scroll-back — on a page
// this long, motion that replays becomes noise the second time down.
//
// Transform and opacity only. Same html[data-motion="on"] gate and failsafe
// as the poster page, so no-JS and reduced-motion readers get the finished
// document.
export function V2Motion() {
  useLayoutEffect(() => {
    const root = document.documentElement;
    if (root.dataset.motion !== "on") return;

    root.dataset.motionReady = "true";
    gsap.registerPlugin(ScrollTrigger);

    const ctx = gsap.context(() => {
      // Hero: the only sequenced part of the page.
      gsap
        .timeline({ defaults: { ease: "power3.out", duration: 0.8, force3D: true } })
        .fromTo(
          '[data-anim="line"]',
          { opacity: 0, y: 24 },
          { opacity: 1, y: 0, duration: 1, stagger: 0.09 },
        )
        .fromTo(
          // Above the fold, and tagged as such — slicing the first N of a
          // 40-element selector breaks the moment a section is reordered.
          '[data-anim="hero-rise"]',
          { opacity: 0, y: 14 },
          { opacity: 1, y: 0, stagger: 0.07 },
          0.25,
        );

      // Below the fold: reveal on entry, once each.
      gsap.utils.toArray<HTMLElement>('[data-anim="rise"]').forEach((el) => {
        gsap.fromTo(
          el,
          { opacity: 0, y: 16 },
          {
            opacity: 1,
            y: 0,
            duration: 0.7,
            ease: "power2.out",
            scrollTrigger: { trigger: el, start: "top 88%", once: true },
          },
        );
      });

      // Statistics count up. Non-numeric values ("1 in 5") are left alone —
      // animating them would mean animating nonsense.
      gsap.utils.toArray<HTMLElement>("[data-count]").forEach((el) => {
        const target = Number(el.dataset.count);
        if (!Number.isFinite(target)) return;
        const decimals = (el.dataset.count ?? "").split(".")[1]?.length ?? 0;
        const state = { value: 0 };
        gsap.to(state, {
          value: target,
          duration: 1.4,
          ease: "power2.out",
          scrollTrigger: { trigger: el, start: "top 90%", once: true },
          onUpdate: () => {
            el.textContent = state.value.toFixed(decimals);
          },
        });
      });

      // The status dot in the rail is the only thing that keeps moving, and
      // it is the only genuinely live element on the page.
      gsap.to('[data-anim="pulse"]', {
        opacity: 0.3,
        duration: 1.6,
        delay: 1.5,
        ease: "sine.inOut",
        repeat: -1,
        yoyo: true,
      });
    });

    return () => ctx.revert();
  }, []);

  return null;
}
