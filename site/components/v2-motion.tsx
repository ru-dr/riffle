"use client";

import { useEffect, useLayoutEffect } from "react";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Lenis from "lenis";

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

    // Lenis for momentum scrolling, driven from GSAP's ticker so ScrollTrigger
    // and the scroller agree on every frame. Reduced motion keeps native.
    let lenis: Lenis | null = null;
    const tick = (t: number) => lenis?.raf(t * 1000);
    if (!reduce) {
      lenis = new Lenis({ duration: 1.15, smoothWheel: true, autoRaf: false });
      lenis.on("scroll", ScrollTrigger.update);
      gsap.ticker.add(tick);
      gsap.ticker.lagSmoothing(0);
    }

    // In-page anchors glide instead of jumping. Offset clears nothing sticky
    // today, but keeps headings off the very top edge.
    const onClick = (e: MouseEvent) => {
      const a = (e.target as HTMLElement).closest<HTMLAnchorElement>("a[data-scroll-to]");
      if (!a) return;
      const hash = a.getAttribute("href");
      if (!hash?.startsWith("#")) return;
      const target = document.querySelector<HTMLElement>(hash);
      if (!target) return;
      e.preventDefault();
      if (lenis) lenis.scrollTo(target, { offset: -24, duration: 1.4 });
      else target.scrollIntoView({ block: "start" });
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
      gsap.ticker.remove(tick);
      lenis?.destroy();
    };
  }, []);

  useLayoutEffect(() => {
    const root = document.documentElement;
    if (root.dataset.motion !== "on") return;

    root.dataset.motionReady = "true";
    gsap.registerPlugin(ScrollTrigger);

    const ctx = gsap.context(() => {
      // Load: one quiet movement in reading order. Transform and opacity
      // only, short distances, long eases — nothing lands hard, and nothing
      // animates a property that costs a layout or a repaint.
      gsap
        .timeline({ defaults: { ease: "expo.out", duration: 1.1 } })
        .fromTo('[data-anim="nav"]', { opacity: 0, y: -6 }, { opacity: 1, y: 0, duration: 0.9, clearProps: "transform" })
        .fromTo('[data-anim="chip"]', { opacity: 0, y: 8 }, { opacity: 1, y: 0, clearProps: "transform" }, 0.12)
        .fromTo('[data-anim="title"]', { opacity: 0, y: 14 }, { opacity: 1, y: 0, duration: 1.3, clearProps: "transform" }, 0.2)
        .fromTo('[data-anim="lede"]', { opacity: 0, y: 10 }, { opacity: 1, y: 0, clearProps: "transform" }, 0.34)
        .fromTo('[data-anim="stackin"]', { opacity: 0, y: 20 }, { opacity: 1, y: 0, duration: 1.4, clearProps: "transform" }, 0.46);

      // Below the fold: batched, so elements entering together rise as one
      // staggered group instead of dozens of tweens firing independently.
      const rise = gsap.utils.toArray<HTMLElement>('[data-anim="rise"]');
      gsap.set(rise, { opacity: 0, y: 12 });
      ScrollTrigger.batch(rise, {
        start: "top 90%",
        once: true,
        onEnter: (els) =>
          gsap.to(els, {
            opacity: 1,
            y: 0,
            duration: 0.9,
            ease: "power3.out",
            stagger: 0.06,
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
