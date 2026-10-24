import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { initAnalytics, pageView, track } from "../analytics";
import { showAccount } from "./account";
import "./landing.css";

/** Motion for the landing page. The HTML is complete without this file (crawlers and no-JS readers see everything);
 *  this only animates it. Respects prefers-reduced-motion. */

gsap.registerPlugin(ScrollTrigger);
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const finePointer = window.matchMedia("(pointer: fine)").matches;
document.body.classList.add("lp-js");

initAnalytics();
showAccount();
pageView("/");
// which call to action people use
document.querySelectorAll<HTMLAnchorElement>("a[href^='/app/']").forEach((a) =>
  a.addEventListener("click", () => track("cta_click", { label: a.textContent?.trim().slice(0, 40), target: a.getAttribute("href") })),
);

// ---- nav: compact once the page scrolls ----
const nav = document.querySelector<HTMLElement>("[data-nav]");
const onScroll = () => nav?.classList.toggle("is-scrolled", window.scrollY > 24);
window.addEventListener("scroll", onScroll, { passive: true });
onScroll();

// ---- split the headline into words that rise into place ----
function splitWords(el: HTMLElement) {
  const walk = (node: Node) => {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === Node.TEXT_NODE) {
        const parts = (child.textContent ?? "").split(/(\s+)/);
        const frag = document.createDocumentFragment();
        for (const p of parts) {
          if (!p) continue;
          if (/^\s+$/.test(p)) {
            frag.append(document.createTextNode(" "));
            continue;
          }
          const w = document.createElement("span");
          w.className = "w";
          const inner = document.createElement("span");
          inner.textContent = p;
          w.append(inner);
          frag.append(w);
        }
        child.replaceWith(frag);
      } else if (child.nodeType === Node.ELEMENT_NODE && (child as Element).tagName !== "BR") {
        walk(child);
      }
    }
  };
  walk(el);
  return el.querySelectorAll<HTMLElement>(".w > span");
}

const headline = document.querySelector<HTMLElement>("[data-split]");
const words = headline ? splitWords(headline) : [];

if (reduced) {
  gsap.set("[data-reveal], [data-hero-fade], [data-hero-kicker], .lp-chart", { opacity: 1 });
} else {
  const intro = gsap.timeline({ defaults: { ease: "expo.out" } });
  intro
    .from("[data-hero-kicker]", { y: 16, opacity: 0, duration: 0.9 })
    .from(words, { yPercent: 115, rotate: 4, duration: 1.3, stagger: 0.06 }, "-=0.6")
    .from("[data-hero-fade]", { y: 24, opacity: 0, duration: 1.1, stagger: 0.12 }, "-=0.9")
    .to(".lp-chart", { opacity: 1, duration: 2, ease: "power2.out" }, 0.2);

  // sections rise in as they enter the viewport
  ScrollTrigger.batch("[data-reveal]", {
    start: "top 88%",
    once: true,
    onEnter: (els) =>
      gsap.fromTo(els, { y: 48, opacity: 0 }, { y: 0, opacity: 1, duration: 1.1, ease: "expo.out", stagger: 0.08, overwrite: true }),
  });

  // lines draw themselves as you scroll past them
  document.querySelectorAll<SVGPathElement>("[data-draw]").forEach((path) => {
    const len = path.getTotalLength();
    gsap.set(path, { strokeDasharray: len, strokeDashoffset: len });
    gsap.to(path, {
      strokeDashoffset: 0,
      ease: "none",
      scrollTrigger: { trigger: path.closest("section") ?? path, start: "top 75%", end: "center 45%", scrub: 0.8 },
    });
  });

  // the hero drifts and fades as it scrolls away
  gsap.to(".lp-hero-inner", {
    yPercent: -18,
    opacity: 0.2,
    ease: "none",
    scrollTrigger: { trigger: ".lp-hero", start: "top top", end: "bottom top", scrub: true },
  });

}

// ---- infographic cards: each plays when it scrolls into view ----
const SVG_NS = "http://www.w3.org/2000/svg";

