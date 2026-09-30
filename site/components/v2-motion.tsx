"use client";

import { useEffect, useLayoutEffect } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

// Motion for the landing page.
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
  // Behaviour that is not decoration, so it runs whatever the motion
  // preference or the state of the page's motion gate: smooth scrolling,
  // anchor navigation, and the services rail following the row in view.
  // The first version put the rail inside the gated effect, so any page load
  // where the gate had already dropped (slow hydration, the failsafe) left
  // the rail frozen on its first entry.
  useEffect(() => {
    gsap.registerPlugin(ScrollTrigger);
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

    // Native scrolling. Lenis interpolated every wheel tick over ~1s, and
    // that trailing catch-up is what read as lag; native scroll runs on the
    // compositor thread. Anchors still glide, via the browser's own smooth
    // scroll, and honour reduced motion.
    const onClick = (e: MouseEvent) => {
      const a = (e.target as HTMLElement).closest<HTMLAnchorElement>("a[data-scroll-to]");
      if (!a) return;
      const hash = a.getAttribute("href");
      if (!hash?.startsWith("#")) return;
      const target = document.querySelector<HTMLElement>(hash);
      if (!target) return;
      e.preventDefault();
      const y = target.getBoundingClientRect().top + window.scrollY - 24;
      window.scrollTo({ top: y, behavior: reduce ? "auto" : "smooth" });
      history.replaceState(null, "", hash);
    };
    document.addEventListener("click", onClick);

    // Rail: the service whose row crosses 40% down the viewport is live.
    // Rows are ~465px, shorter than half a screen, so a 55% line fell past
    // the bottom of a row parked at the top — clicking "explainer" lit "app".
    const rail = gsap.utils.toArray<HTMLElement>("[data-rail]");
    const triggers = gsap.utils.toArray<HTMLElement>("[data-project]").map((row) =>
      ScrollTrigger.create({
        trigger: row,
        start: "top 40%",
        end: "bottom 40%",
        onToggle: ({ isActive }) => {
          if (!isActive) return;
          rail.forEach((item) => item.toggleAttribute("data-active", item.dataset.rail === row.dataset.project));
        },
      }),
    );

    return () => {
      document.removeEventListener("click", onClick);
      triggers.forEach((t) => t.kill());
    };
  }, []);

  useLayoutEffect(() => {
    const root = document.documentElement;
    if (root.dataset.motion !== "on") return;

    root.dataset.motionReady = "true";
    gsap.registerPlugin(ScrollTrigger);

    const ctx = gsap.context(() => {
      // Load, in reading order. Distances are large enough to register as
      // motion and the eases are power3/4 — expo.out finished ~90% of a
      // small move in the first 150ms, which read as content simply
      // appearing. Transform, opacity and one clip-path reveal only.
      gsap
        .timeline({ defaults: { ease: "power3.out" } })
        .fromTo('[data-anim="nav"]', { opacity: 0, y: -14 }, { opacity: 1, y: 0, duration: 0.9, clearProps: "transform" })
        .fromTo(
          '[data-anim="chip"]',
          { opacity: 0, y: 18, scale: 0.94 },
          { opacity: 1, y: 0, scale: 1, duration: 0.9, clearProps: "transform" },
          0.15,
        )
        // The headline wipes up out of its own baseline. It is animated as
        // one element because it paints with background-clip:text.
        .fromTo(
          '[data-anim="title"]',
          // Horizontal insets are negative so the wipe never clips the last
          // glyph's overhang; the element's own padding gives the paint room.
          { opacity: 0, y: 32, clipPath: "inset(0 -12% 100% -12%)" },
          { opacity: 1, y: 0, clipPath: "inset(-10% -12% -10% -12%)", duration: 1.3, ease: "power4.out", clearProps: "transform,clipPath" },
          0.25,
        )
        .fromTo('[data-anim="lede"]', { opacity: 0, y: 22 }, { opacity: 1, y: 0, duration: 1.1, clearProps: "transform" }, 0.5)
        .fromTo(
          '[data-anim="stackin"]',
          { opacity: 0, y: 60, scale: 0.94 },
          { opacity: 1, y: 0, scale: 1, duration: 1.6, ease: "power4.out", clearProps: "transform" },
          0.6,
        );

      // Below the fold: batched, so elements entering together rise as one
      // staggered group instead of dozens of tweens firing independently.
      const rise = gsap.utils.toArray<HTMLElement>('[data-anim="rise"]');
      gsap.set(rise, { opacity: 0, y: 40 });
      ScrollTrigger.batch(rise, {
        start: "top 88%",
        once: true,
        onEnter: (els) =>
          gsap.to(els, {
            opacity: 1,
            y: 0,
            duration: 1.1,
            ease: "power3.out",
            stagger: 0.09,
            clearProps: "transform",
          }),
      });

      // Figures count up once. The real value stays in the markup until the
      // trigger fires, so a missed trigger leaves the true number on screen.
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
              duration: 1.6,
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
    });

    // Every animated node now carries its own inline state. Drop the gate so
    // anything not animated is simply visible.
    root.removeAttribute("data-motion");

    return () => ctx.revert();
  }, []);

  return null;
}
