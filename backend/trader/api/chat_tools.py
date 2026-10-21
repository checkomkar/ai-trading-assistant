"""The assistant's tools. All read-only: strategies, backtests, paper results, uploaded notes, and a strategy *draft*
(which the user must save themselves). Nothing here can place or change an order."""

from __future__ import annotations

import asyncio

from fastapi import Request
from sqlalchemy import func, select

from trader.ai.chat import Tool
from trader.ai.providers import build_provider
from trader.accounts.repo import Principal
from trader.billing.meter import Meter, QuotaExceeded
from trader.ai.strategy_translator import StrategyTranslator
from trader.db import chat_repo, folder_repo
from trader.db.models import BacktestRunRow, StrategyRow
from trader.execution.store import load_trades
from trader.execution.summary import summarize, trade_to_dict


def build_tools(request: Request, user: Principal, meter: Meter, focus_doc_ids: list[str] | None = None) -> dict[str, Tool]:
    """The caller's own data only: every query below is filtered by `user.id`."""
    sm = request.app.state.sessionmaker
    settings = request.app.state.settings
    uid = user.id
    no_paper = {"note": "Paper trading is not available on this account yet."}

    async def list_strategies(_: dict) -> list[dict]:
        async with sm() as s:
            sub = (select(StrategyRow.name, func.max(StrategyRow.version).label("v")).where(StrategyRow.owner == uid)
                   .group_by(StrategyRow.name).subquery())
            rows = (await s.execute(select(StrategyRow).join(
                sub, (StrategyRow.name == sub.c.name) & (StrategyRow.version == sub.c.v))
                .where(StrategyRow.owner == uid).order_by(StrategyRow.name))).scalars().all()
            where = await folder_repo.assignments(s, uid)
            folders = {f["id"]: f["name"] for f in await folder_repo.list_folders(s, uid)}
        return [{"id": r.id, "name": r.name, "version": r.version, "timeframe": r.spec.get("timeframe"),
                 "underlyings": r.spec.get("underlyings"), "description": r.spec.get("description"),
                 "folder": folders.get(where.get(r.name))} for r in rows]

    async def get_strategy(a: dict) -> dict:
        key = str(a.get("name_or_id", "")).strip()
        async with sm() as s:
            q = (select(StrategyRow).where(StrategyRow.owner == uid, (StrategyRow.id == key) | (StrategyRow.name == key))
                 .order_by(StrategyRow.version.desc()))
            row = (await s.execute(q.limit(1))).scalars().first()
            if row is None:  # forgiving: partial, case-insensitive name
                row = (await s.execute(select(StrategyRow).where(StrategyRow.owner == uid, func.lower(StrategyRow.name).contains(key.lower()))
                                       .order_by(StrategyRow.version.desc()).limit(1))).scalars().first()
        if row is None:
            return {"error": f"no saved strategy matches '{key}'. Use list_strategies."}
        return {"id": row.id, "name": row.name, "version": row.version, "english": row.english,
                "spec": row.spec, "assumptions": row.assumptions, "source_text": row.source_text}

    async def list_backtests(a: dict) -> list[dict]:
        limit = min(int(a.get("limit", 10)), 30)
        async with sm() as s:
            rows = (await s.execute(select(BacktestRunRow).where(BacktestRunRow.owner == uid)
                                    .order_by(BacktestRunRow.created_at.desc()).limit(limit))).scalars().all()
        keys = ("trades", "total_net_pnl", "win_rate", "profit_factor", "max_drawdown", "sharpe")
        return [{"id": r.id, "underlying": r.underlying, "start": r.start.isoformat(), "end": r.end.isoformat(),
                 "timeframe": r.timeframe, "strategy": r.strategy, "data_source": r.data_source,
                 "pricing_mode": r.pricing_mode, **{k: (r.summary or {}).get(k) for k in keys}} for r in rows]

    async def get_backtest(a: dict) -> dict:
        async with sm() as s:
            row = await s.get(BacktestRunRow, str(a.get("run_id", "")))
        if row is None or row.owner != uid:
            return {"error": "no such backtest run. Use list_backtests."}
        trades = (row.result or {}).get("trades", [])
        return {"id": row.id, "underlying": row.underlying, "start": row.start.isoformat(), "end": row.end.isoformat(),
                "timeframe": row.timeframe, "strategy": row.strategy, "data_source": row.data_source,
                "pricing_mode": row.pricing_mode, "summary": row.summary, "trade_count": len(trades),
                "first_trades": trades[:5], "worst_trades": sorted(trades, key=lambda t: t.get("net_pnl", 0))[:3]}

    async def paper_journal(a: dict) -> dict:
        if not user.is_admin:  # the paper runner is one shared, operator-only runner until per-account runners exist
            return {"summary": {"trades": 0}, "recent_trades": [], **no_paper}
        async with sm() as s:
            rows = await load_trades(s, day=a.get("day") or None, strategy=a.get("strategy") or None, limit=200)
        trades = [trade_to_dict(r) for r in rows]
        keep = ("trading_day", "symbol", "strategy", "direction", "entry_time", "entry_price", "exit_time",
                "exit_price", "exit_reason", "net_pnl", "data_source")
        return {"summary": summarize(trades), "recent_trades": [{k: t[k] for k in keep} for t in trades[:25]]}

    async def paper_status(_: dict) -> dict:
        if not user.is_admin:
            return {"running": False, **no_paper}
        p = request.app.state.paper
        st = p.runner.status() if p.runner is not None else {"running": False}
        return {"running": st.get("running"), "data_source": p.md.provider.name, "realized_pnl": st.get("realized_pnl"),
                "unrealized_pnl": st.get("unrealized_pnl"), "lanes": st.get("lanes", []) and [
                    {"underlying": ln["underlying"], "strategy": ln["strategy"], "timeframe": ln["timeframe"]}
                    for ln in st["lanes"]], "risk": p.risk.status()}

    async def search_knowledge(a: dict) -> list[dict]:
        query = str(a.get("query", "")).strip()
        async with sm() as s:
            if not query:  # models sometimes send no query: give an overview of what was uploaded
                return [{"file": d.filename, "kind": d.kind, "summary": d.summary, "start": d.text[:1500]}
                        for d in (await chat_repo.list_docs(s, uid))
                        if not focus_doc_ids or d.id in focus_doc_ids][:6] or [{"note": "no files uploaded yet"}]
            hits = await chat_repo.search_knowledge(s, query, min(int(a.get("k", 4)), 8), uid, doc_ids=focus_doc_ids or None)
        return hits or [{"note": "no matching text in the uploaded files; try different words or ask for an overview with an empty query"}]

    async def draft_strategy(a: dict) -> dict:
        text = str(a.get("description", "")).strip()
        if len(text) < 5:
            return {"error": "description is too short"}
        try:
            async with meter.metered(user, "generations", build_provider(settings)) as mp:  # a draft is one generation
                out = await asyncio.to_thread(StrategyTranslator(mp).translate, text, a.get("base_spec"))
        except QuotaExceeded as exc:
            return {"error": exc.message + " Tell the trader this; do not try again."}
        res = {"name": (out.spec or {}).get("name"), "english": out.english, "assumptions": out.assumptions,
               "questions": out.questions, "unsupported": out.unsupported, "errors": out.errors}
        if out.spec:
            res["_card"] = {"type": "strategy_draft", "name": out.spec["name"], "spec": out.spec, "english": out.english,
                            "assumptions": out.assumptions, "model": out.model}
            res["note"] = "A draft card was shown to the trader with a Save button. It is NOT saved yet."
        return res

    return {
        "list_strategies": Tool("Saved strategies (latest version of each): id, name, timeframe, underlyings, and the folder the trader filed it in. args: {}", list_strategies),
        "get_strategy": Tool("One saved strategy with its plain-English rules and full spec. args: {\"name_or_id\": str}", get_strategy,
                              {"type": "object", "properties": {"name_or_id": {"type": "string"}}, "required": ["name_or_id"]}),
        "list_backtests": Tool("Recent backtest runs with headline metrics. args: {\"limit\": int (optional)}", list_backtests,
                               {"type": "object", "properties": {"limit": {"type": "integer"}}}),
        "get_backtest": Tool("One backtest run: summary, trade count, sample and worst trades. args: {\"run_id\": str}", get_backtest,
                              {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]}),
        "paper_journal": Tool("Paper-trading results and recent trades. args: {\"day\": \"YYYY-MM-DD\" (optional), \"strategy\": str (optional)}", paper_journal,
                               {"type": "object", "properties": {"day": {"type": "string"}, "strategy": {"type": "string"}}}),
        "paper_status": Tool("Is the paper runner on, which strategies run, today's P&L and risk counters. args: {}", paper_status),
        "search_knowledge": Tool("Search the trader's uploaded files for passages. args: {\"query\": str, \"k\": int (optional)}", search_knowledge,
                                 {"type": "object", "properties": {"query": {"type": "string"}, "k": {"type": "integer"}}, "required": ["query"]}),
        "draft_strategy": Tool("Turn a plain-English description into a strategy DRAFT for the trader to review and save. "
                               "args: {\"description\": str, \"base_spec\": object (optional, to modify an existing spec)}", draft_strategy,
                               {"type": "object", "properties": {"description": {"type": "string"}, "base_spec": {"type": "object"}},
                                "required": ["description"]}),
    }
