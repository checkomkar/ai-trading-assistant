const inr = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const intIN = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export const fmtPrice = (v: number | null | undefined) => (v == null ? "—" : inr.format(v));
export const fmtInt = (v: number | null | undefined) => (v == null ? "—" : intIN.format(v));

/** Indian compact notation: 1.2Cr, 34.5L, 12.3K. */
export function fmtCompact(v: number | null | undefined): string {
  if (v == null) return "—";
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e7) return `${sign}${(a / 1e7).toFixed(2)}Cr`;
  if (a >= 1e5) return `${sign}${(a / 1e5).toFixed(1)}L`;
  if (a >= 1e3) return `${sign}${(a / 1e3).toFixed(1)}K`;
  return `${sign}${a.toFixed(0)}`;
}

export const fmtSigned = (v: number | null | undefined, digits = 2) =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;

export const fmtPct = (v: number | null | undefined) => (v == null ? "—" : `${fmtSigned(v)}%`);

export const fmtIV = (v: number | null | undefined) => (v == null ? "—" : (v * 100).toFixed(1));

export function istTime(iso: string | Date): string {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  return d.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false });
}

export const trendClass = (v: number | null | undefined) =>
  v == null || v === 0 ? "text-ink-300" : v > 0 ? "text-up" : "text-down";
