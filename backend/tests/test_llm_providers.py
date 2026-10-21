import json

import httpx
import pytest

from trader.ai import providers, speech
from trader.ai.providers import OpenAICompatibleProvider, TranslatorUnavailable, build_provider, describe, extract_json
from trader.ai.strategy_translator import StrategyTranslator
from trader.config import Settings
from trader.strategy.spec import MOMENTUM_V1

ENV = {"spec_json": json.dumps(MOMENTUM_V1), "assumptions": ["RSI period 14"], "questions": [], "unsupported": []}


@pytest.fixture(autouse=True)
def _no_network_and_fresh_health(monkeypatch):
    monkeypatch.setattr(providers, "free_models", lambda *a, **k: [("google/gemma-4-31b-it:free", True),
                                                                   ("nvidia/n:free", False), ("x/a:free", None), ("x/b:free", None)])
    providers._COOLDOWN.clear()


def settings(**kw):
    return Settings(_env_file=None, **kw)


def provider_with(handler, **kw):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(kw.pop("name", "groq"), "m", kw.pop("key", "k"), "https://x/v1", client=client)


def ok(content, finish="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}]})


def test_openai_compatible_translates_and_sends_schema_and_key():
    seen = {}

    def handler(req):
        seen["auth"], seen["body"] = req.headers["authorization"], json.loads(req.content)
        return ok("```json\n" + json.dumps(ENV) + "\n```")  # fenced output is tolerated

    out = StrategyTranslator(provider_with(handler)).translate("momentum on nifty ...")
    assert out.spec["name"] == MOMENTUM_V1["name"] and out.model == "groq/m" and out.errors == []
    assert seen["auth"] == "Bearer k" and seen["body"]["messages"][0]["role"] == "system"
    assert "JSON Schema" in seen["body"]["messages"][0]["content"]
    # the preferred channel: one forced function whose arguments are the schema
    assert seen["body"]["tool_choice"]["function"]["name"] == "respond" and "response_format" not in seen["body"]


def test_repair_round_appends_plain_assistant_text():
    bad = dict(MOMENTUM_V1, exits={"stop_loss_pct": 150, "target_pct": 50})
    replies = [ok(json.dumps({**ENV, "spec_json": json.dumps(bad)})), ok(json.dumps(ENV))]
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return replies.pop(0)

    out = StrategyTranslator(provider_with(handler)).translate("x" * 10)
    assert out.repaired and out.spec is not None
    assert [m["role"] for m in bodies[1]["messages"]] == ["system", "user", "assistant", "user"]


def test_function_call_arguments_are_the_answer():
    resp = httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
        {"function": {"name": "respond", "arguments": json.dumps(ENV)}}]}, "finish_reason": "tool_calls"}]})
    out = StrategyTranslator(provider_with(lambda r: resp)).translate("momentum on nifty ...")
    assert out.spec is not None


def test_falls_back_function_call_then_plain_then_json_mode():
    calls = []

    def handler(req):
        body = json.loads(req.content)
        mode = "tools" if "tools" in body else "json" if "response_format" in body else "plain"
        calls.append(mode)
        if mode == "tools":
            return httpx.Response(400, text="tool_use_failed")  # e.g. a model without function calling
        return ok(json.dumps(ENV)) if mode == "json" else ok("Sure, happy to help with that!")

    StrategyTranslator(provider_with(handler)).translate("momentum on nifty ...")
    assert calls == ["tools", "plain", "json"]


def test_json_in_the_reasoning_field_is_used_when_content_is_empty():
    resp = httpx.Response(200, json={"choices": [{"message": {"content": "", "reasoning": "thinking... " + json.dumps(ENV)},
                                                  "finish_reason": "stop"}]})
    assert json.loads(provider_with(lambda r: resp).complete("s", [], {}).text)["assumptions"] == ["RSI period 14"]


def test_short_rate_limits_are_waited_out_once(monkeypatch):
    slept, replies = [], [httpx.Response(429, headers={"retry-after": "2"}), ok(json.dumps(ENV))]
    monkeypatch.setattr(providers.time, "sleep", slept.append)
    assert provider_with(lambda r: replies.pop(0)).complete("s", [], {}).text
    assert slept == [2.0]
    # a long wait is not worth it: surface the limit so a fallback provider can take over
    monkeypatch.setattr(providers.time, "sleep", lambda s: pytest.fail("must not sleep"))
    with pytest.raises(TranslatorUnavailable, match="rate limiting"):
        provider_with(lambda r: httpx.Response(429, headers={"retry-after": "300"})).complete("s", [], {})


