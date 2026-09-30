"use client";

import { useLayoutEffect } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

// Motion for /v2.
//
// Ownership is the whole design. Before hydration, CSS hides every
// [data-anim] node behind html[data-motion="on"] so nothing flashes. The
// moment this effect runs, GSAP writes an explicit inline starting state onto
// every node it will animate, and the CSS gate is removed. From then on
// nothing can be left invisible by a selector that no tween covers — the
// failure the first version shipped with, where the whole stack diagram sat
// at opacity 0 forever because no timeline ever touched it.
//
// The headline is animated as one element. It paints with
// background-clip:text, and Chromium stops painting that background through
// descendants that get their own compositing layer — so transforming the two
// lines individually left the headline transparent over nothing.
export function V2Motion() {
  useLayoutEffect(() => {
    const root = document.documentElement;
    if (root.dataset.motion !== "on") return;

    root.dataset.motionReady = "true";
    gsap.registerPlugin(ScrollTrigger);

    const ctx = gsap.context(() => {
      const hero = gsap.timeline({ defaults: { ease: "power3.out", duration: 0.8 } });

      hero
        .fromTo('[data-anim="chip"]', { opacity: 0, y: 8 }, { opacity: 1, y: 0, duration: 0.6 })
        .fromTo(
          '[data-anim="title"]',
          { opacity: 0, y: 18 },
          { opacity: 1, y: 0, duration: 0.95, clearProps: "transform" },
          0.08,
        )
        .fromTo('[data-anim="lede"]', { opacity: 0, y: 10 }, { opacity: 1, y: 0 }, 0.22)
        // Plates settle bottom-up: the foundation first, then what rests on it.
        .fromTo(
          '[data-anim="plate"]',
          { opacity: 0, y: -14 },
          { opacity: 1, y: 0, duration: 0.7, stagger: { each: 0.07, from: "end" } },
          0.35,
        )
        .fromTo('[data-anim="lead"]', { opacity: 0 }, { opacity: 1, duration: 0.5, stagger: 0.05 }, 0.8);

      // Everything below the fold: start state set now, reveal once on entry.
      gsap.utils.toArray<HTMLElement>('[data-anim="rise"]').forEach((el) => {
        gsap.set(el, { opacity: 0, y: 14 });
        ScrollTrigger.create({
          trigger: el,
          start: "top 90%",
          once: true,
          onEnter: () =>
            gsap.to(el, { opacity: 1, y: 0, duration: 0.7, ease: "power2.out", clearProps: "transform" }),
        });
      });

      // Figures count up once. The real value stays in the markup until the
      // trigger fires, so a missed trigger leaves the true number on screen
      // rather than a zero.
      gsap.utils.toArray<HTMLElement>("[data-count]").forEach((el) => {
        const raw = el.dataset.count ?? "";
        const target = Number(raw);
        if (!Number.isFinite(target)) return;
        const decimals = raw.split(".")[1]?.length ?? 0;
        const format = (v: number) =>
          decimals ? v.toFixed(decimals) : Math.round(v).toLocaleString("en-US");
        ScrollTrigger.create({
          trigger: el,
          start: "top 92%",
          once: true,
          onEnter: () => {
            const state = { v: 0 };
            gsap.to(state, {
              v: target,
              duration: 1.5,
              ease: "power2.out",
              onUpdate: () => {
                el.textContent = format(state.v);
              },
              onComplete: () => {
                el.textContent = format(target);
              },
            });
          },
        });
      });

      // Services rail tracks the row in view, as their product list does.
      const rail = gsap.utils.toArray<HTMLElement>("[data-rail]");
      gsap.utils.toArray<HTMLElement>("[data-project]").forEach((row) => {
        ScrollTrigger.create({
          trigger: row,
          start: "top 55%",
          end: "bottom 55%",
          onToggle: ({ isActive }) => {
            if (!isActive) return;
            rail.forEach((item) => {
              item.toggleAttribute("data-active", item.dataset.rail === row.dataset.project);
            });
          },
        });
      });

      gsap.to('[data-anim="pulse"]', {
        opacity: 0.3,
        duration: 1.4,
        ease: "sine.inOut",
        repeat: -1,
        yoyo: true,
      });
    });

    // Every animated node now carries its own inline state. Drop the gate so
    // anything not animated is simply visible.
    root.removeAttribute("data-motion");

    return () => ctx.revert();
  }, []);

  return null;
}
