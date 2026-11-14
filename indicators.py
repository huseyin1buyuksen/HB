"""
Göstergeler ve SMC (Smart Money Concepts) yapıları.

Her şey kodlanabilir ve geçmişte doğrulanabilir kurallarla hesaplanır; GELECEĞİ GÖRME YOKTUR:
- Swing noktaları SWING_K mum sonra (sağ taraf tamamlanınca) kullanılabilir hale gelir.
- Üst zaman dilimi (1h/4h) değerleri, o mum KAPANDIKTAN sonra 15m serisine aktarılır.
- Önceki gün/hafta seviyeleri bir önceki tamamlanmış periyottan gelir.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C

TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


# ------------------------------------------------------------------ klasik göstergeler

def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up, dn = rma(d.clip(lower=0), n), rma((-d).clip(lower=0), n)
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    return rma(true_range(df), n)


def macd(close: pd.Series, f: int, s: int, sig: int):
    line = ema(close, f) - ema(close, s)
    signal = ema(line, sig)
    return line, signal, line - signal


def adx(df: pd.DataFrame, n: int):
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = rma(true_range(df), n)
    pdi = 100 * rma(pd.Series(plus_dm, index=df.index), n) / tr
    mdi = 100 * rma(pd.Series(minus_dm, index=df.index), n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return rma(dx.fillna(0), n), pdi, mdi


def bollinger(close: pd.Series, n: int, k: float):
    mid = close.rolling(n).mean()
    sd = close.rolling(n).std()
    up, lo = mid + k * sd, mid - k * sd
    width = (up - lo) / mid
    return up, mid, lo, width


def daily_vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.floor("1D")
    pv = (tp * df["volume"]).groupby(day).cumsum()
    v = df["volume"].groupby(day).cumsum()
    return pv / v.replace(0, np.nan)


def supertrend(df: pd.DataFrame, n: int, mult: float) -> pd.Series:
    """+1 yükseliş, -1 düşüş."""
    a = atr(df, n).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    upper, lower = hl2 + mult * a, hl2 - mult * a
    fu, fl = upper.copy(), lower.copy()
    d = np.ones(len(df))
    for i in range(1, len(df)):
        fu[i] = upper[i] if (upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lower[i] if (lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
        if d[i - 1] == -1 and close[i] > fu[i - 1]:
            d[i] = 1
        elif d[i - 1] == 1 and close[i] < fl[i - 1]:
            d[i] = -1
        else:
            d[i] = d[i - 1]
    return pd.Series(d, index=df.index)


# ------------------------------------------------------------------ zaman dilimleri

def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    rule = {"1h": "1h", "4h": "4h", "1d": "1D", "1w": "W-MON"}[tf]
    kw = {"label": "left", "closed": "left"}
    out = df.resample(rule, **kw).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return out.dropna(subset=["close"])


def align_htf(htf: pd.DataFrame, base_index: pd.DatetimeIndex, htf_minutes: int, base_minutes: int) -> pd.DataFrame:
    """Üst zaman dilimi değerini, o mum KAPANDIĞI anda kapanan 15m mumuna taşır (geleceği görme yok)."""
    shifted = htf.copy()
    shifted.index = shifted.index + pd.Timedelta(minutes=htf_minutes - base_minutes)
    return shifted.reindex(base_index, method="ffill")


def htf_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Üst zaman dilimi trend özeti: +1 yukarı, -1 aşağı, 0 kararsız; tam (2) / kısmi (1) güç."""
    c = df["close"]
    e21, e50, e200 = ema(c, 21), ema(c, 50), ema(c, 200)
    a, pdi, mdi = adx(df, C.ADX_LEN)
    full_up = (c > e50) & (e21 > e50)
    full_dn = (c < e50) & (e21 < e50)
    out = pd.DataFrame(index=df.index)
    out["dir"] = np.where(full_up, 1, np.where(full_dn, -1, 0))
    out["partial"] = np.where(c > e50, 1, np.where(c < e50, -1, 0))
    out["above200"] = (c > e200).astype(int) - (c < e200).astype(int)
    out["adx"], out["pdi"], out["mdi"] = a, pdi, mdi
    out["ret4"] = c.pct_change(4)
    return out


# ------------------------------------------------------------------ SMC / piyasa yapısı

