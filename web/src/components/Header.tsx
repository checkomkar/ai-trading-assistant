import { useEffect, useState } from "react";
import type { SocketStatus } from "../hooks/useSocket";
import type { FeedStatus } from "../api";
import { istTime } from "../format";

const SESSION_STYLE: Record<string, string> = {
  PRE_OPEN: "bg-accent/15 text-accent ring-accent/30",
  OPEN: "bg-up/15 text-up ring-up/30",
  TRADING: "bg-up/15 text-up ring-up/30",
  CLOSING: "bg-accent/15 text-accent ring-accent/30",
  CLOSED: "bg-ink-700/60 text-ink-300 ring-ink-600",
};

const SESSION_LABEL: Record<string, string> = {
  PRE_OPEN: "Pre-open",
  OPEN: "Market open",
  TRADING: "Market open",
  CLOSING: "Closing session",
  CLOSED: "Market closed",
};

interface Props {
  session: string | null;
  simulated: boolean | null;
  provider: string | null;
  socket: SocketStatus;
  feed: FeedStatus | null;
  mode: string | null;
  view: "market" | "paper" | "backtest" | "strategies";
}

export function Header({ session, simulated, provider, socket, feed, mode, view }: Props) {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const live = socket === "open" && feed?.connected;
  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-ink-800 bg-ink-900/80 px-4 py-2.5 backdrop-blur sm:px-6">
      <div className="flex items-center gap-2.5">
        <div className="grid h-7 w-7 place-items-center rounded-md bg-accent text-[13px] font-bold text-ink-950">N</div>
        <span className="font-semibold tracking-tight">NIFTY AI Trader</span>
      </div>

      <nav className="flex gap-1 rounded-md bg-ink-850 p-0.5 text-xs" aria-label="Sections">
        {([["market", "Market", "#/"], ["paper", "Paper", "#/paper"], ["strategies", "Strategies", "#/strategies"], ["backtest", "Backtest", "#/backtest"]] as const).map(([id, label, href]) => (
          <a
            key={id}
            href={href}
            aria-current={view === id ? "page" : undefined}
            className={`rounded px-3 py-1 font-medium ${view === id ? "bg-ink-700 text-ink-100" : "text-ink-400 hover:text-ink-100"}`}
          >
            {label}
          </a>
        ))}
      </nav>

      {simulated && (
        <span
          className="rounded px-2 py-0.5 text-[11px] font-semibold tracking-widest text-sim ring-1 ring-sim/40 bg-sim/10"
          title="Prices come from the built-in simulator, not the exchange"
        >
          SIMULATED DATA
        </span>
      )}
      {provider === "replay" && (
        <span
          className="rounded px-2 py-0.5 text-[11px] font-semibold tracking-widest text-sim ring-1 ring-sim/40 bg-sim/10"
          title="A stored day is being replayed through the live pipeline; option quotes are synthetic"
        >
          REPLAY · SYNTHETIC OPTIONS
        </span>
      )}
      {simulated === false && provider && provider !== "replay" && (
        <span className="rounded px-2 py-0.5 text-[11px] font-semibold tracking-widest text-up ring-1 ring-up/40 bg-up/10">
          LIVE · {provider.toUpperCase()}
        </span>
      )}
      {mode && (
        <span className="rounded px-2 py-0.5 text-[11px] font-semibold tracking-widest text-ink-300 ring-1 ring-ink-600">
          {mode}
        </span>
      )}

      <div className="ml-auto flex items-center gap-4 text-xs">
        {session && (
          <span className={`rounded-full px-2.5 py-1 font-medium ring-1 ${SESSION_STYLE[session] ?? SESSION_STYLE.CLOSED}`}>
            {SESSION_LABEL[session] ?? session}
          </span>
        )}
        <span className="flex items-center gap-1.5 text-ink-300" title={`WebSocket: ${socket}`}>
          <span className={`relative flex h-2 w-2`}>
            {live && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-up opacity-60" />}
            <span className={`relative inline-flex h-2 w-2 rounded-full ${live ? "bg-up" : socket === "connecting" ? "bg-accent" : "bg-down"}`} />
          </span>
          {live ? (
            <span className="num">{feed?.ticks_per_second.toFixed(0)} ticks/s</span>
          ) : socket === "connecting" ? (
            "Connecting…"
          ) : (
            "Disconnected"
          )}
        </span>
        <span className="num text-ink-300" title="India Standard Time">
          {istTime(now)} <span className="text-ink-400">IST</span>
        </span>
      </div>
    </header>
  );
}
