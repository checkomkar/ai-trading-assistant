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


def test_catalogue_groups_every_style_and_is_valid():
    from trader.strategy.library import CATEGORIES, catalogue

    cat = catalogue()
    assert {e["category"] for e in cat} == set(CATEGORIES)  # Momentum, Trend, Scalp and Sideways all have entries
    assert [e["category"] for e in cat] == sorted((e["category"] for e in cat), key=CATEGORIES.index)
    assert sum(e["category"] == "Scalp" for e in cat) >= 3 and sum(e["category"] == "Sideways" for e in cat) >= 3
    for e in cat:
        assert semantic_errors(StrategySpec.model_validate(e["spec"])) == [] and e["english"]
