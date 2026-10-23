"""The model proposes strategy variants to test; code decides which survive (PRD §24.10 B9).

Given a strategy and a summary of its walk-forward result, the model suggests a few variants, each with the hypothesis
it tests. That is all it does: every proposal must parse and validate as a Strategy Spec like any human input, and the
judging (walk-forward, Deflated Sharpe, minimum trades) is done by `research.ai_loop`, never by the model. The model
only ever sees results from before the held-back period, and nothing it writes can place an order: this package does
not import execution, risk or brokers (a test enforces it).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from trader.ai.providers import LLMProvider, TranslatorUnavailable
from trader.ai.strategy_translator import _compact_schema, _operand_table, validate_spec_json

MAX_VARIANTS = 8

ENVELOPE = {
    "type": "object",
    "properties": {
        "variants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string", "description": "One sentence: what this variant tests and why."},
                    "spec_json": {"type": "string", "description": "The complete variant Strategy Spec as a JSON string."},
                },
                "required": ["hypothesis", "spec_json"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["variants"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = f"""You are a research assistant for an intraday NIFTY/BANKNIFTY option-BUYING backtesting platform. \
You are given a Strategy Spec and a summary of its walk-forward (out-of-sample) result, including where it made and lost \
money by market condition. Propose variants of the strategy that test concrete hypotheses suggested by that result, \
for example: skip the condition that loses most, add a trend or volatility filter, change the strike, move the stop to \
the index, change the time window.

Rules:
- Each variant is a COMPLETE Strategy Spec (same JSON schema), with a distinct "name" (the original name plus a short \
suffix), the same "market", "underlyings" and "timeframe".
- Change one or two things per variant, so each tests one idea. Do not just retune numbers by small amounts: a sweep \
does that better.
- Long options only (long = buy CE, short = buy PE). Conditions only see closed bars; never refer to future bars.
- You never trade and never decide which variant is good: the engine tests every variant on the same walk-forward and \
judges by out-of-sample results. Say what you expect, not what will happen.

Operands (name[period range]: meaning):
{_operand_table()}

Strategy Spec JSON Schema:
{_compact_schema()}
"""


@dataclass
class Proposal:
    hypothesis: str
    spec: dict | None
    errors: list[str] = field(default_factory=list)


@dataclass
class Proposals:
    items: list[Proposal]
    model: str
    prompt: str  # the user message sent (the system prompt is fixed and public in this module)
    reply: str  # the model's raw JSON, for the audit log


def user_message(spec: dict, summary: dict, n: int) -> str:
    return (f"Propose {n} variants.\n\nCurrent strategy:\n{json.dumps(spec, separators=(',', ':'))}\n\n"
            f"Its walk-forward result (out-of-sample unless stated):\n{json.dumps(summary, separators=(',', ':'))}")


def propose(provider: LLMProvider, spec: dict, summary: dict, n: int = 5) -> Proposals:
    """Ask the model for `n` variants (at most MAX_VARIANTS). Invalid proposals are kept with their errors, not fixed."""
    n = max(1, min(n, MAX_VARIANTS))
    msg = user_message(spec, summary, n)
    reply = provider.complete(SYSTEM_PROMPT, [{"role": "user", "content": msg}], ENVELOPE)
    try:
        env = json.loads(reply.text)
    except json.JSONDecodeError as exc:
        raise TranslatorUnavailable(f"The model's answer was not valid JSON: {exc}") from exc
    items: list[Proposal] = []
    for v in (env.get("variants") or [])[:n]:
        if not isinstance(v, dict):
            continue
        parsed, errs = validate_spec_json(str(v.get("spec_json", "")))
        if parsed is not None and parsed.market != spec.get("market", "options"):
            errs = [*errs, "changed the market"]
        items.append(Proposal(str(v.get("hypothesis", ""))[:300],
                              None if errs or parsed is None else parsed.model_dump(mode="json", by_alias=True, exclude_none=True),
                              errs))
    return Proposals(items, getattr(provider, "label", provider.name), msg, reply.text)
