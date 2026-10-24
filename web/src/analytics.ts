/** Google Analytics 4, for the landing page and the app.
 *
 *  Off unless `VITE_GA_ID` (a "G-..." measurement id) is set at build time, and loaded only after the visitor accepts
 *  analytics cookies in the banner this module shows (the choice is remembered in this browser). Page views are sent
 *  by hand: the landing page sends "/", the app sends "/app/<route>" on every hash change. Nothing about strategies,
 *  results or the account is sent, only page paths and a few named events. */

const ID = (import.meta.env.VITE_GA_ID as string | undefined)?.trim();
const ENABLED = !!ID && /^G-[A-Z0-9]+$/.test(ID);
const CONSENT_KEY = "analytics-consent"; // "granted" | "denied"

type Gtag = (...args: unknown[]) => void;
declare global {
  interface Window {
    dataLayer?: unknown[];
    gtag?: Gtag;
  }
}

let loaded = false;
const queue: [string, Record<string, unknown>][] = [];

function readConsent(): string | null {
  try {
    return localStorage.getItem(CONSENT_KEY);
  } catch {
    return null;
  }
}

function load() {
  if (loaded || !ENABLED) return;
  loaded = true;
  window.dataLayer = window.dataLayer ?? [];
  window.gtag = function gtag() {
    // eslint-disable-next-line prefer-rest-params
    window.dataLayer!.push(arguments);
  };
  window.gtag("js", new Date());
  window.gtag("config", ID, { send_page_view: false, anonymize_ip: true });
  const s = document.createElement("script");
  s.async = true;
  s.src = `https://www.googletagmanager.com/gtag/js?id=${ID}`;
  document.head.append(s);
  for (const [name, params] of queue.splice(0)) window.gtag("event", name, params);
}

export function track(name: string, params: Record<string, unknown> = {}) {
  if (!ENABLED) return;
  if (loaded) window.gtag?.("event", name, params);
  else if (readConsent() !== "denied" && queue.length < 20) queue.push([name, params]);
}

export function pageView(path: string, title = document.title) {
  track("page_view", { page_path: path, page_location: location.origin + path, page_title: title });
}

function setConsent(v: "granted" | "denied") {
  try {
    localStorage.setItem(CONSENT_KEY, v);
  } catch {
    /* storage blocked: the banner will ask again next visit */
  }
  if (v === "granted") load();
  else queue.length = 0;
}

/** The consent banner: plain DOM with inline styles, so it works on the landing page and in the app alike. */
function banner() {
  const box = document.createElement("div");
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-label", "Analytics cookies");
  box.style.cssText =
    "position:fixed;left:16px;bottom:16px;z-index:100;max-width:380px;padding:14px 16px;border-radius:14px;" +
    "background:rgba(15,19,26,.96);border:1px solid #252d3a;color:#e3e8ef;font:13px/1.5 'IBM Plex Sans',system-ui,sans-serif;" +
    "box-shadow:0 20px 50px -20px rgba(0,0,0,.8);backdrop-filter:blur(10px)";
  box.innerHTML =
    '<p style="margin:0 0 10px">We use Google Analytics cookies to see which pages are used, so we can improve them. ' +
    'No strategies or results are shared. <a href="/app/#/privacy" style="color:#f5b301">Privacy</a></p>' +
    '<div style="display:flex;gap:8px;justify-content:flex-end">' +
    '<button data-v="denied" style="padding:6px 12px;border-radius:8px;border:1px solid #344052;background:transparent;color:#9aa7b8;cursor:pointer">Decline</button>' +
    '<button data-v="granted" style="padding:6px 14px;border-radius:8px;border:0;background:#f5b301;color:#0a0d12;font-weight:600;cursor:pointer">Accept</button></div>';
  box.addEventListener("click", (e) => {
    const v = (e.target as HTMLElement).dataset?.v as "granted" | "denied" | undefined;
    if (!v) return;
    setConsent(v);
    box.remove();
  });
  document.body.append(box);
}

/** Call once per page. */
export function initAnalytics() {
  if (!ENABLED) return;
  const c = readConsent();
  if (c === "granted") load();
  else if (c !== "denied") banner();
}
