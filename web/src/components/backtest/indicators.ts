// Client-side indicators for the trade chart. Inputs are [time, open, high, low, close] bars in time order;
// outputs are [time, value] points, starting once the indicator has enough bars (no look-ahead).

export type Bar = [number, number, number, number, number];
export type Pt = [number, number];

export function ema(bars: Bar[], period: number): Pt[] {
  const k = 2 / (period + 1);
  const out: Pt[] = [];
  let v: number | null = null;
  bars.forEach((b, i) => {
    if (i < period - 1) return;
    v = v === null ? bars.slice(0, period).reduce((s, x) => s + x[4], 0) / period : b[4] * k + v * (1 - k);
    out.push([b[0], v]);
  });
  return out;
}

export function sma(bars: Bar[], period: number): Pt[] {
  const out: Pt[] = [];
  let sum = 0;
  bars.forEach((b, i) => {
    sum += b[4];
    if (i >= period) sum -= bars[i - period][4];
    if (i >= period - 1) out.push([b[0], sum / period]);
  });
  return out;
}

export function bollinger(bars: Bar[], period: number, mult: number): { upper: Pt[]; mid: Pt[]; lower: Pt[] } {
  const upper: Pt[] = [], mid: Pt[] = [], lower: Pt[] = [];
  for (let i = period - 1; i < bars.length; i++) {
    const w = bars.slice(i - period + 1, i + 1).map((b) => b[4]);
    const m = w.reduce((s, x) => s + x, 0) / period;
    const sd = Math.sqrt(w.reduce((s, x) => s + (x - m) ** 2, 0) / period);
    const t = bars[i][0];
    upper.push([t, m + mult * sd]);
    mid.push([t, m]);
    lower.push([t, m - mult * sd]);
  }
  return { upper, mid, lower };
}

/** Wilder's RSI. */
export function rsi(bars: Bar[], period: number): Pt[] {
  const out: Pt[] = [];
  let gain = 0, loss = 0;
  for (let i = 1; i < bars.length; i++) {
    const d = bars[i][4] - bars[i - 1][4];
    const g = Math.max(d, 0), l = Math.max(-d, 0);
    if (i <= period) {
      gain += g;
      loss += l;
      if (i < period) continue;
      gain /= period;
      loss /= period;
    } else {
      gain = (gain * (period - 1) + g) / period;
      loss = (loss * (period - 1) + l) / period;
    }
    out.push([bars[i][0], loss === 0 ? 100 : 100 - 100 / (1 + gain / loss)]);
  }
  return out;
}

export function macd(bars: Bar[], fast = 12, slow = 26, signal = 9): { macd: Pt[]; signal: Pt[]; hist: Pt[] } {
  const f = new Map(ema(bars, fast)), s = ema(bars, slow);
  const line: Pt[] = s.map(([t, v]) => [t, (f.get(t) as number) - v]);
  const asBars: Bar[] = line.map(([t, v]) => [t, v, v, v, v]);
  const sig = ema(asBars, signal);
  const lm = new Map(line);
  return { macd: line, signal: sig, hist: sig.map(([t, v]) => [t, (lm.get(t) as number) - v]) };
}
