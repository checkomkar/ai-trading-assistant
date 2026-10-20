# NIFTY & BANKNIFTY Options Trading Platform

## Product Requirements Document

**Version:** 1.0  
**Status:** Initial Development Blueprint  
**Primary Market:** NSE India  
**Initial Instruments:** NIFTY 50 and BANKNIFTY index options  
**Initial Strategy:** Intraday long option buying  
**Execution Mode:** Paper trading first; live trading only after explicit enablement  
**Primary Technology:** Python + PostgreSQL + React/Next.js  
**Future AI Stack:** PyTorch + LangGraph + LLMs

---

# 1. Product Vision

Build a modular quantitative trading platform for Indian index options that begins as a simple data collection and rule-based paper-trading system and progressively evolves into an AI-assisted quantitative research and execution platform.

The platform should eventually support:

- Real-time market-data ingestion
- Historical market-data storage
- Options-chain analysis
- Technical indicators
- Rule-based strategies
- Backtesting
- Paper trading
- Machine-learning signals
- Market-regime detection
- AI-assisted research
- Strategy generation and evaluation
- Risk management
- Broker execution
- Portfolio analytics
- Automated monitoring

The system must be designed so that **strategy logic, AI models, risk management and broker execution remain separate components**.

---

# 2. Guiding Principles

## 2.1 Safety First

The application must default to:

**DATA → ANALYSIS → PAPER TRADE**

and never:

**DATA → AI → LIVE ORDER**

without explicit risk and execution controls.

---

## 2.2 Deterministic Core

Critical financial operations should not depend on an LLM.

LLMs may provide:

- analysis
- explanations
- research
- strategy hypotheses
- contextual interpretation

But deterministic software controls:

- position sizing
- stop loss
- maximum loss
- order quantity
- margin checks
- trading hours
- kill switches
- broker execution

---

## 2.3 Everything Must Be Measurable

Every signal and trade should produce an auditable record.

Example:

```text
Signal
  ↓
Features
  ↓
Decision
  ↓
Order
  ↓
Fill
  ↓
Position
  ↓
Exit
  ↓
P&L
```

---

# 3. Scope

## Initial Scope

### Instruments

- NIFTY options
- BANKNIFTY options

### Direction

Long options only:

- Buy CE
- Buy PE

### Trading style

Intraday.

### Initial timeframe

5-minute candles.

### Initial strategy

Momentum/breakout hypothesis.

### Initial execution

Paper trading.

---

# 4. Non-Goals for V1

Do NOT initially build:

- Futures trading
- Stock options
- Short options
- Iron condors
- Straddles
- Strangles
- Automated hedging
- HFT
- Reinforcement learning
- LLM-based autonomous trading
- Multi-broker execution
- Portfolio optimization

These belong to later phases.

---

# 5. High-Level Architecture

```text
                    ┌─────────────────────┐
                    │     NSE / Broker    │
                    └──────────┬──────────┘
                               │
                         Market Data
                               │
                               ▼
                    ┌─────────────────────┐
                    │   Data Collector    │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    Data Storage     │
                    │ PostgreSQL/TSDB     │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
       Feature Engine    Option Analytics   Market State
              │                │                │
              └────────────────┼────────────────┘
                               ▼
                    ┌─────────────────────┐
                    │   Strategy Engine   │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │    Risk Engine      │
                    └──────────┬──────────┘
                               │
                     ┌─────────┴─────────┐
                     ▼                   ▼
                Paper Broker        Live Broker
                     │                   │
                     └─────────┬─────────┘
                               ▼
                    ┌─────────────────────┐
                    │ Portfolio / P&L     │
                    └─────────────────────┘
```

---

# 6. Development Roadmap

The application is divided into:

```text
Phase 0   Project Foundation
Phase 1   Market Data
Phase 2   Data Storage
Phase 3   Instrument & Contract Engine
Phase 4   Market Data API
Phase 5   Technical Analysis
Phase 6   Options Analytics
Phase 7   Strategy Engine
Phase 8   Backtesting
Phase 9   Paper Trading
Phase 10  Risk Management
Phase 11  Trading Dashboard
Phase 12  Live Broker Integration
Phase 13  ML Foundation
Phase 14  ML Signal Engine
Phase 15  Market Regime Engine
Phase 16  AI Research Assistant
Phase 17  AI Trading Analyst
Phase 18  Strategy Discovery
Phase 19  Advanced Options Intelligence
Phase 20  Production Infrastructure
Phase 21  Advanced Portfolio System
```

---

# PHASE 0 — PROJECT FOUNDATION

## 0.1 Repository

Create repository:

```text
nifty-ai-trader
```

Recommended structure:

```text
/apps
    /api
    /web

/services
    /market-data
    /strategy
    /backtest
    /execution
    /risk
    /ml
    /ai

/packages
    /models
    /indicators
    /options
    /common

/infrastructure
    /docker
    /database

/tests
/docs
```

---

## 0.2 Development Environment

Set up:

- Python
- Node.js
- PostgreSQL
- Redis
- Docker
- Git
- environment management

Create:

```text
.env.example
docker-compose.yml
README.md
```

---

## 0.3 Configuration

Centralize:

```text
BROKER
DATABASE
REDIS
TRADING_MODE
MARKET_TIMEZONE
RISK_LIMITS
```

Trading mode:

```text
BACKTEST
PAPER
LIVE
```

Default:

```text
PAPER
```

---

## 0.4 Logging

Implement structured logging.

Every service should log:

```text
timestamp
service
event
symbol
severity
message
metadata
```

---

# PHASE 1 — MARKET DATA INGESTION

## 1.1 Broker Connection

Implement broker authentication.

Requirements:

- login
- token handling
- reconnect
- connection health
- graceful shutdown

---

## 1.2 WebSocket

Implement:

```text
connect()
subscribe()
unsubscribe()
reconnect()
heartbeat()
```

---

## 1.3 NIFTY Feed

Collect:

- LTP
- OHLC
- volume
- timestamp

---

## 1.4 BANKNIFTY Feed

Same functionality.

---

## 1.5 Option Chain Discovery

Automatically discover:

- expiry
- strike
- CE
- PE
- instrument token

---

## 1.6 Option Feed

Collect:

```text
LTP
bid
ask
volume
OI
OI change
timestamp
```

---

## 1.7 Market Session Handling

Implement:

```text
PRE_OPEN
OPEN
TRADING
CLOSING
CLOSED
```

The system must understand Indian market hours.

---

## 1.8 Data Quality

Detect:

- stale ticks
- duplicate ticks
- missing ticks
- invalid prices
- timestamp anomalies

---

# PHASE 2 — DATA STORAGE

## 2.1 Database

Use PostgreSQL initially.

---

## 2.2 Core Tables

### instruments

```text
id
symbol
exchange
instrument_type
expiry
strike
option_type
token
```

### market_ticks

```text
timestamp
instrument_id
ltp
bid
ask
volume
oi
```

### candles

```text
timestamp
instrument_id
timeframe
open
high
low
close
volume
```

### option_snapshots

```text
timestamp
underlying
expiry
strike
option_type
ltp
bid
ask
oi
volume
iv
```

---

## 2.3 Data Retention

Define:

- raw tick retention
- candle retention
- aggregated data retention

---

## 2.4 Historical Data Import

Create importer capable of loading:

```text
CSV
JSON
broker historical data
```

---

# PHASE 3 — INSTRUMENT & CONTRACT ENGINE

This component should abstract NSE contract details from strategy code.

## 3.1 Expiry Resolver

Given:

```text
NIFTY
```

return:

```text
nearest expiry
next expiry
monthly expiry
```

---

## 3.2 ATM Resolver

Input:

```text
underlying price
```

Output:

```text
ATM strike
```

---

## 3.3 Strike Resolver

Support:

```text
ATM
ATM + 1
ATM - 1
ATM + 2
ATM - 2
```

---

## 3.4 Option Resolver

Example:

```text
NIFTY
25,000
CE
nearest expiry
```

returns the appropriate contract.

---

# PHASE 4 — MARKET DATA API

Build backend APIs.

Example:

```text
GET /market/nifty
GET /market/banknifty
GET /options/nifty/chain
GET /options/banknifty/chain
GET /market/candles
GET /market/quotes
```

WebSocket APIs:

```text
/ws/market
/ws/options
/ws/signals
```

---

# PHASE 5 — TECHNICAL ANALYSIS ENGINE

Create reusable indicators.

## 5.1 Trend

- EMA
- SMA
- VWAP
- ADX

## 5.2 Momentum

- RSI
- ROC
- MACD

## 5.3 Volatility

- ATR
- Bollinger Bands
- historical volatility

## 5.4 Price Structure

- previous high
- previous low
- opening range
- support
- resistance
- breakout

---

# PHASE 6 — OPTIONS ANALYTICS

This phase becomes increasingly important.

## 6.1 Basic Metrics

Calculate:

- intrinsic value
- extrinsic value
- moneyness

---

## 6.2 Greeks

Calculate/ingest:

- Delta
- Gamma
- Theta
- Vega

---

## 6.3 Implied Volatility

Calculate IV where data permits.

---

## 6.4 IV Percentile

Calculate historical IV percentile.

---

## 6.5 Option Chain Analytics

Calculate:

```text
Call OI
Put OI
OI change
PCR
highest OI strikes
largest OI changes
```

---

## 6.6 Option Selection Engine

Given:

```text
direction
underlying
expiry
risk preference
```

select:

```text
ATM
ITM
OTM
```

Initially only ATM.

---

# PHASE 7 — STRATEGY ENGINE

Create a strategy interface.

```python
class Strategy:

    def generate_signal(self, market_state):
        pass
```

Every strategy should return a standardized signal:

```text
direction
confidence
instrument
entry
stop_loss
target
reason
timestamp
```

---

## 7.1 Strategy #1 — Momentum

Initial hypothesis:

