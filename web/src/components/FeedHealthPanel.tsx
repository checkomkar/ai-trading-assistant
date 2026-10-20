import { useEffect, useState } from "react";
import { getJSON, type DataHealth, type FeedHealth } from "../api";
import { fmtCompact } from "../format";

export function FeedHealthPanel() {
  const [feed, setFeed] = useState<FeedHealth | null>(null);
  const [data, setData] = useState<DataHealth | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      Promise.all([getJSON<FeedHealth>("/health/feed"), getJSON<DataHealth>("/health/data")])
        .then(([f, d]) => {
          if (!alive) return;
          setFeed(f);
          setData(d);
          setErr(false);
        })
        .catch(() => alive && setErr(true));
    load();
    const t = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const issues = Object.entries(data?.issues ?? {}).filter(([k]) => k !== "crossed_quote");
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900 text-xs">
      <div className="flex items-center justify-between border-b border-ink-800 px-3 py-2">
        <h2 className="text-sm font-semibold">Data pipeline</h2>
        {err && <span className="text-down">API unreachable</span>}
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 p-3">
        <Item label="Provider" value={feed ? `${feed.provider}${feed.simulated ? " (sim)" : ""}` : "—"} />
        <Item label="Feed" value={feed ? (feed.connected ? "connected" : "down") : "—"} tone={feed?.connected ? "text-up" : "text-down"} />
        <Item label="Subscribed" value={feed?.subscribed ?? "—"} />
        <Item label="Reconnects" value={feed?.reconnects ?? "—"} tone={feed && feed.reconnects > 0 ? "text-accent" : ""} />
        <Item label="Ticks received" value={fmtCompact(feed?.ticks_received)} />
        <Item label="Rejected ticks" value={fmtCompact(data?.rejected)} tone={data && data.rejected > 0 ? "text-accent" : ""} />
        <Item label="Ticks stored" value={fmtCompact(feed?.writes.ticks)} />
        <Item label="Candles stored" value={fmtCompact(feed?.writes.candles)} />
        <Item label="Chain snapshots" value={fmtCompact(feed?.writes.option_snapshots)} />
        <Item label="DB errors" value={feed?.writes.errors ?? "—"} tone={feed && feed.writes.errors > 0 ? "text-down" : ""} />
      </dl>
      <div className="border-t border-ink-800 px-3 py-2">
        <div className="mb-1 text-[10px] uppercase tracking-wider text-ink-400">Last tick age</div>
        <div className="flex flex-wrap gap-3">
          {Object.entries(feed?.last_tick_age_seconds ?? {}).map(([sym, age]) => (
            <span key={sym} className="num">
              <span className="text-ink-400">{sym}</span>{" "}
              <span className={age == null || age > 10 ? "text-down" : age > 3 ? "text-accent" : "text-up"}>
                {age == null ? "—" : `${age.toFixed(1)}s`}
              </span>
            </span>
          ))}
        </div>
        {(issues.length > 0 || (data?.stale_symbols.length ?? 0) > 0) && (
          <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
            {data?.stale_symbols.map((s) => (
              <span key={s} className="rounded bg-down/15 px-1.5 py-0.5 text-down">stale: {s}</span>
            ))}
            {issues.map(([k, v]) => (
              <span key={k} className="rounded bg-ink-800 px-1.5 py-0.5 text-ink-300">
                {k.replace(/_/g, " ")}: <span className="num">{v}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function Item({ label, value, tone = "" }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="text-ink-400">{label}</dt>
      <dd className={`num ${tone}`}>{value}</dd>
    </div>
  );
}