@pytest.mark.parametrize("resp,match", [
    (httpx.Response(401, text="no"), "rejected the API key"),
    (httpx.Response(429, text="slow"), "rate limiting"),
    (httpx.Response(500, text="boom"), "error 500"),
    (ok("{}", finish="length"), "cut off"),
    (ok("sorry, no JSON here"), "JSON object"),
])
def test_provider_errors_become_translator_unavailable(resp, match):
    with pytest.raises(TranslatorUnavailable, match=match):
        provider_with(lambda req: resp).complete("s", [{"role": "user", "content": "x"}], {})


def test_missing_key_is_reported_before_any_request():
    p = provider_with(lambda req: pytest.fail("must not call out"), key="")
    with pytest.raises(TranslatorUnavailable, match="No API key"):
        p.complete("s", [], {})


def test_extract_json_takes_the_outer_object():
    assert json.loads(extract_json('Sure! {"a": {"b": 1}} hope that helps')) == {"a": {"b": 1}}


def test_extract_json_takes_the_first_object_when_the_model_keeps_talking():
    two = '{"action":"tool","tool":"x"}Now we wait for the result.{"action":"answer","text":"invented"}'
    assert json.loads(extract_json(two))["action"] == "tool"
    assert json.loads(extract_json('{oops} then {"ok": true}')) == {"ok": True}


def test_build_provider_selection():
    assert build_provider(settings()).name == "anthropic"
    g = build_provider(settings(llm_provider="groq", groq_api_key="gk"))
    assert (g.name, g.model, g._api_key) == ("groq", "openai/gpt-oss-120b", "gk") and "groq.com" in g.base_url
    o = build_provider(settings(llm_provider="openrouter", openrouter_api_key="ok", llm_model="meta/x:free", llm_api_key="override", openrouter_free_pool=0))
    assert (o.model, o._api_key) == ("meta/x:free", "override")
    with pytest.raises(TranslatorUnavailable, match="LLM_BASE_URL"):
        build_provider(settings(llm_provider="custom"))
    assert build_provider(settings(llm_provider="custom", llm_base_url="http://localhost:11434/v1", llm_model="llama3")).name == "custom"
    with pytest.raises(TranslatorUnavailable, match="Unknown"):
        build_provider(settings(llm_provider="nope"))


def test_describe_never_leaks_the_key():
    d = describe(settings(llm_provider="groq", groq_api_key="secret-key"))
    assert d["configured"] and "secret-key" not in json.dumps(d)
    assert describe(settings(llm_provider="groq"))["configured"] is False


# ---- Deepgram -----------------------------------------------------------------------------------
def dg(text):
    return httpx.Response(200, json={"results": {"channels": [{"alternatives": [{"transcript": text}]}]}})


def test_transcribe_sends_audio_and_returns_text():
    seen = {}

    def handler(req):
        seen.update(auth=req.headers["authorization"], ctype=req.headers["content-type"], body=req.content, url=str(req.url))
        return dg("  buy nifty calls when RSI is above sixty ")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    text = speech.transcribe(b"\x1aE\xdf\xa3", "audio/webm", settings(deepgram_api_key="dk"), client=client)
    assert text == "buy nifty calls when RSI is above sixty"
    assert seen["auth"] == "Token dk" and seen["ctype"] == "audio/webm" and seen["body"] == b"\x1aE\xdf\xa3"
    assert "model=nova-3" in seen["url"] and "keyterm=VWAP" in seen["url"] and "language=en-IN" in seen["url"]


def test_transcribe_errors():
    with pytest.raises(speech.SpeechUnavailable, match="DEEPGRAM_API_KEY"):
        speech.transcribe(b"x", "audio/webm", settings())
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(speech.SpeechUnavailable, match="rejected"):
        speech.transcribe(b"x", "audio/webm", settings(deepgram_api_key="k"), client=bad)


def test_speech_and_translator_endpoints(tmp_path):
    from fastapi.testclient import TestClient

    from trader.api.main import create_app

    s = settings(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'x.db').as_posix()}", sim_seed=5,
                 sim_backfill_minutes=0, llm_provider="groq", groq_api_key="gk")
    with TestClient(create_app(s)) as c:
        assert c.get("/strategies/translator").json()["provider"] == "groq"
        assert c.get("/speech/status").json()["configured"] is False
        assert c.post("/speech/transcribe", content=b"abc", headers={"content-type": "audio/webm"}).status_code == 503
        assert c.post("/speech/transcribe", content=b"").status_code == 400