```text
Price > VWAP
EMA9 > EMA21
RSI > 55
breakout
```

---

## 7.2 Bearish Momentum

```text
Price < VWAP
EMA9 < EMA21
RSI < 45
breakdown
```

---

## 7.3 No-Trade Conditions

Implement explicit no-trade states:

```text
low volatility
choppy market
near major event
outside trading window
daily loss reached
existing position
insufficient liquidity
```

---

# PHASE 8 — BACKTESTING ENGINE

This is one of the most important phases.

## 8.1 Event-Driven Engine

Process historical data sequentially.

```text
tick/candle
   ↓
features
   ↓
signal
   ↓
order
   ↓
fill
   ↓
position
   ↓
P&L
```

---

## 8.2 Simulated Execution

Model:

- brokerage
- STT
- exchange charges
- GST
- slippage
- bid/ask spread
- latency

---

## 8.3 Position Management

Support:

- entry
- stop
- target
- trailing stop
- time exit
- EOD exit

---

## 8.4 Backtest Metrics

Calculate:

```text
Total P&L
Win rate
Loss rate
Profit factor
Average win
Average loss
Maximum drawdown
Sharpe
Sortino
Expectancy
Average holding time
```

---

## 8.5 Trade-Level Analysis

Record:

```text
trade_id
signal
entry
exit
P&L
MAE
MFE
duration
exit_reason
```

---

# PHASE 9 — PAPER TRADING

## 9.1 Paper Broker

Create:

```python
PaperBroker
```

with the same interface as the real broker.

```text
place_order()
modify_order()
cancel_order()
get_positions()
get_orders()
```

---

## 9.2 Real-Time Paper Execution

```text
Live Market
     ↓
Strategy
     ↓
Paper Broker
     ↓
Virtual Position
```

---

## 9.3 Paper Trading Journal

Record every trade.

---

## 9.4 Daily Summary

Generate:

```text
Trades
Wins
Losses
Gross P&L
Net P&L
Drawdown
Strategy performance
```

---

# PHASE 10 — RISK MANAGEMENT

This should be implemented **before live trading**.

## 10.1 Position Risk

Parameters:

```text
max risk/trade
max quantity
max open positions
```

---

## 10.2 Daily Risk

```text
max daily loss
max consecutive losses
max trades/day
```

---

## 10.3 Portfolio Risk

Initially:

```text
NIFTY exposure
BANKNIFTY exposure
total premium exposure
```

---

## 10.4 Kill Switch

Implement:

```text
Emergency STOP
```

which prevents new orders.

---

## 10.5 Broker Safety

Before order:

```text
Is market open?
Is instrument valid?
Is quantity valid?
Is sufficient margin available?
Is daily loss exceeded?
Is strategy enabled?
Is risk limit exceeded?
```

---

# PHASE 11 — TRADING DASHBOARD

Build React/Next.js dashboard.

## 11.1 Market Overview

Display:

```text
NIFTY
BANKNIFTY
VIX
market status
```

---

## 11.2 Charts

Display:

- candlestick
- VWAP
- EMA
- signals
- entries
- exits

---

## 11.3 Option Chain

Display:

```text
Strike
CE LTP
CE OI
CE ΔOI
PE LTP
PE OI
PE ΔOI
IV
```

---

## 11.4 Signal Panel

Example:

```text
NIFTY

Direction: LONG
Signal: Momentum Breakout

Confidence: 72%

Option:
NIFTY ATM CE

Entry:
₹XXX

Stop:
₹XXX

Target:
₹XXX
```

---

## 11.5 Positions

Display:

- entry
- current price
- unrealized P&L
- stop
- target
- duration

---

## 11.6 Trading Journal

Search/filter:

```text
date
symbol
strategy
direction
result
```

---

# PHASE 12 — LIVE BROKER INTEGRATION

Only after successful paper trading.

## 12.1 Broker Interface

```python
class Broker:

    place_order()
    modify_order()
    cancel_order()
    positions()
    orders()
```

---

## 12.2 Broker Adapter

Implement:

```text
ShoonyaBroker
```

---

## 12.3 Dry Run

Support:

```text
LIVE_DATA + PAPER_EXECUTION
```

---

## 12.4 Micro-Live

Enable extremely limited live trading.

Hard limits:

```text
max trades/day
max quantity
max daily loss
```

---

## 12.5 Live Reconciliation

Continuously compare:

```text
Internal position
vs
Broker position
```

If mismatch:

```text
STOP TRADING
```

---

# PHASE 13 — MACHINE LEARNING FOUNDATION

Only start this after collecting sufficient historical/paper data.

## 13.1 Dataset Generator

Create training datasets.

Features:

```text
technical indicators
option metrics
market structure
time of day
volatility
OI
IV
```

Target examples:

```text
future return
future direction
future volatility
```

---

## 13.2 Baseline Models

Start with:

- Logistic Regression
- Random Forest
- XGBoost/LightGBM

Do NOT immediately start with deep learning.

---

## 13.3 Classification

Predict:

```text
UP
DOWN
FLAT
```

---

## 13.4 Probability

Output:

```text
P(UP)
P(DOWN)
P(FLAT)
```

---

## 13.5 Model Evaluation

Use:

- walk-forward validation
- out-of-sample testing
- precision
- recall
- ROC-AUC
- calibration
- trading P&L

The final evaluation must be **trading performance**, not merely ML accuracy.

---

# PHASE 14 — ML SIGNAL ENGINE

Combine deterministic and ML signals.

```text
Technical Strategy
       │
       ├──────────┐
       │          │
       ▼          ▼
   Rule Signal   ML Signal
       │          │
       └────┬─────┘
            ▼
       Signal Engine
```

Example:

```text
Rule signal = LONG
ML probability = 0.73
```

Trade only if both conditions are satisfied.

---

# PHASE 15 — MARKET REGIME ENGINE

Classify the market:

```text
TRENDING_UP
TRENDING_DOWN
RANGE
HIGH_VOLATILITY
LOW_VOLATILITY
BREAKOUT
BREAKDOWN
```

Strategy behavior becomes regime-dependent.

Example:

```text
TRENDING_UP
→ momentum strategy enabled

RANGE
→ momentum strategy disabled

HIGH_VOLATILITY
→ reduce position size
```

---

# PHASE 16 — AI RESEARCH ASSISTANT

Now introduce an LLM.

The first AI should **not trade**.

Its job is research.

## Capabilities

Ask:

> Why did this strategy lose money today?

The system provides:

```text
trades
market conditions
signals
features
```

The LLM analyzes them.

---

## 16.1 Daily Analysis

Generate:

```text
Market summary
Strategy performance
Largest losses
Largest wins
Anomalies
Potential improvements
```

---

## 16.2 Trade Explanation

For every trade:

```text
Why entered
What conditions existed
Why exited
What went wrong/right
```

---

# PHASE 17 — AI TRADING ANALYST

Introduce LangGraph.

Initial graph:

```text
Market Data
     ↓
Technical Analyst
     ↓
Options Analyst
     ↓
Risk Analyst
     ↓
Decision Synthesizer
```

The output is:

```text
TRADE
NO TRADE
```

The AI still cannot directly execute.

---

# PHASE 18 — AI STRATEGY DISCOVERY

Build an automated research loop.

```text
Hypothesis
    ↓
Feature Generation
    ↓
Backtest
    ↓
Walk Forward
    ↓
Risk Analysis
    ↓
Accept / Reject
```

Example hypothesis:

> "Does ATM IV expansion combined with increasing put OI improve downside prediction?"

The system generates the experiment automatically.

---

# PHASE 19 — ADVANCED OPTIONS INTELLIGENCE

Introduce:

## 19.1 IV Surface

Track:

```text
strike
expiry
IV
```

---

## 19.2 Volatility Skew

Analyze:

```text
OTM PE IV
ATM IV
OTM CE IV
```

---

## 19.3 Gamma Exposure

Estimate market gamma positioning.

---

## 19.4 OI Dynamics

Detect:

```text
fresh call writing
fresh put writing
short covering
long buildup
long unwinding
```

Treat these as signals/features rather than guaranteed directional predictions.

---

# PHASE 20 — PRODUCTION INFRASTRUCTURE

Containerize:

```text
market-data
api
strategy
risk
execution
database
redis
frontend
```

Use Docker Compose initially.

---

## Monitoring

Implement:

- service health
- WebSocket health
- database health
- broker health
- strategy health
- execution health

---

## Alerts

Send alerts for:

```text
broker disconnected
market data stale
order rejected
position mismatch
risk limit breached
service failure
```

---

# PHASE 21 — ADVANCED PORTFOLIO SYSTEM

Eventually support:

```text
NIFTY
BANKNIFTY
Futures
Stocks
Options strategies
```

Add:

- portfolio allocation
- correlation
- volatility targeting
- capital allocation
- strategy allocation
- portfolio-level drawdown control

---

# 7. Recommended Initial Technology Stack

## Backend

```text
Python
FastAPI
Pydantic
asyncio
```

## Database

```text
PostgreSQL
```

Later:

```text
TimescaleDB
```

## Cache / Messaging

```text
Redis
```

## Frontend

```text
Next.js
React
TypeScript
Tailwind
TradingView Lightweight Charts
```

## Quant

```text
NumPy
Pandas
Polars
SciPy
TA-Lib / pandas-ta
```

## ML

```text
scikit-learn
XGBoost
LightGBM
PyTorch
```

## AI

```text
LangGraph
LLM API
```

## Infrastructure

```text
Docker
Docker Compose
GitHub
```

---

# 8. Suggested Repository Architecture

