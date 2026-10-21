import type { FeedStatus } from "../api";

const COPY: Record<NonNullable<FeedStatus["action"]>, { title: string; button: string }> = {
  broker_required: { title: "Connect your broker to see live data", button: "Connect a broker" },
  signin_required: { title: "Sign in to your broker to start today's data", button: "Sign in on the Brokers page" },
  feed_busy: { title: "Live data is busy right now", button: "Check the Brokers page" },
  feed_unavailable: { title: "Your live data could not start", button: "Open the Brokers page" },
};

/** Shown instead of the market view when this account has no live feed of its own yet. Prices come from YOUR broker. */
export function ConnectBrokerCard({ action, message }: { action: NonNullable<FeedStatus["action"]>; message: string | null }) {
  const c = COPY[action];
  return (
    <main className="mx-auto grid w-full max-w-xl flex-1 place-items-center p-6">
      <section className="w-full rounded-lg border border-ink-800 bg-ink-900 p-6 text-center">
        <h1 className="text-base font-semibold">{c.title}</h1>
        <p className="mt-2 text-sm text-ink-400">{message}</p>
        <p className="mt-2 text-xs text-ink-400">
          Prices come from your own broker account, so each user sees data under their own broker's terms. Strategies, backtests and the
          assistant work without it.
        </p>
        {action !== "feed_busy" && (
          <a href="#/brokers" className="mt-4 inline-block rounded-md bg-accent px-4 py-2 text-sm font-semibold text-ink-950">{c.button}</a>
        )}
      </section>
    </main>
  );
}
