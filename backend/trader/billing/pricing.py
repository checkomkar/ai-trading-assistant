"""What a model call costs us, from token counts. USD per million tokens (input, output).

Unknown models are priced high on purpose: an unlisted paid model should make the ledger over-count, never
under-count, so the budget cap stays a real cap. Free OpenRouter models (`:free`) cost nothing.
"""

from __future__ import annotations

# (substring of "<provider>/<model>", (input, output)); the first match wins, so order specific before general.
PRICES: list[tuple[str, tuple[float, float]]] = [
    (":free", (0.0, 0.0)),
    ("openrouter/free", (0.0, 0.0)),
    ("anthropic/claude-haiku", (1.0, 5.0)),
    ("anthropic/claude-sonnet", (3.0, 15.0)),
    ("anthropic/claude-opus", (5.0, 25.0)),
    ("anthropic/claude-fable", (5.0, 25.0)),
    ("groq/openai/gpt-oss-120b", (0.15, 0.60)),
    ("groq/openai/gpt-oss-20b", (0.075, 0.30)),
    ("groq/llama", (0.60, 0.80)),
    ("openai/gpt-4o-mini", (0.15, 0.60)),
    ("openai/gpt-4o", (2.5, 10.0)),
]
DEFAULT_PRICE = (5.0, 25.0)  # unknown model: assume the dearest tier we use


def price_for(label: str) -> tuple[float, float]:
    low = label.lower()
    for needle, price in PRICES:
        if needle in low:
            return price
    return DEFAULT_PRICE


def cost_usd(label: str, tokens_in: int, tokens_out: int) -> float:
    pin, pout = price_for(label)
    return (tokens_in * pin + tokens_out * pout) / 1_000_000


def estimate_tokens(text: str) -> int:
    """~4 characters per token. Only used when a provider omits its usage block."""
    return max(1, len(text) // 4)
