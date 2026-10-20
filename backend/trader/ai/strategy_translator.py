"""Natural language -> Strategy Spec (PRD §19.2, §19.4).

Claude only *translates*. Its answer is treated as untrusted input: the spec is parsed with
the strict Pydantic models and the semantic validator, and anything that fails gets exactly one
repair round with the errors fed back. Ambiguity comes back as questions, never as guesses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from trader.strategy.spec import INDICATORS, MOMENTUM_V1, StrategySpec, render_english, semantic_errors

ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "spec_json": {"type": "string", "description": "The Strategy Spec as a JSON string, or empty if you must ask first."},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "questions": {"type": "array", "items": {"type": "string"}},
        "unsupported": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["spec_json", "assumptions", "questions", "unsupported"],
    "additionalProperties": False,
}


def _operand_table() -> str:
    rows = []
    for name, (needs, bounds, label, level) in INDICATORS.items():
        period = f"period {bounds[0]}-{bounds[1]}" if needs and bounds else "no period"
        rows.append(f"- `{name}` ({period}; {level} scale): {label.format(p='N')}")
    return "\n".join(rows)


SYSTEM_PROMPT = f"""You translate a trader's plain-English description of an intraday NIFTY/BANKNIFTY \
option-buying strategy into a Strategy Spec (JSON) for a deterministic backtesting engine. You never \
write code and never place trades; the user reviews your spec before anything runs.

The platform only BUYS options: "long" = buy ATM CE, "short" = buy ATM PE. No option selling, spreads, \
multi-leg trades, stocks, or futures trading. Decisions are made on each candle's close and orders fill at \
the next candle's open.

## Spec format (JSON Schema)
{json.dumps(StrategySpec.model_json_schema(by_alias=True), separators=(',', ':'))}

## Operands (whitelist; anything else is unsupported)
{_operand_table()}
Each operand may take "shift": N (N bars ago, 0-20). There is no way to refer to future bars.

## Rules you must follow
- Conditions: {{"cmp": [a, op, b]}} with op in > < >= <=; {{"cross": [a, "above"|"below", b]}} (true only on the \
bar where it crosses); {{"between": [value, low, high]}}; and {{"all": [...]}}, {{"any": [...]}}, {{"not": ...}}.
- "Price above/below VWAP" means `fut_close` vs `vwap` (index candles have no volume, so VWAP comes from futures). \
Never compare `close`/`high`/`low`/EMA with `vwap`.
- "Breakout" with a lookback means close vs `highest`/`lowest` of the previous N bars. If the trader says \
"breakout" without saying of what, ask.
- If both directions are wanted and the put rules are the mirror image, use {{"mirror_of": "long"}} for the short side.
- Exit conditions (exits.conditions) are written from the long side's point of view; they are mirrored for puts.
- Use defaults only for genuinely standard choices (RSI/ADX/ATR period 14, MACD 12/26/9, EMA as written) and \
list every default you applied in "assumptions". If a choice changes the strategy materially and the text does \
not settle it (e.g. stop/target missing, which breakout level, which timeframe when several are implied), \
leave spec_json empty and ask in "questions".
- Put anything that cannot be expressed (news, order flow, option Greeks history, selling options, other \
instruments) in "unsupported" and explain briefly.
- Keep the name short (max 80 chars).

## Example
Text: "Momentum on NIFTY 5 min: buy calls when futures are above VWAP, EMA 9 above EMA 21, RSI over 55 and \
price breaks the last 30 minutes' high; mirror for puts. Only 9:30 to 14:45, skip when ADX under 18 or ATR \
under 0.04% of price. SL 25%, target 50%, trail 15% after 30% profit, max 3 trades a day."
spec_json: {json.dumps(MOMENTUM_V1, separators=(',', ':'))}
"""


class TranslatorUnavailable(RuntimeError):
    """No Anthropic credentials, or the API could not be reached."""


@dataclass
class Translation:
    spec: dict | None
    english: str | None
    assumptions: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    model: str = ""
    repaired: bool = False


def validate_spec_json(text: str) -> tuple[StrategySpec | None, list[str]]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"spec_json is not valid JSON: {exc}"]
    try:
        spec = StrategySpec.model_validate(raw)
    except ValidationError as exc:
        return None, [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()][:20]
    errs = semantic_errors(spec)
    return (spec if not errs else None), errs


def _user_message(text: str, base_spec: dict | None) -> str:
    if base_spec:
        return (f"Current spec (edit it; keep everything the request doesn't change):\n"
                f"{json.dumps(base_spec, separators=(',', ':'))}\n\nRequested change:\n{text}")
    return text


class StrategyTranslator:
    def __init__(self, model: str, api_key: str = "", client: Any = None) -> None:
        self.model = model
        self._api_key = api_key
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic

            try:
                self._client = anthropic.Anthropic(api_key=self._api_key) if self._api_key else anthropic.Anthropic()
            except anthropic.AnthropicError as exc:  # no credentials of any kind
                raise TranslatorUnavailable(
                    "No Anthropic credentials. Set ANTHROPIC_API_KEY in .env (or run `ant auth login`).") from exc
        return self._client

    def _call(self, messages: list[dict]) -> dict:
        import anthropic

        try:
            resp = self._get_client().beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=messages,
                output_config={"effort": "medium",
                               "format": {"type": "json_schema", "schema": ENVELOPE_SCHEMA}},
                # Route policy declines to a fallback model instead of failing the request.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise TranslatorUnavailable("Anthropic rejected the API key (ANTHROPIC_API_KEY).") from exc
        except TypeError as exc:
            # The SDK raises TypeError("Could not resolve authentication method...") when no
            # API key, auth token or CLI profile exists at all.
            if "authentication" not in str(exc).lower():
                raise
            raise TranslatorUnavailable(
                "No Anthropic credentials. Set ANTHROPIC_API_KEY in .env (or run `ant auth login`).") from exc
        except anthropic.APIConnectionError as exc:
            raise TranslatorUnavailable("Could not reach the Anthropic API.") from exc
        if resp.stop_reason == "refusal":
            raise TranslatorUnavailable("The model declined this request.")
        if resp.stop_reason == "max_tokens":
            raise TranslatorUnavailable("The translation was cut off (max_tokens); simplify the description.")
        text = next(b.text for b in resp.content if b.type == "text")
        return {"envelope": json.loads(text), "content": resp.content}

    def translate(self, text: str, base_spec: dict | None = None) -> Translation:
        messages: list[dict] = [{"role": "user", "content": _user_message(text, base_spec)}]
        first = self._call(messages)
        env = first["envelope"]
        out = Translation(None, None, env.get("assumptions", []), env.get("questions", []),
                          env.get("unsupported", []), model=self.model)
        if not env.get("spec_json"):
            return out  # questions / unsupported only
        spec, errs = validate_spec_json(env["spec_json"])
        if errs:
            # One repair round, append-only: show the model exactly what failed validation.
            messages += [{"role": "assistant", "content": first["content"]},
                         {"role": "user", "content": "That spec failed validation:\n- " + "\n- ".join(errs)
                          + "\nReturn a corrected spec_json (or ask questions if the request is ambiguous)."}]
            env = self._call(messages)["envelope"]
            out.repaired = True
            out.assumptions = env.get("assumptions", out.assumptions)
            out.questions = env.get("questions", [])
            out.unsupported = env.get("unsupported", out.unsupported)
            if not env.get("spec_json"):
                return out
            spec, errs = validate_spec_json(env["spec_json"])
        out.errors = errs
        if spec is not None:
            out.spec = spec.model_dump(mode="json", by_alias=True, exclude_none=True)
            out.english = render_english(spec)
        return out
