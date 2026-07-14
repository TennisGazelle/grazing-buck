# grazing-buck

A small, open-source DSL for declarative, conditional trading strategies --
nested if/else trees over tickers, weighted allocations, and sort-and-select
logic, expressed as plain YAML instead of a drag-and-drop UI. Comes with a
backtester and a broker-agnostic execution script.

```
name: The Holy Grail
root:
  type: if
  condition: "price(TQQQ) > sma(TQQQ, 200)"
  then:
    type: if
    condition: "rsi(TQQQ, 10) > 79"
    then: {type: asset, ticker: UVXY}
    else: {type: asset, ticker: TQQQ}
  else:
    ...
```

`strategies/holy_grail.yaml` is a worked example: a nested conditional
allocation strategy that rotates between a few leveraged ETFs and a bond fund
based on trend and momentum.

## Why

Rules-based allocation strategies (nested weight / if-else / sort-select
trees over price, moving average, and RSI conditions) are often built inside
proprietary drag-and-drop editors. This project makes the same kind of
strategy a plain text file you can version, diff, review, generate, and run
anywhere -- including against a broker of your choice.

## How it works

- **`grazing_buck/dsl.py`** -- parses a YAML strategy file into a tree of
  nodes (`weight`, `if`, `sort_select`, `asset`). Conditions are small
  python-esque expression strings, e.g. `"price(TQQQ) > sma(TQQQ, 200)"`,
  parsed with Python's `ast` module and evaluated through a strict whitelist
  -- never `eval()` on raw text.
- **`grazing_buck/engine.py`** -- evaluates a tree into a `{ticker: weight}`
  allocation. Evaluation is *lazy*: an `if` node only evaluates the branch it
  actually takes, so a strategy can reference tickers that never end up
  needed for a given day without the run failing.
- **`grazing_buck/providers.py`** -- a `DataProvider` interface with two
  implementations: `CSVProvider` (reads OHLCV CSVs, computes SMA/RSI itself,
  used for backtesting) and `SnapshotProvider` (takes a small JSON blob of
  already-fetched indicator values, used for live/paper evaluation). The
  engine never talks to a specific data source directly.
- **`grazing_buck/backtest.py`** -- walks a date range day by day,
  re-evaluates the tree, rebalances, and compounds returns. Reports total
  return, CAGR, annualized volatility, Sharpe, and max drawdown.
- **`scripts/execute.py`** -- broker-agnostic: given a strategy, an indicator
  snapshot, and a budget, prints the target allocation and the buy/sell
  dollar amounts needed to get there. It never places an order itself --
  something else (see "Going live" below) decides whether to actually submit
  those orders to a broker.

## Quick start

```
pip install -r requirements.txt

# run the unit tests (synthetic data, exercises every branch of the tree)
python -m unittest discover -s tests -v

# backtest the Holy Grail against the included sample data
python scripts/run_backtest.py strategies/holy_grail.yaml \
    --price-dir data/prices --start 2026-04-29 --end 2026-07-13
```

Sample output from the included data (1 year of real TQQQ/UVXY daily bars,
May-July 2026 window, $10,000 start):

```
Strategy: The Holy Grail
Window:   2026-04-29 -> 2026-07-13  (51 trading days)
Equity:   10,000.00 -> 9,707.98

Metrics:
  total_return             -2.92%
  cagr                    -13.87%
  annual_volatility         73.63%
  sharpe                    0.164
  max_drawdown             -22.30%
  trading_days           50.000
```

Over that window the strategy correctly rotated between `TQQQ` (42 days) and
`UVXY` (9 days) as TQQQ's price crossed its 200-day SMA and its 10-day RSI
moved above 79 -- exercising the DSL's `weight`, `if/else`, `sma()`, and
`rsi()` logic against real market data end to end.

### Data included / how to get more

`data/prices/TQQQ.csv` and `data/prices/UVXY.csv` ship with ~1 year of real
daily bars so the backtest above runs out of the box. The Holy Grail strategy
also references `TECL`, `SOXL`, `SQQQ`, and `BSV` for its lower branches --
those aren't needed for the included window (TQQQ never closed below its
200-day SMA in that period) thanks to lazy evaluation, but you'll want full
data for a real multi-year backtest. Two ways to get it:

1. **yfinance** (works on any normal machine with internet):
   ```
   pip install yfinance pandas
   python scripts/fetch_yfinance_data.py TQQQ UVXY TECL SOXL SQQQ BSV --years 5
   ```
2. **Your broker's historicals API** -- write bars into the same
   `date,open,high,low,close,volume` CSV format in `data/prices/`.

## Writing your own strategy

Four node types, nest them however you like:

- `weight` -- split allocation across `children`. `method: equal` (default)
  or `method: specified` with a matching `weights: [...]` list.
- `if` -- a `condition` string using `price(TICKER)`, `sma(TICKER, N)`,
  `rsi(TICKER, N)`, `ema(TICKER, N)`, comparisons (`>`, `<`, `>=`, `<=`, `==`,
  `!=`), and `and`/`or`. Requires both `then` and `else`.
- `sort_select` -- rank `children` (must be `asset` nodes) by an `indicator`
  (`price`/`sma`/`rsi`/`ema`) + `window`, `order: asc|desc`, keep the top
  `select` N, equal-weighted.
- `asset` -- a leaf: `ticker: SYMBOL`.

See `strategies/holy_grail.yaml` for a full example and
`tests/test_engine.py` for more before/after examples of each branch.

## Going live (paper or real)

`scripts/execute.py` is intentionally broker-agnostic -- it takes a strategy
and a JSON "snapshot" of already-fetched indicator values and returns target
orders:

```
python scripts/execute.py strategies/holy_grail.yaml \
    --snapshot snapshot.json --budget 1000 --mode paper
```

Something else has to build `snapshot.json` (fetch current price/SMA/RSI for
every ticker the strategy might need) and, in live mode, decide whether to
actually submit the resulting orders to a broker. That's by design: this repo
doesn't hard-code a broker API, so you can wire it up to Robinhood, Alpaca,
IBKR, or a paper-trading sandbox -- whatever `DataProvider` you write.

**Important safety note on unattended live trading:** most brokerage APIs are
designed to require a human to review and confirm an order before it's
placed for real money. Running `execute.py` on an hourly schedule and wiring
its output straight into a "place order" call removes that human-in-the-loop
step. If you automate this, start in `--mode paper` and read the output for a
while before ever letting anything place a real order, and keep a hard budget
cap in whatever you build on top.

## Caveats

- No transaction costs, slippage, or spread modeled in the backtester.
- No support yet for multi-leg `weight` re-normalization mid-tree, leverage
  constraints, or intraday rebalancing -- daily close-to-close only.
- RSI uses the standard Wilder smoothing method.

## License

MIT -- see `LICENSE`.