# ---- fallbacks ----------------------------------------------------------------------------------
def test_build_provider_chain_skips_unkeyed_and_duplicate_fallbacks():
    s = settings(llm_provider="groq", groq_api_key="g", openrouter_api_key="o", llm_model="big",
                 llm_fallbacks="groq:big, openrouter, openai, anthropic:claude-x", openrouter_free_pool=0)
    p = build_provider(s)
    # primary groq/big; "groq:big" is a duplicate; openai has no key; anthropic has no key
    assert [q.label for q in p.providers] == ["groq/big", "openrouter/google/gemma-4-31b-it:free"]
    # LLM_MODEL belongs to the primary: the fallback keeps its own default
    assert describe(s)["fallbacks"] == ["openrouter/google/gemma-4-31b-it:free"]
    assert build_provider(settings(llm_provider="groq", groq_api_key="g")).name == "groq"  # no chain, no wrapper


def two(first_resp, second_resp):
    a = provider_with(lambda req: first_resp, name="groq")
    b = provider_with(lambda req: second_resp, name="openrouter")
    return providers.FallbackProvider([a, b])


def test_falls_back_when_the_first_provider_fails():
    fb = two(httpx.Response(429, text="slow"), ok(json.dumps(ENV)))
    out = StrategyTranslator(fb).translate("momentum on nifty ...")
    assert out.spec is not None and out.model == "openrouter/m"  # reports who really answered


def test_all_providers_failing_lists_every_reason():
    with pytest.raises(TranslatorUnavailable) as e:
        two(httpx.Response(401), httpx.Response(500, text="down")).complete("s", [], {})
    assert "groq/m" in str(e.value) and "rejected" in str(e.value) and "openrouter/m" in str(e.value)


def test_first_provider_is_used_when_it_works():
    fb = two(ok(json.dumps(ENV)), httpx.Response(500))
    assert StrategyTranslator(fb).translate("momentum on nifty ...").model == "groq/m"


# ---- native tool calling (the assistant) ----------------------------------------------------------
SPECS = [{"name": "search_knowledge", "description": "search files",
          "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}]


def call_resp(name, args):
    return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
        {"function": {"name": name, "arguments": json.dumps(args)}}]}, "finish_reason": "tool_calls"}]})


def test_native_tool_call_becomes_a_tool_action_and_respond_becomes_an_answer():
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        return call_resp("search_knowledge", {"query": "risk"})

    a = json.loads(provider_with(handler).complete("s", [], {}, SPECS).text)
    assert a["action"] == "tool" and a["tool"] == "search_knowledge" and json.loads(a["args_json"]) == {"query": "risk"}
    names = [t["function"]["name"] for t in seen[0]["tools"]]
    assert names == ["search_knowledge", "respond"] and seen[0]["tool_choice"] == "required"
    r = json.loads(provider_with(lambda q: call_resp("respond", {"text": "2% per trade", "remember": ["x"]})).complete("s", [], {}, SPECS).text)
    assert r["action"] == "answer" and r["text"] == "2% per trade" and r["remember"] == ["x"]


# ---- OpenRouter: free models only ------------------------------------------------------------------
def test_openrouter_refuses_paid_models_by_default():
    with pytest.raises(TranslatorUnavailable, match="not a free model"):
        build_provider(settings(llm_provider="openrouter", openrouter_api_key="k", llm_model="anthropic/claude-sonnet-4.5"))
    assert build_provider(settings(llm_provider="openrouter", openrouter_api_key="k", llm_model="openrouter/free")).model == "openrouter/free"
    assert build_provider(settings(llm_provider="openrouter", openrouter_api_key="k")).model.endswith(":free")  # the default
    paid = build_provider(settings(llm_provider="openrouter", openrouter_api_key="k", llm_model="x/paid", openrouter_free_only=False))
    assert paid.model == "x/paid"  # only when the guard is explicitly switched off


def test_a_paid_openrouter_fallback_is_skipped_not_used():
    s = settings(llm_provider="groq", groq_api_key="g", openrouter_api_key="o",
                 llm_fallbacks="openrouter:meta-llama/llama-3.3-70b-instruct,openrouter:google/gemma-4-31b-it:free", openrouter_free_pool=0)
    labels = [q.label for q in build_provider(s).providers]
    assert labels == ["groq/openai/gpt-oss-120b", "openrouter/google/gemma-4-31b-it:free"]


