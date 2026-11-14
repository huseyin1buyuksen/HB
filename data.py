"""
MEXC USDT-M Futures veri katmanı (ccxt). Sadece herkese açık veri; API anahtarı gerekmez.
Her veri için zaman damgası tutulur; bayat veriyle sinyal üretilmez.
"""
from __future__ import annotations

import time

import pandas as pd

import config as C
import indicators as I


def get_exchange():
    import ccxt  # pip install ccxt

    ex = getattr(ccxt, C.EXCHANGE)({"enableRateLimit": True, "options": {"defaultType": C.MARKET_TYPE}})
    ex.load_markets()
    return ex


def is_usdt_perp(m: dict) -> bool:
    return bool(m and m.get("swap") and m.get("linear") and m.get("quote") == C.QUOTE and m.get("active", True))


def pick_universe(ex, top_n: int | None = None) -> tuple[list[str], dict]:
    """24s USDT hacmine göre en büyük vadeli kontratlar. Dönüş: semboller, ticker sözlüğü."""
    tickers = ex.fetch_tickers(params={"type": C.MARKET_TYPE})
    rows = []
    for sym, t in tickers.items():
        m = ex.markets.get(sym)
        if not is_usdt_perp(m) or m["base"] in C.EXCLUDE_BASES:
            continue
        rows.append((sym, quote_volume(t)))
    rows.sort(key=lambda r: r[1], reverse=True)
    return [s for s, _ in rows[: (top_n or C.TOP_N)]], tickers


def quote_volume(t: dict) -> float:
    qv = t.get("quoteVolume")
    if qv:
        return float(qv)
    info = t.get("info") or {}
    try:
        return float(info.get("amount24") or 0)
    except (TypeError, ValueError):
        return 0.0


def fetch_ohlcv(ex, symbol: str, tf: str, bars: int | None = None, since_ms: int | None = None) -> pd.DataFrame:
    tf_ms = I.TF_MIN[tf] * 60_000
    now = ex.milliseconds()
    since = since_ms if since_ms is not None else now - ((bars or 300) + 2) * tf_ms
    rows = []
    while True:
        batch = ex.fetch_ohlcv(symbol, tf, since=since, limit=1000)
        if not batch:
            break
        rows += batch
        nxt = batch[-1][0] + tf_ms
        if nxt <= since or nxt > now or len(batch) < 2:
            break
        since = nxt
        time.sleep(ex.rateLimit / 1000)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"]).drop_duplicates("ts").sort_values("ts")
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop(columns="ts").astype(float)
    if len(df) and df.index[-1].value // 10**6 + tf_ms > now:   # kapanmamış son mumu at
        df = df.iloc[:-1]
    return df


def is_stale(df: pd.DataFrame, tf: str, now_ms: int) -> bool:
    if df.empty:
        return True
    tf_ms = I.TF_MIN[tf] * 60_000
    last_close_ms = df.index[-1].value // 10**6 + tf_ms
    return now_ms - last_close_ms > C.MAX_STALE_BARS * tf_ms


def market_rules(ex, symbol: str) -> dict:
    m = ex.markets.get(symbol, {})
    prec = (m.get("precision") or {}).get("amount")
    lim = ((m.get("limits") or {}).get("amount") or {}).get("min")
    return {"contract_size": m.get("contractSize") or 1.0, "amount_step": prec if prec and prec < 1e6 else 1.0,
            "min_amount": lim, "tick": (m.get("precision") or {}).get("price")}


def live_snapshot(ex, symbol: str, ticker: dict | None, oi_store: dict | None = None) -> dict:
    """Spread, ±%0.5 derinlik, defter dengesizliği, funding, OI (ve önceki taramaya göre değişimi), 24s hacim."""
    out = {"ts": pd.Timestamp.now(tz="UTC"), "source": f"MEXC Futures (ccxt) · {symbol}"}
    t = ticker or {}
    info = t.get("info") or {}
    out["quote_vol_24h"] = quote_volume(t) if t else None
    cs = (ex.markets.get(symbol) or {}).get("contractSize") or 1.0
    try:
        ob = ex.fetch_order_book(symbol, limit=50)
        bid, ask = ob["bids"][0][0], ob["asks"][0][0]
        mid = (bid + ask) / 2
        out["spread_pct"] = (ask - bid) / mid * 100
        bd = sum(p * a * cs for p, a, *_ in ob["bids"] if p >= mid * 0.995)
        ad = sum(p * a * cs for p, a, *_ in ob["asks"] if p <= mid * 1.005)
        out["depth_usdt"] = min(bd, ad)
        out["imbalance"] = (bd - ad) / (bd + ad) if bd + ad else 0.0
        out["bid"], out["ask"] = bid, ask
    except Exception as e:
        out["ob_error"] = str(e)
    fr = t.get("fundingRate") if t else None
    if fr is None:
        fr = info.get("fundingRate")
    try:
        fri = ex.fetch_funding_rate(symbol)
        fr = fri.get("fundingRate", fr)
        out["next_funding"] = fri.get("fundingTimestamp") or fri.get("nextFundingTimestamp")
    except Exception:
        pass
    out["funding"] = float(fr) if fr is not None else None
    hold = info.get("holdVol")
    if hold is not None:
        try:
            last = float(t.get("last") or info.get("lastPrice"))
            oi = float(hold) * cs * last
            out["oi_usdt"] = oi
            if oi_store is not None:
                hist = oi_store.setdefault(symbol, [])
                now = time.time()
                old = [x for x in hist if now - x[0] >= 15 * 60]
                if old:
                    out["oi_change"] = oi / old[-1][1] - 1
                    out["oi_change_since_min"] = int((now - old[-1][0]) / 60)
                hist.append((now, oi))
                del hist[:-50]
        except (TypeError, ValueError):
            pass
    return out
