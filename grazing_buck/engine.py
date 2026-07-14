"""Evaluate a parsed Strategy tree into a ticker -> weight allocation.

Evaluation is lazy: an `if` node only evaluates (and therefore only needs
data for) the branch it actually takes, so a strategy can reference tickers
that are never actually needed for a given historical window without the
backtest failing.

Condition strings like "price(TQQQ) > sma(TQQQ, 200)" are parsed with
Python's `ast` module and evaluated through a small whitelist of node types
and functions -- never `eval()`/`exec()` on raw text.
"""
from __future__ import annotations

import ast
from datetime import date as Date
from typing import Dict, Optional

from .dsl import AssetNode, IfNode, Node, SortSelectNode, Strategy, WeightNode
from .providers import DataProvider

_ALLOWED_FUNCS = {"price", "sma", "rsi", "ema"}
_ALLOWED_COMPARE_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)
_ALLOWED_BOOL_OPS = (ast.And, ast.Or)


class ConditionError(ValueError):
    pass


def _eval_condition(condition: str, provider: DataProvider, as_of: Optional[Date]) -> bool:
    try:
        tree = ast.parse(condition, mode="eval")
    except SyntaxError as e:
        raise ConditionError(f"Could not parse condition {condition!r}: {e}") from e

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = ev(comparator)
                if not isinstance(op, _ALLOWED_COMPARE_OPS):
                    raise ConditionError(f"Operator {op} not allowed in condition")
                if isinstance(op, ast.Lt) and not (left < right):
                    return False
                if isinstance(op, ast.LtE) and not (left <= right):
                    return False
                if isinstance(op, ast.Gt) and not (left > right):
                    return False
                if isinstance(op, ast.GtE) and not (left >= right):
                    return False
                if isinstance(op, ast.Eq) and not (left == right):
                    return False
                if isinstance(op, ast.NotEq) and not (left != right):
                    return False
                left = right
            return True
        if isinstance(node, ast.BoolOp):
            if not isinstance(node.op, _ALLOWED_BOOL_OPS):
                raise ConditionError("Only 'and'/'or' boolean ops are allowed")
            values = [ev(v) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
                raise ConditionError(f"Function {ast.dump(node.func)} not allowed")
            fname = node.func.id
            args = [ev(a) for a in node.args]
            if fname == "price":
                (ticker,) = args
                return provider.price(ticker, as_of)
            if fname == "sma":
                ticker, window = args
                return provider.sma(ticker, int(window), as_of)
            if fname == "rsi":
                ticker, window = args
                return provider.rsi(ticker, int(window), as_of)
            if fname == "ema":
                ticker, window = args
                return provider.ema(ticker, int(window), as_of)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            # bare ticker symbol used as a string, e.g. inside price(TQQQ)
            return node.id
        if isinstance(node, ast.Num):  # py<3.8 compat
            return node.n
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        raise ConditionError(f"Unsupported expression: {ast.dump(node)}")

    result = ev(tree)
    return bool(result)


def _indicator_value(indicator: str, ticker: str, window, provider: DataProvider, as_of):
    if indicator == "price":
        return provider.price(ticker, as_of)
    if indicator == "sma":
        return provider.sma(ticker, int(window), as_of)
    if indicator == "rsi":
        return provider.rsi(ticker, int(window), as_of)
    if indicator == "ema":
        return provider.ema(ticker, int(window), as_of)
    raise ConditionError(f"Unknown indicator {indicator!r}")


def evaluate(
    node: Node,
    provider: DataProvider,
    as_of: Optional[Date] = None,
    weight: float = 1.0,
) -> Dict[str, float]:
    """Return {ticker: weight} for the given node, lazily evaluating only
    the branches that are actually reached.
    """
    if isinstance(node, AssetNode):
        return {node.ticker: weight}

    if isinstance(node, WeightNode):
        allocation: Dict[str, float] = {}
        n = len(node.children)
        if node.method == "specified":
            child_weights = node.weights
        else:
            child_weights = [1.0 / n] * n
        for child, w in zip(node.children, child_weights):
            sub = evaluate(child, provider, as_of, weight * w)
            for ticker, tw in sub.items():
                allocation[ticker] = allocation.get(ticker, 0.0) + tw
        return allocation

    if isinstance(node, IfNode):
        taken = node.then_branch if _eval_condition(node.condition, provider, as_of) else node.else_branch
        return evaluate(taken, provider, as_of, weight)

    if isinstance(node, SortSelectNode):
        scored = []
        for child in node.children:
            assert isinstance(child, AssetNode)
            value = _indicator_value(node.indicator, child.ticker, node.window, provider, as_of)
            scored.append((value, child.ticker))
        reverse = node.order == "desc"
        scored.sort(key=lambda t: t[0], reverse=reverse)
        chosen = scored[: node.select]
        w = weight / len(chosen)
        allocation: Dict[str, float] = {}
        for _, ticker in chosen:
            allocation[ticker] = allocation.get(ticker, 0.0) + w
        return allocation

    raise ConditionError(f"Unknown node: {node}")


def evaluate_strategy(strategy: Strategy, provider: DataProvider, as_of: Optional[Date] = None) -> Dict[str, float]:
    return evaluate(strategy.root, provider, as_of)
