"""Plans, limits and the unit economics behind them (PRD §23.5).

Two kinds of limit work together:
  * counters the customer can see and understand (generations, chat messages, backtests ...), and
  * `ai_budget_usd`, a hard ceiling on what the period's AI calls may actually cost us. The counters are the
    product; the budget is what keeps the margin if a customer uses every unit on the most expensive model.

Backtests, paper trading and charts call no model, so they cost almost nothing and are limited only to protect CPU.
`tests/test_billing.py` fails if a paid plan's budget would eat more than MAX_AI_COST_SHARE of its revenue.
"""

from __future__ import annotations

from dataclasses import dataclass, field

USD_INR = 88.0  # planning rate for the margin check only; invoices use whatever the payment provider charges
GST = 0.18
MAX_AI_COST_SHARE = 0.40  # at most 40% of revenue (after GST) may go to the model providers


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    price_inr: int  # per month, GST included
    tagline: str
    lifetime: bool  # True = the limits are for the whole life of the account (trial), not per month
    generations: int | None  # AI strategy translations. None = unlimited
    chat_messages: int | None
    speech_requests: int | None
    backtests: int | None
    strategies: int | None  # saved strategies at one time
    docs: int | None  # uploaded files in the assistant's knowledge base
    ai_budget_usd: float | None  # hard cap on model cost per period
    features: tuple[str, ...] = field(default_factory=tuple)
    public: bool = True

    def limit(self, resource: str) -> int | None:
        return getattr(self, resource)

    @property
    def revenue_ex_gst_inr(self) -> float:
        return self.price_inr / (1 + GST)

    @property
    def ai_budget_inr(self) -> float:
        return (self.ai_budget_usd or 0) * USD_INR


# What a plan is measured on. `generations` and `chat_messages` and `speech_requests` call a model (AI units);
# `backtests` is metered per run; `strategies` and `docs` are stock limits counted from the tables themselves.
AI_RESOURCES = ("generations", "chat_messages", "speech_requests")
EVENT_RESOURCES = (*AI_RESOURCES, "backtests")
STOCK_RESOURCES = ("strategies", "docs")

PLANS: dict[str, Plan] = {p.id: p for p in (
    Plan("trial", "Trial", 0, "Try the full workflow, free.", lifetime=True,
         generations=5, chat_messages=25, speech_requests=10, backtests=30, strategies=10, docs=3, ai_budget_usd=0.40,
         features=("5 AI strategy generations", "25 assistant messages", "30 backtests", "Live charts and option chain")),
    Plan("pro", "Pro", 499, "For a trader building and testing strategies.", lifetime=False,
         generations=60, chat_messages=300, speech_requests=300, backtests=400, strategies=100, docs=25, ai_budget_usd=1.80,
         features=("60 AI generations / month", "300 assistant messages / month", "400 backtests / month",
                   "Voice input", "Upload PDFs, sheets and images")),
    Plan("max", "Max", 1499, "For heavy research and many strategies.", lifetime=False,
         generations=250, chat_messages=1200, speech_requests=1200, backtests=2500, strategies=500, docs=100, ai_budget_usd=5.50,
         features=("250 AI generations / month", "1,200 assistant messages / month", "2,500 backtests / month",
                   "Voice input", "Upload PDFs, sheets and images", "Priority when providers are busy")),
    # Not sold: the operator's own account and local single-user mode (AUTH_ENABLED=false).
    Plan("owner", "Owner", 0, "No limits.", lifetime=False, generations=None, chat_messages=None, speech_requests=None,
         backtests=None, strategies=None, docs=None, ai_budget_usd=None, public=False),
)}

PAID = ("pro", "max")
FREE_PLAN = "trial"


def get_plan(plan_id: str) -> Plan:
    return PLANS.get(plan_id) or PLANS[FREE_PLAN]


def public_plans() -> list[Plan]:
    return [p for p in PLANS.values() if p.public]


def margin_report(plan: Plan) -> dict:
    """Worst case for a paid plan: the customer spends the whole AI budget. Used by the tests and by docs/pricing.md."""
    rev = plan.revenue_ex_gst_inr
    cost = plan.ai_budget_inr
    return {"plan": plan.id, "revenue_ex_gst_inr": round(rev), "max_ai_cost_inr": round(cost),
            "ai_share": round(cost / rev, 3) if rev else None, "gross_margin_inr": round(rev - cost)}
