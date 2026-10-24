import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import "./landing.css";

/** Motion for the landing page. The HTML is complete without this file (crawlers and no-JS readers see everything);
 *  this only animates it. Respects prefers-reduced-motion. */

gsap.registerPlugin(ScrollTrigger);
const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const finePointer = window.matchMedia("(pointer: fine)").matches;
document.body.classList.add("lp-js");

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
  gsap.set("[data-reveal], [data-hero-fade], [data-hero-kicker], .lp-canvas", { opacity: 1 });
} else {
  const intro = gsap.timeline({ defaults: { ease: "expo.out" } });
  intro
    .from("[data-hero-kicker]", { y: 16, opacity: 0, duration: 0.9 })
    .from(words, { yPercent: 115, rotate: 4, duration: 1.3, stagger: 0.06 }, "-=0.6")
    .from("[data-hero-fade]", { y: 24, opacity: 0, duration: 1.1, stagger: 0.12 }, "-=0.9")
    .to(".lp-canvas", { opacity: 1, duration: 2, ease: "power2.out" }, 0.2);

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

  // marquee speeds up with scroll velocity
  const track = document.querySelector<HTMLElement>("[data-marquee]");
  if (track) {
    ScrollTrigger.create({
      onUpdate: (self) => {
        const v = Math.min(Math.abs(self.getVelocity()) / 1200, 3);
        track.style.animationDuration = `${38 / (1 + v)}s`;
      },
    });
  }
}

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

// ---- the 3D scene loads after the page is usable, and only where WebGL works ----
const canvas = document.querySelector<HTMLCanvasElement>("[data-scene]");
if (canvas) {
  const start = () =>
    import("./scene")
      .then((m) => m.mountScene(canvas, { reduced }))
      .catch(() => canvas.remove());
  if ("requestIdleCallback" in window) (window as Window & { requestIdleCallback: (cb: () => void) => void }).requestIdleCallback(start);
  else setTimeout(start, 200);
}
