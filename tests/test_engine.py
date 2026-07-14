"""Unit tests for the DSL parser + engine, using a small synthetic
DataProvider (no network, no real market data needed) so every node type --
including branches the real Holy Grail backtest window never reaches -- is
exercised.

Run with:  python -m pytest tests/ -q   (or: python tests/test_engine.py)
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grazing_buck.dsl import DslError, collect_tickers, parse_strategy
from grazing_buck.engine import evaluate_strategy
from grazing_buck.providers import DataProvider


class FakeProvider(DataProvider):
    """Fixed indicator values per ticker, ignoring `as_of` -- good enough
    for testing tree logic in isolation from any real price history.
    """

    def __init__(self, prices=None, smas=None, rsis=None):
        self.prices = prices or {}
        self.smas = smas or {}
        self.rsis = rsis or {}

    def price(self, ticker, as_of=None):
        return self.prices[ticker]

    def sma(self, ticker, window, as_of=None):
        return self.smas[(ticker, window)]

    def rsi(self, ticker, window, as_of=None):
        return self.rsis[(ticker, window)]


HOLY_GRAIL_YAML = (Path(__file__).resolve().parent.parent / "strategies" / "holy_grail.yaml").read_text()


class TestParsing(unittest.TestCase):
    def test_parses_holy_grail(self):
        strategy = parse_strategy(HOLY_GRAIL_YAML)
        self.assertEqual(strategy.name, "The Holy Grail")

    def test_collect_tickers_finds_all_six(self):
        strategy = parse_strategy(HOLY_GRAIL_YAML)
        tickers = set(collect_tickers(strategy.root))
        self.assertEqual(tickers, {"TQQQ", "UVXY", "TECL", "SOXL", "SQQQ", "BSV"})

    def test_rejects_malformed_node(self):
        with self.assertRaises(DslError):
            parse_strategy("root:\n  type: bogus\n")

    def test_weight_node_requires_children(self):
        with self.assertRaises(DslError):
            parse_strategy("root:\n  type: weight\n  method: equal\n  children: []\n")


class TestEngineHolyGrail(unittest.TestCase):
    def setUp(self):
        self.strategy = parse_strategy(HOLY_GRAIL_YAML)

    def _run(self, provider):
        return evaluate_strategy(self.strategy, provider)

    def test_branch_uvxy_when_uptrend_and_overbought(self):
        # price > sma200, rsi10 > 79 -> UVXY, 100%
        provider = FakeProvider(
            prices={"TQQQ": 60.0},
            smas={("TQQQ", 200): 50.0},
            rsis={("TQQQ", 10): 85.0},
        )
        self.assertEqual(self._run(provider), {"UVXY": 1.0})

    def test_branch_tqqq_when_uptrend_and_not_overbought(self):
        provider = FakeProvider(
            prices={"TQQQ": 60.0},
            smas={("TQQQ", 200): 50.0},
            rsis={("TQQQ", 10): 40.0},
        )
        self.assertEqual(self._run(provider), {"TQQQ": 1.0})

    def test_branch_tecl_when_downtrend_and_oversold(self):
        provider = FakeProvider(
            prices={"TQQQ": 40.0},
            smas={("TQQQ", 200): 50.0},
            rsis={("TQQQ", 10): 20.0},
        )
        self.assertEqual(self._run(provider), {"TECL": 1.0})

    def test_branch_soxl_when_downtrend_and_soxl_oversold(self):
        provider = FakeProvider(
            prices={"TQQQ": 40.0},
            smas={("TQQQ", 200): 50.0, ("TQQQ", 20): 45.0},
            rsis={("TQQQ", 10): 50.0, ("SOXL", 10): 25.0},
        )
        self.assertEqual(self._run(provider), {"SOXL": 1.0})

    def test_branch_sort_select_picks_lowest_rsi(self):
        # downtrend, tqqq rsi not extreme, soxl rsi not oversold,
        # price < sma20 -> sort {SQQQ, BSV} by rsi asc, pick 1
        provider = FakeProvider(
            prices={"TQQQ": 40.0},
            smas={("TQQQ", 200): 50.0, ("TQQQ", 20): 45.0},
            rsis={("TQQQ", 10): 50.0, ("SOXL", 10): 60.0, ("SQQQ", 10): 30.0, ("BSV", 10): 55.0},
        )
        self.assertEqual(self._run(provider), {"SQQQ": 1.0})

    def test_branch_fallback_tqqq(self):
        provider = FakeProvider(
            prices={"TQQQ": 46.0},
            smas={("TQQQ", 200): 50.0, ("TQQQ", 20): 45.0},
            rsis={("TQQQ", 10): 50.0, ("SOXL", 10): 60.0},
        )
        self.assertEqual(self._run(provider), {"TQQQ": 1.0})

    def test_lazy_evaluation_does_not_require_unused_tickers(self):
        # Only TQQQ + UVXY data provided; strategy should still evaluate
        # fine as long as it never needs TECL/SOXL/SQQQ/BSV for this path.
        provider = FakeProvider(
            prices={"TQQQ": 60.0},
            smas={("TQQQ", 200): 50.0},
            rsis={("TQQQ", 10): 90.0},
        )
        self.assertEqual(self._run(provider), {"UVXY": 1.0})


class TestWeightNode(unittest.TestCase):
    def test_equal_weight_split(self):
        strategy = parse_strategy(
            "root:\n"
            "  type: weight\n"
            "  method: equal\n"
            "  children:\n"
            "    - type: asset\n"
            "      ticker: AAA\n"
            "    - type: asset\n"
            "      ticker: BBB\n"
        )
        alloc = evaluate_strategy(strategy, FakeProvider())
        self.assertAlmostEqual(alloc["AAA"], 0.5)
        self.assertAlmostEqual(alloc["BBB"], 0.5)

    def test_specified_weight_split(self):
        strategy = parse_strategy(
            "root:\n"
            "  type: weight\n"
            "  method: specified\n"
            "  weights: [0.7, 0.3]\n"
            "  children:\n"
            "    - type: asset\n"
            "      ticker: AAA\n"
            "    - type: asset\n"
            "      ticker: BBB\n"
        )
        alloc = evaluate_strategy(strategy, FakeProvider())
        self.assertAlmostEqual(alloc["AAA"], 0.7)
        self.assertAlmostEqual(alloc["BBB"], 0.3)


if __name__ == "__main__":
    unittest.main()
