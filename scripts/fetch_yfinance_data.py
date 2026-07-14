#!/usr/bin/env python3
"""Populate data/prices/TICKER.csv files using yfinance.

Not runnable from inside a network-restricted sandbox, but works fine on a
normal machine with internet access:

    pip install yfinance pandas
    python scripts/fetch_yfinance_data.py TQQQ UVXY TECL SOXL SQQQ BSV \
        --years 5 --out data/prices
"""
import argparse
import os


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--out", default="data/prices")
    args = ap.parse_args()

    import yfinance as yf  # imported lazily so the rest of the package has no hard dependency on it

    os.makedirs(args.out, exist_ok=True)
    for ticker in args.tickers:
        df = yf.download(ticker, period=f"{args.years}y", interval="1d", auto_adjust=False)
        df = df.reset_index()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        out_path = os.path.join(args.out, f"{ticker}.csv")
        with open(out_path, "w") as f:
            f.write("date,open,high,low,close,volume\n")
            for _, row in df.iterrows():
                f.write(
                    f"{row['Date'].date()},{row['Open']},{row['High']},"
                    f"{row['Low']},{row['Close']},{int(row['Volume'])}\n"
                )
        print(f"wrote {out_path} ({len(df)} rows)")


if __name__ == "__main__":
    main()
