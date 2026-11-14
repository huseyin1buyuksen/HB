"""
Sinyal mantığı (LONG + SHORT). Backtest, web sitesi ve Telegram botu AYNI fonksiyonları kullanır.

Tüm sinyaller mumun KAPANIŞINDA hesaplanır; işlem bir sonraki mumun açılışında yapılır
(geleceği görme hatası olmasın diye).

LONG : fiyat EMA üstünde + momentum pozitif + önceki N mumun ZİRVESİNİ yukarı kırar
SHORT: fiyat EMA altında + momentum negatif + önceki N mumun DİBİNİ aşağı kırar
"""
import numpy as np
import pandas as pd

import config as C

TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def bars_per_day(tf: str | None = None) -> float:
    return 1440 / TF_MINUTES.get(tf or C.TIMEFRAME, 1440)


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
    df["hh"] = df["high"].shift(1).rolling(p["breakout_lookback"]).max()   # long giriş seviyesi
    df["lo"] = df["low"].shift(1).rolling(p["breakout_lookback"]).min()    # short giriş seviyesi
    df["ll"] = df["low"].shift(1).rolling(p["exit_lookback"]).min()        # long çıkış seviyesi
    df["hx"] = df["high"].shift(1).rolling(p["exit_lookback"]).max()       # short çıkış seviyesi
    df["atr"] = atr(df, p["atr_period"])
    df["avg_qvol"] = (df["close"] * df["volume"]).rolling(20).mean()
    liquid = df["avg_qvol"] >= C.MIN_AVG_QUOTE_VOLUME / bars_per_day()

    df["entry_long"] = C.ENABLE_LONG & (df["close"] > df["ema"]) & (df["mom"] > 0) & (df["close"] > df["hh"]) & liquid
    df["entry_short"] = C.ENABLE_SHORT & (df["close"] < df["ema"]) & (df["mom"] < 0) & (df["close"] < df["lo"]) & liquid
    df["exit_long"] = df["close"] < df["ll"]
    df["exit_short"] = df["close"] > df["hx"]
    # eski adlar (geriye uyumluluk)
    df["entry_signal"], df["exit_signal"] = df["entry_long"], df["exit_long"]
    return df


def regime_series(btc: pd.DataFrame, p: dict | None = None) -> tuple[pd.Series, pd.Series]:
    """BTC rejimi: (boğa, ayı). Boğada sadece LONG, ayıda sadece SHORT açılır; ikisi de değilse giriş yok."""
    p = {**default_params(), **(p or {})}
    ema = btc["close"].ewm(span=p["trend_ema"], adjust=False).mean()
    mom = btc["close"] / btc["close"].shift(p["momentum_lookback"]) - 1
    return (btc["close"] > ema) & (mom > 0), (btc["close"] < ema) & (mom < 0)


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
