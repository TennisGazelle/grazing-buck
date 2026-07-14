#!/usr/bin/env python3
"""CLI: evaluate a strategy against a live indicator snapshot and
produce target dollar orders. Broker-agnostic by design.

This script does NOT talk to any broker. It expects a JSON "snapshot" file
already populated with the price/SMA/RSI values the strategy needs, e.g.:

    {
      "TQQQ": {"price": 61.2, "sma": {"200": 55.1, "20": 60.4}, "rsi": {"10": 42.3}},
      "UVXY": {"price": 24.3, "sma": {}, "rsi": {}}
    }

Something else (a scheduled task using broker MCP tools, a robin_stocks
script, an Alpaca script, ...) is responsible for building that snapshot.
This script just does the DSL evaluation + position sizing + paper/live
order-intent split, so it's reusable no matter what broker you're on.

Usage:
    python scripts/execute.py strategies/holy_grail.yaml \
        --snapshot snapshot.json --budget 1000 --mode paper \
        [--positions positions.json]

Output: JSON array of orders to stdout, e.g.
    [{"ticker": "TQQQ", "side": "buy", "dollar_amount": 250.0}, ...]

--mode paper (default): orders are computed and printed/logged, nothing else
    happens. Safe to run unattended.
--mode live: identical computation; it is the CALLER's responsibility to
    decide whether to actually submit these orders to a broker. This script
    never places an order itself -- keeping "decide what to trade" cleanly
    separate from "am I allowed to trade for real" is the whole point.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grazing_buck.dsl import load_strategy
from grazing_buck.engine import evaluate_strategy
from grazing_buck.providers import SnapshotProvider


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy")
    ap.add_argument("--snapshot", required=True, help="Path to indicator snapshot JSON")
    ap.add_argument("--budget", type=float, required=True, help="Total dollars to allocate")
    ap.add_argument("--mode", choices=["paper", "live"], default="paper")
    ap.add_argument("--positions", help="Optional current-holdings JSON: {\"TQQQ\": 12.5, ...} (dollar value held)")
    args = ap.parse_args()

    strategy = load_strategy(args.strategy)
    with open(args.snapshot) as f:
        snapshot = json.load(f)
    provider = SnapshotProvider(snapshot)

    allocation = evaluate_strategy(strategy, provider)
    target_dollars = {t: w * args.budget for t, w in allocation.items()}

    current_dollars = {}
    if args.positions:
        with open(args.positions) as f:
            current_dollars = json.load(f)

    orders = []
    all_tickers = set(target_dollars) | set(current_dollars)
    for ticker in sorted(all_tickers):
        target = target_dollars.get(ticker, 0.0)
        current = current_dollars.get(ticker, 0.0)
        delta = target - current
        if abs(delta) < 1.0:  # ignore sub-$1 rebalances
            continue
        orders.append({
            "ticker": ticker,
            "side": "buy" if delta > 0 else "sell",
            "dollar_amount": round(abs(delta), 2),
            "target_weight": round(allocation.get(ticker, 0.0), 4),
        })

    print(json.dumps({
        "mode": args.mode,
        "strategy": strategy.name,
        "target_allocation": {t: round(w, 4) for t, w in allocation.items()},
        "orders": orders,
    }, indent=2))


if __name__ == "__main__":
    main()