def swings(df: pd.DataFrame, k: int) -> pd.DataFrame:
    """Doğrulanmış swing tepe/dipler. Değerler k mum gecikmeyle (doğrulandıkları anda) görünür."""
    h, l = df["high"], df["low"]
    win = 2 * k + 1
    ph = h.where(h == h.rolling(win, center=True).max())
    pl = l.where(l == l.rolling(win, center=True).min())
    ph_conf, pl_conf = ph.shift(k), pl.shift(k)          # k mum sonra bilinir

    def last_two(conf: pd.Series):
        pts = conf.dropna()
        last = pts.reindex(df.index).ffill()
        prev = pts.shift(1).reindex(df.index).ffill()
        return last, prev

    out = pd.DataFrame(index=df.index)
    out["sh"], out["sh_prev"] = last_two(ph_conf)
    out["sl"], out["sl_prev"] = last_two(pl_conf)
    out["new_sh"] = ph_conf.notna()
    out["new_sl"] = pl_conf.notna()
    return out


def smc(df: pd.DataFrame, k: int, recent: int, atr_s: pd.Series) -> pd.DataFrame:
    sw = swings(df, k)
    c, h, l = df["close"], df["high"], df["low"]
    out = sw.copy()
    out["struct_bull"] = (sw["sh"] > sw["sh_prev"]) & (sw["sl"] > sw["sl_prev"])   # HH + HL
    out["struct_bear"] = (sw["sh"] < sw["sh_prev"]) & (sw["sl"] < sw["sl_prev"])   # LH + LL
    sh_before, sl_before = sw["sh"].shift(1), sw["sl"].shift(1)
    # BOS: kapanış son doğrulanmış swing seviyesini bu mumda kırar (tetikleyici mum kapanışı)
    out["bos_up"] = (c > sh_before) & (c.shift(1) <= sh_before)
    out["bos_dn"] = (c < sl_before) & (c.shift(1) >= sl_before)
    # CHoCH: yapı tersken gelen ilk karşı yön kırılımı
    out["choch_up"] = out["bos_up"] & out["struct_bear"].shift(1, fill_value=False)
    out["choch_dn"] = out["bos_dn"] & out["struct_bull"].shift(1, fill_value=False)
    out["break_level_up"] = sh_before
    out["break_level_dn"] = sl_before
    # Likidite süpürmesi: iğne swing seviyesini geçip kapanış geri içeride
    sweep_up = (l < sl_before) & (c > sl_before)
    sweep_dn = (h > sh_before) & (c < sh_before)
    out["sweep_bull"] = sweep_up.rolling(recent, min_periods=1).max().astype(bool)
    out["sweep_bear"] = sweep_dn.rolling(recent, min_periods=1).max().astype(bool)
    # FVG: 3 mumluk boşluk; yakın zamanda oluşmuş ve fiyat boşluğun doğru tarafında
    fvg_up = l > h.shift(2)
    fvg_dn = h < l.shift(2)
    fvg_up_bottom = h.shift(2).where(fvg_up).ffill(limit=recent)
    fvg_dn_top = l.shift(2).where(fvg_dn).ffill(limit=recent)
    out["fvg_bull"] = fvg_up_bottom.notna() & (c > fvg_up_bottom)
    out["fvg_bear"] = fvg_dn_top.notna() & (c < fvg_dn_top)
    # Eşit tepe / dip (likidite havuzu): son iki swing birbirine 0.1 ATR'den yakın
    out["eq_high"] = (sw["sh"] - sw["sh_prev"]).abs() < 0.1 * atr_s
    out["eq_low"] = (sw["sl"] - sw["sl_prev"]).abs() < 0.1 * atr_s
    return out


def period_levels(df: pd.DataFrame) -> pd.DataFrame:
    """Önceki günün ve haftanın yüksek/düşük seviyeleri (tamamlanmış periyotlardan)."""
    out = pd.DataFrame(index=df.index)
    day_key = df.index.floor("1D")
    week_key = (df.index - pd.to_timedelta(df.index.dayofweek, unit="D")).floor("1D")
    for key, hi, lo in ((day_key, "pdh", "pdl"), (week_key, "pwh", "pwl")):
        g = df.groupby(key).agg(high=("high", "max"), low=("low", "min")).shift(1)
        out[hi] = g["high"].reindex(key).to_numpy()
        out[lo] = g["low"].reindex(key).to_numpy()
    return out
