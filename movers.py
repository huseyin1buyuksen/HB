"""
PUMP / DUMP AVCISI (agresif mod) — deneysel strateji.

Fikir:
  1) Son 4s / 6s / 12s içinde SERT pump veya dump yapan coinleri bul ("hareket edenler").
  2) Her hareket edenin, hareket BAŞLAMADAN ÖNCEKİ 8 saatlik görüntüsünü (fiyat yolu + hacim yolu) çıkar.
  3) Diğer coinlerin ŞU ANKİ son 8 saatlik görüntüsünü bunlarla karşılaştır (korelasyon benzerliği).
     → "📐 Benzer kalıp": pump öncesi görüntüye çok benzeyen coinler (sıradaki pump adayı olabilir).
  4) Hareket edenle son 12 saatte BİRLİKTE hareket edip daha az yükselmiş/düşmüş coinler
     → "🔗 Geride kalan eşlikçi": aynı akımın gecikmeli takipçisi olabilir.
  5) Tetik: aday, son 2 saatin tepesini (dump için dibini) hacimle kırarsa "FIRSAT" olur.

Geçmiş verideki isabeti evaluate() ile ölçülür; sonuç sitede gösterilir. Yatırım tavsiyesi değildir.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import indicators as I

WINDOWS = {"4s": 16, "6s": 24, "12s": 48}                 # 15m mum sayısı
MOVE_PCT = {"4s": 0.08, "6s": 0.10, "12s": 0.15}          # bu kadar ve üzeri hareket "sert" sayılır
MOVE_Z = 2.5                                               # coinin kendi oynaklığına göre en az bu kadar sigma
PRE_BARS = 32                                              # hareket öncesi 8 saatlik kalıp
SIM_MIN = 0.80                                             # benzer kalıp eşiği
CORR_MIN = 0.60                                            # eşlikçi korelasyon eşiği
LAG_RATIO = 0.40                                           # eşlikçinin hareketi, hareket edeninkinin en fazla %40'ı
MIN_QVOL = 1_000_000                                       # 24s hacmi bunun altındaki coinler taranmaz
MAX_COINS = 250
BREAK_BARS = 8                                             # tetik: son 2 saatin tepesi/dibi
VOL_MULT = 1.5


# ------------------------------------------------------------------ veri

def universe(ex, max_coins: int = MAX_COINS) -> tuple[list[str], dict]:
    import data as D
    tickers = ex.fetch_tickers(params={"type": C.MARKET_TYPE})
    rows = []
    for sym, t in tickers.items():
        m = ex.markets.get(sym)
        if (not D.is_usdt_perp(m) or m["base"] in C.EXCLUDE_BASES or m["base"].endswith(C.EXCLUDE_SUFFIXES)
                or D.is_tradfi(m)):
            continue
        qv = D.quote_volume(t)
        if qv >= MIN_QVOL:
            rows.append((sym, qv))
    rows.sort(key=lambda r: r[1], reverse=True)
    return [s for s, _ in rows[:max_coins]], tickers


def load_all(fetch, symbols: list[str], bars: int = 200, progress=None) -> dict[str, pd.DataFrame]:
    out = {}
    for i, s in enumerate(symbols):
        try:
            df = fetch(s, "15m", bars)
            if len(df) >= 80:
                out[s] = df
        except Exception:
            pass
        if progress:
            progress((i + 1) / len(symbols), s)
    return out


# ------------------------------------------------------------------ yardımcılar

def _z(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    sd = x.std()
    return (x - x.mean()) / sd if sd > 0 else np.zeros_like(x)


def signature(df: pd.DataFrame, end: int) -> tuple[np.ndarray, np.ndarray] | None:
    """end (hariç) indeksinden önceki PRE_BARS mumun normalize fiyat ve hacim yolu."""
    if end < PRE_BARS + 1:
        return None
    seg = df.iloc[end - PRE_BARS:end]
    px = np.log(seg["close"].to_numpy())
    vol = np.log1p(seg["volume"].to_numpy())
    return _z(px), _z(vol)


def similarity(a, b) -> float:
    pc = float(np.corrcoef(a[0], b[0])[0, 1]) if a[0].std() and b[0].std() else 0.0
    vc = float(np.corrcoef(a[1], b[1])[0, 1]) if a[1].std() and b[1].std() else 0.0
    return 0.7 * np.nan_to_num(pc) + 0.3 * np.nan_to_num(vc)


def vol_pct(df: pd.DataFrame, end: int) -> float:
    r = np.diff(np.log(df["close"].to_numpy()[max(0, end - 96):end]))
    return float(r.std()) if len(r) > 10 else np.nan


# ------------------------------------------------------------------ 1) hareket edenler

def find_movers(data: dict[str, pd.DataFrame], at: int | None = None) -> list[dict]:
    """at: her coinde kullanılacak son mumun indeksi (None = en son). Aynı coin birden çok pencerede çıkarsa en sert olan tutulur."""
    best = {}
    for sym, df in data.items():
        end = len(df) if at is None else at
        if end < 48 + PRE_BARS + 2:
            continue
        c = df["close"].to_numpy()[:end]
        sig = vol_pct(df, end - 48)
        for name, w in WINDOWS.items():
            ret = c[-1] / c[-1 - w] - 1
            z = abs(np.log1p(ret)) / (sig * np.sqrt(w)) if sig and sig > 0 else 0
            if abs(ret) < MOVE_PCT[name] or z < MOVE_Z:
                continue
            seg = c[-1 - w:]
            start = end - 1 - w + (int(np.argmin(seg)) if ret > 0 else int(np.argmax(seg)))
            d = {"symbol": sym, "window": name, "ret": float(ret), "z": float(z), "side": 1 if ret > 0 else -1,
                 "start_idx": start, "start_time": df.index[start], "end": end}
            if sym not in best or abs(ret) > abs(best[sym]["ret"]):
                best[sym] = d
    return sorted(best.values(), key=lambda d: -abs(d["ret"]))


# ------------------------------------------------------------------ 2) adaylar

def find_candidates(data: dict[str, pd.DataFrame], movers: list[dict], at: int | None = None) -> list[dict]:
    mover_syms = {m["symbol"] for m in movers}
    pats = []
    for m in movers:                       # hareket başlangıcı ±birkaç mum kayabilir: birkaç hizalamayı dene
        sgs = [sg for off in range(-4, 3) if (sg := signature(data[m["symbol"]], m["start_idx"] + 1 + off)) is not None]
        if sgs:
            pats.append((m, sgs))
    out = []
    for sym, df in data.items():
        if sym in mover_syms:
            continue
        end = len(df) if at is None else at
        cur = signature(df, end)
        if cur is None:
            continue
        c = df["close"].to_numpy()[:end]
        best = None
        # 📐 benzer kalıp
        for m, sgs in pats:
            s = max(similarity(cur, sg) for sg in sgs)
            if s >= SIM_MIN and (best is None or s > best["score"]):
                best = {"symbol": sym, "kind": "📐 Benzer kalıp", "score": s, "side": m["side"], "ref": m,
                        "own_ret": float(c[-1] / c[-49] - 1)}
        # 🔗 geride kalan eşlikçi
        for m in movers:
            mdf = data[m["symbol"]]
            mend = len(mdf) if at is None else at
            a = np.diff(np.log(mdf["close"].to_numpy()[mend - 49:mend]))
            b = np.diff(np.log(c[-49:]))
            if len(a) != len(b) or a.std() == 0 or b.std() == 0:
                continue
            corr = float(np.corrcoef(a, b)[0, 1])
            mret = m["ret"]
            own = float(c[-1] / c[-1 - WINDOWS[m["window"]]] - 1)
            lag_ok = np.sign(own) == np.sign(mret) and abs(own) <= LAG_RATIO * abs(mret)
            if corr >= CORR_MIN and lag_ok and (best is None or corr > best["score"]):
                best = {"symbol": sym, "kind": "🔗 Geride kalan eşlikçi", "score": corr, "side": m["side"], "ref": m,
                        "own_ret": own}
        if best:
            best["trigger"] = trigger(df, end, best["side"])
            best["price"] = float(c[-1])
            best["time"] = df.index[end - 1]
            out.append(best)
    return sorted(out, key=lambda d: (not d["trigger"], -d["score"]))


def trigger(df: pd.DataFrame, end: int, side: int) -> bool:
    """Son kapanan mum, önceki BREAK_BARS mumun tepesini (short için dibini) ortalamanın VOL_MULT katı hacimle kırdı mı?"""
    if end < BREAK_BARS + 21:
        return False
    hi, lo, cl, vo = (df[k].to_numpy()[:end] for k in ("high", "low", "close", "volume"))
    vavg = vo[-21:-1].mean()
    if vavg <= 0 or vo[-1] < VOL_MULT * vavg:
        return False
    return cl[-1] > hi[-1 - BREAK_BARS:-1].max() if side == 1 else cl[-1] < lo[-1 - BREAK_BARS:-1].min()


def scan(fetch, symbols: list[str], progress=None) -> dict:
    data = load_all(fetch, symbols, 200, progress)
    movers = find_movers(data)
    cands = find_candidates(data, movers)
    return {"data": data, "movers": movers, "candidates": cands, "n": len(data)}


# ------------------------------------------------------------------ geçmiş isabet ölçümü

def evaluate(data: dict[str, pd.DataFrame], step: int = 8, horizon: int = 24, move: float = 0.04) -> dict:
    """Geçmişteki her step mumda tarayıcıyı çalıştırır; adayın sonraki horizon mumda önce lehte move
    mi yoksa aleyhte move mi gittiğini sayar. Aynı ölçüm, aynı anlarda rastgele (aday olmayan) coinler için de yapılır."""
    n = min(len(df) for df in data.values())
    data = {s: df.iloc[-n:] for s, df in data.items()}
    res = {"📐 Benzer kalıp": [], "🔗 Geride kalan eşlikçi": [], "tetikli": [], "rastgele": []}
    rng = np.random.default_rng(1)
    syms = list(data)
    for at in range(48 + PRE_BARS + 100, n - horizon, step):
        movers = find_movers(data, at)
        if not movers:
            continue
        cands = find_candidates(data, movers, at)
        csyms = {c["symbol"] for c in cands}
        for c in cands:
            o = _outcome(data[c["symbol"]], at, c["side"], horizon, move)
            res[c["kind"]].append(o)
            if c["trigger"]:
                res["tetikli"].append(o)
        pool = [s for s in syms if s not in csyms and s not in {m["symbol"] for m in movers}]
        for s in rng.choice(pool, size=min(len(cands) or 1, len(pool)), replace=False) if pool else []:
            res["rastgele"].append(_outcome(data[s], at, movers[0]["side"], horizon, move))
    rows = []
    for k, v in res.items():
        v = np.array(v)
        if len(v) == 0:
            rows.append({"Grup": k, "Örnek": 0, "Lehte önce": "—", "Aleyhte önce": "—", "Sonuçsuz": "—"})
            continue
        rows.append({"Grup": k, "Örnek": len(v), "Lehte önce": f"%{(v == 1).mean() * 100:.0f}",
                     "Aleyhte önce": f"%{(v == -1).mean() * 100:.0f}", "Sonuçsuz": f"%{(v == 0).mean() * 100:.0f}"})
    return {"table": pd.DataFrame(rows), "bars": n, "horizon_h": horizon / 4, "move_pct": move * 100}


def _outcome(df, at, side, horizon, move) -> int:
    e = df["close"].iloc[at - 1]
    hi = df["high"].to_numpy()[at:at + horizon]
    lo = df["low"].to_numpy()[at:at + horizon]
    up, dn = e * (1 + move), e * (1 - move)
    win = (hi >= up) if side == 1 else (lo <= dn)
    lose = (lo <= dn) if side == 1 else (hi >= up)
    wi = np.argmax(win) if win.any() else 10 ** 9
    li = np.argmax(lose) if lose.any() else 10 ** 9
    return 0 if wi == li == 10 ** 9 else (-1 if li <= wi else 1)


def describe(c: dict) -> str:
    m = c["ref"]
    base, mb = c["symbol"].split("/")[0], m["symbol"].split("/")[0]
    mv = "PUMP" if m["side"] == 1 else "DUMP"
    if c["kind"].startswith("📐"):
        why = (f"{mb} son {m['window']}te %{m['ret'] * 100:+.1f} {mv} yaptı. {base}'in son 8 saatlik fiyat+hacim görüntüsü, "
               f"{mb}'nin {mv} başlamadan önceki 8 saatine %{c['score'] * 100:.0f} benziyor.")
    else:
        why = (f"{mb} son {m['window']}te %{m['ret'] * 100:+.1f} {mv} yaptı. {base} son 12 saatte onunla %{c['score'] * 100:.0f} "
               f"korelasyonla hareket etti ama sadece %{c['own_ret'] * 100:+.1f} gitti (geride kaldı).")
    trig = ("✅ Tetik geldi: son 2 saatin " + ("tepesi" if c["side"] == 1 else "dibi") + " yüksek hacimle kırıldı."
            if c["trigger"] else "⏳ Tetik bekleniyor (son 2 saatin " + ("tepesini" if c["side"] == 1 else "dibini") + " hacimle kırması).")
    return f"{why}\n{trig}"
