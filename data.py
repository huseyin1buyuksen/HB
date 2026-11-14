"""MEXC'den mum verisi çeker ve data/ klasörüne CSV olarak önbelleğe alır (sadece herkese açık veri, API anahtarı gerekmez)."""
import os
import time

import pandas as pd

import config as C

LEVERAGED_SUFFIXES = ("3L", "3S", "5L", "5S", "UP", "DOWN", "BULL", "BEAR")


def get_exchange():
    import ccxt  # pip install ccxt

    ex = getattr(ccxt, C.EXCHANGE)({"enableRateLimit": True})
    ex.load_markets()
    return ex


def pick_universe(ex) -> list[str]:
    """Ayarlarda liste verilmemişse: USDT hacmine göre en büyük TOP_N spot altcoin."""
    if C.SYMBOLS:
        return list(C.SYMBOLS)
    tickers = ex.fetch_tickers()
    rows = []
    for sym, t in tickers.items():
        m = ex.markets.get(sym)
        if not m or not m.get("spot") or not m.get("active", True) or m.get("quote") != C.QUOTE:
            continue
        base = m["base"]
        if base in C.EXCLUDE_BASES or base.endswith(LEVERAGED_SUFFIXES):
            continue
        qv = t.get("quoteVolume") or 0
        rows.append((sym, qv))
    rows.sort(key=lambda r: r[1], reverse=True)
    return [s for s, _ in rows[: C.TOP_N]]


def history_start() -> str:
    """Backtest için veri başlangıcı: günlükte HISTORY_START, kısa zaman dilimlerinde son HISTORY_DAYS gün."""
    days = C.HISTORY_DAYS.get(C.TIMEFRAME)
    if not days:
        return C.HISTORY_START
    return (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).strftime("%Y-%m-%d")


def fetch_ohlcv(ex, symbol: str, since: str | None = None, limit_bars: int | None = None) -> pd.DataFrame:
    tf_ms = ex.parse_timeframe(C.TIMEFRAME) * 1000
    if limit_bars:
        since_ms = ex.milliseconds() - (limit_bars + 2) * tf_ms
    else:
        since_ms = ex.parse8601((since or history_start()) + "T00:00:00Z")
    rows = []
    while True:
        batch = ex.fetch_ohlcv(symbol, C.TIMEFRAME, since=since_ms, limit=500)
        if not batch:
            break
        rows += batch
        nxt = batch[-1][0] + tf_ms
        if nxt <= since_ms or len(batch) < 2:
            break
        since_ms = nxt
        if since_ms > ex.milliseconds():
            break
        time.sleep(ex.rateLimit / 1000)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates("ts").sort_values("ts")
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop(columns="ts").astype(float)
    # Henüz kapanmamış son mumu at
    if len(df) and df.index[-1].value // 10**6 + tf_ms > ex.milliseconds():
        df = df.iloc[:-1]
    return df


def load_history(symbols: list[str], refresh: bool = False) -> dict[str, pd.DataFrame]:
    os.makedirs(C.DATA_DIR, exist_ok=True)
    out, ex = {}, None
    for sym in symbols:
        path = os.path.join(C.DATA_DIR, f"{sym.replace('/', '_')}_{C.TIMEFRAME}.csv")
        if os.path.exists(path) and not refresh:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
        else:
            ex = ex or get_exchange()
            print(f"  indiriliyor: {sym}")
            try:
                df = fetch_ohlcv(ex, sym)
            except Exception as e:  # listeden kalkmış vb.
                print(f"  ! {sym} atlandı: {e}")
                continue
            df.to_csv(path)
        if len(df) > 60:
            out[sym] = df
    return out