/** Fill the mini chart with candles from a seeded walk (same picture every visit). */
function buildCandles(svg: SVGSVGElement) {
  let seed = 3;
  const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const n = 22;
  let p = 50;
  const rows: { o: number; c: number; h: number; l: number }[] = [];
  for (let i = 0; i < n; i++) {
    const o = p;
    const c = o + (rand() - 0.42) * 14;
    rows.push({ o, c, h: Math.max(o, c) + rand() * 6, l: Math.min(o, c) - rand() * 6 });
    p = c;
  }
  const lo = Math.min(...rows.map((r) => r.l));
  const hi = Math.max(...rows.map((r) => r.h));
  const y = (v: number) => 84 - ((v - lo) / (hi - lo)) * 78;
  rows.forEach((r, i) => {
    const x = 6 + i * 9.6;
    const g = document.createElementNS(SVG_NS, "g");
    g.setAttribute("class", "lp-cdl");
    const up = r.c >= r.o;
    const color = up ? "#22c55e" : "#f43f5e";
    const wick = document.createElementNS(SVG_NS, "line");
    wick.setAttribute("x1", String(x + 3));
    wick.setAttribute("x2", String(x + 3));
    wick.setAttribute("y1", String(y(r.h)));
    wick.setAttribute("y2", String(y(r.l)));
    wick.setAttribute("stroke", color);
    wick.setAttribute("stroke-width", "1.2");
    const body = document.createElementNS(SVG_NS, "rect");
    body.setAttribute("x", String(x));
    body.setAttribute("width", "6");
    body.setAttribute("y", String(Math.min(y(r.o), y(r.c))));
    body.setAttribute("height", String(Math.max(Math.abs(y(r.o) - y(r.c)), 1.2)));
    body.setAttribute("rx", "1");
    body.setAttribute("fill", color);
    g.append(wick, body);
    svg.append(g);
  });
  return svg.querySelectorAll<SVGGElement>(".lp-cdl");
}

function typeInto(el: HTMLElement, text: string, tl: gsap.core.Timeline) {
  const o = { n: 0 };
  tl.to(o, {
    n: text.length,
    duration: text.length * 0.028,
    ease: "none",
    onUpdate: () => {
      el.textContent = text.slice(0, Math.round(o.n));
    },
  });
}

