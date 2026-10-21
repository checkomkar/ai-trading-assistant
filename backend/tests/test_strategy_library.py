from trader.strategy.library import LIBRARY
from trader.strategy.rule_strategy import RuleStrategy
from trader.strategy.spec import StrategySpec, render_english, semantic_errors


def test_every_library_strategy_is_valid_and_builds():
    names = [s["name"] for s in LIBRARY]
    assert len(names) == len(set(names)) >= 10
    for raw in LIBRARY:
        spec = StrategySpec.model_validate(raw)
        assert semantic_errors(spec) == []
        assert "Buy the ATM put (PE)" in render_english(spec)
        RuleStrategy(spec)
