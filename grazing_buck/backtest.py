"""Walk-forward backtester for grazing_buck strategies.

Each trading day (from `start` to `end`, inclusive) the strategy tree is
re-evaluated against that day's close, producing a target allocation. The
portfolio is rebalanced to that allocation at the close and held until the
next evaluation. Returns are compounded close-to-close. No transaction costs
or slippage are modeled (see README for caveats).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date as Date
from typing import Dict, List

from .dsl import Strategy, collect_tickers
from .engine import evaluate_strategy
from .providers import CSVProvider


@dataclass
class BacktestResult:
    dates: List[Date] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)
    allocations: List[Dict[str, float]] = field(default_factory=list)
    daily_returns: List[float] = field(default_factory=list)

    def metrics(self) -> Dict[str, float]:
        if len(self.equity_curve) < 2:
            return {}
        start_eq = self.equity_curve[0]
        end_eq = self.equity_curve[-1]
        n_days = len(self.equity_curve) - 1
        total_return = end_eq / start_eq - 1.0
        years = n_days / 252.0
        cagr = (end_eq / start_eq) ** (1 / years) - 1 if years > 0 else float("nan")

        rets = self.daily_returns
        mean = sum(rets) / len(rets)
        variance = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
        daily_vol = math.sqrt(variance)
        annual_vol = daily_vol * math.sqrt(252)
        sharpe = (mean * 252) / annual_vol if annual_vol > 0 else float("nan")

        peak = self.equity_curve[0]
        max_dd = 0.0
        for eq in self.equity_curve:
            peak = max(peak, eq)
            dd = (eq - peak) / peak
            max_dd = min(max_dd, dd)

        return {
            "total_return": total_return,
            "cagr": cagr,
            "annual_volatility": annual_vol,
            "sharpe": sharpe,
            "max_drawdown": max_dd,
            "trading_days": n_days,
        }


def run_backtest(
    strategy: Strategy,
    price_dir: str,
    start: Date,
    end: Date,
    initial_equity: float = 10_000.0,
) -> BacktestResult:
    provider = CSVProvider(price_dir)

    # Use the tree's primary/first-referenced ticker to define the trading
    # calendar (assumes all referenced tickers trade on the same days, true
    # for US equities/ETFs).
    calendar_ticker = collect_tickers(strategy.root)[0]
    all_dates = [d for d in provider.trading_dates(calendar_ticker) if start <= d <= end]
    if not all_dates:
        raise ValueError(f"No trading dates for {calendar_ticker} between {start} and {end}")

    result = BacktestResult()
    equity = initial_equity
    prev_allocation: Dict[str, float] = {}
    prev_prices: Dict[str, float] = {}

    for i, d in enumerate(all_dates):
        allocation = evaluate_strategy(strategy, provider, as_of=d)

        if i > 0:
            day_return = 0.0
            for ticker, w in prev_allocation.items():
                new_price = provider.price(ticker, as_of=d)
                old_price = prev_prices[ticker]
                day_return += w * (new_price / old_price - 1.0)
            equity *= 1.0 + day_return
            result.daily_returns.append(day_return)

        result.dates.append(d)
        result.equity_curve.append(equity)
        result.allocations.append(allocation)

        prev_allocation = allocation
        prev_prices = {t: provider.price(t, as_of=d) for t in allocation}

    return result
