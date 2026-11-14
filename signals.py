"""
Sinyal taraması (LONG + SHORT): hem web sitesi (app.py) hem Telegram botu (signal_bot.py) bunu kullanır.
fetch(symbol, bars) -> OHLCV DataFrame döndüren bir fonksiyon alır; böylece veri kaynağından bağımsızdır.

held: elindeki pozisyonlar {"SOL/USDT": {"side": "LONG" | "SHORT", "entry_date": "2026-11-14 10:30"}}
"""
from __future__ import annotations

import pandas as pd

import config as C
import strategy as S

# Kırılım seviyesine bu kadar yakın olan trenddeki coinler "İZLE" listesine girer
NEAR_BREAKOUT = {"15m": 0.01, "1h": 0.015, "4h": 0.03, "1d": 0.03}


def regime_label(bull: bool, bear: bool) -> str:
    return "BOĞA" if bull else ("AYI" if bear else "NÖTR")


def scan(fetch, symbols: list[str], held: dict | None = None, p: dict | None = None) -> dict:
    p = {**S.default_params(), **(p or {})}
    held = held or {}
    near = NEAR_BREAKOUT.get(C.TIMEFRAME, 0.03)
    bars = max(p["trend_ema"] * 3, 200)
    m = p["atr_stop_mult"]

    btc = fetch(C.REGIME_SYMBOL, bars)
    bull_s, bear_s = S.regime_series(btc, p)
    bull, bear = bool(bull_s.iloc[-1]), bool(bear_s.iloc[-1])
    allow_long = bull or not C.USE_BTC_FILTER
    allow_short = bear or not C.USE_BTC_FILTER
    out = {
        "bar_time": btc.index[-1],
        "bull": bull,
        "bear": bear,
        "regime": regime_label(bull, bear),
        "regime_ok": bull or bear,
        "filter_on": C.USE_BTC_FILTER,
        "btc_close": float(btc["close"].iloc[-1]),
        "rows": [],
        "errors": [],
        "frames": {C.REGIME_SYMBOL: btc},
    }

    for sym in dict.fromkeys(list(symbols) + list(held)):
        try:
            df = S.add_indicators(fetch(sym, bars), p)
        except Exception as e:  # listeden kalkmış, ağ hatası vb.
            out["errors"].append(f"{sym}: {e}")
            continue
        if len(df) < p["trend_ema"] + 5:
            continue
        out["frames"][sym] = df
        last = df.iloc[-1]
        up = bool(last["close"] > last["ema"] and last["mom"] > 0)
        down = bool(last["close"] < last["ema"] and last["mom"] < 0)
        liquid = last["avg_qvol"] >= C.MIN_AVG_QUOTE_VOLUME / S.bars_per_day()
        row = {
            "Coin": sym, "Yön": "", "Durum": "", "Not": "",
            "Kapanış": float(last["close"]), "Stop": float("nan"),
            "Momentum": float(last["mom"]),
            "Kırılım seviyesi": float("nan"), "Kırılıma uzaklık": float("nan"),
            "Ort. hacim (USDT)": float(last["avg_qvol"]),
        }

        if sym in held:
            h = held[sym] if isinstance(held[sym], dict) else {}
            side = str(h.get("side", "LONG")).upper()
            since = h.get("entry_date")
            seg = df.loc[since:] if since else df.iloc[-1:]
            if not len(seg):
                seg = df.iloc[-1:]
            row["Yön"] = side
            if side == "SHORT":
                atr_stop = seg["close"].min() + m * last["atr"]
                row["Stop"] = float(min(atr_stop, last["hx"]))
                if last["close"] > atr_stop:
                    row["Durum"], row["Not"] = "ÇIKIŞ", "ATR stop üstünde kapandı"
                elif last["exit_short"]:
                    row["Durum"], row["Not"] = "ÇIKIŞ", f"{p['exit_lookback']} mum zirvesi kırıldı"
                else:
                    row["Durum"], row["Not"] = "TUT", "Düşüş trendi sürüyor"
            else:
                atr_stop = seg["close"].max() - m * last["atr"]
                row["Stop"] = float(max(atr_stop, last["ll"]))
                if last["close"] < atr_stop:
                    row["Durum"], row["Not"] = "ÇIKIŞ", "ATR stop altında kapandı"
                elif last["exit_long"]:
                    row["Durum"], row["Not"] = "ÇIKIŞ", f"{p['exit_lookback']} mum dibi kırıldı"
                else:
                    row["Durum"], row["Not"] = "TUT", "Yükseliş trendi sürüyor"

        elif last["entry_long"]:
            row.update({"Yön": "LONG", "Stop": float(last["close"] - m * last["atr"]), "Kırılım seviyesi": float(last["hh"])})
            row["Durum"], row["Not"] = (("GİRİŞ", "Zirve kırılımı + trend + momentum") if allow_long
                                        else ("FİLTRELENDİ", "Long kırılımı var ama BTC boğa değil"))
        elif last["entry_short"]:
            row.update({"Yön": "SHORT", "Stop": float(last["close"] + m * last["atr"]), "Kırılım seviyesi": float(last["lo"])})
            row["Durum"], row["Not"] = (("GİRİŞ", "Dip kırılımı + düşüş trendi + negatif momentum") if allow_short
                                        else ("FİLTRELENDİ", "Short kırılımı var ama BTC ayı değil"))
        elif up and C.ENABLE_LONG:
            dist = last["hh"] / last["close"] - 1
            row.update({"Yön": "LONG", "Kırılım seviyesi": float(last["hh"]), "Kırılıma uzaklık": float(dist)})
            if 0 <= dist <= near and liquid:
                row["Durum"], row["Not"] = "İZLE", f"Zirve kırılımına %{dist * 100:.2f} kaldı"
            else:
                row["Durum"], row["Not"] = "TREND", "Yükseliş trendinde, kırılım yok"
        elif down and C.ENABLE_SHORT:
            dist = 1 - last["lo"] / last["close"]
            row.update({"Yön": "SHORT", "Kırılım seviyesi": float(last["lo"]), "Kırılıma uzaklık": float(dist)})
            if 0 <= dist <= near and liquid:
                row["Durum"], row["Not"] = "İZLE", f"Dip kırılımına %{dist * 100:.2f} kaldı"
            else:
                row["Durum"], row["Not"] = "TREND", "Düşüş trendinde, kırılım yok"
        else:
            row["Durum"], row["Not"] = "—", "Net trend yok"
        out["rows"].append(row)

    order = {"ÇIKIŞ": 0, "GİRİŞ": 1, "TUT": 2, "İZLE": 3, "FİLTRELENDİ": 4, "TREND": 5, "—": 6}

    def key(r):
        return (order.get(r["Durum"], 9), -abs(r["Momentum"]))

    out["rows"].sort(key=key)
    # Aynı anda en fazla MAX_OPEN_POSITIONS pozisyon: en güçlü momentumlu girişler önce
    slots = C.MAX_OPEN_POSITIONS - sum(r["Durum"] == "TUT" for r in out["rows"])
    n = 0
    for r in out["rows"]:
        if r["Durum"] == "GİRİŞ":
            n += 1
            if n > slots:
                r["Durum"], r["Not"] = "İZLE", "Giriş sinyali var ama pozisyon limiti dolu"
    out["rows"].sort(key=key)
    return out
