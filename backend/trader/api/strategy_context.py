"""What the assistant is told when the trader talks about the strategy open on the Strategies page.

Built on the server from the saved rules and the owner's own backtest row, never from numbers the browser sends, and
re-built on every message so it follows edits and new runs. Kept compact: it is re-sent with each turn.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, ValidationError

from trader.db.models import BacktestRunRow
from trader.strategy.spec import StrategySpec, render_english, semantic_errors

MAX_SAMPLE = 5


class StrategyContextIn(BaseModel):
    spec: dict
    backtest_id: str | None = Field(None, max_length=32)


def _backtest_lines(row: BacktestRunRow) -> list[str]:
    r, m = row.result or {}, (row.result or {}).get("metrics", {})
    trades = r.get("trades", [])
    equity = (r.get("params", {}).get("strategy", {}) or {}).get("market") == "equity"
    lines = [f"Latest backtest (id {row.id}): {row.start} to {row.end}, {row.timeframe} candles, {r.get('data_source')} data, "
             f"{r.get('pricing_mode')} pricing.",
             f"  trades {m.get('trades')}, net P&L {m.get('total_net_pnl')}, return {m.get('return_pct')}%, win rate {m.get('win_rate')}%, "
             f"profit factor {m.get('profit_factor')}, avg win {m.get('average_win')}, avg loss {m.get('average_loss')}, "
             f"expectancy {m.get('expectancy')}, max drawdown {m.get('max_drawdown')} ({m.get('max_drawdown_pct')}%), "
             f"Sharpe {m.get('sharpe')}, charges {m.get('total_charges')}, gross P&L {m.get('total_gross_pnl')}.",
             f"  exit reasons {json.dumps(m.get('exit_reasons', {}))}; signals {r.get('signals')}; "
             f"not taken {json.dumps(r.get('skipped_entries', {}))}.",
             f"  average hold {m.get('average_holding_days') if equity else m.get('average_holding_minutes')} "
             f"{'days' if equity else 'minutes'}; average MAE {m.get('average_mae_pct')}%, MFE {m.get('average_mfe_pct')}%."]
    if equity and m.get("by_symbol"):
        by = m["by_symbol"]
        lines.append("  best stocks: " + ", ".join(f"{x['symbol']} {x['net_pnl']}" for x in by[:MAX_SAMPLE])
                     + "; worst: " + ", ".join(f"{x['symbol']} {x['net_pnl']}" for x in by[-MAX_SAMPLE:]))
    if m.get("monthly_pnl"):
        lines.append("  monthly P&L: " + ", ".join(f"{x['month']} {x['pnl']}" for x in m["monthly_pnl"][-12:]))
    worst = sorted(trades, key=lambda t: t["net_pnl"])[:3]
    if worst:
        lines.append("  worst trades: " + "; ".join(f"{t['contract']} {t['entry_time'][:10]} -> {t['exit_reason']} {t['net_pnl']}" for t in worst))
    if r.get("warnings"):
        lines.append("  warnings: " + " | ".join(str(w)[:160] for w in r["warnings"][:3]))
    return lines


def build(ctx: StrategyContextIn, backtest: BacktestRunRow | None) -> str:
    try:
        spec = StrategySpec.model_validate(ctx.spec)
    except ValidationError:
        return "[The trader is editing a strategy whose rules are not valid yet, so discuss it in general terms.]"
    errs = semantic_errors(spec)
    out = ["[Context: the trader has this strategy open on the Strategies page and is asking you about it.",
           "Strategy in plain English:", render_english(spec),
           "Rules (JSON): " + json.dumps(spec.model_dump(mode="json", by_alias=True, exclude_none=True), separators=(",", ":"))]
    if errs:
        out.append("Problems with the rules: " + "; ".join(errs))
    if backtest is not None:
        out += _backtest_lines(backtest)
    else:
        out.append("It has NOT been backtested yet (or the trader has not run one since the last change).")
    out.append("How to answer: be specific to THIS strategy; point out weaknesses and when it would fail; suggest concrete, "
               "testable improvements one at a time (exits, filters, sizing, timeframe); warn about overfitting and small samples "
               "(a handful of trades proves nothing); use only the numbers above or from tools, never invent results. If you "
               "propose changed rules, call draft_strategy with base_spec set to the rules above so the trader can load the "
               "draft and backtest it. Everything here is research and paper trading, not investment advice.]")
    return "\n".join(out)
