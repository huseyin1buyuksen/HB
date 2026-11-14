"""
Sinyal taraması: hem web sitesi (app.py) hem Telegram botu (signal_bot.py) bunu kullanır.
fetch(symbol, bars) -> OHLCV DataFrame döndüren bir fonksiyon alır; böylece veri kaynağından bağımsızdır.
"""
from __future__ import annotations

import pandas as pd

import config as C
import strategy as S

NEAR_BREAKOUT = 0.03  # kırılım seviyesine %3'ten yakın trenddeki coinler "izle" listesine girer


def scan(fetch, symbols: list[str], held: dict | None = None, p: dict | None = None) -> dict:
    p = {**S.default_params(), **(p or {})}
    held = held or {}
    bars = max(p["trend_ema"] * 3, 200)

    btc = fetch(C.REGIME_SYMBOL, bars)
    regime = S.regime_series(btc, p)
    btc_ema = btc["close"].ewm(span=p["trend_ema"], adjust=False).mean()
    out = {
        "bar_time": btc.index[-1],
        "regime_ok": bool(regime.iloc[-1]),
        "btc_close": float(btc["close"].iloc[-1]),
        "btc_ema": float(btc_ema.iloc[-1]),
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
        trend = bool(last["close"] > last["ema"] and last["mom"] > 0)
        dist = last["hh"] / last["close"] - 1 if last["hh"] > 0 else float("nan")
        row = {
            "Coin": sym,
            "Kapanış": float(last["close"]),
            "Momentum (20)": float(last["mom"]),
            "Kırılım seviyesi": float(last["hh"]),
            "Kırılıma uzaklık": float(dist),
            "Ort. hacim (USDT)": float(last["avg_qvol"]),
            "Stop": float("nan"),
            "Durum": "",
            "Not": "",
        }
        if sym in held:
            since = held[sym].get("entry_date") if isinstance(held[sym], dict) else None
            seg = df.loc[since:] if since else df.iloc[-1:]
            highest = seg["close"].max() if len(seg) else last["close"]
            atr_stop = highest - p["atr_stop_mult"] * last["atr"]
            row["Stop"] = float(max(atr_stop, last["ll"]))
            if last["close"] < atr_stop:
                row["Durum"], row["Not"] = "ÇIKIŞ", "ATR stop altında kapandı"
            elif last["exit_signal"]:
                row["Durum"], row["Not"] = "ÇIKIŞ", f"{p['exit_lookback']} mum dibi kırıldı"
            else:
                row["Durum"], row["Not"] = "TUT", "Trend sürüyor"
        elif last["entry_signal"]:
            row["Stop"] = float(last["close"] - p["atr_stop_mult"] * last["atr"])
            if out["regime_ok"]:
                row["Durum"], row["Not"] = "GİRİŞ", "Kırılım + trend + momentum"
            else:
                row["Durum"], row["Not"] = "FİLTRELENDİ", "Kırılım var ama BTC filtresi kapalı"
        elif trend and 0 <= dist <= NEAR_BREAKOUT and last["avg_qvol"] >= C.MIN_AVG_QUOTE_VOLUME:
            row["Durum"], row["Not"] = "İZLE", f"Kırılıma %{dist * 100:.1f} kaldı"
        elif trend:
            row["Durum"], row["Not"] = "TREND", "Trendde, kırılım yok"
        else:
            row["Durum"], row["Not"] = "—", "Trend yok"
        out["rows"].append(row)

    order = {"ÇIKIŞ": 0, "GİRİŞ": 1, "TUT": 2, "İZLE": 3, "FİLTRELENDİ": 4, "TREND": 5, "—": 6}
    out["rows"].sort(key=lambda r: (order.get(r["Durum"], 9), -r["Momentum (20)"]))
    # Aynı anda en fazla MAX_OPEN_POSITIONS giriş öner (en güçlü momentum önce)
    slots = C.MAX_OPEN_POSITIONS - sum(r["Durum"] == "TUT" for r in out["rows"])
    n = 0
    for r in out["rows"]:
        if r["Durum"] == "GİRİŞ":
            n += 1
            if n > slots:
                r["Durum"], r["Not"] = "İZLE", "Giriş sinyali var ama pozisyon limiti dolu"
    out["rows"].sort(key=lambda r: (order.get(r["Durum"], 9), -r["Momentum (20)"]))
    return out
