"""
COIN ANALİZİ — tek bir MEXC USDT-M kontratını LONG ve SHORT için inceler.

Ne yapar:
  1) 15m / 1h / 4h / 1g trend tablosu ve gösterge eğilimi (-100 … +100)
  2) Geçmişten ÖLÇÜLEN olasılıklar: coinin kendi geçmişinde bugünküne benzer durumlarda
     fiyat önce hedefe mi, önce stopa mı gitti? (örnek sayısı ile birlikte)
  3) Yükseliş / düşüş / yatay senaryoları ve hedef seviyeleri
  4) TAHMİNİ likidasyon haritası (hacim × varsayılan kaldıraç dağılımı; gerçek veri değildir)
  5) Kasa planı: ilk giriş (kasanın %X'i marjin), planlı ekleme, yedek, stop, TP'ler,
     likidasyon fiyatı, stopta / TP'de kasanın yüzde kaçı gider-gelir
  6) Türkçe yorum
  7) Monte Carlo: verilen isabet, R/R ve risk ile 10 günde hedefe ulaşma / kasayı kaybetme olasılığı

Hiçbir çıktı yatırım tavsiyesi değildir; olasılıklar geçmiş veriye dayanır ve geleceği garanti etmez.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config as C
import indicators as I

MMR = 0.005                    # bakım marjini oranı (yaklaşık; MEXC kademeleri kontrata göre değişir)
FEE = C.FEE_RATE               # taker komisyonu (giriş + çıkış ayrı ayrı uygulanır)
LEV_MIX = {5: 0.12, 10: 0.28, 20: 0.25, 25: 0.10, 50: 0.17, 100: 0.08}   # likidasyon haritası varsayımı
LIQ_HALF_LIFE_H = 120          # açık pozisyonların yarılanma süresi (saat) — eski pozisyonlar kapanmış sayılır
HORIZON_1H = 48                # plan olasılık ufku: 48 adet 1H mum (2 gün)
DIR_ATR, DIR_H = 2.0, 24       # yön testi: 24 saat içinde ±2 ATR'den hangisi önce
TF_W = {"1g": 15, "4h": 20, "1h": 15, "15m": 10}
DIR_TXT = {1: "Yukarı", -1: "Aşağı", 0: "Kararsız"}


# ------------------------------------------------------------------ yardımcılar

def _pct(a, b):
    return (a / b - 1) * 100


def fmt_px(x: float) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    ax = abs(x)
    d = 2 if ax >= 100 else 3 if ax >= 10 else 4 if ax >= 1 else 5 if ax >= 0.1 else 6 if ax >= 0.01 else 8
    return f"{x:,.{d}f}"


def tf_summary(df: pd.DataFrame) -> dict:
    """Bir zaman dilimi için trend özeti (son KAPANMIŞ mum)."""
    t = I.htf_trend(df).iloc[-1]
    c = df["close"]
    r = I.rsi(c, 14).iloc[-1]
    _, _, hist = I.macd(c, C.MACD_FAST, C.MACD_SLOW, C.MACD_SIGNAL)
    st = I.supertrend(df, C.ST_LEN, C.ST_MULT).iloc[-1]
    return {"dir": int(t["dir"]), "partial": int(t["partial"]), "above200": int(t["above200"]),
            "adx": float(t["adx"]), "rsi": float(r), "macd_up": bool(hist.iloc[-1] > 0),
            "supertrend": int(st) if np.isfinite(st) else 0, "ret": float(c.pct_change(4).iloc[-1] * 100)}


# ------------------------------------------------------------------ geçmişten ölçülen olasılıklar

def first_touch(high, low, close, atr, idx, side, stop_atr, tgt_atr, horizon):
    """Her i için: giriş close[i]; hedef ve stop ATR cinsinden. Sonuç: +1 hedef önce, -1 stop önce, 0 süre doldu.
    Hedef ve stop aynı mumdaysa stop önce sayılır (kötümser varsayım)."""
    out = np.zeros(len(idx), dtype=int)
    n = len(close)
    for j, i in enumerate(idx):
        e, a = close[i], atr[i]
        if not np.isfinite(a) or a <= 0:
            out[j] = 0
            continue
        end = min(n, i + 1 + horizon)
        hh, ll = high[i + 1:end], low[i + 1:end]
        if side == 1:
            hit_t, hit_s = hh >= e + tgt_atr * a, ll <= e - stop_atr * a
        else:
            hit_t, hit_s = ll <= e - tgt_atr * a, hh >= e + stop_atr * a
        ts = np.argmax(hit_t) if hit_t.any() else 10 ** 9
        ss = np.argmax(hit_s) if hit_s.any() else 10 ** 9
        out[j] = 0 if ts == ss == 10 ** 9 else (-1 if ss <= ts else 1)
    return out


def state_frame(h1: pd.DataFrame, h4: pd.DataFrame) -> pd.DataFrame:
    """Benzer durum eşleştirmesi için her 1H mumun durumu (geleceği görmeden)."""
    st = pd.DataFrame(index=h1.index)
    t1 = I.htf_trend(h1)
    st["h1"] = t1["dir"]
    st["h4"] = I.align_htf(I.htf_trend(h4), h1.index, 240, 60)["dir"].fillna(0).astype(int)
    r = I.rsi(h1["close"], 14)
    st["rsi"] = np.where(r < 40, -1, np.where(r > 60, 1, 0))
    e9, e21 = I.ema(h1["close"], 9), I.ema(h1["close"], 21)
    st["ema"] = np.where(e9 > e21, 1, -1)
    st["atr"] = I.atr(h1, C.ATR_LEN)
    return st


def similar_index(st: pd.DataFrame, horizon: int, min_n: int = 80):
    """Bugünkü duruma benzeyen geçmiş mumlar. Örnek az ise şartlar sırayla gevşetilir."""
    cur = st.iloc[-1]
    usable = st.iloc[200:len(st) - horizon]           # göstergeler oturmuş ve sonucu bilinen mumlar
    levels = [["h4", "h1", "rsi", "ema"], ["h4", "h1", "rsi"], ["h4", "h1"], ["h1"], []]
    for keys in levels:
        m = np.ones(len(usable), dtype=bool)
        for k in keys:
            m &= (usable[k] == cur[k]).to_numpy()
        idx = np.flatnonzero(m) + 200
        if len(idx) >= min_n or not keys:
            return idx, keys
    return np.array([], dtype=int), []


KEY_TXT = {"h4": "4H trend", "h1": "1H trend", "rsi": "RSI bölgesi", "ema": "EMA9/21 dizilimi"}


def probabilities(h1: pd.DataFrame, h4: pd.DataFrame, plan_long: dict, plan_short: dict, horizon: int = HORIZON_1H) -> dict:
    st = state_frame(h1, h4)
    hi, lo, cl = h1["high"].to_numpy(), h1["low"].to_numpy(), h1["close"].to_numpy()
    at = st["atr"].to_numpy()
    idx, keys = similar_index(st, horizon)
    base_idx = np.arange(200, len(st) - horizon)
    out = {"n": int(len(idx)), "n_all": int(len(base_idx)), "keys": [KEY_TXT[k] for k in keys], "horizon_h": horizon}

    def rates(res):
        n = max(len(res), 1)
        return {"win": float((res == 1).sum() / n), "loss": float((res == -1).sum() / n), "none": float((res == 0).sum() / n)}

    # simetrik yön testi: ±DIR_ATR hangisi önce?
    sym_up = first_touch(hi, lo, cl, at, idx, 1, DIR_ATR, DIR_ATR, DIR_H)
    out["dir"] = {"up": float((sym_up == 1).mean()) if len(idx) else np.nan,
                  "down": float((sym_up == -1).mean()) if len(idx) else np.nan,
                  "range": float((sym_up == 0).mean()) if len(idx) else np.nan}
    base = first_touch(hi, lo, cl, at, base_idx, 1, DIR_ATR, DIR_ATR, DIR_H)
    out["moe"] = 1.96 * math.sqrt(0.25 / max(len(idx), 1)) * 100   # ±hata payı (yüzde puan)
    out["dir_base"] = {"up": float((base == 1).mean()), "down": float((base == -1).mean()), "range": float((base == 0).mean())}
    # planların kendi stop/TP mesafeleri ile
    for name, p, sd in (("long", plan_long, 1), ("short", plan_short, -1)):
        s_atr = p["stop_dist"] / p["atr"]
        d = {}
        for k, tp in enumerate(p["tps"][:2]):
            t_atr = abs(tp - p["entry"]) / p["atr"]
            d[f"tp{k + 1}"] = rates(first_touch(hi, lo, cl, at, idx, sd, s_atr, t_atr, horizon))
        out[name] = d
    return out


# ------------------------------------------------------------------ tahmini likidasyon haritası

def liquidation_map(h1: pd.DataFrame, price: float, span: float = 0.25, bins: int = 120, lookback: int = 24 * 30) -> dict:
    """Her 1H mumda açılan pozisyonların hacimle orantılı olduğu ve LEV_MIX kaldıraç dağılımını kullandığı varsayılır.
    Fiyatın sonradan geçtiği likidasyon seviyeleri 'patlamış' sayılır ve haritadan çıkar. Eski pozisyonlar zamanla
    kapanmış kabul edilir (yarılanma süresi LIQ_HALF_LIFE_H). Sonuç göreli büyüklüktür, USDT değildir."""
    d = h1.tail(lookback)
    p = ((d["high"] + d["low"] + d["close"]) / 3).to_numpy()
    w = (d["volume"] * p).to_numpy()
    age = np.arange(len(d))[::-1]
    w = w * 0.5 ** (age / LIQ_HALF_LIFE_H)
    lo, hi = d["low"].to_numpy(), d["high"].to_numpy()
    # i'den SONRAKİ mumların en düşük / en yüksek değeri
    min_after = np.append(np.minimum.accumulate(lo[::-1])[::-1][1:], np.inf)
    max_after = np.append(np.maximum.accumulate(hi[::-1])[::-1][1:], -np.inf)
    edges = np.linspace(price * (1 - span), price * (1 + span), bins + 1)
    long_h, short_h = np.zeros(bins), np.zeros(bins)
    for lev, share in LEV_MIX.items():
        lq_long = p * (1 - 1 / lev + MMR)
        lq_short = p * (1 + 1 / lev - MMR)
        alive_l = min_after > lq_long
        alive_s = max_after < lq_short
        long_h += np.histogram(lq_long[alive_l], bins=edges, weights=w[alive_l] * share)[0]
        short_h += np.histogram(lq_short[alive_s], bins=edges, weights=w[alive_s] * share)[0]
    mids = (edges[:-1] + edges[1:]) / 2
    long_h[mids >= price] = 0
    short_h[mids <= price] = 0
    tot = max(long_h.sum() + short_h.sum(), 1e-12)
    long_h, short_h = long_h / tot * 100, short_h / tot * 100

    def clusters(hist, n=3):
        order = np.argsort(hist)[::-1]
        res = []
        for i in order:
            if hist[i] <= 0 or len(res) >= n:
                break
            if all(abs(i - j) > 3 for j, _, _ in res):
                res.append((i, float(mids[i]), float(hist[i])))
        return [{"price": px, "share": sh, "dist_pct": _pct(px, price)} for _, px, sh in res]

    near = np.abs(mids / price - 1) <= 0.08
    above, below = float(short_h[near].sum()), float(long_h[near].sum())
    return {"mids": mids, "long": long_h, "short": short_h, "above": clusters(short_h), "below": clusters(long_h),
            "near_above": above, "near_below": below,
            "magnet": 1 if above > below * 1.25 else -1 if below > above * 1.25 else 0}


# ------------------------------------------------------------------ seviyeler ve kasa planı

def key_levels(h1: pd.DataFrame, d1: pd.DataFrame | None, price: float, atr: float) -> dict:
    sw = I.swings(h1, C.SWING_K)
    pl = I.period_levels(h1).iloc[-1]
    lv_up, lv_dn = [], []
    cand = {"1H swing tepe": sw["sh"].iloc[-1], "Önceki 1H swing tepe": sw["sh_prev"].iloc[-1],
            "1H swing dip": sw["sl"].iloc[-1], "Önceki 1H swing dip": sw["sl_prev"].iloc[-1],
            "Dünün yükseği (PDH)": pl["pdh"], "Dünün düşüğü (PDL)": pl["pdl"],
            "Geçen hafta yükseği": pl["pwh"], "Geçen hafta düşüğü": pl["pwl"],
            "1H EMA200": I.ema(h1["close"], 200).iloc[-1], "1H EMA50": I.ema(h1["close"], 50).iloc[-1]}
    if d1 is not None and len(d1) > 30:
        cand["Son 30 gün zirvesi"] = d1["high"].tail(30).max()
        cand["Son 30 gün dibi"] = d1["low"].tail(30).min()
    for name, v in cand.items():
        if v is None or not np.isfinite(v):
            continue
        (lv_up if v > price + 0.2 * atr else lv_dn if v < price - 0.2 * atr else []).append((float(v), name))
    lv_up.sort()
    lv_dn.sort(reverse=True)
    return {"up": lv_up, "down": lv_dn}


def build_plan(side: int, price: float, atr: float, levels: dict, liq: dict) -> dict:
    """Yapısal plan: giriş, planlı ekleme seviyesi, stop ve 3 hedef (fiyat seviyeleri; kaldıraçtan bağımsız)."""
    sd = side
    sup = [v for v, _ in (levels["down"] if sd == 1 else levels["up"])]          # aleyhte seviyeler
    res = [v for v, _ in (levels["up"] if sd == 1 else levels["down"])]          # lehte seviyeler
    sup += [c["price"] for c in (liq["below"] if sd == 1 else liq["above"])]
    res += [c["price"] for c in (liq["above"] if sd == 1 else liq["below"])]
    sup = sorted({v for v in sup if sd * (price - v) >= 0.6 * atr}, key=lambda v: sd * (price - v))
    res = sorted({v for v in res if sd * (v - price) >= 0.8 * atr}, key=lambda v: sd * (v - price))
    # ekleme: ilk anlamlı destek (0.6–3 ATR arası), yoksa 1.2 ATR
    add_dist = next((sd * (price - v) for v in sup if sd * (price - v) <= 3 * atr), 1.2 * atr)
    add = price - sd * (add_dist - 0.1 * atr)                                    # desteğin hemen önüne limit
    # stop: eklemenin altındaki bir sonraki yapı - 0.3 ATR; 1.8–6 ATR arasında tutulur
    beyond = [v for v in sup if sd * (price - v) > add_dist + 0.5 * atr]
    stop_dist = (sd * (price - beyond[0]) + 0.3 * atr) if beyond else add_dist + 1.0 * atr
    stop_dist = float(np.clip(stop_dist, max(1.8 * atr, add_dist + 0.6 * atr), 6 * atr))
    stop = price - sd * stop_dist
    # hedefler: lehteki seviyeler, en az 1R / 2R / 3R mantığıyla sıralı
    tps, last = [], 0.0
    for k, mult in enumerate((1.0, 2.0, 3.2)):
        want = mult * stop_dist
        lvl = next((sd * (v - price) for v in res if sd * (v - price) >= max(want * 0.8, last + 0.5 * atr)), None)
        dist = (lvl - 0.1 * atr) if lvl is not None and lvl <= want * 1.6 else want
        dist = max(dist, last + 0.5 * atr)
        tps.append(price + sd * dist)
        last = dist
    return {"side": sd, "entry": price, "add": add, "stop": stop, "stop_dist": stop_dist, "add_dist": add_dist,
            "tps": tps, "atr": atr}


def size_plan(plan: dict, capital: float, leverage: int, first_pct: float, add_pct: float) -> dict:
    """Kaldıraç ve marjin yüzdeleriyle dolar bazında sonuçlar (izole marjin varsayımı)."""
    sd, e1, e2, stop = plan["side"], plan["entry"], plan["add"], plan["stop"]
    m1, m2 = capital * first_pct, capital * add_pct
    q1, q2 = m1 * leverage / e1, m2 * leverage / e2
    avg_b = (q1 * e1 + q2 * e2) / (q1 + q2)
    liq = lambda avg: avg * (1 - sd * (1 / leverage - MMR))
    fee = lambda q, px: q * px * FEE
    loss_a = q1 * abs(e1 - stop) + fee(q1, e1) + fee(q1, stop)
    loss_b = loss_a + q2 * abs(e2 - stop) + fee(q2, e2) + fee(q2, stop)
    tps = []
    for k, (tp, w) in enumerate(zip(plan["tps"], C.TP_SPLIT)):
        ga = q1 * w * sd * (tp - e1) - fee(q1 * w, tp)
        gb = (q1 + q2) * w * sd * (tp - avg_b) - fee((q1 + q2) * w, tp)
        tps.append({"price": tp, "pct_move": abs(_pct(tp, e1)), "gain_a": ga, "gain_b": gb})
    gain_all_a = sum(t["gain_a"] for t in tps) - fee(q1, e1)
    gain_all_b = sum(t["gain_b"] for t in tps) - fee(q1, e1) - fee(q2, e2)
    stop_frac = abs(e1 - stop) / e1
    safe_lev = max(1, int(math.floor(1 / (stop_frac / 0.8 + MMR))))
    liq_a, liq_b = liq(e1), liq(avg_b)
    if sd * (liq_a - stop) > 0:          # likidasyon stoptan önce: kayıp marjinle sınırlı (pozisyon patlar)
        loss_a = m1
    if sd * (liq_b - stop) > 0:
        loss_b = m1 + m2
    return {"capital": capital, "leverage": leverage, "m1": m1, "m2": m2, "reserve": capital - m1 - m2,
            "q1": q1, "q2": q2, "notional1": q1 * e1, "notional2": q2 * e2, "avg_b": avg_b,
            "liq_a": liq_a, "liq_b": liq_b,
            "liq_before_stop_a": sd * (liq_a - stop) > 0, "liq_before_stop_b": sd * (liq_b - stop) > 0,
            "loss_a": loss_a, "loss_b": loss_b, "loss_a_pct": loss_a / capital * 100, "loss_b_pct": loss_b / capital * 100,
            "tps": tps, "gain_all_a": gain_all_a, "gain_all_b": gain_all_b, "stop_pct": stop_frac * 100,
            "add_pct_move": abs(_pct(e2, e1)), "safe_lev": safe_lev}


# ------------------------------------------------------------------ yön eğilimi

def bias_score(tfs: dict, h1: pd.DataFrame, btc_dir: int, liq: dict, funding: float | None) -> tuple[float, list]:
    parts = []
    for tf, w in TF_W.items():
        t = tfs.get(tf)
        if t is None:
            continue
        v = t["dir"] if t["dir"] != 0 else 0.5 * t["partial"]
        parts.append((f"{tf} trend: {DIR_TXT[t['dir']]}", w * v))
    t1 = tfs["1h"]
    mom = (0.5 if t1["rsi"] > 55 else -0.5 if t1["rsi"] < 45 else 0) + (0.5 if t1["macd_up"] else -0.5)
    parts.append((f"1H momentum (RSI {t1['rsi']:.0f}, MACD {'+' if t1['macd_up'] else '−'})", 10 * mom))
    sm = I.smc(h1, C.SWING_K, C.RECENT_BARS, I.atr(h1, C.ATR_LEN)).iloc[-1]
    s = 1 if sm["struct_bull"] else -1 if sm["struct_bear"] else 0
    parts.append((f"1H yapı: {'HH+HL' if s == 1 else 'LH+LL' if s == -1 else 'karışık'}", 10 * s))
    parts.append((f"BTC 1H: {DIR_TXT.get(btc_dir, '—')}", 10 * (btc_dir or 0)))
    parts.append((f"Likidasyon mıknatısı: {'yukarıda' if liq['magnet'] == 1 else 'aşağıda' if liq['magnet'] == -1 else 'dengeli'}",
                  5 * liq["magnet"]))
    if funding is not None:
        fv = -1 if funding > 0.0005 else 1 if funding < -0.0005 else 0
        parts.append((f"Funding {funding * 100:+.3f}% ({'longlar kalabalık' if fv == -1 else 'shortlar kalabalık' if fv == 1 else 'nötr'})", 5 * fv))
    return float(np.clip(sum(v for _, v in parts), -100, 100)), parts


# ------------------------------------------------------------------ ana fonksiyon

def analyze(symbol: str, df15: pd.DataFrame, h1: pd.DataFrame, h4: pd.DataFrame, d1: pd.DataFrame | None,
            btc1h: pd.DataFrame | None, live: dict | None, capital: float = 100.0, leverage: int = 10,
            first_pct: float = 0.5, add_pct: float = 0.25, side_choice: str = "Otomatik") -> dict:
    live = live or {}
    price = float(live.get("last") or df15["close"].iloc[-1])
    tfs = {"15m": tf_summary(df15), "1h": tf_summary(h1), "4h": tf_summary(h4)}
    if d1 is not None and len(d1) >= 60:
        tfs["1g"] = tf_summary(d1)
    atr1h = float(I.atr(h1, C.ATR_LEN).iloc[-1])
    btc_dir = int(I.htf_trend(btc1h)["dir"].iloc[-1]) if btc1h is not None else 0
    liq = liquidation_map(h1, price)
    levels = key_levels(h1, d1, price, atr1h)
    funding = live.get("funding")
    score, parts = bias_score(tfs, h1, btc_dir, liq, funding)

    pl_long = build_plan(1, price, atr1h, levels, liq)
    pl_short = build_plan(-1, price, atr1h, levels, liq)
    prob = probabilities(h1, h4, pl_long, pl_short)

    up, dn = prob["dir"]["up"], prob["dir"]["down"]
    emp = 0 if not np.isfinite(up) or abs(up - dn) < 0.06 else (1 if up > dn else -1)
    ind = 1 if score >= 25 else -1 if score <= -25 else 0
    if ind != 0 and emp == ind:
        direction, conf = ind, "Orta-yüksek (göstergeler ve geçmiş benzer durumlar aynı yönü gösteriyor)"
    elif ind != 0 and emp == 0:
        direction, conf = ind, "Düşük-orta (göstergeler yön gösteriyor, geçmiş istatistik nötr)"
    elif ind == 0 and emp != 0:
        direction, conf = emp, "Düşük (göstergeler kararsız, sadece geçmiş istatistik bir yöne eğilimli)"
    elif ind != 0 and emp == -ind:
        direction, conf = 0, "Çok düşük (göstergeler ile geçmiş istatistik ÇELİŞİYOR)"
    else:
        direction, conf = 0, "Yok (iki taraf da kararsız)"

    side = {"LONG": 1, "SHORT": -1}.get(side_choice, direction or (1 if score >= 0 else -1))
    plan = pl_long if side == 1 else pl_short
    sized = size_plan(plan, capital, leverage, first_pct, add_pct)
    p_side = prob["long" if side == 1 else "short"]

    res = {"symbol": symbol, "price": price, "atr1h": atr1h, "atr_pct": atr1h / price * 100, "tfs": tfs,
           "btc_dir": btc_dir, "liq": liq, "levels": levels, "score": score, "parts": parts, "prob": prob,
           "direction": direction, "confidence": conf, "side": side, "side_forced": side_choice != "Otomatik",
           "plans": {1: pl_long, -1: pl_short}, "plan": plan, "sized": sized, "p_side": p_side,
           "funding": funding, "live": live, "bar_time": df15.index[-1]}
    res["scenarios"] = scenarios(res)
    res["comment"] = commentary(res, first_pct, add_pct)
    return res


def scenarios(r: dict) -> list[dict]:
    p, price, atr = r["prob"]["dir"], r["price"], r["atr1h"]
    up_t = [v for v, _ in r["levels"]["up"]][:2]
    dn_t = [v for v, _ in r["levels"]["down"]][:2]
    out = [
        {"name": "📈 Yükseliş", "p": p["up"], "trigger": f"{fmt_px(price + 0.5 * atr)} üzerinde 1H kapanış",
         "targets": " → ".join(fmt_px(x) for x in (up_t or [price + DIR_ATR * atr])),
         "note": f"{DIR_H} saatte önce +{DIR_ATR:g} ATR'ye ulaşma oranı (benzer geçmiş durumlar)"},
        {"name": "📉 Düşüş", "p": p["down"], "trigger": f"{fmt_px(price - 0.5 * atr)} altında 1H kapanış",
         "targets": " → ".join(fmt_px(x) for x in (dn_t or [price - DIR_ATR * atr])),
         "note": f"{DIR_H} saatte önce −{DIR_ATR:g} ATR'ye ulaşma oranı"},
        {"name": "↔️ Yatay", "p": p["range"], "trigger": f"{fmt_px(price - DIR_ATR * atr)} – {fmt_px(price + DIR_ATR * atr)} bandında kalma",
         "targets": "Bant kenarlarından tepki", "note": f"{DIR_H} saat içinde iki yönde de ±{DIR_ATR:g} ATR'ye ulaşmama"},
    ]
    return out


def commentary(r: dict, first_pct: float, add_pct: float) -> str:
    s, p, z = r["side"], r["plan"], r["sized"]
    word = "LONG" if s == 1 else "SHORT"
    fall = "düşerse" if s == 1 else "yükselirse"
    lines = []
    dtxt = {1: "YUKARI (LONG)", -1: "AŞAĞI (SHORT)", 0: "KARARSIZ"}[r["direction"]]
    lines.append(f"**Yön tahmini: {dtxt}** · Gösterge eğilimi {r['score']:+.0f}/100 · Güven: {r['confidence']}.")
    pd_ = r["prob"]["dir"]
    if np.isfinite(pd_["up"]):
        lines.append(f"Geçmişte bugüne benzeyen {r['prob']['n']} durumda fiyat {DIR_H} saat içinde "
                     f"önce **+{DIR_ATR:g} ATR**'ye %{pd_['up'] * 100:.0f}, önce **−{DIR_ATR:g} ATR**'ye %{pd_['down'] * 100:.0f} oranında gitti; "
                     f"%{pd_['range'] * 100:.0f} oranında ikisine de ulaşmadı (hata payı ±{r['prob']['moe']:.0f} puan"
                     f"{'; örnek az, dikkat' if r['prob']['n'] < 100 else ''}).")
    if r["direction"] == 0 and not r["side_forced"]:
        lines.append("⚠️ Net yön yok. Bu durumda en mantıklı hamle **beklemek**: aşağıdaki plan sadece daha güçlü taraf için hazırlandı.")
    elif r["side_forced"] and r["direction"] not in (0, s):
        lines.append(f"⚠️ {word} yönünü sen seçtin ama analiz ters yönü gösteriyor. Bu işlemde rüzgâr karşıdan eser.")
    lines.append(
        f"**Plan ({word}):** Kasanın **%{first_pct * 100:.0f}**'i ({z['m1']:.2f}$) ile {z['leverage']}x kaldıraçla "
        f"**{fmt_px(p['entry'])}** civarından gir (pozisyon büyüklüğü ≈ {z['notional1']:.0f}$).")
    if add_pct > 0:
        lines.append(
            f"Fiyat **{fmt_px(p['add'])}**'e {fall} (%{z['add_pct_move']:.1f}) kasanın **%{add_pct * 100:.0f}**'i "
            f"({z['m2']:.2f}$) kadar **ekle** → ortalama giriş {fmt_px(z['avg_b'])} olur. Bu seviye bir destek/likidite "
            f"bölgesi olduğu için seçildi; plansız, panikle ekleme yapma.")
    if z["reserve"] > 0:
        lines.append(f"Kalan **{z['reserve']:.2f}$** (%{z['reserve'] / z['capital'] * 100:.0f}) yedekte kalsın; bu işleme girmesin.")
    lines.append(
        f"**Stop: {fmt_px(p['stop'])}** (girişten %{z['stop_pct']:.2f}). Sadece ilk giriş doluysa stopta kayıp ≈ "
        f"**{z['loss_a']:.2f}$ (kasanın %{z['loss_a_pct']:.0f}'i)**; ekleme de dolduysa ≈ **{z['loss_b']:.2f}$ (%{z['loss_b_pct']:.0f})**.")
    tp_txt = " · ".join(f"TP{k + 1} {fmt_px(t['price'])} (+{t['gain_a']:.2f}$)" for k, t in enumerate(z["tps"]))
    lines.append(f"**Hedefler** (pozisyonun %30 / %40 / %30'unu kapat): {tp_txt}. Hepsi gelirse ≈ **+{z['gain_all_a']:.2f}$** "
                 f"(kasanın %{z['gain_all_a'] / z['capital'] * 100:.0f}'i). TP1 alınınca stopu girişe çek.")
    ps = r["p_side"]
    lines.append(f"Geçmiş benzer durumlarda bu planın **TP1'i stoptan önce %{ps['tp1']['win'] * 100:.0f}**, "
                 f"**TP2'si %{ps['tp2']['win'] * 100:.0f}** oranında geldi; stop önce %{ps['tp1']['loss'] * 100:.0f}.")
    if z["liq_before_stop_a"] or z["liq_before_stop_b"]:
        lines.append(f"🚨 **{z['leverage']}x çok yüksek:** likidasyon fiyatı ({fmt_px(z['liq_a'])}) stoptan ÖNCE geliyor; "
                     f"stop çalışmadan pozisyon patlar ve marjinin tamamı gider. Bu stop için en fazla **{z['safe_lev']}x** kullan.")
    else:
        lines.append(f"Likidasyon ≈ {fmt_px(z['liq_a'])} (ekleme sonrası {fmt_px(z['liq_b'])}); stop bundan önce, iyi. "
                     f"Bu stop mesafesi için güvenli üst kaldıraç ≈ {z['safe_lev']}x.")
    big = (r["liq"]["above"] if s == 1 else r["liq"]["below"])
    if big:
        lines.append(f"Tahmini en yoğun {'short' if s == 1 else 'long'} likidasyon bölgesi **{fmt_px(big[0]['price'])}** "
                     f"(%{big[0]['dist_pct']:+.1f}); fiyat oraya çekilirse hızlı hareket (squeeze) olabilir, TP3 için izle.")
    lines.append("İptal: plan, fiyat stopa ulaşırsa ya da yön tahmini ters döner ve 4H trend değişirse geçersizdir. "
                 "Bu bir eğitim çıktısıdır, yatırım tavsiyesi değildir.")
    return "\n\n".join(lines)


# ------------------------------------------------------------------ Demo kasa: hedef yolu ve Monte Carlo

def target_path(start: float, target: float, days: int) -> pd.DataFrame:
    g = (target / start) ** (1 / days) - 1
    return pd.DataFrame({"Gün": range(0, days + 1), "Hedef bakiye ($)": [round(start * (1 + g) ** d, 2) for d in range(days + 1)]}), g


def monte_carlo(start: float, target: float, days: int, trades_per_day: int, risk_pct: float, win_rate: float,
                rr: float, bust_at: float = 10.0, paths: int = 20000, seed: int = 7) -> dict:
    """Her işlemde bakiyenin risk_pct'i riske edilir: kazanırsa +rr×risk, kaybederse −risk (komisyon dahil değil).
    Hedefe ulaşan yol orada durur; bakiye bust_at altına inerse kasa bitti sayılır."""
    rng = np.random.default_rng(seed)
    n = days * trades_per_day
    bal = np.full(paths, float(start))
    done = np.zeros(paths, dtype=bool)
    hit = np.zeros(paths, dtype=bool)
    bust = np.zeros(paths, dtype=bool)
    curve = np.zeros((n + 1, 3))
    curve[0] = start
    for t in range(n):
        win = rng.random(paths) < win_rate
        step = np.where(win, 1 + risk_pct * rr, 1 - risk_pct)
        bal = np.where(done, bal, bal * step)
        hit |= (~done) & (bal >= target)
        bust |= (~done) & (bal <= bust_at)
        done = hit | bust
        curve[t + 1] = np.percentile(bal, [10, 50, 90])
    exp_r = win_rate * rr - (1 - win_rate)
    kelly = win_rate - (1 - win_rate) / rr if rr > 0 else 0
    streak = math.ceil(math.log(bust_at / start) / math.log(1 - risk_pct)) if risk_pct < 1 else 1
    return {"p_target": float(hit.mean()), "p_bust": float(bust.mean()), "median": float(np.median(bal)),
            "p10": float(np.percentile(bal, 10)), "p90": float(np.percentile(bal, 90)), "curve": curve, "n_trades": n,
            "exp_r": exp_r, "kelly": max(kelly, 0.0), "streak_to_bust": streak}
