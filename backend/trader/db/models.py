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
    owner: Mapped[str] = mapped_column(String(32), nullable=False, default="local", index=True)
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
    owner: Mapped[str] = mapped_column(String(32), nullable=False, default="local", index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(16), nullable=False)
    english: Mapped[str] = mapped_column(String, nullable=False)
    source_text: Mapped[str | None] = mapped_column(String)  # the natural-language request, if any
    assumptions: Mapped[list] = mapped_column(JSON, default=list)
    translator_model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (Index("ux_strategies_owner_name_version", "owner", "name", "version", unique=True),)


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


# ---- assistant: conversations, uploaded knowledge, memory (all owner-scoped; owner is "local" until accounts) ----
class ChatConversationRow(Base):
    __tablename__ = "chat_conversations"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    owner: Mapped[str] = mapped_column(String(32), nullable=False, default="local", index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ChatMessageRow(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(16), ForeignKey("chat_conversations.id", ondelete="CASCADE"),
                                                 nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(12), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(String, nullable=False)
    cards: Mapped[list] = mapped_column(JSON, default=list)  # e.g. strategy drafts the user can save
    doc_ids: Mapped[list] = mapped_column(JSON, default=list)  # files attached to this message
    provider: Mapped[str | None] = mapped_column(String(96))  # which model answered
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class KnowledgeDocRow(Base):
    """An uploaded file, reduced to text. The original bytes are not kept."""

    __tablename__ = "knowledge_docs"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    owner: Mapped[str] = mapped_column(String(32), nullable=False, default="local", index=True)
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    mime: Mapped[str] = mapped_column(String(96), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # pdf | docx | xlsx | text | image
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ready | no_text | no_vision
    text: Mapped[str] = mapped_column(String, nullable=False, default="")
    summary: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class KnowledgeChunkRow(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(String(16), ForeignKey("knowledge_docs.id", ondelete="CASCADE"),
                                        nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(32), nullable=False, default="local", index=True)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(String, nullable=False)


class MemoryNoteRow(Base):
    """A fact the assistant keeps about the trader's setup. Visible and deletable in the UI."""

    __tablename__ = "memory_notes"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    owner: Mapped[str] = mapped_column(String(32), nullable=False, default="local", index=True)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="assistant")  # assistant | user
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---- strategy folders: grouping for the Strategies page. Keyed by strategy *name*, so every version stays together ----
class StrategyFolderRow(Base):
    __tablename__ = "strategy_folders"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    owner: Mapped[str] = mapped_column(String(32), nullable=False, default="local", index=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class StrategyFolderItemRow(Base):
    """A strategy sits in at most one folder per owner; no row means "ungrouped"."""

    __tablename__ = "strategy_folder_items"

    owner: Mapped[str] = mapped_column(String(32), primary_key=True, default="local")
    strategy_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    folder_id: Mapped[str] = mapped_column(String(16), ForeignKey("strategy_folders.id"), nullable=False, index=True)


# ---- SaaS: accounts, sessions, usage ledger, billing events (PRD §23) ----
class AccountRow(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # the `owner` value on every owned row
    email: Mapped[str | None] = mapped_column(String(254), unique=True)  # None for a phone-only account
    name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    password_hash: Mapped[str | None] = mapped_column(String(256))  # None for Google / phone-only accounts
    phone: Mapped[str | None] = mapped_column(String(20), unique=True)  # E.164, verified by OTP
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)  # Google account id
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="trial")
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # paid plans: start of this billing month
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # paid-until; passing it drops the plan to trial
    billing_customer: Mapped[str | None] = mapped_column(String(64))  # payment provider's ids
    billing_subscription: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AuthSessionRow(Base):
    """A login. Only a hash of the token is stored, so a database leak does not hand out sessions."""

    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(32), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UsageEventRow(Base):
    """Append-only ledger of metered work. One row per generation / chat message / backtest / speech request, and
    cost-only rows (units=0) for incidental model calls. `status` is "held" while the call runs, then "done";
    a call that fails before it costs anything is deleted (refunded)."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    owner: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # generations | chat_messages | speech_requests | backtests | ai_other
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    model: Mapped[str | None] = mapped_column(String(128))
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="done")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (Index("ix_usage_owner_kind_ts", "owner", "kind", "created_at"),)


class BillingEventRow(Base):
    """Webhook deliveries already applied, so a retried webhook is idempotent."""

    __tablename__ = "billing_events"

    id: Mapped[str] = mapped_column(String(96), primary_key=True)  # the provider's event id
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    account_id: Mapped[str | None] = mapped_column(String(32))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class BrokerConnectionRow(Base):
    """A customer's own broker API keys. `public` holds the non-secret fields (shown back to them); `secret_blob` is
    Fernet ciphertext of everything, written once and never returned (trader/accounts/vault.py)."""

    __tablename__ = "broker_connections"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    owner: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    broker: Mapped[str] = mapped_column(String(16), nullable=False)
    public: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_blob: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (Index("ux_broker_owner_broker", "owner", "broker", unique=True),)


class LoginOtpRow(Base):
    """A one-time code sent by SMS. Only a hash is kept; it expires in minutes and allows a few guesses."""

    __tablename__ = "login_otps"

    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
