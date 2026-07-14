#!/usr/bin/env python3
"""CLI: backtest a strategy against local CSV price data.

Usage:
    python scripts/run_backtest.py strategies/holy_grail.yaml \
        --price-dir data/prices --start 2025-08-15 --end 2026-07-13
"""
import argparse
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from grazing_buck.backtest import run_backtest
from grazing_buck.dsl import load_strategy


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy", help="Path to a strategy YAML file")
    ap.add_argument("--price-dir", default="data/prices", help="Directory of TICKER.csv files")
    ap.add_argument("--start", required=True, type=_parse_date)
    ap.add_argument("--end", required=True, type=_parse_date)
    ap.add_argument("--initial-equity", type=float, default=10_000.0)
    args = ap.parse_args()

    strategy = load_strategy(args.strategy)
    result = run_backtest(strategy, args.price_dir, args.start, args.end, args.initial_equity)
    metrics = result.metrics()

    print(f"Strategy: {strategy.name}")
    print(f"Window:   {result.dates[0]} -> {result.dates[-1]}  ({len(result.dates)} trading days)")
    print(f"Equity:   {args.initial_equity:,.2f} -> {result.equity_curve[-1]:,.2f}")
    print()
    print("Metrics:")
    for k, v in metrics.items():
        if k in ("total_return", "cagr", "annual_volatility", "max_drawdown"):
            print(f"  {k:20s} {v * 100:8.2f}%")
        else:
            print(f"  {k:20s} {v:8.3f}")
    print()
    print("Last 5 allocations:")
    for d, alloc in list(zip(result.dates, result.allocations))[-5:]:
        alloc_str = ", ".join(f"{t}:{w:.0%}" for t, w in alloc.items())
        print(f"  {d}  {alloc_str}")


if __name__ == "__main__":
    main()
