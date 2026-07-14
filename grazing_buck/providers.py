"""Data providers feed the engine indicator values (price / SMA / RSI / EMA).

Any provider just needs to answer: "as of this date, for this ticker, what
is this indicator's value?" That keeps the engine and backtester completely
decoupled from *where* the numbers come from -- a CSV of historical bars,
yfinance, Robinhood, or a hand-built snapshot for live/paper trading.
"""
from __future__ import annotations

import csv
import os
from abc import ABC, abstractmethod
from datetime import date as Date
from typing import Dict, List, Optional


class DataProvider(ABC):
    """Abstract base: implement these three and the whole engine works."""

    @abstractmethod
    def price(self, ticker: str, as_of: Optional[Date] = None) -> float:
        ...

    @abstractmethod
    def sma(self, ticker: str, window: int, as_of: Optional[Date] = None) -> float:
        ...

    @abstractmethod
    def rsi(self, ticker: str, window: int, as_of: Optional[Date] = None) -> float:
        ...

    def ema(self, ticker: str, window: int, as_of: Optional[Date] = None) -> float:
        raise NotImplementedError("ema() not implemented by this provider")


def _wilder_rsi(closes: List[float], window: int) -> List[Optional[float]]:
    """Classic Wilder RSI, the same convention most trading platforms use.
    Returns a list aligned with `closes`; the first `window` entries are None
    (not enough history yet).
    """
    n = len(closes)
    rsi: List[Optional[float]] = [None] * n
    if n <= window:
        return rsi

    gains = [0.0] * n
    losses = [0.0] * n
    for i in range(1, n):
        change = closes[i] - closes[i - 1]
        gains[i] = max(change, 0.0)
        losses[i] = max(-change, 0.0)

    avg_gain = sum(gains[1:window + 1]) / window
    avg_loss = sum(losses[1:window + 1]) / window

    def _rsi_from_avgs(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1.0 + rs))

    rsi[window] = _rsi_from_avgs(avg_gain, avg_loss)
    for i in range(window + 1, n):
        avg_gain = (avg_gain * (window - 1) + gains[i]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i]) / window
        rsi[i] = _rsi_from_avgs(avg_gain, avg_loss)

    return rsi


class CSVProvider(DataProvider):
    """Reads OHLCV CSVs from a directory (one file per ticker: TICKER.csv
    with columns date,open,high,low,close,volume) and serves price/SMA/RSI
    as of any date in that history. This is what the backtester uses.

    Also usable as a general historical lookup outside of backtesting.
    """

    def __init__(self, price_dir: str):
        self.price_dir = price_dir
        self._dates: Dict[str, List[Date]] = {}
        self._closes: Dict[str, List[float]] = {}
        self._rsi_cache: Dict[tuple, List[Optional[float]]] = {}

    def _load(self, ticker: str) -> None:
        if ticker in self._closes:
            return
        path = os.path.join(self.price_dir, f"{ticker}.csv")
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"No price data for {ticker} at {path}. "
                f"Fetch it first (see scripts/fetch_yfinance_data.py or the README)."
            )
        dates, closes = [], []
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                y, m, d = row["date"].split("-")
                dates.append(Date(int(y), int(m), int(d)))
                closes.append(float(row["close"]))
        self._dates[ticker] = dates
        self._closes[ticker] = closes

    def _index_as_of(self, ticker: str, as_of: Optional[Date]) -> int:
        self._load(ticker)
        dates = self._dates[ticker]
        if as_of is None:
            return len(dates) - 1
        # last index with date <= as_of
        idx = None
        for i, d in enumerate(dates):
            if d <= as_of:
                idx = i
            else:
                break
        if idx is None:
            raise ValueError(f"No price data for {ticker} on or before {as_of}")
        return idx

    def trading_dates(self, ticker: str) -> List[Date]:
        self._load(ticker)
        return self._dates[ticker]

    def price(self, ticker: str, as_of: Optional[Date] = None) -> float:
        idx = self._index_as_of(ticker, as_of)
        return self._closes[ticker][idx]

    def sma(self, ticker: str, window: int, as_of: Optional[Date] = None) -> float:
        idx = self._index_as_of(ticker, as_of)
        closes = self._closes[ticker]
        if idx + 1 < window:
            raise ValueError(
                f"Not enough history for {ticker} SMA({window}) as of index {idx} "
                f"(have {idx + 1} bars)"
            )
        window_slice = closes[idx + 1 - window: idx + 1]
        return sum(window_slice) / window

    def rsi(self, ticker: str, window: int, as_of: Optional[Date] = None) -> float:
        self._load(ticker)
        cache_key = (ticker, window)
        if cache_key not in self._rsi_cache:
            self._rsi_cache[cache_key] = _wilder_rsi(self._closes[ticker], window)
        idx = self._index_as_of(ticker, as_of)
        value = self._rsi_cache[cache_key][idx]
        if value is None:
            raise ValueError(
                f"Not enough history for {ticker} RSI({window}) as of index {idx}"
            )
        return value


class SnapshotProvider(DataProvider):
    """A provider backed by a small hand-built (or externally fetched) JSON
    snapshot: {"TQQQ": {"price": 61.2, "sma": {"200": 55.1}, "rsi": {"10": 42.3}}, ...}

    This is what live/paper execution uses -- the caller (e.g. a scheduled
    task pulling quotes from a broker) fetches whatever indicator values the
    strategy needs and hands them to the engine through this provider. It has
    zero knowledge of any specific broker API.
    """

    def __init__(self, snapshot: dict):
        self.snapshot = snapshot

    def price(self, ticker: str, as_of=None) -> float:
        return float(self.snapshot[ticker]["price"])

    def sma(self, ticker: str, window: int, as_of=None) -> float:
        return float(self.snapshot[ticker]["sma"][str(window)])

    def rsi(self, ticker: str, window: int, as_of=None) -> float:
        return float(self.snapshot[ticker]["rsi"][str(window)])

    def ema(self, ticker: str, window: int, as_of=None) -> float:
        return float(self.snapshot[ticker]["ema"][str(window)])
