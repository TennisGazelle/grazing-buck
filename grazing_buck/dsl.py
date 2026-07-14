"""Parse a strategy YAML file into a tree of Node objects.

Grammar (informal)
-------------------
root:
  name: str
  root: <node>

<node> is one of:

  type: weight
  method: equal              # (only "equal" supported today)
  children: [<node>, ...]

  type: if
  condition: "price(TQQQ) > sma(TQQQ, 200)"   # python-esque expression
  then: <node>
  else: <node>

  type: sort_select
  indicator: rsi              # rsi | sma | ema | price
  window: 10                  # ignored for price
  order: asc | desc           # asc = lowest indicator value first
  select: 1                   # how many children to keep, equal-weighted
  children: [<node>, ...]     # must all be "asset" nodes today

  type: asset
  ticker: TQQQ

Conditions are parsed with Python's `ast` module and evaluated through a
tiny whitelist (see engine.py) -- no `eval()` of arbitrary code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import yaml


class DslError(ValueError):
    """Raised for malformed strategy files."""


@dataclass
class Node:
    type: str


@dataclass
class WeightNode(Node):
    method: str
    children: List[Node] = field(default_factory=list)
    weights: Optional[List[float]] = None  # used when method == "specified"


@dataclass
class IfNode(Node):
    condition: str
    then_branch: Node = None
    else_branch: Node = None


@dataclass
class SortSelectNode(Node):
    indicator: str
    window: Optional[int]
    order: str
    select: int
    children: List[Node] = field(default_factory=list)


@dataclass
class AssetNode(Node):
    ticker: str


@dataclass
class Strategy:
    name: str
    root: Node
    rebalance: str = "on_trigger"


def _parse_node(data: dict) -> Node:
    if not isinstance(data, dict) or "type" not in data:
        raise DslError(f"Expected a node object with a 'type' key, got: {data!r}")

    node_type = data["type"]

    if node_type == "weight":
        method = data.get("method", "equal")
        children_raw = data.get("children", [])
        if not children_raw:
            raise DslError("weight node requires at least one child")
        children = [_parse_node(c) for c in children_raw]
        weights = data.get("weights")
        if method == "specified" and (not weights or len(weights) != len(children)):
            raise DslError("method: specified requires a 'weights' list matching children length")
        return WeightNode(type="weight", method=method, children=children, weights=weights)

    if node_type == "if":
        if "condition" not in data:
            raise DslError("if node requires a 'condition' string")
        then_raw = data.get("then")
        else_raw = data.get("else")
        if then_raw is None or else_raw is None:
            raise DslError("if node requires both 'then' and 'else' branches")
        return IfNode(
            type="if",
            condition=data["condition"],
            then_branch=_parse_node(then_raw),
            else_branch=_parse_node(else_raw),
        )

    if node_type == "sort_select":
        children_raw = data.get("children", [])
        if not children_raw:
            raise DslError("sort_select node requires at least one child")
        children = [_parse_node(c) for c in children_raw]
        for c in children:
            if c.type != "asset":
                raise DslError("sort_select children must currently be 'asset' nodes")
        select = int(data.get("select", 1))
        if select < 1 or select > len(children):
            raise DslError(f"select ({select}) must be between 1 and the number of children ({len(children)})")
        return SortSelectNode(
            type="sort_select",
            indicator=data.get("indicator", "rsi"),
            window=data.get("window"),
            order=data.get("order", "asc"),
            select=select,
            children=children,
        )

    if node_type == "asset":
        if "ticker" not in data:
            raise DslError("asset node requires a 'ticker'")
        return AssetNode(type="asset", ticker=str(data["ticker"]).upper())

    raise DslError(f"Unknown node type: {node_type!r}")


def parse_strategy(yaml_text: str) -> Strategy:
    """Parse a strategy YAML document into a Strategy object."""
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict) or "root" not in data:
        raise DslError("Strategy file must be a mapping with a top-level 'root' node")
    return Strategy(
        name=data.get("name", "unnamed"),
        root=_parse_node(data["root"]),
        rebalance=data.get("rebalance", "on_trigger"),
    )


def load_strategy(path: str) -> Strategy:
    with open(path, "r") as f:
        return parse_strategy(f.read())


def collect_tickers(node: Node) -> List[str]:
    """Walk the whole tree (both branches of every if) and return the set of
    tickers that could ever be needed -- useful for pre-fetching price data.
    """
    tickers: List[str] = []

    def walk(n: Node):
        if isinstance(n, AssetNode):
            if n.ticker not in tickers:
                tickers.append(n.ticker)
        elif isinstance(n, WeightNode):
            for c in n.children:
                walk(c)
        elif isinstance(n, IfNode):
            walk(n.then_branch)
            walk(n.else_branch)
            for t in _tickers_in_condition(n.condition):
                if t not in tickers:
                    tickers.append(t)
        elif isinstance(n, SortSelectNode):
            for c in n.children:
                walk(c)

    walk(node)
    return tickers


def _tickers_in_condition(condition: str) -> List[str]:
    """Best-effort extraction of ticker symbols referenced inside a condition
    string like 'price(TQQQ) > sma(TQQQ, 200)'. Used only for pre-fetch hints.
    """
    import re

    return list(dict.fromkeys(re.findall(r"\(([A-Z]{1,6})[,)]", condition)))
