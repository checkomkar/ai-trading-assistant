# STRIKTEK: plans, metering and the SaaS layer

Status 2026-10-21: built and tested locally (`AUTH_ENABLED=true`). Not deployed. Payments are wired for Razorpay but untested against the real service.

## Plans (INR per month, GST included)

| | Trial (free) | Pro ₹499 | Max ₹1,499 |
|---|---|---|---|
| AI strategy generations | **5 for the whole trial** | 60 / month | 250 / month |
| Assistant messages | 25 | 300 | 1,200 |
| Voice inputs | 10 | 300 | 1,200 |
| Backtests (no AI cost) | 30 | 400 | 2,500 |
| Saved strategies | 10 | 100 | 500 |
| Uploaded files | 3 | 25 | 100 |
| Hard AI cost ceiling (USD / period) | 0.40 | 1.80 | 5.50 |

Backtests, charts and (for the operator) paper trading call no model, so they are limited only to protect CPU.
The numbers live in `backend/trader/billing/plans.py`; change them there.

## How the margin is protected

Counters are what the customer sees. The **AI cost ceiling** is what protects us: every model call records its real
token count and cost (`billing/pricing.py`) in the `usage_events` ledger, and once a period's cost reaches the ceiling
all AI calls stop, even if counters remain. Unknown models are priced high on purpose, and free OpenRouter models cost 0.

`tests/test_billing.py::test_plan_margins_hold` fails if a paid plan's ceiling exceeds 40% of its revenue after GST:

| Plan | Revenue ex-GST | Worst-case AI cost | AI share | Left before payment fees |
|---|---|---|---|---|
| Pro | ₹423 | ₹158 | 37% | ₹265 |
| Max | ₹1,270 | ₹484 | 38% | ₹786 |

Typical use is far below the ceiling: a translation is roughly 3k tokens in and 1.5k out, about $0.003 on Groq
gpt-oss-120b and about $0.03 on a premium model. On the cheap model a Pro customer using all 60 generations costs
well under ₹20. The ceiling matters when the platform key points at an expensive model.
Payment fees and your own hosting come out of the "left" column.

## What a "generation" is

One strategy translation (the Translate button, or the assistant drafting a strategy). If no model call was billed
(for example every provider is down) the unit is given back. Assistant messages are a separate, larger allowance.

## Operating it

```bash
# run with accounts on (Settings: AUTH_ENABLED=true, ADMIN_EMAILS=you@example.com, AUTH_COOKIE_SECURE=true behind HTTPS)
.venv/Scripts/python -m trader.scripts.accounts list
.venv/Scripts/python -m trader.scripts.accounts grant friend@example.com pro --days 30
.venv/Scripts/python -m trader.scripts.accounts claim-local you@example.com   # move today's local data onto your account
```

Admin accounts get the unlimited `owner` plan and the Paper tab. Customers do not see Paper: the paper runner is
one shared runner until per-account runners exist (PRD §23.4, S1).

## Options checked on 2026-10-21 (not yet built)

