import yfinance as yf

import matplotlib.pyplot as plt

import io



def get_price_now(t):

    try:

        df = yf.Ticker(t).history(period="1d")

        return float(df["Close"].iloc[-1]) if not df.empty else None

    except:

        return None



def get_ath_52w(t):

    try:

        df = yf.Ticker(t).history(period="1y")

        if df.empty:

            return None, None

        return float(df["Close"].max()), df.index[df["Close"].idxmax()].strftime("%Y-%m-%d")

    except:

        return None, None



def build_chart_bytes(ticker, ath):

    try:

        df = yf.Ticker(ticker).history(period="1y")

        if df.empty or ath is None:

            return None

        buf = io.BytesIO()

        plt.figure(figsize=(6, 3))

        plt.plot(df.index, df["Close"])

        plt.axhline(ath, linestyle="--")

        plt.title(f"{ticker} ATH {ath:.2f} USD")

        plt.tight_layout()

        plt.savefig(buf, format="png")

        plt.close()

        buf.seek(0)

        return buf

    except:

        return None



def calc_change(now, ago):

    if now and ago and ago != 0:

        return (now - ago) / ago * 100


    return None