```text
nifty-trader/
│
├── apps/
│   ├── api/
│   └── web/
│
├── services/
│   ├── market_data/
│   │   ├── collectors/
│   │   ├── websocket/
│   │   └── processors/
│   │
│   ├── instruments/
│   │
│   ├── features/
│   │
│   ├── options/
│   │
│   ├── strategies/
│   │
│   ├── backtest/
│   │
│   ├── paper_trading/
│   │
│   ├── risk/
│   │
│   ├── execution/
│   │
│   ├── ml/
│   │
│   └── ai/
│
├── database/
│   ├── migrations/
│   └── schemas/
│
├── infrastructure/
│   ├── docker/
│   └── monitoring/
│
├── tests/
│
├── scripts/
│
├── docs/
│
├── .env.example
├── docker-compose.yml
└── README.md
```

---

# 9. Definition of Done for Each Major Stage

## Stage A — Data

The system can:

- connect to broker
- receive live NIFTY data
- receive BANKNIFTY data
- discover options
- record option-chain data
- recover from WebSocket disconnection

---

## Stage B — Research

The system can:

- load historical data
- calculate indicators
- generate signals
- backtest strategies
- calculate realistic transaction costs
- generate performance reports

---

## Stage C — Paper Trading

The system can:

- receive live data
- generate signals
- simulate orders
- manage positions
- calculate live P&L
- maintain trade journal

---

## Stage D — ML

The system can:

- generate training datasets
- train models
- perform walk-forward validation
- generate probabilities
- compare ML vs rule-based strategies

---

## Stage E — AI

The system can:

- analyze market conditions
- explain trades
- analyze strategy performance
- generate research hypotheses
- evaluate experiments

---

## Stage F — Live

The system can:

- execute orders
- reconcile broker positions
- enforce risk limits
- stop trading on anomalies
- provide complete audit logs

---

# 10. First 30 Development Tasks

Do NOT attempt Phase 1–21 immediately.

Your initial backlog should literally be:

### Sprint 1 — Foundation

1. Create Git repository.
2. Create Python project.
3. Create Next.js project.
4. Create Docker Compose.
5. Start PostgreSQL.
6. Create configuration system.
7. Create logging system.
8. Create basic FastAPI server.
9. Create health endpoint.
10. Create database migration system.

### Sprint 2 — Broker/Data

11. Implement broker authentication.
12. Implement WebSocket connection.
13. Subscribe to NIFTY.
14. Subscribe to BANKNIFTY.
15. Log incoming ticks.
16. Implement reconnect.
17. Discover option contracts.
18. Subscribe to ATM CE/PE.
19. Store ticks.
20. Validate stored data.

### Sprint 3 — Data Platform

21. Create candle aggregation.
22. Create option snapshots.
23. Build historical importer.
24. Create market-data API.
25. Create option-chain API.
26. Create basic dashboard.
27. Display NIFTY chart.
28. Display BANKNIFTY chart.
29. Display option chain.
30. Display WebSocket connection status.

**Stop here and verify the data pipeline before building the strategy.**

---

# 11. First Milestone

The first meaningful milestone is:

## M1 — Market Data Platform

You should be able to open your dashboard at 10:30 AM and see:

```text
NIFTY
25,xxx
▲ 0.xx%

BANKNIFTY
56,xxx
▼ 0.xx%

        NIFTY OPTION CHAIN

Strike       CE OI       CE LTP       PE LTP       PE OI
---------------------------------------------------------
24,900       xxx         xxx          xxx          xxx
24,950       xxx         xxx          xxx          xxx
25,000       xxx         xxx          xxx          xxx
25,050       xxx         xxx          xxx          xxx
25,100       xxx         xxx          xxx          xxx
```

while the backend continuously stores the data.

**No trading yet.**

Once this works reliably, everything else becomes substantially easier.

---

# 12. Critical Design Rule

Keep these interfaces independent:

```text
MarketDataProvider
        ↓
FeatureProvider
        ↓
Strategy
        ↓
RiskManager
        ↓
ExecutionProvider
```

For example:

```python
signal = strategy.generate_signal(market_state)

approved = risk_manager.validate(
    signal,
    portfolio
)

if approved:
    execution.place_order(signal)
```

This allows you to later replace:

```text
RuleStrategy
```

with:

```text
MLStrategy
```

and eventually:

```text
AIStrategy
```

without rewriting the broker/execution system.

---

# 13. Long-Term Evolution

The platform should evolve approximately like this:

```text
                 TODAY
                   │
                   ▼
        ┌─────────────────────┐
        │ Market Data          │
        │ NIFTY/BANKNIFTY      │
        └──────────┬──────────┘
                   ▼
        ┌─────────────────────┐
        │ Rule-Based Strategy │
        └──────────┬──────────┘
                   ▼
             Backtesting
                   ▼
            Paper Trading
                   │
                   ▼
              ML Signals
                   │
                   ▼
            Regime Detection
                   │
                   ▼
            AI Researcher
                   │
                   ▼
           AI Trading Analyst
                   │
                   ▼
          Automated Research
                   │
                   ▼
          Strategy Discovery
                   │
                   ▼
          Multi-Strategy System
                   │
                   ▼
             AI Quant Lab
```

The final objective isn't simply an **"AI trading bot."**

It is a **quantitative research and trading platform** where you can continuously collect data, formulate hypotheses, test strategies, measure them out-of-sample, paper trade them, and eventually promote validated strategies into controlled live execution.

**The immediate next task should therefore be Phase 0 + Phase 1: get the repository, Docker environment, broker connectivity, and NIFTY/BANKNIFTY WebSocket collector working.** Everything else can be developed incrementally on top of that foundation.

---

# 14. Improvements & Implementation Notes (added during build)

These refine the blueprint above. Where they conflict with earlier sections, this section wins.

## 14.1 Market data without a broker: `SimulatedProvider` + replay

Sprint 2 assumes broker access, but development happens evenings, weekends and before credentials exist.

- Add a `SimulatedMarketDataProvider` that implements the same `MarketDataProvider` interface as the broker adapter. It generates NIFTY / BANKNIFTY / INDIA VIX ticks (GBM with intraday volatility profile) and a full option chain priced with Black-Scholes plus a volatility smile, with OI that evolves over the session.
- Add a `ReplayProvider` (later) that replays stored `market_ticks` at N× speed. This makes the dashboard, paper broker and strategy engine testable at any hour and gives deterministic integration tests (seeded RNG).
- Provider is chosen by config: `MARKET_DATA_PROVIDER=simulated | shoonya | replay`.

## 14.2 NSE contract facts must be data-driven, never hard-coded

Contract specs change (SEBI/NSE circulars). The instrument engine must read them from the broker instrument master; config values are only fallbacks for the simulator.

