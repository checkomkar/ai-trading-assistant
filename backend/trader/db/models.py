"""ORM schema (PRD §2.2 with §14.2 / §14.4 additions). Timestamps are UTC `timestamptz`."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# SQLite only auto-increments INTEGER PRIMARY KEY columns.
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class InstrumentRow(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    underlying: Mapped[str] = mapped_column(String(32), nullable=False)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(8), nullable=False)
    expiry: Mapped[date | None] = mapped_column(Date)
    strike: Mapped[float | None] = mapped_column(Float)
    option_type: Mapped[str | None] = mapped_column(String(2))
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    tick_size: Mapped[float] = mapped_column(Float, default=0.05)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_instruments_chain", "underlying", "expiry", "strike", "option_type"),)


class MarketTickRow(Base):
    __tablename__ = "market_ticks"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    ltp: Mapped[float] = mapped_column(Float, nullable=False)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    oi: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (Index("ix_market_ticks_instrument_ts", "instrument_id", "ts"),)


class CandleRow(Base):
    __tablename__ = "candles"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    timeframe: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, default=0)
    oi: Mapped[int | None] = mapped_column(BigInteger)  # open interest at bar close (derivatives)


class OptionSnapshotRow(Base):
    __tablename__ = "option_snapshots"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    underlying: Mapped[str] = mapped_column(String(32), nullable=False)
    expiry: Mapped[date] = mapped_column(Date, nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    option_type: Mapped[str] = mapped_column(String(2), nullable=False)
    spot: Mapped[float | None] = mapped_column(Float)
    ltp: Mapped[float | None] = mapped_column(Float)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    oi: Mapped[int | None] = mapped_column(BigInteger)
    oi_change: Mapped[int | None] = mapped_column(BigInteger)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    iv: Mapped[float | None] = mapped_column(Float)
    delta: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (Index("ix_option_snapshots_chain_ts", "underlying", "expiry", "ts"),)


class EventRow(Base):
    """Append-only audit trail (PRD §2.3, §14.4)."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    service: Mapped[str] = mapped_column(String(32), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (Index("ix_events_type_ts", "event_type", "ts"),)


class BacktestRunRow(Base):
    """One backtest run: request, data provenance, metrics and the full trade list (PRD M3)."""

    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    underlying: Mapped[str] = mapped_column(String(32), nullable=False)
    start: Mapped[date] = mapped_column(Date, nullable=False)
    end: Mapped[date] = mapped_column(Date, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    data_source: Mapped[str] = mapped_column(String(16), nullable=False)  # real | simulated
    pricing_mode: Mapped[str] = mapped_column(String(16), nullable=False)  # synthetic | real
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class StrategyRow(Base):
    """Immutable strategy versions (PRD §19.4). A new edit is a new row with version + 1."""

    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(16), nullable=False)
    english: Mapped[str] = mapped_column(String, nullable=False)
    source_text: Mapped[str | None] = mapped_column(String)  # the natural-language request, if any
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    translator_model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (Index("ux_strategies_name_version", "name", "version", unique=True),)


class PaperTradeRow(Base):
    """Journal of closed paper trades (PRD §9.3). `correlation_id` joins it to the `events` audit trail."""

    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    trading_day: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # IST date, YYYY-MM-DD
    underlying: Mapped[str] = mapped_column(String(32), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy: Mapped[str] = mapped_column(String(80), nullable=False)
    strategy_version: Mapped[str | None] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(48), nullable=False)
    gross_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    charges: Mapped[float] = mapped_column(Float, nullable=False)
    net_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    mae_pct: Mapped[float | None] = mapped_column(Float)
    mfe_pct: Mapped[float | None] = mapped_column(Float)
    minutes_held: Mapped[float | None] = mapped_column(Float)
    signal_reason: Mapped[str | None] = mapped_column(String)
    confidence: Mapped[float | None] = mapped_column(Float)
    data_source: Mapped[str] = mapped_column(String(16), nullable=False)  # fyers | simulated | replay
    option_pricing: Mapped[str] = mapped_column(String(48), nullable=False)
    features: Mapped[dict] = mapped_column(JSON, default=dict)


class PaperStateRow(Base):
    """Latest snapshot of the paper account: open positions, risk counters, kill switch (PRD §22.3).
    One row per key; it is overwritten, never appended. The audit trail stays in `events`."""

    __tablename__ = "paper_state"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    trading_day: Mapped[str] = mapped_column(String(10), nullable=False)  # IST date the snapshot was taken
    saved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    data_source: Mapped[str] = mapped_column(String(16), nullable=False)  # fyers | simulated | replay
    state: Mapped[dict] = mapped_column(JSON, default=dict)
