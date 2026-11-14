"""
Sinyal mantığı. Backtest ve canlı bot AYNI fonksiyonları kullanır,
böylece test ettiğin şeyle çalıştırdığın şey birebir aynı olur.

Tüm sinyaller mumun KAPANIŞINDA hesaplanır; işlem bir sonraki mumun açılışında yapılır
(geleceği görme hatası olmasın diye).
"""
import numpy as np
import pandas as pd

import config as C


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def add_indicators(df: pd.DataFrame, p: dict | None = None) -> pd.DataFrame:
    p = {**default_params(), **(p or {})}
    df = df.copy()
    df["ema"] = df["close"].ewm(span=p["trend_ema"], adjust=False).mean()
    df["mom"] = df["close"] / df["close"].shift(p["momentum_lookback"]) - 1
    df["hh"] = df["high"].shift(1).rolling(p["breakout_lookback"]).max()
    df["ll"] = df["low"].shift(1).rolling(p["exit_lookback"]).min()
    df["atr"] = atr(df, p["atr_period"])
    df["avg_qvol"] = (df["close"] * df["volume"]).rolling(20).mean()

    df["entry_signal"] = (
        (df["close"] > df["ema"])
        & (df["mom"] > 0)
        & (df["close"] > df["hh"])
        & (df["avg_qvol"] >= C.MIN_AVG_QUOTE_VOLUME)
    )
    df["exit_signal"] = df["close"] < df["ll"]
    return df


def regime_series(btc: pd.DataFrame, p: dict | None = None) -> pd.Series:
    """BTC trend filtresi: BTC kendi EMA'sının üstünde ve momentumu pozitifse True."""
    p = {**default_params(), **(p or {})}
    ema = btc["close"].ewm(span=p["trend_ema"], adjust=False).mean()
    mom = btc["close"] / btc["close"].shift(p["momentum_lookback"]) - 1
    return (btc["close"] > ema) & (mom > 0)


def default_params() -> dict:
    return {
        "trend_ema": C.TREND_EMA,
        "momentum_lookback": C.MOMENTUM_LOOKBACK,
        "breakout_lookback": C.BREAKOUT_LOOKBACK,
        "exit_lookback": C.EXIT_LOOKBACK,
        "atr_period": C.ATR_PERIOD,
        "atr_stop_mult": C.ATR_STOP_MULT,
    }


def position_size(equity: float, price: float, atr_value: float, stop_mult: float) -> float:
    """Stop'a kadar kayıp = sermayenin RISK_PER_TRADE kadarı; tek pozisyon MAX_POSITION_PCT ile sınırlı."""
    if not np.isfinite(atr_value) or atr_value <= 0 or price <= 0:
        return 0.0
    risk_amount = equity * C.RISK_PER_TRADE
    qty = risk_amount / (stop_mult * atr_value)
    max_qty = equity * C.MAX_POSITION_PCT / price
    return max(0.0, min(qty, max_qty))
