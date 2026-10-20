import { useEffect, useMemo, useRef, useState } from "react";
import { getJSON, type ChainRow, type Expiries, type OptionChain as Chain, type OptionLeg } from "../api";
import { useSocket } from "../hooks/useSocket";
import { fmtCompact, fmtIV, fmtPrice, trendClass } from "../format";

const WINDOWS = [5, 10, 15, 20];

interface Props {
  underlying: string;
  onUnderlying: (u: string) => void;
}

export function OptionChain({ underlying, onUnderlying }: Props) {
  const [expiries, setExpiries] = useState<Expiries | null>(null);
  const [expiry, setExpiry] = useState<string | null>(null);
  const [width, setWidth] = useState(10);
  const [chain, setChain] = useState<Chain | null>(null);
  const atmRef = useRef<HTMLTableRowElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const scrolledFor = useRef<string>("");

  useEffect(() => {
    setChain(null);
    setExpiry(null);
    getJSON<Expiries>(`/options/${underlying}/expiries`).then((e) => {
      setExpiries(e);
      setExpiry(e.listed[0] ?? null);
    });
  }, [underlying]);

  const sub = useMemo(() => ({ underlying, expiry, strikes: width }), [underlying, expiry, width]);
  const status = useSocket<Chain & { type: string }>(
    "/ws/options",
    (msg) => {
      if (msg.type !== "chain" || msg.underlying !== underlying) return;
      if (expiry && msg.expiry !== expiry) return;
      setChain(msg);
    },
    expiry ? sub : undefined,
  );

  // Centre the ATM row once per underlying/expiry, not on every refresh.
  useEffect(() => {
    const key = `${underlying}:${expiry}`;
    const box = scrollRef.current;
    const row = atmRef.current;
    if (chain && box && row && scrolledFor.current !== key) {
      // Scroll only the table container — scrollIntoView would also scroll the page.
      box.scrollTop = row.offsetTop - box.clientHeight / 2 + row.clientHeight / 2;
      scrolledFor.current = key;
    }
  }, [chain, underlying, expiry]);

  const maxOI = useMemo(() => {
    let m = 1;
    chain?.rows.forEach((r) => (m = Math.max(m, r.ce?.oi ?? 0, r.pe?.oi ?? 0)));
    return m;
  }, [chain]);

  const s = chain?.summary;
  return (
    <section className="flex min-h-0 flex-col rounded-lg border border-ink-800 bg-ink-900">
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-800 px-3 py-2">
        <h2 className="mr-1 text-sm font-semibold">Option chain</h2>
        <div className="flex gap-1">
          {["NIFTY", "BANKNIFTY"].map((u) => (
            <button
              key={u}
              onClick={() => onUnderlying(u)}
              className={`rounded px-2.5 py-1 text-xs font-medium ${underlying === u ? "bg-ink-700 text-ink-100" : "text-ink-400 hover:text-ink-100"}`}
            >
              {u}
            </button>
          ))}
        </div>
        <select
          value={expiry ?? ""}
          onChange={(e) => setExpiry(e.target.value)}
          className="num rounded border border-ink-700 bg-ink-850 px-2 py-1 text-xs text-ink-100 outline-none focus:border-accent"
          aria-label="Expiry"
        >
          {expiries?.listed.map((d) => (
            <option key={d} value={d}>
              {new Date(d + "T00:00:00").toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "2-digit" })}
            </option>
          ))}
        </select>
        {expiries && (
          <span className="text-[11px] text-ink-400">
            {expiries.cycle.toLowerCase()} · lot {expiries.lot_size}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1 text-[11px] text-ink-400">
          ±
          {WINDOWS.map((w) => (
            <button
              key={w}
              onClick={() => setWidth(w)}
              className={`num rounded px-1.5 py-0.5 ${width === w ? "bg-ink-700 text-ink-100" : "hover:text-ink-100"}`}
            >
              {w}
            </button>
          ))}
          <span className={`ml-2 h-1.5 w-1.5 rounded-full ${status === "open" ? "bg-up" : "bg-accent"}`} title={`Chain stream: ${status}`} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-px border-b border-ink-800 bg-ink-800 text-xs sm:grid-cols-5">
        <Stat label="Spot" value={fmtPrice(chain?.spot)} />
        <Stat label="PCR (OI)" value={s?.pcr?.toFixed(2) ?? "—"} tone={s?.pcr == null ? "" : s.pcr > 1 ? "text-up" : s.pcr < 0.7 ? "text-down" : ""} />
        <Stat label="ATM IV" value={s?.atm_iv != null ? `${fmtIV(s.atm_iv)}%` : "—"} />
        <Stat label="Max call OI" value={s?.max_call_oi_strike?.toFixed(0) ?? "—"} hint="resistance" tone="text-down" />
        <Stat label="Max put OI" value={s?.max_put_oi_strike?.toFixed(0) ?? "—"} hint="support" tone="text-up" />
      </div>

      <div ref={scrollRef} className="relative max-h-[560px] min-h-0 flex-1 overflow-auto xl:max-h-none">
        <table className="num w-full min-w-[720px] border-collapse text-xs">
          <thead className="sticky top-0 z-10 bg-ink-850 text-[10px] uppercase tracking-wider text-ink-400">
            <tr>
              <th colSpan={4} className="border-b border-ink-700 py-1 text-center font-medium text-ink-300">Calls</th>
              <th className="border-b border-ink-700" />
              <th colSpan={4} className="border-b border-ink-700 py-1 text-center font-medium text-ink-300">Puts</th>
            </tr>
            <tr className="[&>th]:px-2 [&>th]:py-1.5 [&>th]:font-medium">
              <th className="text-left">OI</th>
              <th className="text-right">ΔOI</th>
              <th className="text-right">IV</th>
              <th className="text-right">LTP</th>
              <th className="text-center text-ink-100">Strike</th>
              <th className="text-left">LTP</th>
              <th className="text-left">IV</th>
              <th className="text-left">ΔOI</th>
              <th className="text-right">OI</th>
            </tr>
          </thead>
          <tbody>
            {!chain && (
              <tr>
                <td colSpan={9} className="py-10 text-center text-ink-400">Loading chain…</td>
              </tr>
            )}
            {chain?.rows.map((r) => (
              <Row key={r.strike} row={r} spot={chain.spot} atm={chain.atm_strike} maxOI={maxOI} rowRef={r.strike === chain.atm_strike ? atmRef : undefined} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Stat({ label, value, hint, tone = "" }: { label: string; value: string; hint?: string; tone?: string }) {
  return (
    <div className="bg-ink-900 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-ink-400">
        {label} {hint && <span className="normal-case tracking-normal text-ink-600">· {hint}</span>}
      </div>
      <div className={`num mt-0.5 text-sm font-medium ${tone}`}>{value}</div>
    </div>
  );
}

function OIBar({ leg, maxOI, side }: { leg: OptionLeg | null; maxOI: number; side: "ce" | "pe" }) {
  const pct = leg?.oi ? (leg.oi / maxOI) * 100 : 0;
  return (
    <td className={`relative px-2 py-1 ${side === "ce" ? "text-left" : "text-right"}`}>
      <div
        className={`absolute inset-y-1 ${side === "ce" ? "right-0 bg-down/20" : "left-0 bg-up/20"} rounded-sm`}
        style={{ width: `${pct}%` }}
      />
      <span className="relative">{fmtCompact(leg?.oi)}</span>
    </td>
  );
}

function Row({
  row,
  spot,
  atm,
  maxOI,
  rowRef,
}: {
  row: ChainRow;
  spot: number | null;
  atm: number | null;
  maxOI: number;
  rowRef?: React.Ref<HTMLTableRowElement>;
}) {
  const isAtm = row.strike === atm;
  const ceITM = spot != null && row.strike < spot;
  const peITM = spot != null && row.strike > spot;
  const itmBg = "bg-accent/[0.04]";
  return (
    <tr ref={rowRef} className={`border-b border-ink-800/70 hover:bg-ink-800/60 ${isAtm ? "bg-ink-800" : ""}`}>
      <OIBar leg={row.ce} maxOI={maxOI} side="ce" />
      <td className={`px-2 py-1 text-right ${trendClass(row.ce?.oi_change)} ${ceITM ? itmBg : ""}`}>{fmtCompact(row.ce?.oi_change)}</td>
      <td className={`px-2 py-1 text-right text-ink-300 ${ceITM ? itmBg : ""}`}>{fmtIV(row.ce?.iv)}</td>
      <td className={`px-2 py-1 text-right font-medium ${ceITM ? itmBg : ""}`}>{fmtPrice(row.ce?.ltp)}</td>
      <td className={`px-3 py-1 text-center font-semibold ${isAtm ? "text-accent" : "text-ink-100"} bg-ink-850`}>
        {row.strike.toFixed(0)}
      </td>
      <td className={`px-2 py-1 text-left font-medium ${peITM ? itmBg : ""}`}>{fmtPrice(row.pe?.ltp)}</td>
      <td className={`px-2 py-1 text-left text-ink-300 ${peITM ? itmBg : ""}`}>{fmtIV(row.pe?.iv)}</td>
      <td className={`px-2 py-1 text-left ${trendClass(row.pe?.oi_change)} ${peITM ? itmBg : ""}`}>{fmtCompact(row.pe?.oi_change)}</td>
      <OIBar leg={row.pe} maxOI={maxOI} side="pe" />
    </tr>
  );
}
