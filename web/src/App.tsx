import { useCallback, useEffect, useMemo, useState } from "react";
import { getJSON, type CandleMessage, type MarketMessage } from "./api";
import { useSocket } from "./hooks/useSocket";
import { Header } from "./components/Header";
import { QuoteCard } from "./components/QuoteCard";
import { PriceChart } from "./components/PriceChart";
import { OptionChain } from "./components/OptionChain";
import { FeedHealthPanel } from "./components/FeedHealthPanel";
import { StrategiesPage } from "./pages/StrategiesPage";
import { PaperPage } from "./pages/PaperPage";
import { AssistantPage } from "./pages/AssistantPage";
import { PricingPage } from "./pages/PricingPage";
import { BrokersPage } from "./pages/BrokersPage";
import { RISK_WARNING } from "./pages/LegalPage";
import { UpgradePrompt } from "./components/UpgradePrompt";
import { useAuth } from "./auth";
import { brand } from "./brand";
import { ErrorBoundary } from "./components/ErrorBoundary";

type View = "market" | "paper" | "strategies" | "assistant" | "pricing" | "brokers";
const viewFromHash = (): View =>
  location.hash === "#/pricing" ? "pricing" : location.hash === "#/brokers" ? "brokers" : location.hash === "#/assistant" ? "assistant" : location.hash === "#/backtest" || location.hash === "#/strategies" ? "strategies" : location.hash === "#/paper" ? "paper" : "market";

export default function App() {
  const [market, setMarket] = useState<MarketMessage | null>(null);
  const [liveCandle, setLiveCandle] = useState<CandleMessage | null>(null);
  const [chartSymbol, setChartSymbol] = useState("NIFTY");
  const [timeframe, setTimeframe] = useState("5m");
  const [chainUnderlying, setChainUnderlying] = useState("NIFTY");
  const [mode, setMode] = useState<string | null>(null);
  const [rawView, setView] = useState<View>(viewFromHash);
  const { me } = useAuth();
  // The paper runner is one shared, operator-only runner for now; customers don't see its tab.
  const showPaper = !!me?.is_admin;
  const view: View = rawView === "paper" && !showPaper ? "market" : rawView;

  useEffect(() => {
    const onHash = () => setView(viewFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    getJSON<{ trading_mode: string }>("/health")
      .then((h) => setMode(h.trading_mode))
      .catch(() => setMode(null));
  }, []);

  const onMessage = useCallback((msg: MarketMessage | CandleMessage) => {
    if (msg.type === "market") setMarket(msg);
    else if (msg.type === "candle") setLiveCandle(msg);
  }, []);
  const sub = useMemo(() => ({ candles: { symbol: chartSymbol, timeframe } }), [chartSymbol, timeframe]);
  const socket = useSocket<MarketMessage | CandleMessage>("/ws/market", onMessage, sub);

  const q = market?.quotes;
  const pick = (u: string) => {
    setChartSymbol(u);
    setChainUnderlying(u);
  };

  return (
    <div className={view === "assistant" ? "flex h-dvh flex-col overflow-hidden" : "flex min-h-full flex-col"}>
      <Header session={market?.session ?? null} simulated={market?.simulated ?? null} provider={market?.provider ?? null} socket={socket} feed={market?.feed ?? null} mode={mode} view={view} showPaper={showPaper} />

      {market?.feed.error && !market.feed.connected && (
        <div className="border-b border-down/30 bg-down/10 px-4 py-2 text-xs text-down sm:px-6" role="alert">
          <span className="font-semibold">Market data stopped:</span> {market.feed.error}
        </div>
      )}

      {view === "brokers" ? (
        <ErrorBoundary label="Brokers page">
          <BrokersPage />
        </ErrorBoundary>
      ) : view === "pricing" ? (
        <ErrorBoundary label="Pricing page">
          <PricingPage />
        </ErrorBoundary>
      ) : view === "paper" ? (
        <ErrorBoundary label="Paper trading page">
          <PaperPage />
        </ErrorBoundary>
      ) : view === "strategies" ? (
        <ErrorBoundary label="Strategies page">
          <StrategiesPage />
        </ErrorBoundary>
      ) : view === "assistant" ? (
        <ErrorBoundary label="Assistant page">
          <AssistantPage />
        </ErrorBoundary>
      ) : (
      <main className="mx-auto grid w-full max-w-[1600px] flex-1 grid-cols-1 gap-4 p-4 sm:p-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <QuoteCard label="NIFTY 50" quote={q?.NIFTY} active={chartSymbol === "NIFTY"} onClick={() => pick("NIFTY")} />
            <QuoteCard label="BANK NIFTY" quote={q?.BANKNIFTY} active={chartSymbol === "BANKNIFTY"} onClick={() => pick("BANKNIFTY")} />
            <QuoteCard label="INDIA VIX" quote={q?.INDIAVIX} active={chartSymbol === "INDIAVIX"} onClick={() => setChartSymbol("INDIAVIX")} />
          </div>
          <PriceChart
            symbol={chartSymbol}
            timeframe={timeframe}
            onSymbol={setChartSymbol}
            onTimeframe={setTimeframe}
            liveCandle={liveCandle}
          />
          <FeedHealthPanel />
        </div>
        <div className="flex min-w-0 flex-col xl:h-[calc(100vh-7rem)]">
          <OptionChain underlying={chainUnderlying} onUnderlying={setChainUnderlying} />
        </div>
      </main>
      )}

      <footer className="border-t border-ink-800 px-6 py-3 text-[11px] text-ink-400">
        <p className="font-medium text-down/90">Risk warning: {RISK_WARNING}</p>
        <p className="mt-1">
          {brand.name} · {brand.tagline}. Data → Analysis → Paper, no real orders. <a className="underline hover:text-ink-100" href="#/terms">Terms</a> · <a className="underline hover:text-ink-100" href="#/privacy">Privacy</a>
        </p>
      </footer>
      <UpgradePrompt />
    </div>
  );
}