# ---- OpenRouter free-model pool ------------------------------------------------------------------
def test_openrouter_pool_adds_other_free_models_after_the_pinned_one():
    s = settings(llm_provider="openrouter", openrouter_api_key="k", llm_model="x/a:free", openrouter_free_pool=2)
    assert [q.label for q in build_provider(s).providers] == [
        "openrouter/x/a:free", "openrouter/google/gemma-4-31b-it:free", "openrouter/nvidia/n:free"]
    # pool off, or free-only off: only what was asked for
    assert build_provider(settings(llm_provider="openrouter", openrouter_api_key="k", openrouter_free_pool=0)).name == "openrouter"
    off = build_provider(settings(llm_provider="openrouter", openrouter_api_key="k", llm_model="paid/m", openrouter_free_only=False))
    assert off.model == "paid/m"  # no pool when the free-only guard is off: pooling never mixes in other billing
    # no key: no pool at all
    assert build_provider(settings(llm_provider="groq", groq_api_key="g")).name == "groq"


def test_pool_sits_after_groq_in_a_mixed_chain():
    s = settings(llm_provider="groq", groq_api_key="g", openrouter_api_key="k", llm_fallbacks="openrouter", openrouter_free_pool=1)
    assert [q.label for q in build_provider(s).providers] == [
        "groq/openai/gpt-oss-120b", "openrouter/google/gemma-4-31b-it:free", "openrouter/nvidia/n:free"]


def test_every_pool_model_is_free():
    s = settings(llm_provider="openrouter", openrouter_api_key="k", openrouter_free_pool=4)
    assert all(providers.is_free_openrouter_model(q.model) for q in build_provider(s).providers)


def test_a_failing_model_is_skipped_for_a_minute_and_the_next_one_answers():
    calls = []

    def handler_for(tag, resp):
        def h(req):
            calls.append(tag)
            return resp
        return h

    def chain():
        return providers.FallbackProvider([
            provider_with(handler_for("a", httpx.Response(429)), name="openrouter"),
            provider_with(handler_for("b", ok(json.dumps(ENV))), name="openrouter")])

    # same label for a and b above would share cooldown: give them distinct models
    fb = chain()
    fb.providers[0].model, fb.providers[1].model = "x/a:free", "x/b:free"
    assert json.loads(fb.complete("s", [], {}).text)["assumptions"] and calls == ["a", "b"]
    calls.clear()
    fb.complete("s", [], {})  # a is cooling down: b goes first and a is not tried at all
    assert calls == ["b"]


def test_free_models_discovery_filters_and_ranks(monkeypatch):
    providers._FREE_CACHE.update(at=0.0, models=providers.STATIC_FREE)
    monkeypatch.undo()  # use the real function: this test mocks the HTTP layer instead
    catalogue = {"data": [
        {"id": "a/small:free", "context_length": 1000, "pricing": {"prompt": "0", "completion": "0"},
         "supported_parameters": ["tools"], "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
        {"id": "b/big:free", "context_length": 9000, "pricing": {"prompt": "0", "completion": "0"},
         "supported_parameters": ["tools"], "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]}},
        {"id": "c/paid", "context_length": 99999, "pricing": {"prompt": "0.001", "completion": "0.002"},
         "supported_parameters": ["tools"], "architecture": {"output_modalities": ["text"]}},
        {"id": "d/zero-but-not-free-tier", "context_length": 5, "pricing": {"prompt": "0", "completion": "0"},
         "supported_parameters": ["tools"], "architecture": {"output_modalities": ["text"]}},
        {"id": "e/notools:free", "context_length": 5, "pricing": {"prompt": "0", "completion": "0"},
         "supported_parameters": [], "architecture": {"output_modalities": ["text"]}},
        {"id": "f/guard-safety:free", "context_length": 5, "pricing": {"prompt": "0", "completion": "0"},
         "supported_parameters": ["tools"], "architecture": {"output_modalities": ["text"]}},
    ]}
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: httpx.Response(200, json=catalogue))
    got = providers.free_models(ttl=0.0001)
    assert got == [("b/big:free", True), ("a/small:free", False)]  # paid, non-:free, no-tools, safety models excluded
    providers._FREE_CACHE.update(at=0.0, models=providers.STATIC_FREE)