| Item | Fallback default (verify before live) |
|---|---|
| NIFTY strike interval | 50 |
| BANKNIFTY strike interval | 100 |
| NIFTY lot size | 65 |
| BANKNIFTY lot size | 30 |
| NIFTY expiry | Weekly, Tuesday |
| BANKNIFTY expiry | **Monthly only**, last Tuesday (no weekly since SEBI's Nov-2024 rule of one weekly benchmark per exchange) |
| Holiday on expiry day | Expiry moves to previous trading day |

Consequence for §3.1: "nearest expiry" for BANKNIFTY is the monthly contract, which changes theta/gamma behaviour vs NIFTY weeklies — strategies must not assume both underlyings have weekly expiries.

`instruments` table gains: `underlying`, `tradingsymbol`, `lot_size`, `tick_size`, `is_active`.

## 14.3 Exchange calendar

- `config/nse_holidays.yaml` holds trading holidays (populate from the yearly NSE circular; special sessions such as Muhurat trading listed separately).
- Session states (§1.7) computed from calendar + clock: `PRE_OPEN` 09:00–09:15, `OPEN`/`TRADING` 09:15–15:30 (with `OPEN` = first 15 min), `CLOSING` 15:30–15:40 (closing-price session), `CLOSED` otherwise.
- Store all timestamps in UTC (`timestamptz`); convert to `Asia/Kolkata` only at the edges (UI, session logic, candle bucketing).

## 14.4 Audit trail as a first-class table

§2.3 ("everything measurable") is implemented as an append-only `events` table: `id, ts, service, event_type, correlation_id, payload(json)`. Signal → order → fill → exit records share a `correlation_id`, so any P&L line can be traced back to the exact features that produced it. Orders use client-generated idempotency keys.

## 14.5 Backtest correctness rules

- Signals computed on candle **close**; fills at **next candle open** (plus slippage). No same-bar fills — prevents look-ahead bias.
- Option fills use bid/ask when available, otherwise mid ± modelled half-spread.
- Transaction costs are a versioned config (`costs.yaml`), not constants: brokerage per order, STT on option sell premium, exchange transaction charges, SEBI fees, stamp duty on buy, 18% GST on (brokerage + exchange + SEBI). Rates change with budgets/circulars.
- Expiry-day and holiday edge cases covered by tests.

## 14.6 Options analytics inputs

Greeks/IV need a risk-free rate (config, default ~6.5% — 91-day T-bill proxy) and time-to-expiry measured in trading time to 15:30 IST on expiry day. IV solver: Newton-Raphson with bisection fallback; return `null` (not garbage) when price < intrinsic or near-zero vega.

## 14.7 Data quality is persisted

Data-quality violations (§1.8) are counted per instrument per minute and exposed at `/health/data`, so the dashboard can show feed health and the risk engine can block trading on stale data (feeds §10.5).

## 14.8 Repository layout (pragmatic V1)

Instead of many separately-deployed services from day one, V1 is **one installable Python package** (`backend/trader`) whose sub-packages mirror the service boundaries (`market_data`, `instruments`, `options`, `indicators`, `strategy`, `risk`, `execution`, `backtest`, `ai`). Boundaries are enforced by interfaces (§12), so a module can be split into its own process later without rewrites. Frontend is **Vite + React + TypeScript + Tailwind + Lightweight Charts** (a realtime dashboard gains nothing from SSR; Next.js can be adopted later if needed).

```text
ai-assisted-trading/
├── backend/
│   ├── trader/            # python package (api, market_data, instruments, options, db, ...)
│   ├── alembic/           # migrations
│   ├── config/            # nse_holidays.yaml, costs.yaml
│   └── tests/
├── web/                   # Vite React dashboard
├── docker-compose.yml     # postgres + redis
├── .env.example
└── README.md
```

## 14.9 Local dev without Docker

`DATABASE_URL` defaults to SQLite so the stack runs with only Python + Node. Postgres (and later TimescaleDB) via `docker compose up -d`. Redis is optional in V1 — an in-process async pub/sub bus implements the same interface.

## 14.10 Secrets & live-trading guard

- Broker credentials (user id, password, TOTP secret, API key) only in `.env`, never logged; `.env` is git-ignored.
- `TRADING_MODE=LIVE` additionally requires `LIVE_TRADING_CONFIRM=I_UNDERSTAND_THE_RISK`; otherwise the app refuses to start in LIVE.

## 14.11 Gaps added to scope

- INDIA VIX feed (needed by §11.1 and regime engine).
- **Near-month futures feed.** NSE index ticks carry no traded volume, so VWAP (§5.1, Strategy #1 in §7.1) and any volume-based feature must be computed from NIFTY / BANKNIFTY futures. Futures are subscribed and get candles alongside the indices.
- `/health`, `/health/data`, `/health/feed` endpoints from Sprint 1 (feeds Phase 20 monitoring early).
- Frontend shows data-source badge (`SIMULATED` / `LIVE`) prominently so simulated data is never mistaken for real.

## 14.12 Build status

See §16 for the milestone tracker.

---

# 15. Market-Data Broker Selection

Evaluated October 2026 for: real-time NIFTY / BANKNIFTY / VIX / futures / options ticks, intraday history with OI, and **expired option contract history** (essential for backtesting an option-buying strategy).

| Broker | Data API cost | Intraday history | Expired options history | WebSocket | Login | Notes |
|---|---|---|---|---|---|---|
| **Fyers** | **Free** (Standard); Prime is paid | 1 yr (Standard), 5+ yrs (Prime); OI via `oi_flag` | Yes — `/history/fno/expired/*` with OI and Greeks. Standard: daily candles (2 yrs) but only **10 intraday requests/day**. Prime: full intraday | 100 symbols Standard, 5,000 Prime | OAuth, token valid until 06:30 next day | Official Python SDK, option-chain endpoint with Greeks, public symbol master incl. previous OI |
| Zerodha Kite | ₹500/month (free personal plan has **no** data) | Years, with OI | No (expired options not served) | 3,000 symbols/connection | Daily login | Most mature ecosystem |
| Upstox | Free V3 history | Minute data from 2022, OI | Yes, but requires paid **Upstox Plus** | 1,500–5,000 | Token expires 03:30 daily | Some community reports of empty expired datasets |
| Dhan | ₹499/month for data | 5 yrs intraday | Partial | 5,000 | Long-lived token | Strong option-chain API |
| Angel One SmartAPI | Free | 1–2 yrs | No | 1,000 | Daily login + TOTP | Reliability complaints at high volatility |
| Shoonya (Finvasia) | Free | Limited | No | Yes | Daily + TOTP | Zero brokerage, sparse docs |

**Decision: Fyers for market data (M2 onwards).** The free Standard plan covers everything needed for live data and a year of index and futures intraday history. It also has the cleanest path to expired option candles with OI and Greeks.

Expired-option *intraday* history at scale needs **Fyers Prime**; Upstox Plus is the alternative. Until then, M3 backtests options in two modes:
- **Synthetic:** ATM premiums are priced with Black-Scholes from the underlying and an IV proxy (INDIA VIX or recorded ATM IV). This is available immediately but optimistic on spreads.
- **Real:** recorded option candles, from our own collector (which starts accumulating in M2) or from Prime downloads.

Both modes must report which one was used. The Standard plan's 100-symbol socket cap fits V1: 3 indices + 2 futures + ATM ±10 strikes × CE/PE for NIFTY's nearest weekly and BANKNIFTY's monthly comes to 89 symbols. The subscription manager recentres the window as spot moves (§17.3). Upgrade to Prime only if deeper history or wider chains become necessary.

**Execution broker is a separate decision (M5).** The `Broker` interface (§12) keeps it swappable. Under SEBI's retail-algo framework (effective 1 April 2026), API *orders* must originate from a broker-registered static IP with daily 2FA sessions. Read-only market-data use is unaffected.

Lessons from the real Fyers symbol master that confirm §14.2:
- BANKNIFTY futures tick size is 0.20, not 0.05.
- Holiday-shifted expiries are already applied (e.g. a Monday expiry when Tuesday is a holiday).
- Contract expiry timestamps are 15:40 IST.

All of these are loaded from the master, never hard-coded.

---

# 16. Milestones

Each milestone is a shippable state with an explicit definition of done. Do not start the next until the current one's DoD is verified.

| # | Milestone | Phases | Stage (§9) | Status |
|---|---|---|---|---|
| M1 | Market Data Platform (simulated) | 0–4 | A (partial) | ✅ Done |
| M2 | Live Market Data (Fyers) | 1, 2, 3 (real broker), 2.4 | A | 🚧 Connected & history loaded; market-hours DoD pending (§21) |
| M3 | Research Engine | 5, 6, 7, 8 | B | ✅ Running on 1 year of real data (§21); contract-note cost check pending |
| M3b | Custom Strategy Builder (natural language) | 7 (extended), §19 | B | ✅ Built; live translator test needs an Anthropic key (§21) |
| M4 | Paper Trading | 9, 10, 11 | C | 🚧 Built; 20-day unattended run pending (§22) |
| M5 | Controlled Live Trading | 12, 20 (monitoring + alerts) | F | Planned |
| M6 | ML Signals & Regimes | 13, 14, 15 | D | Planned |
| M7 | AI Research Assistant & Analyst | 16, 17 | E | Planned |
| M8 | Strategy Discovery & Advanced Options | 18, 19 | E | Planned |
| M9 | Production & Portfolio | 20 (full), 21 | — | Planned |

## M1 — Market Data Platform ✅

Covered by §10 Sprints 1–3 and §11. Delivered against the simulator: dashboard, REST and WebSocket APIs, candle aggregation, chain snapshots, data-quality monitor, historical importer, 52 tests.

## M2 — Live Market Data (Fyers)

**Goal:** the M1 dashboard runs on real NSE data, and the database fills with real history that M3 can backtest on.

Scope:
1. Fyers auth: a CLI login (OAuth code flow with a local redirect listener). The token is cached in `data/fyers_token.json` and checked for expiry before each start.
2. Instrument discovery from the Fyers public symbol master. Lot size, tick size, expiry and previous OI/close come from the master. The master is cached once per day.
3. Data WebSocket adapter behind `MarketDataProvider`. The SDK's threaded socket is bridged into asyncio. The SDK's own reconnect is disabled in favour of the base class's backoff and resubscribe.
4. Subscription manager: indices + near futures + ATM ±N strikes for configured expiries, capped at `FYERS_MAX_SYMBOLS`. It recentres when spot drifts at least N/2 strikes.
5. History backfill for indices and futures: on startup it fills the gap since the last stored candle, and a CLI backfills N days. Requests are chunked and rate-limited.
6. Expired-options downloader (CLI) for past NIFTY / BANKNIFTY expiries, ATM ±K strikes, with OI and Greeks. It respects the plan quota (Standard: 10 intraday calls/day; Prime: unrestricted) and resumes where it stopped.
7. Populate `nse_holidays.yaml`, and cross-check expiries against the symbol master at startup, logging any mismatch.

DoD:
- During market hours, NIFTY and BANKNIFTY on the dashboard match the broker terminal to the tick.
- The chain shows real OI, ΔOI and IV.
- A WebSocket drop (e.g. disabling the network for 30s) is recovered automatically, and gap and stale counters show it.
- Ticks, candles and snapshots for a full session are stored, and a day's 1m candles reconcile with Fyers history within ±1 tick per bar.
- At least 6 months of index and futures 1m candles are stored.
- Live option ticks and snapshots are accumulating daily.
- Expired option candles have been downloaded for as many recent expiries as the plan allows.

## M3 — Research Engine

**Goal:** answer "does the momentum hypothesis (§7.1/7.2) make money after costs?" with an honest, reproducible backtest.

Scope:
- Indicators library (Phase 5), vectorised and incremental with identical results. VWAP comes from futures volume.
- Options analytics additions (Phase 6): IV percentile, and the option selection engine (ATM first).
- Strategy interface and standardised `Signal` (Phase 7), Momentum long/short, and explicit no-trade states.
- Event-driven backtester (Phase 8) with §14.5 rules, `costs.yaml`, slippage and spread, position management (stop, target, trailing, time and EOD exits).
- Reports covering the §8.4 metrics plus trade list with MAE/MFE, equity curve, and per-day and per-expiry-day breakdown.
- Walk-forward split (in-sample / out-of-sample) as a first-class option.
- Dashboard: indicator overlays (VWAP/EMA), and a backtest runner with a results page.

DoD:
- The same strategy produces identical signals in backtest and in live paper mode on replayed data (parity test).
- Costs reconcile with a broker contract note for a sample trade within ₹1.
- The report for Strategy #1 is generated on at least 6 months of data with out-of-sample results shown separately.

## M4 — Paper Trading

**Goal:** run validated strategies on live data with virtual money and full auditability.

Scope:
- `PaperBroker` with the `Broker` interface (Phase 9). Fills use live bid/ask with latency.
- Risk engine (Phase 10): per-trade, daily and portfolio limits, kill switch, and the pre-order checklist (§10.5), including blocking trades on stale data (§14.7).
- Journal and daily summary.
- Dashboard: signal panel, positions, journal with filters, kill-switch button, and risk-limit status (Phase 11).
- Replay provider (§14.1) to rehearse any stored day at N× speed.

DoD:
- 20 consecutive trading days of unattended paper trading with no unhandled errors.
- Every trade is traceable signal → order → fill → exit via `correlation_id`.
- Paper P&L is within an agreed tolerance of a backtest run on the same days' recorded data.

## M5 — Controlled Live Trading

**Goal:** micro-live execution with hard safety rails.

Scope:
- Live broker adapter (Phase 12), with the broker chosen at this point; Fyers is the default candidate.
- Static IP hosting per SEBI rules.
- `LIVE_DATA + PAPER_EXECUTION` dry run.
- Micro-live hard limits.
- Continuous position reconciliation that halts on mismatch.
- Monitoring and alerts (Phase 20 subset): broker or feed down, stale data, order rejected, position mismatch, risk breach. Delivered via Telegram or email.

DoD:
- 10 trading days of micro-live trading (1 lot, at most 2 trades per day) with zero reconciliation mismatches.
- Kill switch tested live.
- Alerts tested end to end.

## M6 — ML Signals & Regimes

Phases 13–15:
- Dataset generator from stored features.
- Baseline models (LogReg / RF / LightGBM) with walk-forward evaluation on trading P&L.
- ML gating of rule signals.
- Regime classifier that enables or disables strategies.

DoD: ML-gated strategy beats the rule-only strategy out-of-sample after costs, or the gating is not adopted.

## M7 — AI Research Assistant & Analyst

Phases 16–17:
- LLM daily analysis and trade explanations, built from journal + features + market context.
- LangGraph analyst producing TRADE / NO TRADE as an *input* to the deterministic engine. It is never an executor (§2.1).

DoD: daily report generated automatically, and every explanation cites the stored data it used.

## M8 — Strategy Discovery & Advanced Options

Phases 18–19:
- Automated hypothesis → backtest → walk-forward → accept/reject loop.
- IV surface, skew, GEX and OI-dynamics features.

DoD: at least one discovered hypothesis is evaluated end to end with a reproducible experiment record.

## M9 — Production & Portfolio

Phases 20–21:
- Containerised services, full observability, TimescaleDB.
- Multi-strategy capital allocation and portfolio-level drawdown control.

---

# 17. M2 Implementation Notes

## 17.1 Configuration

```text
MARKET_DATA_PROVIDER=fyers
FYERS_CLIENT_ID=XXXXXXXX-100        # App ID from myapi.fyers.in
FYERS_SECRET_KEY=...
FYERS_REDIRECT_URI=http://127.0.0.1:8765/fyers/callback
FYERS_MAX_SYMBOLS=100               # Standard plan socket cap
FYERS_STRIKES_EACH_SIDE=10
FYERS_EXPIRIES_PER_UNDERLYING=1
HISTORY_BACKFILL_DAYS=5             # startup gap-fill for index/futures candles
```

## 17.2 Daily routine

Before 09:00 IST, run:

```bash
python -m trader.brokers.fyers.login
```

This opens the browser, captures the redirect and stores the token. Then start the API. The token is valid until 06:30 IST the next day. SEBI's framework requires a daily interactive 2FA login anyway, so unattended TOTP scraping is intentionally not implemented.

## 17.3 Subscription window

The desired set is: indices + near futures + for each underlying, the next `FYERS_EXPIRIES_PER_UNDERLYING` expiries × strikes within ATM ± `FYERS_STRIKES_EACH_SIDE` × {CE, PE}.

It is trimmed to `FYERS_MAX_SYMBOLS`, dropping the farthest strikes first. It is recomputed every 30s. Changes are applied as subscribe/unsubscribe diffs, never as a full reset.

## 17.4 M2 status (2026-10-20)

| Scope item | Status |
|---|---|
| 1. Auth CLI + token cache + expiry check | Built and unit-tested. Needs a first real login. |
| 2. Instrument discovery from the symbol master | **Verified against the live public master:** 6,444 contracts, lot and tick sizes from the master |
| 3. WebSocket adapter (thread to asyncio bridge, base-class reconnect) | Built and tested with a fake SDK socket. Needs a live session. |
| 4. Subscription manager (ATM window, symbol cap, recentring) | Built and tested |
| 5. History gap-fill + `backfill_history` CLI | Built and tested with a fake REST client. Needs a live token. |
| 6. Expired-options downloader with daily quota | Built and tested against the documented response format. Needs a live token. |
| 7. Holidays + expiry cross-check | Cross-check **verified on live data**. It found two missing holidays (10-Nov, 24-Nov-2026), now added as inferred. The rest of the 2026 and 2027 holidays still need entering from the NSE circular. |
| Schema migrations at start-up (incl. legacy SQLite) | Built and tested. Adds `candles.oi`. |

The M2 DoD (§16) can only be signed off during market hours with a real Fyers login.

---

# 18. M3 Implementation Notes (2026-10-20)

Backtesting was prioritised ahead of live verification, so M3 started before M2's live sign-off. The engine runs on whatever history is stored: real Fyers candles once backfilled, or simulated history until then.

## 18.1 What is built

| Piece | Location | Notes |
|---|---|---|
| Indicators (Phase 5) | `trader/indicators/` | EMA, SMA, RSI, ATR, ADX, MACD, ROC, Bollinger, HV, rolling extremes, session VWAP. Incremental, so backtest and live share code. Tested against independent batch implementations. |
| Feature engine | `trader/features.py` | Price indicators on the index. VWAP on futures (indices have no volume). VIX carried forward. Opening range, previous-day high/low. |
| Strategy interface + Momentum #1 (Phase 7) | `trader/strategy/` | Standardised `Signal` with a features snapshot for audit. Explicit `NoTrade` reasons (§7.3). |
| Cost model | `config/costs.yaml`, `trader/backtest/costs.py` | Versioned by date. STT 0.10% before / 0.15% from 1-Apr-2026. Fyers ₹20/order. |
| Option pricing | `trader/backtest/pricing.py` | `synthetic` (Black-Scholes, IV = VIX × multiplier, modelled spread) and `real` (stored option candles). Historical expiry rules by date. |
| Engine (Phase 8) | `trader/backtest/engine.py` | Rules: next-bar-open fills, gap fills, stop-before-target in an ambiguous bar, trailing stop, EOD exit at 15:15. Engine-enforced risk gate covering daily loss and max trades, independent of the strategy (§2.2). |
| Metrics (§8.4) | `trader/backtest/metrics.py` | P&L, win/loss rate, PF, expectancy, max DD, Sharpe/Sortino (daily, zero days included), holding time, MAE/MFE, exit reasons, by direction, daily and monthly P&L, equity curve. |
| Persistence + API | `backtest_runs` table, `/backtests` | `POST /backtests`, `GET /backtests`, `GET /backtests/{id}`, `GET /backtests/coverage` |
| Dashboard | Backtest tab | Parameter form, data coverage, equity curve, daily P&L, breakdowns, paged trade table, recent runs |
| CLI | `trader.scripts.backtest` | Prints the report, writes JSON and a trades CSV to `data/backtests/` |
| Simulated history | `trader.scripts.generate_sim_history` | `SIM:*` instruments only. Every run on it is labelled SIMULATED. |

## 18.2 Remaining for the M3 DoD

1. Backfill real history: index, futures and VIX, 5m, about 1 year on the Fyers Standard plan.
2. Run Strategy #1 on it and review the report. Add an in-sample / out-of-sample split (walk-forward).
3. Reconcile one trade's charges against a real Fyers contract note. `clearing_pct` (0.009%) in particular needs checking.
4. For `real` pricing, download expired option candles (10 calls/day on Standard), or start recording live chains.
5. Add indicator overlays (VWAP/EMA) and entry/exit markers to the price chart. Add a backtest↔live parity test once paper trading (M4) exists.

## 18.3 Known limitations (shown in every report where relevant)

- **Synthetic option prices** ignore IV skew, intraday IV changes beyond VIX, and real liquidity. Expect real results to be worse.
- **Expiry weekdays before 1-Sep-2025** are best-effort, especially BANKNIFTY. They only affect time-to-expiry in synthetic mode.
- **Lot size** is the current contract's (NIFTY 65 / BANKNIFTY 30) for the whole period. Historical lot-size changes are not modelled yet.

## 18.4 Update (2026-10-20): trade chart and timeframes

- Backtest results include a **candlestick chart of the underlying** with EMA overlays (from the same feature engine the strategy used), entry arrows (▲ CE / ▼ PE) and exit dots coloured by P&L. Clicking a trade, in the table or on the chart, zooms to it and labels it. Endpoint: `GET /backtests/{id}/chart`.
- Backtests accept **1, 2, 3, 5, 10, 15, 20, 30 and 60-minute** timeframes. Any timeframe that isn't stored is **built exactly from a finer stored one**, anchored at 09:15 like NSE charts (§20).

---

# 20. Fyers Historical Data: Requirements, Timeframes, Resampling

**What is needed**
1. A Fyers trading account with KYC completed.
2. An API app created at myapi.fyers.in. Creating it is free; choose type *individual*.
3. A daily login (`python -m trader.brokers.fyers.login`). Tokens expire at 06:30 IST.
4. `python -m trader.scripts.backfill_history`. It resumes from the last stored candle and chunks requests at 90 days, below the API's 100-day cap per intraday call.

**Resolutions the API serves:** 1, 2, 3, 5, 10, 15, 20, 30, 60, 120 and 240 minutes, plus daily, weekly and monthly. The expired-contract endpoint also lists 45 and 180 minutes. Seconds-level data (5S and up) is Prime-only.

**History depth**

| Plan | Intraday | Daily |
|---|---|---|
| Standard (free) | ~1 year | ~5 years |
| Prime | 5+ years | 25+ years |

**Resampling policy:** store the finest resolution available, which is **1 minute** on Standard, and derive everything else.
- **Going up is exact:** open = first open, high = max, low = min, close = last close, volume = sum, OI = last. Buckets are anchored to 09:15 IST.
- **Going down is impossible:** a 5m candle cannot be split into 1m candles. Never synthesise lower timeframes.
- **Volume for VWAP:** index candles carry **no volume**, so VWAP and volume features come from futures candles (§14.11).

---

# 19. Custom Strategies from Natural Language (Milestone M3b)

## 19.1 Goal

A user describes a strategy in plain English, reviews exactly what the system understood, and backtests it without writing code. For example:

> "On NIFTY 5-minute candles, buy the ATM call when price closes above VWAP, the 9 EMA crosses above the 21 EMA and RSI is above 60. Only between 9:45 and 14:30. Stop loss 20%, target 40%, trail by 10% after 25% profit. Max 2 trades a day, skip if ADX is below 20. Mirror it for puts."

## 19.2 Safety principle: the LLM translates, it never executes

This applies §2.1 and §2.2 directly. The LLM (Claude API) only **converts text into a declarative Strategy Spec** (JSON). It never writes or runs code, never sees orders, and is not in the backtest or live execution path. A deterministic, schema-validated interpreter evaluates the spec bar by bar, using the same `FeatureEngine` and indicators as the hand-written strategies. A natural-language strategy can never go straight to live trading: like any strategy, it must pass backtest (M3) and paper trading (M4) first.

## 19.3 Strategy Spec (v1)

```json
{
  "spec_version": 1,
  "name": "VWAP + EMA cross momentum",
  "underlyings": ["NIFTY"],
  "timeframe": "5m",
  "sides": {
    "long":  {"option": "CE", "entry": {"all": [
                {"cmp": [{"ind": "close"}, ">", {"ind": "vwap"}]},
                {"cross": [{"ind": "ema", "period": 9}, "above", {"ind": "ema", "period": 21}]},
                {"cmp": [{"ind": "rsi", "period": 14}, ">", {"const": 60}]}]}},
    "short": {"mirror_of": "long"}
  },
  "filters": {"time_window": ["09:45", "14:30"], "all": [{"cmp": [{"ind": "adx", "period": 14}, ">=", {"const": 20}]}]},
  "option_selection": {"strike": "ATM", "expiry": "nearest", "roll_on_expiry_day": true},
  "exits": {"stop_loss_pct": 20, "target_pct": 40, "trail": {"activate_pct": 25, "trail_pct": 10},
            "time_exit_bars": null, "conditions": {"any": []}, "eod": "15:15"},
  "risk": {"max_trades_per_day": 2}
}
```

- **Operands:** a whitelist only. Covers OHLC, the Phase 5 indicators (EMA, SMA, RSI, ATR, ADX, +DI/−DI, MACD/signal/histogram, ROC, Bollinger bands, VWAP), opening range, previous-day high/low/close, rolling highest/lowest(n), INDIA VIX, time of day, and constants. Every parameter has bounds.
- **Operators:** `>`, `<`, `>=`, `<=`, `between`, `crosses above` / `crosses below` (defined on two consecutive closed bars), and `all` / `any` / `not` trees.
- **No look-ahead by construction:** operands can only reference the current closed bar or earlier (`{"ind": "close", "shift": 1}`). There is no operand for future bars.
- **Mirroring:** `mirror_of` flips comparisons (above↔below, `>`↔`<`, highest↔lowest, CE↔PE). RSI thresholds reflect around 50.
- **Mapping:** the existing Momentum #1 strategy must be expressible as a spec. This is the parity test in §19.7.

## 19.4 Flow

1. **Describe:** a free-text box with examples.
2. **Translate:** the Claude API is called with structured output constrained to the Strategy Spec JSON schema. It returns:
   - (a) the spec
   - (b) a list of **assumptions**, e.g. "RSI period not given → 14", "'VWAP' → futures session VWAP"
   - (c) **clarifying questions** when the text is ambiguous, e.g. "Should 'breakout' mean above the previous day's high or the opening range?"

   Ambiguity produces questions, never silent guesses.
3. **Review:** the spec is rendered back to plain English **deterministically, by code, not by the LLM**, so the user reads what will actually run. An editable condition builder and a raw JSON view are both available.
4. **Validate:** JSON-schema check, then semantic checks:
   - unknown or out-of-range parameters
   - contradictory conditions, e.g. RSI > 70 and RSI < 30
   - empty time windows
   - stop ≥ 100%
   - indicator warm-up longer than a session
5. **Save** as an immutable, versioned row in a `strategies` table: text, spec, spec hash, model id, assumptions.
6. **Backtest:** from the builder or the Backtest tab. Every run records the spec hash for reproducibility, and runs can be compared side by side.
7. **Iterate:** "make the stop 15% and only trade after 10:00" edits the existing spec, producing a new version with a visible diff.

## 19.5 Components

| Component | Notes |
|---|---|
| `trader/strategy/spec.py` | Pydantic models + JSON schema for the Strategy Spec, semantic validator, deterministic English renderer |
| `trader/strategy/rule_strategy.py` | `RuleStrategy(Strategy)`: interprets a spec on `Features`. Supports crosses via previous-bar state and the extra indicators a spec requests. |
| `trader/ai/strategy_translator.py` | Claude API call with structured output, the schema, few-shot examples, and assumptions/questions extraction |
| API | `POST /strategies/translate`, `POST /strategies` (save version), `GET /strategies`, `GET /strategies/{id}/versions`. `POST /backtests` accepts `strategy_id`. |
| UI | **Strategies** tab: describe → review (English + builder + JSON) → backtest. Version history and run comparison. |
| Config | `ANTHROPIC_API_KEY` and `STRATEGY_TRANSLATOR_MODEL` in `.env`. Without a key, the builder still works in manual (form/JSON) mode. |

## 19.6 Non-goals for v1

- Strategies needing data we don't have (order book, news, options Greeks history) are rejected with an explanation.
- Multi-leg or option-selling strategies are excluded (§4).
- Arbitrary Python or "custom formula" escape hatches are not provided.

## 19.7 Definition of done

- The Momentum #1 strategy written as a spec produces **trade-for-trade identical** results to the coded `MomentumStrategy` (parity test).
- A suite of at least 20 example prompts (simple, compound, ambiguous, impossible) is evaluated:
  - valid prompts yield specs matching hand-written expected specs
  - ambiguous prompts yield clarifying questions
  - impossible prompts are rejected with reasons
- The deterministic English rendering of every saved spec round-trips. Re-parsing the rendered text yields an equivalent spec.
- No code path lets LLM output reach order placement.

---

# 21. Status Update (2026-10-20/21): real data, first results, M3b

## 21.1 Fyers connection (M2)
- **Login and token:** login works through the CLI. The token is cached until 06:00 IST.
- **Live stream:** the API runs on `MARKET_DATA_PROVIDER=fyers` and subscribes 89 symbols (under the 100 cap). Indices, futures and ATM ±10 strikes all stream.
- **Timeframes, measured:** 1/2/3/5/10/15/20/30/45/60/120/180/240 minutes and D/W/M all work. Seconds are rejected (Prime only).
- **History limits, measured:** 1-minute history goes back exactly 1 year, with at most 100 days per request. Requests are clamped to `FYERS_INTRADAY_HISTORY_DAYS` (364).
- **Stored history:** 460,509 real 1-minute candles: NIFTY, BANKNIFTY, INDIA VIX, and both near-month futures with OI.
- **Data checks:**
  - 245 sessions, with no inconsistent OHLC bars
  - resampled 5m and 15m candles match Fyers' native candles on all 300 compared
  - 15 holidays inferred from data gaps were added to the calendar
  - pre-open prints (09:08–09:14) on 3 days are excluded from backtests
- **Day change:** it was wrong on a holiday (the feed's prev_close equals the last close). It is now derived from stored candles: the close of the session before the current one. NIFTY −257.20 (−1.11%) now matches the Fyers app.
- **Still open for the M2 DoD:** tick-for-tick comparison and disconnect recovery *during market hours*, and option-chain OI/IV during a live session.

## 21.2 First real results: Momentum #1, 5m, synthetic option prices, 23-Oct-2025 → 19-Oct-2026

| Run | Trades | Net P&L | PF | Max DD |
|---|---|---|---|---|
| NIFTY, full year | 412 | +₹45,388 | 1.11 | ₹68,615 (37%) |
| BANKNIFTY, full year | 285 | −₹76,718 | 0.86 | ₹86,057 (81%) |
| NIFTY in-sample (Oct–May) | 243 | +₹53,568 | 1.21 | ₹34,612 |
| NIFTY out-of-sample (Jun–Oct) | 169 | −₹3,683 | 0.98 | ₹38,301 |

The edge does not survive out-of-sample. Most of the profit came from short (PE) trades in one period. Charges were 39% of NIFTY's gross P&L. BANKNIFTY's monthly-only expiry leaves most trades running to EOD. **Conclusion: Strategy #1 is not validated.** Synthetic pricing is optimistic, so real results would be worse.

## 21.3 M3b: custom strategies, as built
- **Spec + interpreter:** `trader/strategy/spec.py` (schema, semantic validator, mirroring, deterministic English) and `trader/strategy/rule_strategy.py` (three-valued evaluation, crosses, shifts, rule exits).
- **Engine:** supports `exit_signal` (rule exits fill at the next bar's open).
- **Parity (DoD):** Momentum #1 written as a spec reproduces the coded strategy **trade for trade on the real year: 697/697 trades identical** (NIFTY + BANKNIFTY).
- **Translator:** `trader/ai/strategy_translator.py` uses Claude (`claude-opus-5-5`, structured JSON envelope, server-side refusal fallback). Its output is validated by the same Pydantic models, with one append-only repair round. Ambiguity returns questions.
- **API and storage:** `/strategies` (validate, template, translate, save as immutable versions, list, versions). `POST /backtests` takes `strategy_id`, and every run records the spec hash and version id.
- **UI:** the Strategies tab covers describe → assumptions/questions → "What will run" (code-rendered English) → JSON editor with live validation → refine in English → save version → backtest inline.
- **v1 simplification:** the condition editor is JSON with live validation and the English preview. A point-and-click condition builder is a later improvement.
- **Pending:**
  - a live translator run (needs `ANTHROPIC_API_KEY`)
  - the 20-prompt translator evaluation suite (§19.7)
  - the English round-trip check (§19.7)

## 21.4 Fixes found while running on real data
- An empty `SIM_SEED=` in `.env` crashed config loading.
- Requests beyond 1 year failed the whole backfill. They are now clamped, and per-symbol failures no longer abort the run.
- The backtest loader preferred a few days of native 5m candles (from the live collector) over a full year of 1m. It now picks the stored timeframe that covers the most of the requested range.
- The translator returned HTTP 500 instead of a clear message when no Anthropic credentials exist.

# 22. M4 Implementation Notes: Paper Trading (2026-10-21)

## 22.1 What is built
Everything in the M4 scope is built and tested except the 20-day unattended run, which needs real market days.

- **Broker interface and `PaperBroker`** (`trader/execution/`): `Broker` is async (`place_order`, `modify_order`, `cancel_order`, `get_positions`, `get_orders`). `PaperBroker` fills BUY at the live ask and SELL at the live bid, after a latency (`PAPER_LATENCY_MS`, default 500), using the quote at fill time. Time moves only through `process(now)`, so replays are deterministic. Quotes without bid/ask fall back to last ± the modelled half-spread. It is long-only (selling more than the open quantity is rejected), idempotent on `client_order_id`, refuses entries on stale or missing quotes, and charges costs from `costs.yaml`. Exits take the last known quote and are flagged if it is stale.
- **Risk engine** (`trader/risk/engine.py`): deterministic and pure. It runs the §10.5 checklist as 15 independent checks and records all of them, not just the first failure: kill switch, strategy enabled, market open, instrument valid, quantity valid, **data fresh** (underlying and option quote, §14.7), margin, daily loss (realised + unrealised), consecutive losses, trades per day, open positions, lots per trade, risk per trade (premium × quantity × stop %), per-underlying exposure, total premium exposure. Exits are never blocked. A daily-loss breach with an open position flattens it. The kill switch blocks entries and, by default, flattens.
- **Live runner** (`trader/execution/runner.py`): on each closed bar, `FeatureEngine` → `MomentumStrategy` / `RuleStrategy` → risk → `PaperBroker`. The strategy definition is the same `BacktestRequest` the backtester takes, so exits (trail, EOD, time) come from the same place. Stops and targets are checked on every heartbeat against the live bid. The option comes from the broker symbol master (nearest expiry, ATM, lot size from the master). Indicators are warmed from stored candles.
- **Audit trail:** every step is an `events` row sharing one `correlation_id`: `signal.generated` (with the feature snapshot) → `risk.approved|rejected` (all checks) → `order.placed` → `order.filled` → `exit.triggered` → `order.placed` → `order.filled` → `trade.closed`. `GET /paper/trace/{correlation_id}` returns it.
- **Journal and summary:** table `paper_trades` (migration `c8d2e6a1f340`); each row records `data_source` (fyers / simulated / replay) and `option_pricing`. `GET /paper/journal` filters by day, symbol, strategy, direction, result and data source. `GET /paper/summary` gives trades, wins, losses, gross, charges, net, drawdown, profit factor and per-strategy buckets.
- **API** (`/paper/*`): status, start, stop, kill, kill/reset, strategy enable/disable, risk, positions, orders, signals, journal, summary, trace, events. There is **no endpoint that accepts an order**; a test enforces this.
- **Dashboard** (Paper tab): runner state and data-source badge, P&L, a KILL SWITCH button (with confirm), signal panel, open positions, risk-limit meters, latest audit events, journal with filters and summary tiles, and a click-through audit trail per trade.
- **`ReplayProvider`** (`MARKET_DATA_PROVIDER=replay`, `REPLAY_DATE`, `REPLAY_SPEED`, `REPLAY_SOURCE`): plays stored 1m candles (index, near future, VIX) as 12 ticks a minute, shaped so the collector rebuilds the stored OHLC and futures volume exactly (tested). It has its own clock, uses `REPLAY:` tokens and **does not persist market data**, so it cannot overwrite real candles. **Option quotes are synthetic** (Black-Scholes on the replayed spot, IV = VIX × multiplier, modelled spread), because stored data has no historical option bid/ask. The header and journal say so.
- **Rehearsal CLI** (`python -m trader.scripts.paper_rehearsal --underlying NIFTY --start .. --end ..`): replays days in-process with a hand-driven clock (no background tasks, so it is deterministic), journals the trades, then runs the backtester on the same days for comparison.

## 22.2 First rehearsal on real stored data (NIFTY, 12–19 Oct 2026, Momentum #1, 5m)
Paper pipeline: 14 trades, net ₹14,940 (gross ₹15,951, charges ₹1,011). Backtest on the same days: 14 trades, net ₹14,682. The pipeline reproduces the backtest's trades; the ₹258 difference comes from fill timing (5 s after bar close, at the live ask, vs the next bar's open) and tick-level stop checks. **Both use synthetic option prices and a strategy that failed out-of-sample (§21.2). This validates the plumbing, not the strategy, and it says nothing about profit.**

## 22.3 Decisions to know about
- **Risk defaults** (`RISK_*` in `.env`): daily loss ₹5,000, per-trade risk ₹4,000 (a one-lot NIFTY ATM trade at 25% stop risks about ₹3,200, so one loss takes most of the daily budget), 1 lot, 1 open position, 5 trades a day, 3 consecutive losses, ₹40k per underlying and ₹60k total premium, quotes fresher than 10 s. A first draft with ₹3,000 per-trade risk silently blocked every NIFTY entry at spot 26,500, which is the check working as designed; the limit is a deliberate setting.
- **Entries are blocked when the market is closed, with no override.** To exercise the pipeline at any hour, use the replay provider.
- **Fill timing:** the replay ticks every 5 s, so an order's fill lands on the first tick at least `latency` after the signal. Live, it is the first quote after latency.
- **Restart persistence:** done in the M4 hardening pass, see §22.5.
- **Daily-summary generation** is on demand (`/paper/summary`); an end-of-day `daily.summary` event and report are not yet emitted.
- **Single runner, per-lane risk shared:** lanes (underlying × strategy) share one broker and one risk engine, so limits are portfolio-wide.

## 22.4 M4 DoD status
| DoD item | Status |
|---|---|
| 20 consecutive trading days unattended, no unhandled errors | Not started. Needs live market days (`PAPER_AUTOSTART=true`). Restart persistence is done (§22.5), so a restart mid-session no longer loses a position or the kill switch. |
| Every trade traceable signal → order → fill → exit via `correlation_id` | Done, tested (`test_full_day_trades_are_traceable_signal_to_exit`). |
| Paper P&L within tolerance of a backtest on the same days | Machinery done (rehearsal CLI). Tolerance not yet agreed; first result in §22.2. Must be re-checked on recorded live option data, not synthetic. |
| (M3 DoD) Same signals in backtest and paper on replayed data | Done: features and first signal match on identical bars (`test_first_signal_matches_the_backtest_on_the_same_bars`). |

## 22.5 M4 hardening: restart persistence and review fixes (2026-10-21)

### Restart persistence
Open paper positions, the day's risk counters and the kill switch now survive a restart.

- **Storage:** table `paper_state` (migration `d4a7f1b93c58`), one row per data source (`paper-fyers`, `paper-simulated`, `paper-replay`), overwritten on change. `PaperStore.set_state` buffers it and `flush` writes it in the same transaction as the audit events, so the snapshot and the events that explain it never disagree. The runner saves at the end of every heartbeat (only if it changed); the kill-switch, reset and strategy-toggle endpoints save immediately, even with no runner.
- **Contents** (`execution/state.py`): `PaperBroker` cash, realised P&L, order sequence and positions; `RiskEngine` day, trades, day P&L, consecutive losses, open reservations, kill switch and disabled strategies; and, per open position, the runner's `_Live` (signal, entry order, stop, initial stop, target, high-water, low-water, last bid, bars held, exit reason). Not saved: pending orders (a restarted runner places a fresh exit for a position that was leaving) and entries that were ordered but not yet filled (they cannot survive a restart, so their risk reservation is left out).
- **Restore** (`PaperService.restore`, called at API start-up before anything can trade): the kill switch and disabled strategies come back **whatever day they were saved on** (a latch must not clear by restarting); positions, cash, P&L and counters come back **only if the snapshot is from the same IST trading day**. An older snapshot is *not* resumed: status `stale`, a `state.stale` event listing the abandoned positions, and `restore` in `GET /paper/status`. Other statuses: `none`, `restored`, `ignored` (other data source or format), `disabled`, `error` (unreadable state: the kill switch is tripped, because we cannot vouch for it).
- **Adoption:** `PaperRunner.start(restored=...)` / `adopt()` hands each restored position to the lane for its underlying (preferring the same strategy label). If a position has no lane, is not in the symbol master, or does not match the broker's position, **the start is refused** (HTTP 400) rather than leaving a position nobody manages. Adoption emits `position.restored` under the original `correlation_id`, so the trace reads signal → risk → order → fill → **restored** → exit → fill → trade.closed.
- **Replay does not persist** (`PaperService(persist_state=None)` defaults to off for providers with their own clock): its clock restarts at the open, so a saved position would come back from the future. The in-process rehearsal harness opts in for tests, and its snapshot is keyed `paper-replay`, so a rehearsal can never overwrite live state.
- **Tests:** `tests/test_paper_persistence.py` (crash mid-trade and finish it, counters not reset, kill switch survives, stale day not resumed, data-source isolation, no-lane refusal, stop/start, plus an API restart).

### Review fixes (`execution/`, `risk/`, `replay.py`, `routes/paper.py`)
1. **Stop then start orphaned a position.** `PaperRunner.stop()` left the broker position and the risk reservation behind; the next runner had no `_Live` for it, so it was never exited and blocked `max_open_positions` for the rest of the day. `stop()` now withdraws unfilled entries and in-flight exits, and `PaperService.start` hands the open positions to the new runner.
2. **One failing step skipped everything after it.** An exception in bar handling (a strategy that raises) aborted the whole heartbeat, so that tick's fills and stop checks were skipped. Each step of `tick()` is now isolated and recorded as `runner.error`.
3. **A late heartbeat dropped bars.** `lane.pending` held one bar, so two closes between heartbeats lost the older one and left a hole in the indicators. It is now a queue, drained oldest first.
4. **A second start on the same day reset realised P&L and order history.** `_roll_day` treated a fresh runner as a new day. It now starts from the risk engine's day.
5. **A pending order could be enlarged after the risk check** through `modify_order`. Raising the quantity now raises `ValueError`. Nothing calls it today; the interface was the hole.
6. **`kill()` could discard a filled entry.** The cancel of an unfilled entry now notices a fill that landed first and keeps it as a position.

Verified, no change: every entry order is created in `PaperRunner._on_signal`, after `RiskEngine.check`; the only other `broker.submit` is the exit in `_place_exit`. No route accepts an order. Heartbeat and candle handling cannot interleave: `on_candle` only enqueues, and `tick` has no suspension point except the final flush.

### Open questions (not changed)
- **A restart mid-bar builds that bar from a partial bucket.** The candle aggregator is empty after a restart, so the first bar closing after it has only the ticks since. Warm-up covers earlier bars. Fix needs a backfill of the in-progress bucket from stored candles or the broker.
- **An exit with no quote retries every second, forever.** If the held option stops ticking (outside the subscription window, or after a restart before its first tick), `PaperBroker` rejects the exit ("no quote") and `_manage` re-places it each heartbeat, one `alert.exit_rejected` event per second. Needs a rule: subscribe the held contract explicitly, and escalate after N attempts.
- **Cash is not restored from a previous day.** After a restart on a new day the account is back to `PAPER_CAPITAL` while a long-running process would carry its P&L forward. Left alone because "restore only same-day state" and a flat account is the safe default.
- **`/paper/stop` with an open position leaves it unmanaged** until the next start (now preserved, not lost). It should probably flatten or refuse.
- **`/paper/start` accepts duplicate underlyings** (two lanes on one underlying). Risk limits still apply, so the second lane's entries are rejected; harmless but noisy.
- **Risk is checked at the signal-time ask, the fill uses the quote ~0.5 s later.** A fast-moving option can fill above the checked price; the effect on `risk_per_trade` is small but unbounded in principle.
- **`ReplayProvider` after a reconnect re-emits the step in progress** (`t < clock` is skipped, `t == clock` is not). Cumulative volume stays correct; ticks are duplicated once.
- **The snapshot is written once per heartbeat at most**, so a hard crash can lose up to a second of high-water movement. Stops are re-derived from the saved stop, which only ever ratchets up, so the failure is a slightly wider trailing stop, not a lost position.

# 23. Product Direction: SaaS (2026-10-21)

**Status: direction, not a commitment.** Today this is a single-user research and paper-trading tool (one trader, one machine, one broker login). The aim is to grow it into a hosted multi-user product. This section records what that changes, so milestones M4-M5 are built without closing the door. Nothing here changes the rules in §2 or §22.

## 23.1 Gating questions (answer before building anything multi-user)
These are legal and commercial, not technical, and they decide the shape of the product. None is answered here; each needs a lawyer or the vendor.

1. **What exactly is sold?** A tool users run on their own strategies (software) is very different from signals or recommendations (SEBI Investment Adviser / Research Analyst territory). The platform must not generate buy/sell calls for other people unless registered. The safe framing is: users define strategies, the platform backtests and paper-trades them. The §2 rule that LLM output never places orders also keeps the LLM on the "translate the user's own words" side.
2. **Market data licensing.** Fyers API data is licensed for the account holder's own use. A SaaS that fans one feed out to many users is redistribution, and NSE requires a data-vendor licence for that. Until that is settled, the only clean model is **bring your own broker**: each tenant connects their own Fyers (or other) account and the platform reads data with their token.
3. **Retail algo rules (SEBI framework effective 1 April 2026, see §15).** API orders must come from a broker-registered static IP with daily 2FA. A hosted platform placing live orders for many clients is a different regulatory position from one person's own machine. Live trading for tenants (M5 and later) is therefore out of scope until this is understood. **The SaaS is paper-and-research only until then.**
4. **Personal data.** Users' trades, broker tokens and identity fall under India's DPDP Act 2023 (consent, retention, deletion on request).
5. **Pricing basis and liability.** What is a customer paying for, and what does the terms-of-service say about simulated results not predicting returns (see §21.2 and §22.2: the one strategy tested so far failed out-of-sample).

## 23.2 What stays non-negotiable at any scale
- No code path lets LLM output place an order. In a multi-tenant product the LLM is also a cost and abuse surface (§23.5).
- Every entry goes through `RiskEngine.check`. Risk limits are per tenant but **bounded by platform hard caps** that a tenant cannot raise, and there is a global kill switch the operator controls on top of each tenant's own.
- Real, simulated and replay data stay separate and labelled. Every report states data source and option-pricing mode.
- One tenant can never read, affect or slow another's data, state or runner.

## 23.3 Where the current code is single-tenant
Found by reading the code with this question, so the refactor list is concrete.

| Area | Today | Needed |
|---|---|---|
| `PaperService` / `RiskEngine` / `PaperBroker` / `PaperStore` | One instance per API process (`app.state.paper`) | One *account context* per tenant (and per strategy account if a tenant runs several), created and torn down on demand |
| Tables `paper_trades`, `events`, `strategies`, `backtest_runs`, `paper_state` | No owner column. `paper_state` is keyed by data source only | `tenant_id` (and `account_id`) on every row, indexed; `paper_state` keyed by (account, data source); Postgres row-level security as a second line of defence |
| Settings (`RISK_*`, `PAPER_*`, capital, strategy) | Process-wide env vars | Per-account settings rows, validated against platform caps; env keeps only platform defaults and caps |
| Credentials (`.env`, one Fyers token, one Anthropic key) | One owner | Per-tenant broker tokens encrypted at rest (KMS), never logged; one platform Anthropic key with per-tenant quota and metering |
| Market data collector | One provider, one set of subscriptions, one 100-symbol cap | Shared collector per data licence; with BYO-broker, one feed per tenant, so the symbol cap (100 on Standard) is per tenant and the subscription manager must be tenant-scoped |
| Runner | One asyncio runner in the API process | Separate worker processes (queue-driven) so one tenant's strategy or crash cannot stall the API or others; the §22.5 state snapshot is already what makes a worker restartable |
| Auth | None (localhost) | OIDC login, sessions, roles (owner, member, read-only); every route resolves tenant from the session, never from a request field |
| Daily Fyers login (interactive 2FA, token dies 06:00 IST) | The user signs in on the same machine | A tenant-facing "connect broker" flow and a visible "needs re-login" state; a runner whose token expired must pause and alert, not trade or crash |
| Frontend | Single-user dashboard | Tenant-aware, with an account switcher, onboarding, and the risk/kill-switch controls remaining one click away |

Already in our favour: the `Broker` interface hides the venue, the risk engine is pure and deterministic, all state is an explicit snapshot (§22.5), every action carries a `correlation_id`, Alembic owns the schema, and SQLAlchemy already runs on PostgreSQL.

## 23.4 Roadmap
Each stage ships and is useful on its own. No stage turns on live order placement.

- **S0, now:** single user, local machine. Finish M4's 20-day unattended run; this is also the first evidence the runner survives real days.
- **S1, tenancy seams (no user-visible change):** add `account_id` columns (default `"default"`) and thread an `AccountContext` through `PaperService`, risk, broker, store and routes; per-account settings; `paper_state` keyed by account. Test: two accounts in one process cannot see or affect each other's positions, kill switch or journal.
- **S2, hosted single-tenant, then multi-user closed beta:** Postgres, auth, per-tenant encrypted broker tokens, workers for runners, per-tenant quotas (backtests, LLM translations, concurrent runners), BYO-broker data. Invite-only, paper only, a handful of known users.
- **S3, product basics:** onboarding, billing (a payments provider that supports INR and GST invoices), usage metering, admin console with the global kill switch, status page, support tooling, backups and a tested restore, data export and delete (DPDP).
- **S4, live trading (M5) for tenants:** only after §23.1 items 1 and 3 are resolved in writing. Likely each tenant's own broker with their own static IP, never pooled orders.

## 23.5 Cost, abuse and reliability
- **LLM translator:** metered per tenant with a hard monthly cap, and the Strategy Spec validation (§19) stays the only thing that crosses from LLM to engine. Prompts and specs are tenant data.
- **Backtests and replays are CPU-heavy** (a full-year backtest is minutes). They run on a job queue with per-tenant concurrency limits, never in the request path.
- **Isolation of failure:** a tenant's strategy exception, stale feed or expired token degrades that tenant only (the §22.5 per-step isolation is the model), and surfaces as an alert to them.
- **Audit trail** stays append-only and tenant-scoped; operators' access to a tenant's data is itself logged.
- **Observability:** per-tenant metrics (runner heartbeat lag, errors, feed age, risk rejections) feed alerts, since "unattended for 20 days" becomes "unattended for every customer, every day".

## 23.6 Open decisions for the owner
1. Is the first paying customer a retail trader, a small prop desk, or a trading educator? Each implies a different compliance stance and feature set.
2. BYO-broker only, or pursue a data licence?
3. Hosting region and budget (India region for latency and data-residency comfort).
4. Single broker (Fyers) at launch, or a second adapter early so the product is not tied to one vendor's API terms.

