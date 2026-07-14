"""grazing_buck: a small DSL for declarative, conditional trading strategies.

Expresses nested weight / if-else / sort-select trees over tickers as a
plain YAML file that can be parsed, evaluated against any price data
source, backtested, and (optionally) executed against a broker.
"""

__version__ = "0.1.0"