document.querySelectorAll<HTMLElement>("[data-anim]").forEach((card) => {
  const kind = card.dataset.anim;
  if (kind === "candles") {
    const svg = card.querySelector<SVGSVGElement>("[data-candles]");
    const cdls = svg ? buildCandles(svg) : [];
    if (!reduced) {
      gsap.set(cdls, { scaleY: 0, transformOrigin: "50% 50%", transformBox: "fill-box", opacity: 0 });
      gsap.to(cdls, {
        scaleY: 1,
        opacity: 1,
        duration: 0.5,
        ease: "back.out(2)",
        stagger: 0.05,
        scrollTrigger: { trigger: card, start: "top 80%", once: true },
      });
    }
    return;
  }
  if (reduced) {
    card.classList.add("lp-reduced");
    const t = card.querySelector<HTMLElement>("[data-type]");
    if (t) t.textContent = t.dataset.type ?? "";
    return;
  }
  const tl = gsap.timeline({ paused: true });
  if (kind === "translate") {
    const t = card.querySelector<HTMLElement>("[data-type]")!;
    const chips = card.querySelectorAll(".lp-chips li");
    gsap.set(chips, { opacity: 0.12, y: 0, scale: 1 });
    tl.repeat(-1).repeatDelay(2.2);
    tl.call(() => {
      t.textContent = "";
    });
    tl.to(chips, { opacity: 0.12, y: 10, scale: 0.92, duration: 0.3 });
    typeInto(t, t.dataset.type ?? "", tl);
    tl.fromTo(card.querySelector(".lp-flow"), { x: -8, opacity: 0.3 }, { x: 0, opacity: 1, duration: 0.4 });
    tl.to(chips, { opacity: 1, y: 0, scale: 1, duration: 0.45, ease: "back.out(2.2)", stagger: 0.14 });
  } else if (kind === "folds") {
    const trains = card.querySelectorAll(".lp-folds i");
    const tests = card.querySelectorAll(".lp-folds b");
    tl.from(trains, { scaleX: 0, duration: 0.7, ease: "power3.out", stagger: 0.18 });
    tl.from(tests, { scaleX: 0, opacity: 0, duration: 0.5, ease: "power3.out", stagger: 0.18 }, 0.45);
    // then the out-of-sample windows keep breathing
    tl.call(() => {
      gsap.to(tests, { opacity: 0.6, duration: 1.1, repeat: -1, yoyo: true, ease: "sine.inOut", stagger: 0.25 });
    });
  } else if (kind === "bars") {
    tl.from(card.querySelectorAll(".lp-bars i"), { scaleX: 0, duration: 0.9, ease: "expo.out", stagger: 0.12 });
  } else if (kind === "chips") {
    const chips = card.querySelectorAll(".lp-cloud li");
    tl.from(chips, { opacity: 0, scale: 0.6, duration: 0.4, ease: "back.out(2.5)", stagger: { each: 0.05, from: "random" } });
    chips.forEach((c, i) =>
      gsap.to(c, { y: i % 2 ? -4 : 4, duration: 2 + (i % 4) * 0.4, repeat: -1, yoyo: true, ease: "sine.inOut", delay: i * 0.1 }),
    );
  } else if (kind === "guards") {
    const checks = [...card.querySelectorAll<HTMLElement>(".lp-checks li")];
    const pass = card.querySelector(".lp-pass");
    const dot = card.querySelector(".lp-order-dot");
    tl.repeat(-1).repeatDelay(1.8);
    tl.call(() => checks.forEach((c) => c.classList.remove("on")));
    tl.set(pass, { opacity: 0, x: -10 });
    tl.to(dot, { scale: 1.6, duration: 0.25, yoyo: true, repeat: 1 });
    checks.forEach((c) => tl.call(() => c.classList.add("on"), undefined, "+=0.32"));
    tl.to(pass, { opacity: 1, x: 0, duration: 0.45, ease: "back.out(2)" }, "+=0.25");
  }
  ScrollTrigger.create({ trigger: card, start: "top 80%", once: true, onEnter: () => tl.play() });
});

// ---- counters ----
document.querySelectorAll<HTMLElement>("[data-count]").forEach((el) => {
  const to = Number(el.dataset.count);
  if (reduced || !Number.isFinite(to)) return;
  const o = { v: 0 };
  gsap.to(o, {
    v: to,
    duration: 2,
    delay: 0.9,
    ease: "power3.out",
    onUpdate: () => {
      el.textContent = String(Math.round(o.v));
    },
  });
});

// ---- pointer effects: magnetic buttons, cards that tilt and catch the light ----
if (finePointer && !reduced) {
  document.querySelectorAll<HTMLElement>("[data-magnetic]").forEach((btn) => {
    const x = gsap.quickTo(btn, "x", { duration: 0.5, ease: "power3.out" });
    const y = gsap.quickTo(btn, "y", { duration: 0.5, ease: "power3.out" });
    btn.addEventListener("pointermove", (e) => {
      const r = btn.getBoundingClientRect();
      x((e.clientX - r.left - r.width / 2) * 0.25);
      y((e.clientY - r.top - r.height / 2) * 0.35);
    });
    btn.addEventListener("pointerleave", () => {
      x(0);
      y(0);
    });
  });
  document.querySelectorAll<HTMLElement>("[data-tilt]").forEach((card) => {
    const rx = gsap.quickTo(card, "rotationX", { duration: 0.6, ease: "power3.out" });
    const ry = gsap.quickTo(card, "rotationY", { duration: 0.6, ease: "power3.out" });
    gsap.set(card, { transformPerspective: 900 });
    card.addEventListener("pointermove", (e) => {
      const r = card.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width;
      const py = (e.clientY - r.top) / r.height;
      card.style.setProperty("--mx", `${px * 100}%`);
      card.style.setProperty("--my", `${py * 100}%`);
      rx((0.5 - py) * 6);
      ry((px - 0.5) * 8);
    });
    card.addEventListener("pointerleave", () => {
      rx(0);
      ry(0);
    });
  });
}
