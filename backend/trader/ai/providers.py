"""LLM providers for the strategy translator: Anthropic natively, and anything that speaks the OpenAI
chat-completions API (OpenRouter, Groq, OpenAI, Ollama, Together, ...) through one HTTP client.

A provider only turns "system prompt + messages + a JSON schema" into text. Its answer is untrusted input
that the translator validates; no provider ever sees an order, a position or a broker (PRD §2).
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


# Free model tiers count the reply allowance against their per-minute token limit (Groq: 8,000 for one request), so ask for no more
# than a strategy spec or a chat answer needs.
MAX_REPLY_TOKENS = 2000
BUSY_MESSAGE = "The AI is busy or unavailable right now. Please try again in a minute."


def public_message(exc: Exception, is_admin: bool) -> str:
    """Customers get a plain message; the provider chain's own errors (keys, tiers, rate limits) are the operator's business."""
    return str(exc) if is_admin else BUSY_MESSAGE


class TranslatorUnavailable(RuntimeError):
    """No credentials, a rejected key, a refusal, or the provider could not be reached."""


@dataclass
class LLMReply:
    text: str  # the JSON object the model produced
    tokens_in: int = 0  # what the provider billed; 0 = it did not say (the meter then estimates)
    tokens_out: int = 0
    model: str = ""  # label of the model that really answered (differs from the first choice after a fallback)


class LLMProvider(Protocol):
    name: str
    model: str

    @property
    def label(self) -> str: ...

    def complete(self, system: str, messages: list[dict], schema: dict, tools: list[dict] | None = None) -> LLMReply: ...

    def describe_image(self, data: bytes, mime: str, prompt: str) -> str: ...


# ---- Anthropic -----------------------------------------------------------------------------------
class AnthropicProvider:
    name = "anthropic"

    @property
    def label(self) -> str:
        return f"{self.name}/{self.model}"

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

    def complete(self, system: str, messages: list[dict], schema: dict, tools: list[dict] | None = None) -> LLMReply:
        import anthropic  # `tools` is ignored: Claude follows the JSON action format from the prompt and schema

        try:
            resp = self._get_client().beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=messages,
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
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
        usage = getattr(resp, "usage", None)
        return LLMReply(text, int(getattr(usage, "input_tokens", 0) or 0), int(getattr(usage, "output_tokens", 0) or 0),
                        self.label)

    def describe_image(self, data: bytes, mime: str, prompt: str) -> str:
        import anthropic

        content = [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(data).decode()}},
                   {"type": "text", "text": prompt}]
        try:
            resp = self._get_client().messages.create(model=self.model, max_tokens=2000,
                                                      messages=[{"role": "user", "content": content}])
        except anthropic.AuthenticationError as exc:
            raise TranslatorUnavailable("Anthropic rejected the API key (ANTHROPIC_API_KEY).") from exc
        except (anthropic.APIConnectionError, anthropic.APIStatusError, TypeError) as exc:
            raise TranslatorUnavailable(f"Anthropic could not read the image: {exc}") from exc
        return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---- OpenAI-compatible (OpenRouter, Groq, OpenAI, Ollama, ...) ------------------------------------
MAX_RATE_WAIT = 45.0  # seconds: a per-minute token limit resets within this, so waiting beats failing


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers.get("retry-after", ""))
    except ValueError:
        return None


def extract_json(text: str) -> str:
    """Models without a strict JSON mode wrap the object in prose or ``` fences: take the outermost {...}."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    # The FIRST complete object: a model that keeps talking (or invents the tool result it is waiting for) may
    # append more JSON after it, and that must not be mistaken for part of the answer.
    decoder, pos = json.JSONDecoder(), text.find("{")
    while pos >= 0:
        try:
            obj, end = decoder.raw_decode(text, pos)
        except ValueError:
            pos = text.find("{", pos + 1)
            continue
        if isinstance(obj, dict):
            return text[pos:end]
        pos = text.find("{", end)
    raise TranslatorUnavailable("The model did not return a JSON object.")


class OpenAICompatibleProvider:
    def __init__(self, name: str, model: str, api_key: str, base_url: str, timeout: float = 120.0,
                 client: httpx.Client | None = None, extra_headers: dict | None = None,
                 vision: bool | None = None) -> None:
        self.name, self.model, self.base_url = name, model, base_url.rstrip("/")
        self.vision = vision  # False = known to be text-only (skip it for images); None = unknown, try
        self._api_key, self._timeout, self._client = api_key, timeout, client
        self._headers = extra_headers or {}

    @property
    def label(self) -> str:
        return f"{self.name}/{self.model}"

    def _post(self, body: dict) -> httpx.Response:
        headers = {"Content-Type": "application/json", **self._headers}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        for attempt in (0, 1, 2):
            try:
                if self._client is not None:
                    resp = self._client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
                else:
                    resp = httpx.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=self._timeout)
            except httpx.HTTPError as exc:
                raise TranslatorUnavailable(f"Could not reach {self.name} ({self.base_url}).") from exc
            wait = _retry_after(resp)
            if resp.status_code == 429 and attempt < 2 and wait is not None and wait <= MAX_RATE_WAIT:
                time.sleep(wait)  # a short per-minute limit: waiting beats failing over to another model
                continue
            return resp
        return resp

    def complete(self, system: str, messages: list[dict], schema: dict, tools: list[dict] | None = None) -> LLMReply:
        """Structured output, best channel first: a forced function call whose arguments are the schema (what
        gpt-oss/Llama/GPT models are trained for), then a plain reply, then the API's JSON mode."""
        if self.name != "custom" and not self._api_key:
            raise TranslatorUnavailable(f"No API key for {self.name}. Set it in .env (see .env.example).")
        # With native tools the schema text is dead weight, and tokens count against per-minute limits.
        sys_msg = system if tools else (
            f"{system}\n\nReply with ONE JSON object and nothing else, matching this JSON Schema:\n"
            f"{json.dumps(schema, separators=(',', ':'))}")
        base: dict = {"model": self.model, "temperature": 0, "max_tokens": MAX_REPLY_TOKENS,
                      "messages": [{"role": "system", "content": sys_msg}, *messages]}
        if tools:
            # Native function calling: the real lookups plus `respond`, and the model must call exactly one. Models
            # trained for tool use (gpt-oss, Llama, GPT) behave far better here than when asked to imitate a protocol.
            fns = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                      "parameters": t["parameters"]}} for t in tools]
            fns.append({"type": "function", "function": {
                "name": "respond", "description": "Give the final reply to the trader (after any lookups you needed).",
                "parameters": {"type": "object", "properties": {
                    "text": {"type": "string", "description": "The reply (markdown allowed)."},
                    "remember": {"type": "array", "items": {"type": "string"},
                                 "description": "Durable facts about the trader's setup (usually empty)."}},
                               "required": ["text"]}}})
            first = {"tools": fns, "tool_choice": "required"}
        else:
            first = {"tools": [{"type": "function", "function": {
                "name": "respond", "description": "Send your reply, in the required JSON format.", "parameters": schema}}],
                "tool_choice": {"type": "function", "function": {"name": "respond"}}}
        modes = [first, {}, {"response_format": {"type": "json_object"}}]
        last_err: TranslatorUnavailable | None = None
        for i, extra in enumerate(modes):
            resp = self._post({**base, **extra})
            if resp.status_code in (401, 403):
                raise TranslatorUnavailable(f"{self.name} rejected the API key.")
            if resp.status_code == 429:
                raise TranslatorUnavailable(f"{self.name} is rate limiting this key; try again shortly.")
            if resp.status_code >= 400:
                # this mode isn't supported by the model/provider (or it produced invalid output): try the next one
                last_err = TranslatorUnavailable(f"{self.name} error {resp.status_code}: {resp.text[:300]}")
                continue
            try:
                text = self._parse(resp, native_tools=bool(tools) and "tool_choice" in extra
                                   and extra["tool_choice"] == "required")
                usage = (resp.json().get("usage") or {}) if resp.content else {}
                return LLMReply(text, int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0),
                                self.label)
            except TranslatorUnavailable as exc:
                if "declined" in str(exc) or "cut off" in str(exc):
                    raise
                last_err = exc
        raise last_err or TranslatorUnavailable(f"No answer from {self.name}.")

    def _parse(self, resp: httpx.Response, native_tools: bool = False) -> str:
        try:
            choice = resp.json()["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, ValueError) as exc:
            raise TranslatorUnavailable(f"Unexpected response from {self.name}.") from exc
        if msg.get("refusal"):
            raise TranslatorUnavailable("The model declined this request.")
        if choice.get("finish_reason") == "length":
            raise TranslatorUnavailable("The translation was cut off (max tokens); simplify the description.")
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, dict):
                args = json.dumps(args)
            if native_tools and fn.get("name") == "respond":  # translate native calls into the engine's action format
                a = json.loads(args or "{}")
                return json.dumps({"action": "answer", "tool": "", "args_json": "{}", "text": a.get("text", ""),
                                   "remember": a.get("remember") or []})
            if native_tools and fn.get("name"):
                return json.dumps({"action": "tool", "tool": fn["name"], "args_json": args or "{}", "text": "",
                                   "remember": []})
            if args:  # the forced `respond` call of structured-output mode: its arguments are the answer
                return extract_json(args)
        return extract_json(msg.get("content") or msg.get("reasoning") or "")

    def describe_image(self, data: bytes, mime: str, prompt: str) -> str:
        if self.name != "custom" and not self._api_key:
            raise TranslatorUnavailable(f"No API key for {self.name}. Set it in .env (see .env.example).")
        if self.vision is False:
            raise TranslatorUnavailable(f"{self.model} is text-only")
        url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        body = {"model": self.model, "max_tokens": 2000, "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]}]}
        resp = self._post(body)
        if resp.status_code >= 400:
            raise TranslatorUnavailable(f"{self.name} could not read the image ({resp.status_code}): {resp.text[:200]}")
        try:
            return (resp.json()["choices"][0]["message"].get("content") or "").strip()
        except (KeyError, IndexError, ValueError) as exc:
            raise TranslatorUnavailable(f"Unexpected response from {self.name}.") from exc


# ---- selection -----------------------------------------------------------------------------------
PRESETS: dict[str, dict[str, str]] = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "google/gemma-4-31b-it:free"},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
    "custom": {"base_url": "", "model": ""},  # any OpenAI-compatible server: set LLM_BASE_URL and LLM_MODEL
}
PROVIDERS = ("anthropic", *PRESETS)


def is_free_openrouter_model(model: str) -> bool:
    """OpenRouter's free tier: model ids ending in ':free', plus its 'openrouter/free' router. Anything else bills credits."""
    return model.endswith(":free") or model == "openrouter/free"


def _build_one(name: str, settings: Any, primary: bool, model: str = "") -> LLMProvider:
    """LLM_MODEL / LLM_BASE_URL / LLM_API_KEY describe the primary provider only; fallbacks use their own keys."""
    if name not in PROVIDERS:
        raise TranslatorUnavailable(f"Unknown LLM provider '{name}'. Use one of: {', '.join(PROVIDERS)}.")
    override = (lambda v: v if primary else "")
    model = model or override(settings.llm_model)
    key = override(settings.llm_api_key)
    if name == "anthropic":
        return AnthropicProvider(model or settings.strategy_translator_model, key or settings.anthropic_api_key)
    preset = PRESETS[name]
    key = key or getattr(settings, f"{name}_api_key", "")
    base_url, model = override(settings.llm_base_url) or preset["base_url"], model or preset["model"]
    if name == "openrouter" and settings.openrouter_free_only and not is_free_openrouter_model(model):
        raise TranslatorUnavailable(
            f"OPENROUTER_FREE_ONLY is on and '{model}' is not a free model (free ones end in ':free'). "
            "Pick a free model, or set OPENROUTER_FREE_ONLY=false to allow paid ones.")
    if not base_url or not model:
        raise TranslatorUnavailable(f"LLM_PROVIDER={name} needs LLM_BASE_URL and LLM_MODEL in .env.")
    return OpenAICompatibleProvider(name, model, key, base_url)


def _has_key(p: LLMProvider) -> bool:
    return bool(getattr(p, "_api_key", "")) or p.name == "custom"


# Provider health, shared by all requests: it is infrastructure state, not anything owned by a trader.
_COOLDOWN: dict[str, float] = {}
COOLDOWN_SECONDS = 60.0


class FallbackProvider:
    """Tries each provider in order and moves on when one is unavailable (no key, rate limit, outage, refusal).
    The answer is still validated by the translator, so a different model never changes what is trusted."""

    def __init__(self, providers: list[LLMProvider]) -> None:
        self.providers = providers
        self.used: LLMProvider = providers[0]
        self.name, self.model = providers[0].name, providers[0].model

    @property
    def label(self) -> str:
        return self.used.label

    def _order(self) -> list[LLMProvider]:
        """Models that failed recently go last, so a rate-limited free model isn't retried on every request."""
        now = time.monotonic()
        ready = [p for p in self.providers if _COOLDOWN.get(p.label, 0) <= now]
        return ready + [p for p in self.providers if p not in ready]

    def complete(self, system: str, messages: list[dict], schema: dict, tools: list[dict] | None = None) -> LLMReply:
        failures: list[str] = []
        for p in self._order():
            try:
                reply = p.complete(system, messages, schema, tools)
            except TranslatorUnavailable as exc:
                failures.append(f"{p.label}: {exc}")
                if "declined" not in str(exc):  # a refusal says nothing about the model's health
                    _COOLDOWN[p.label] = time.monotonic() + COOLDOWN_SECONDS
                continue
            _COOLDOWN.pop(p.label, None)
            self.used = p
            return reply
        raise TranslatorUnavailable("; ".join(failures) if len(failures) > 1 else failures[0].split(": ", 1)[1])

    def describe_image(self, data: bytes, mime: str, prompt: str) -> str:
        failures: list[str] = []
        for p in self.providers:
            try:
                text = p.describe_image(data, mime, prompt)
            except TranslatorUnavailable as exc:
                failures.append(f"{p.label}: {exc}")
                continue
            if text:
                self.used = p
                return text
        raise TranslatorUnavailable("No configured model could read the image. " + "; ".join(failures))


# ---- OpenRouter's free models ----------------------------------------------------------------------------
# Used in this order when found; the rest of the discovered free models follow by context size.
PREFERRED_FREE = ["google/gemma-4-31b-it:free", "nvidia/nemotron-3-super-120b-a12b:free", "minimax/minimax-m3.1-flash:free",
                  "google/gemma-4-26b-a4b-it:free", "poolside/laguna-s-2.1:free", "thinkingmachines/inkling:free"]
STATIC_FREE = [(m, None) for m in PREFERRED_FREE]
_FREE_CACHE: dict[str, Any] = {"at": 0.0, "models": STATIC_FREE}


def free_models(base_url: str = "https://openrouter.ai/api/v1", ttl: float = 3600.0) -> list[tuple[str, bool | None]]:
    """(model id, accepts images) for OpenRouter models that cost nothing and support tool calls, best first.
    Discovered from OpenRouter's public catalogue and cached; a static list is the fallback if it can't be reached."""
    now = time.monotonic()
    if _FREE_CACHE["at"] and now - _FREE_CACHE["at"] < ttl:
        return _FREE_CACHE["models"]
    try:
        data = httpx.get(f"{base_url}/models", timeout=10).json()["data"]
        found: dict[str, tuple[int, bool]] = {}
        for m in data:
            pr = m.get("pricing") or {}
            mid = m.get("id", "")
            if not (is_free_openrouter_model(mid) and float(pr.get("prompt", 1)) == 0 and float(pr.get("completion", 1)) == 0):
                continue
            arch = m.get("architecture") or {}
            if "tools" not in (m.get("supported_parameters") or []) or "text" not in (arch.get("output_modalities") or ["text"]):
                continue
            if any(w in mid for w in ("safety", "guard")) or mid == "openrouter/free":
                continue
            found[mid] = (int(m.get("context_length") or 0), "image" in (arch.get("input_modalities") or []))
        ranked = [m for m in PREFERRED_FREE if m in found] + sorted((m for m in found if m not in PREFERRED_FREE),
                                                                    key=lambda m: -found[m][0])
        models = [(m, found[m][1]) for m in ranked] or STATIC_FREE
        _FREE_CACHE.update(at=now, models=models)
    except Exception:  # noqa: BLE001 - offline or changed API: keep the last known list, retry in 5 minutes
        _FREE_CACHE["at"] = now - ttl + 300
    return _FREE_CACHE["models"]


def _openrouter_pool(settings: Any, chain: list[LLMProvider]) -> list[LLMProvider]:
    """Extra free models to try after an OpenRouter provider in the chain fails (OPENROUTER_FREE_POOL = how many)."""
    out: list[LLMProvider] = []
    if not settings.openrouter_free_only or settings.openrouter_free_pool <= 0:
        return out  # pooling is only ever done with free models
    key = settings.llm_api_key if settings.llm_provider == "openrouter" and settings.llm_api_key else settings.openrouter_api_key
    for model, vision in free_models():
        if len(out) >= settings.openrouter_free_pool:
            break
        if all(f"openrouter/{model}" != q.label for q in [*chain, *out]):
            out.append(OpenAICompatibleProvider("openrouter", model, key, PRESETS["openrouter"]["base_url"],
                                                timeout=60.0, vision=vision))
    return out


def build_provider(settings: Any, is_admin: bool = False) -> LLMProvider:
    """For operator accounts: the paid OpenRouter models in ADMIN_LLM_MODELS first (the free-only rule does not apply to them),
    then the normal chain below. Everyone else gets the normal chain.

    Normal chain: LLM_PROVIDER first, then each LLM_FALLBACKS entry (`name` or `name:model`) that has credentials. If OpenRouter
    is in the chain, other free OpenRouter models follow it, so one model being down or limited doesn't stop the chat."""
    primary = _build_one((settings.llm_provider or "anthropic").strip().lower(), settings, primary=True)
    chain = [primary]
    for entry in (e.strip() for e in (settings.llm_fallbacks or "").split(",") if e.strip()):
        name, _, model = entry.partition(":")
        try:
            p = _build_one(name.strip().lower(), settings, primary=False, model=model.strip())
        except TranslatorUnavailable:
            continue  # a bad or disallowed fallback (e.g. a paid OpenRouter model) is skipped, never used
        if _has_key(p) and all(p.label != q.label for q in chain):  # "available" = has a key, and not a duplicate
            chain.append(p)
    if any(q.name == "openrouter" and _has_key(q) for q in chain):
        at = max(i for i, q in enumerate(chain) if q.name == "openrouter")
        chain[at + 1:at + 1] = _openrouter_pool(settings, chain)  # right after the last pinned OpenRouter model
    base: LLMProvider = FallbackProvider(chain) if len(chain) > 1 else primary
    paid = _admin_models(settings) if is_admin else []
    if not paid:
        return base
    return FallbackProvider([*paid, *(base.providers if isinstance(base, FallbackProvider) else [base])])


def _admin_models(settings: Any) -> list[LLMProvider]:
    ids = [m.strip() for m in (getattr(settings, "admin_llm_models", "") or "").split(",") if m.strip()]
    if not ids or not getattr(settings, "openrouter_api_key", ""):
        return []
    allow_paid = settings.model_copy(update={"openrouter_free_only": False}) if hasattr(settings, "model_copy") else settings
    return [_build_one("openrouter", allow_paid, primary=False, model=m) for m in ids]


def describe(settings: Any) -> dict:
    """What the UI shows: which provider and model translate, the fallbacks, and whether a key is present (never the key)."""
    try:
        p = build_provider(settings)
    except TranslatorUnavailable as exc:
        return {"provider": settings.llm_provider, "model": settings.llm_model, "configured": False,
                "fallbacks": [], "note": str(exc)}
    chain = p.providers if isinstance(p, FallbackProvider) else [p]
    first = chain[0]
    # The Anthropic SDK can also authenticate from a CLI profile, so an empty key is only a hint there.
    ok = _has_key(first) or first.name == "anthropic"
    return {"provider": first.name, "model": first.model, "configured": ok,
            "fallbacks": [q.label for q in chain[1:]], "note": None if ok else f"No API key for {first.name}."}