**Supabase for the database: yes, workable.** It is plain PostgreSQL; the code already runs on SQLAlchemy + Alembic.
Needs the `asyncpg` driver and care with connections: Supabase recommends the direct connection (IPv6) or the
session pooler (port 5432) for a long-running server, and the transaction pooler (port 6543) only with SQLAlchemy `NullPool`
and prepared statements off. Run Alembic against the direct or session URL. Keep the high-volume market tick tables out of the free
tier's size limit (store candles and option snapshots, not raw ticks, or keep them on separate storage).
Sources: [Using SQLAlchemy with Supabase](https://supabase.com/docs/guides/troubleshooting/using-sqlalchemy-with-supabase-FUqebT), [connecting to Postgres](https://supabase.co/docs/guides/database/connecting-to-postgres).

**Supabase Auth with Google sign-in: workable, and simpler than the password code.** The React app calls
`supabase.auth.signInWithOAuth({provider: "google"})`; the backend verifies the Supabase access token (JWT) against the
project's JWKS (asymmetric keys are recommended), then maps the token's `sub` to our `accounts.id`. This would replace
`/auth/signup` and `/auth/login` (no passwords to store, Google has verified the email, which also reduces trial abuse).
The plans, meter and per-account scoping stay as they are. Source: [Supabase + FastAPI](https://dev.to/j0/integrating-fastapi-with-supabase-auth-780).

**Dodo Payments instead of Razorpay: possible, different trade-off.** Dodo is a merchant of record: it sells to the
customer and handles tax (including for customers abroad), supports subscriptions and UPI in India, and its
webhooks follow the Standard Webhooks spec (`webhook-id`, `webhook-timestamp`, `webhook-signature`).
Its own pages list India domestic payments at about 4% plus a fixed fee, with subscriptions +0.5%, and admit it costs
more than Razorpay for India-only INR sales. These figures come from Dodo's marketing, so confirm the current rate card.
Choose Dodo if you expect customers outside India (you would not have to register for foreign taxes);
choose Razorpay if the market is India and you want lower fees (and GST invoices on your own account).
Sources: [Dodo vs Razorpay](https://dodopayments.com/blogs/razorpay-alternatives/), [Dodo pricing](https://dodopayments.com/id/pricing), [Dodo webhooks](https://docs.dodopayments.com/developer-resources/webhooks).

Both providers fit behind one small interface (create checkout, cancel, verify and decode a webhook, map plan ids), because
access is already granted only by a verified webhook.

## Known gaps before real customers

- Email verification and password reset (not needed if Supabase Google login replaces passwords).
- Trial abuse: one person can open many accounts. Verified Google sign-in, an invite code (`SIGNUP_INVITE_CODE`) or a phone check reduce it.
- The Razorpay flow is unit-tested with a signed fake webhook but has not been run against Razorpay's test mode.
- Backtests run in the request path; a job queue and per-account concurrency limit are still to do (PRD §23.5).
- Per-account paper trading, per-account broker keys, and Postgres row-level security (PRD §23.4 S1-S2).
- Legal: terms, privacy policy (DPDP Act), and the research-only positioning (PRD §23.1) need a lawyer's review.

## Zero-cost-until-revenue plan (decided 2026-10-21)

- **Payments: Razorpay.** Fees are per successful payment only: about 2% plus GST on domestic cards and UPI, with no setup or annual charge
  ([Razorpay pricing explained](https://razorpay.com/blog/razorpay-payment-gateway-pricing-explained/)). I did not confirm from a Razorpay page that
  test mode is free, but their documentation describes it as the way to build before going live; check when you create the account.
  Subscriptions may need a separate approval from Razorpay.
- **Database: Supabase free plan, but not raw ticks.** Sources report 500 MB per project and pausing after 7 idle days on the free plan
  ([overview](https://www.jetadmin.io/blog/supabase-pricing-2026-guide-to-plans-limits-and-real-world-costs/)); confirm on supabase.com.
  Measured here: 1.7 million tick rows in 3 trading days (about 600k a day) and a 323 MB database after 3 days, so raw ticks alone would fill the free plan in about a week.
  Nothing reads the tick table (backtests use 1-minute candles; option chains use snapshots), so for a hosted database set `STORE_TICKS=false`.
  Candles (about 750k rows for a year of 1m NIFTY and BANKNIFTY) and snapshots are what to keep. A free project that pauses when idle is acceptable for a beta, not for paying customers.
- **Live feed per customer is not Supabase's job.** Each customer's live data comes from their own broker keys; Supabase would only hold results.
- **Hosting:** any free tier works for a closed beta (for example the Cloudflare tunnel in `docs/deploy-striktek-si.md` on your own machine). Moving to always-on hosting is the first real cost.

## Brokers (bring your own keys)

`Brokers` page: a catalogue (Fyers, Upstox, Angel One, Dhan, Zerodha, Shoonya) and encrypted per-customer key storage
(`trader/accounts/vault.py`, Fernet, server key in `CREDENTIAL_KEY`; generate with `python -m trader.scripts.accounts gen-key`). Keys are write-only:
no route returns a secret, and a test checks the database holds only ciphertext.

What is **not** built yet: only the Fyers adapter exists, and it still reads the operator's `.env`, not the saved customer keys. Using a customer's keys needs
a per-account data feed (PRD §23.3: one collector, 100-symbol cap and daily login per customer), one adapter per broker behind the `MarketDataProvider` interface,
and a "connect and test" step per broker. Broker data prices reported on 2026-10-21 (verify): Fyers, Upstox, Angel One free; Dhan about ₹499 a month; Zerodha about ₹500 a month
([comparison](https://algotest.in/blog/best-brokers-for-algo-trading-in-india.md)).
