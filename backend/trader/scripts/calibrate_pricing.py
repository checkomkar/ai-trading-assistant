"""Fit synthetic option pricing to stored real option prices, and measure how much better it gets (PRD §24.9 B8).

    python -m trader.scripts.calibrate_pricing --underlying NIFTY --start 2023-01-01 --end 2026-10-20
    python -m trader.scripts.calibrate_pricing --underlying both --start 2023-01-01 --end 2026-10-20 --holdout-days 120

Needs the index, VIX (fetch_prime_history --steps index) and expired option candles (--steps options).

1. Reads stored 1m option candles near the money (one every 15 minutes), backs out each one's implied volatility and
   divides it by INDIA VIX.
2. Fits the median ratio per moneyness x days-to-expiry x time-of-day bucket on all but the last --holdout-days, and
   saves it to data/calibration/<UNDERLYING>.json. Synthetic and hybrid backtests use it from then on (reports say so).
3. On the held-out days, compares the pricing error of the calibrated model with the plain "VIX x multiplier" model.
4. If the live collector recorded option quotes, reports the typical bid-ask spread (a better `spread_pct`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, timedelta

from trader.backtest import calibration as C
from trader.config import get_settings
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.options.selection import IV_MULT


async def run(a: argparse.Namespace) -> None:
    settings = get_settings()
    url = settings.resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    cut = a.end - timedelta(days=a.holdout_days)
    try:
        for und in (["NIFTY", "BANKNIFTY"] if a.underlying == "both" else [a.underlying]):
            print(f"\n== {und} ==")
            try:
                obs = await C.collect(sm, und, a.start, a.end, every_minutes=a.every,
                                      progress=lambda n, t: print(f"  expiry {n}/{t}", end="\r", flush=True))
            except ValueError as exc:
                print(f"  {exc}")
                continue
            print()
            fit_obs = [(s, o) for s, o in obs if o["at"].date() <= cut]
            val_obs = [o for s, o in obs if o["at"].date() > cut]
            if not fit_obs:
                print(f"  no usable option candles before {cut}: download expired options first")
                continue
            cal = C.fit([s for s, _ in fit_obs], und, a.start, cut)
            path = C.save(cal, settings.data_dir)
            print(f"  fitted on {len(fit_obs):,} samples ({a.start} .. {cut}); saved {path}")
            print(f"  overall implied vol / VIX ratio: {cal.global_ratio:.3f} (the plain model assumes {IV_MULT[und]})")
            print("  ratio by moneyness x days to expiry (samples):")
            for k, (r, n) in sorted(cal.by_md.items()):
                print(f"    {k:<32} {r:6.3f}  ({n:,})")
            report = {"version": cal.version, "fitted": [a.start.isoformat(), cut.isoformat()],
                      "validation_days": [(cut + timedelta(days=1)).isoformat(), a.end.isoformat()]}
            if val_obs:
                v = C.validate(cal, val_obs, IV_MULT[und])
                report["validation"] = v
                print(f"  held-out {cut + timedelta(days=1)} .. {a.end}: median absolute pricing error "
                      f"{v['calibrated'].get('median_abs_error_pct')}% calibrated vs {v['plain_vix'].get('median_abs_error_pct')}% "
                      f"plain (bias {v['calibrated'].get('bias_pct')}% vs {v['plain_vix'].get('bias_pct')}%)")
            else:
                print("  no option candles in the held-out days: nothing to validate on (use a smaller --holdout-days)")
            spread = await C.spread_estimate(sm, und)
            if spread:
                report["spread"] = spread
                print(f"  recorded live quotes: median bid-ask spread {spread['median_spread_pct']}% of mid "
                      f"(90th pct {spread['p90_spread_pct']}%); backtests assume 0.30%")
            rp = path.with_name(f"{und}_report.json")
            rp.write_text(json.dumps(report, indent=1), encoding="utf-8")
            print(f"  report: {rp}")
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--underlying", default="NIFTY", choices=["NIFTY", "BANKNIFTY", "both"])
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    p.add_argument("--holdout-days", type=int, default=90, help="most recent days kept out of the fit, for validation")
    p.add_argument("--every", type=int, default=15, help="sample one candle every N minutes")
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
