// Generates public/hero-candles.svg: the landing hero's background chart, a candlestick strip with a moving average.
// The strip is two identical tiles side by side and every price returns to where it started, so sliding it left by
// half its width (CSS) loops without a seam. Seeded, so the picture is the same every time it is regenerated.
//
//   node scripts/hero-candles.mjs
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const N = 64; // candles per tile
const STEP = 30; // px per candle
const BODY = 16; // body width
const W = N * STEP; // tile width
const H = 360;
const PAD = 40;
const UP = "#22c55e";
const DOWN = "#f43f5e";
const MA = "#f5b301";

let seed = 20261025;
const rand = () => (seed = (seed * 16807) % 2147483647) / 2147483647;

// a walk with trends that come and go, then the drift removed so the tile ends at its starting price
const closes = [];
let p = 0;
let drift = 0;
for (let i = 0; i < N; i++) {
  drift = drift * 0.88 + (rand() - 0.5) * 0.9;
  p += drift + (rand() - 0.5) * 1.6;
  closes.push(p);
}
const end = closes[N - 1];
for (let i = 0; i < N; i++) closes[i] -= (end * (i + 1)) / N; // closes[N-1] = 0 = the open of candle 0

const candles = closes.map((c, i) => {
  const o = i === 0 ? 0 : closes[i - 1];
  const h = Math.max(o, c) + rand() * 1.4;
  const l = Math.min(o, c) - rand() * 1.4;
  return { o, h, l, c };
});
const lo = Math.min(...candles.map((k) => k.l));
const hi = Math.max(...candles.map((k) => k.h));
const y = (v) => PAD + ((hi - v) / (hi - lo)) * (H - 2 * PAD);
const r = (v) => Math.round(v * 10) / 10;

// EMA over three laps so the middle lap has no warm-up, then the same values on both tiles
const ema = [];
let e = closes[0];
for (let lap = 0; lap < 3; lap++)
  for (let i = 0; i < N; i++) {
    e += (closes[i] - e) * (2 / 13);
    if (lap === 2) ema.push(e);
  }

let body = "";
for (let t = 0; t < 2; t++) {
  for (let i = 0; i < N; i++) {
    const k = candles[i];
    const x = t * W + i * STEP + STEP / 2;
    const col = k.c >= k.o ? UP : DOWN;
    const top = y(Math.max(k.o, k.c));
    const bh = Math.max(y(Math.min(k.o, k.c)) - top, 2);
    body += `<line x1="${r(x)}" x2="${r(x)}" y1="${r(y(k.h))}" y2="${r(y(k.l))}" stroke="${col}" stroke-width="2"/>`;
    body += `<rect x="${r(x - BODY / 2)}" y="${r(top)}" width="${BODY}" height="${r(bh)}" rx="2" fill="${col}"/>`;
  }
}
const pts = [];
for (let t = 0; t < 2; t++) for (let i = 0; i < N; i++) pts.push(`${r(t * W + i * STEP + STEP / 2)},${r(y(ema[i]))}`);
pts.push(`${r(2 * W + STEP / 2)},${r(y(ema[0]))}`); // close the loop into the next lap

const svg =
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${2 * W} ${H}" width="${2 * W}" height="${H}" fill="none">` +
  `<g>${body}</g><polyline points="${pts.join(" ")}" stroke="${MA}" stroke-width="2.5" stroke-linejoin="round" opacity=".85"/></svg>\n`;

writeFileSync(fileURLToPath(new URL("../public/hero-candles.svg", import.meta.url)), svg);
console.log(`hero-candles.svg: ${2 * N} candles, ${2 * W}x${H}, ${svg.length} bytes`);
