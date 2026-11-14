"""
QUANT FUTURES — puanlama, zorunlu şartlar ve işlem planı.

Puan tablosu (100):
  Çoklu zaman dilimi trend uyumu ... 20  (teknik, backtest edilir)
  Piyasa yapısı ve SMC ............. 20  (teknik, backtest edilir)
  Hacim ............................ 10  (teknik, backtest edilir)
  OI + funding ..................... 5   (CANLI veri, geçmişi yok)
  Momentum göstergeleri ............ 15  (teknik, backtest edilir)
  Likidite ve emir defteri ......... 10  (CANLI veri, geçmişi yok)
  BTC/ETH piyasa uyumu ............. 10  (teknik, backtest edilir)
  Volatilite ve risk/getiri ........ 10  (teknik, backtest edilir)
Teknik kısım 85 puan; canlı kısım 15 puan. Backtest teknik puanı 100'e ölçekleyerek test eder.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

import config as C
import indicators as I

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

SIDES = {"LONG": 1, "SHORT": -1}
TECH_MAX = 85


def cost_per_unit(price):
    """Birim başına tahmini işlem maliyeti: giriş+çıkış komisyonu, kayma ve bir funding periyodu."""
    return price * (2 * C.FEE_RATE + 2 * C.SLIPPAGE + abs(C.FUNDING_PER_8H))


def build_features(df15: pd.DataFrame, h1: pd.DataFrame | None = None, h4: pd.DataFrame | None = None,
                   btc15: pd.DataFrame | None = None, btc1h: pd.DataFrame | None = None,
                   eth1h: pd.DataFrame | None = None) -> pd.DataFrame:
    """15m mum serisinden tüm göstergeleri, teknik puanları, zorunlu şartları ve işlem planını hesaplar.
    h1/h4 verilmezse 15m'den üretilir (backtest böyle çalışır). Satır başına hesaplanan her şey o mumun
    KAPANIŞINDA bilinen bilgiyle sınırlıdır."""
    b = I.TF_MIN[C.BASE_TF]
    h1 = h1 if h1 is not None else I.resample(df15, "1h")
    h4 = h4 if h4 is not None else I.resample(df15, "4h")
    f = pd.DataFrame(index=df15.index)
    o, h, l, c, v = (df15[k] for k in ("open", "high", "low", "close", "volume"))
    f["open"], f["high"], f["low"], f["close"], f["volume"] = o, h, l, c, v

    for n in C.EMAS:
        f[f"ema{n}"] = I.ema(c, n)
    f["rsi7"], f["rsi14"] = I.rsi(c, C.RSI_FAST), I.rsi(c, C.RSI_SLOW)
    f["macd"], f["macd_sig"], f["macd_hist"] = I.macd(c, C.MACD_FAST, C.MACD_SLOW, C.MACD_SIGNAL)
    f["adx"], f["pdi"], f["mdi"] = I.adx(df15, C.ADX_LEN)
    f["atr"] = I.atr(df15, C.ATR_LEN)
    f["atr_pct"] = f["atr"] / c
    _, _, _, bbw = I.bollinger(c, C.BB_LEN, C.BB_STD)
    f["bb_width"], f["bb_expanding"] = bbw, bbw > bbw.rolling(20).mean()
    f["vwap"] = I.daily_vwap(df15)
    f["supertrend"] = I.supertrend(df15, C.ST_LEN, C.ST_MULT)
    f["rel_vol"] = v / v.rolling(20).mean()
    up_v = v.where(c > o, 0).rolling(10).sum()
    dn_v = v.where(c < o, 0).rolling(10).sum()
    tot = v.rolling(10).sum().replace(0, np.nan)
    f["up_share"], f["dn_share"] = up_v / tot, dn_v / tot

    s = I.smc(df15, C.SWING_K, C.RECENT_BARS, f["atr"])
    f = f.join(s)
    f = f.join(I.period_levels(df15))

    t1 = I.align_htf(I.htf_trend(h1), f.index, 60, b).add_prefix("h1_")
    t4 = I.align_htf(I.htf_trend(h4), f.index, 240, b).add_prefix("h4_")
    f = f.join(t1).join(t4)
    if btc1h is None and btc15 is not None:
        btc1h = I.resample(btc15, "1h")
    if btc1h is not None:
        f = f.join(I.align_htf(I.htf_trend(btc1h), f.index, 60, b)[["dir"]].rename(columns={"dir": "btc_dir"}))
    else:
        f["btc_dir"] = np.nan
    if eth1h is not None:
        f = f.join(I.align_htf(I.htf_trend(eth1h), f.index, 60, b)[["dir"]].rename(columns={"dir": "eth_dir"}))
    else:
        f["eth_dir"] = np.nan
    f["btc_ret1h"] = btc15["close"].pct_change(4).reindex(f.index, method="ffill") if btc15 is not None else np.nan

    for name, sd in SIDES.items():
        _score_side(f, name, sd)
    return f


def _score_side(f: pd.DataFrame, name: str, sd: int):
    L = sd == 1
    c, atr = f["close"], f["atr"]
    P = name[0]  # "L" / "S"

    # 1) Çoklu zaman dilimi trend uyumu (20)
    trend = (np.where(f["h4_dir"] == sd, 7, np.where(f["h4_partial"] == sd, 3.5, 0))
             + np.where(f["h1_dir"] == sd, 6, np.where(f["h1_partial"] == sd, 3, 0)))
    stack = ((f["ema9"] > f["ema21"]) & (f["ema21"] > f["ema50"])) if L else ((f["ema9"] < f["ema21"]) & (f["ema21"] < f["ema50"]))
    di_ok = (f["h1_pdi"] > f["h1_mdi"]) if L else (f["h1_mdi"] > f["h1_pdi"])
    trend = trend + np.where(stack, 4, 0) + np.where((f["h1_adx"] > 20) & di_ok, 3, 0)

    # 2) Piyasa yapısı ve SMC (20)
    struct = f["struct_bull"] if L else f["struct_bear"]
    bos = f["bos_up"] if L else f["bos_dn"]
    choch = f["choch_up"] if L else f["choch_dn"]
    sweep = f["sweep_bull"] if L else f["sweep_bear"]
    fvg = f["fvg_bull"] if L else f["fvg_bear"]
    # karşıdaki en yakın likidite/direnç seviyesine mesafe (önceki gün/hafta, son swing)
    lv = np.vstack([f[k].to_numpy() for k in (("pdh", "pwh", "sh") if L else ("pdl", "pwl", "sl"))])
    cc = c.to_numpy()
    dist = np.where(sd * (lv - cc) > 0, sd * (lv - cc), np.inf).min(axis=0)
    f[f"{P}_room"] = dist
    structure = (np.where(struct, 6, 0) + np.where(bos, 5, 0) + np.where(sweep, 3, 0) + np.where(fvg, 3, 0)
                 + np.where(dist >= atr.to_numpy(), 3, 0))

    # 3) Hacim (10) — taker verisi yok; mum yönüne göre hacim payı kullanılır
    share = f["up_share"] if L else f["dn_share"]
    volume = (np.where(f["rel_vol"] >= 1.5, 6, np.where(f["rel_vol"] >= 1.2, 3, 0))
              + np.where(share >= 0.6, 4, np.where(share >= 0.5, 2, 0)))

    # 4) Momentum (15)
    r14, r7, hist = f["rsi14"], f["rsi7"], f["macd_hist"]
    if L:
        rsi_pts = np.where((r14 > 50) & (r14 < 70), 4, np.where(r14 >= 70, 1, 0)) + np.where(r7 > 50, 2, 0)
        macd_pts = np.where((hist > 0) & (hist > hist.shift(1)), 4, np.where(hist > 0, 2, 0)) + np.where(f["macd"] > f["macd_sig"], 2, 0)
    else:
        rsi_pts = np.where((r14 < 50) & (r14 > 30), 4, np.where(r14 <= 30, 1, 0)) + np.where(r7 < 50, 2, 0)
        macd_pts = np.where((hist < 0) & (hist < hist.shift(1)), 4, np.where(hist < 0, 2, 0)) + np.where(f["macd"] < f["macd_sig"], 2, 0)
    momentum = rsi_pts + macd_pts + np.where(f["supertrend"] == sd, 3, 0)

    # 5) BTC/ETH uyumu (10)
    market = (np.where(f["btc_dir"] == sd, 5, np.where(f["btc_dir"] == 0, 2, 0))
              + np.where(f["eth_dir"] == sd, 3, np.where(f["eth_dir"] == 0, 1, 0))
              + np.where(sd * f["btc_ret1h"] > -0.005, 2, 0))

    # ---- işlem planı: yapısal stop, net R'li hedefler (dirençle sınırlı)
    swing = f["sl"] if L else f["sh"]
    stop = swing - sd * C.STOP_ATR_BUFFER * atr
    risk = sd * (c - stop)
    cpu = cost_per_unit(c)
    valid_risk = (risk > 0) & (risk >= C.STOP_MIN_ATR * atr) & (risk <= C.STOP_MAX_ATR * atr)
    tps, nets = [], []
    cap = pd.Series(dist, index=f.index) - 0.1 * atr           # karşıdaki likidite seviyesinin hemen önü
    prev = None
    for k in (1, 2, 3):
        raw = k * risk + cpu                                   # hedef net ~kR olacak şekilde maliyet eklenir
        if k < 3:
            gross = np.minimum(raw, cap)                       # TP1/TP2 dirençten önce alınır
        else:                                                  # TP3: en az 3R; seviye 3R-5R arasındaysa ona uzanır
            gross = np.where((cap > raw) & (cap <= 5 * risk + cpu), cap, raw)
            gross = pd.Series(gross, index=f.index)
        if prev is not None:
            gross = np.maximum(gross, prev)
        prev = gross
        tps.append(c + sd * gross)
        nets.append((gross - cpu) / risk)
    rr_net = sum(w * n for w, n in zip(C.TP_SPLIT, nets))
    f[f"{P}_stop"], f[f"{P}_risk"] = stop, risk
    for k in range(3):
        f[f"{P}_tp{k + 1}"], f[f"{P}_r{k + 1}"] = tps[k], nets[k]
    f[f"{P}_rr"] = rr_net
    f[f"{P}_cost_r"] = cpu / risk

    # 6) Volatilite ve risk/getiri (10)
    vwap_side = (c > f["vwap"]) if L else (c < f["vwap"])
    vol_rr = (np.where((f["atr_pct"] >= 0.003) & (f["atr_pct"] <= 0.03), 3, 0) + np.where(f["bb_expanding"], 2, 0)
              + np.where(vwap_side, 2, 0) + np.where(valid_risk & (rr_net >= 2), 3, 0))

    comps = {"trend": trend, "yapi": structure, "hacim": volume, "momentum": momentum, "piyasa": market, "vol_rr": vol_rr}
    for k, val in comps.items():
        f[f"{P}_{k}"] = val
    f[f"{P}_tech"] = sum(comps.values())
    f[f"{P}_tech100"] = f[f"{P}_tech"] / TECH_MAX * 100

    # ---- ZORUNLU ŞARTLAR (hepsi sağlanmadan sinyal yok)
    f[f"{P}_g_trend"] = (f["h4_dir"] == sd) | (f["h1_dir"] == sd)
    f[f"{P}_g_struct"] = struct | choch
    f[f"{P}_g_trigger"] = bos
    f[f"{P}_g_momentum"] = ((r14 > 50) & (hist > 0)) if L else ((r14 < 50) & (hist < 0))
    f[f"{P}_g_volume"] = f["rel_vol"] >= 1.0
    f[f"{P}_g_btc"] = ~(f["btc_dir"] == -sd)
    f[f"{P}_g_stop"] = valid_risk
    f[f"{P}_g_rr"] = valid_risk & (rr_net >= C.MIN_NET_RR)
    gates = [g for g in GATE_NAMES]
    f[f"{P}_gates_ok"] = np.logical_and.reduce([f[f"{P}_{g}"].fillna(False).to_numpy(dtype=bool) for g in gates])
    # giriş bölgesi ve iptal seviyesi
    lvl = f["break_level_up"] if L else f["break_level_dn"]
    f[f"{P}_zone_lo"] = np.where(L, np.maximum(lvl, c - 0.25 * atr), c)
    f[f"{P}_zone_hi"] = np.where(L, c, np.minimum(lvl, c + 0.25 * atr))
    f[f"{P}_invalid"] = lvl - sd * 0.1 * atr


GATE_NAMES = ["g_trend", "g_struct", "g_trigger", "g_momentum", "g_volume", "g_btc", "g_stop", "g_rr"]
GATE_TEXT = {
    "g_trend": "4H veya 1H trend {dir} yönlü değil",
    "g_struct": "15M {dir} piyasa yapısı (HH/HL, LH/LL veya CHoCH) doğrulanmadı",
    "g_trigger": "Tetikleyici yok: kapanış son swing seviyesini kırmadı (BOS)",
    "g_momentum": "RSI/MACD {dir} momentumu desteklemiyor",
    "g_volume": "Hacim hareketi doğrulamıyor (ortalamanın altında)",
    "g_btc": "BTC 1H trendi işlemle çelişiyor",
    "g_stop": "Stop teknik olarak mantıklı belirlenemiyor (swing çok yakın/uzak)",
    "g_rr": f"Net risk/getiri yetersiz (< {C.MIN_NET_RR} R)",
}


# ------------------------------------------------------------------ canlı veri puanı ve karar

def live_score(sd: int, live: dict) -> tuple[float, dict, list[str], list[str]]:
    """OI + funding (5) ve likidite/emir defteri (10). Dönüş: puan, detay, eksik veri notları, İŞLEM YOK sebepleri."""
    pts, det, missing, blockers = 0.0, {}, [], []
    fr = live.get("funding")
    if fr is None:
        missing.append("funding")
    else:
        crowded = sd * fr > C.MAX_FUNDING_WITH
        if abs(fr) > C.EXTREME_FUNDING:
            blockers.append(f"Funding aşırı ({fr * 100:.3f}%) — kalabalık pozisyonlanma")
        p = 0 if crowded else 3
        pts += p
        det["funding"] = p
    oi_chg = live.get("oi_change")
    if oi_chg is None:
        missing.append("OI değişimi")
        det["oi"] = 0
    else:
        # fiyatla aynı yönde artan OI trendi destekler
        p = 2 if oi_chg > 0 else 0
        pts += p
        det["oi"] = p
    spread = live.get("spread_pct")
    if spread is None:
        missing.append("spread")
    else:
        if spread > C.MAX_SPREAD_PCT:
            blockers.append(f"Spread olağan dışı yüksek (%{spread:.3f})")
        pts += 4 if spread <= C.GOOD_SPREAD_PCT else (2 if spread <= C.MAX_SPREAD_PCT else 0)
    depth = live.get("depth_usdt")
    if depth is None:
        missing.append("emir defteri derinliği")
    else:
        if depth < C.MIN_DEPTH_USDT / 2:
            blockers.append(f"Likidite yetersiz (±%0.5 derinlik {depth:,.0f} USDT)")
        pts += 4 if depth >= C.MIN_DEPTH_USDT else (2 if depth >= C.MIN_DEPTH_USDT / 2 else 0)
    imb = live.get("imbalance")  # (bid - ask) / (bid + ask)
    if imb is not None and sd * imb > 0.1:
        pts += 2
    qv = live.get("quote_vol_24h")
    if qv is not None and qv < C.MIN_QUOTE_VOL_24H:
        blockers.append(f"24s hacim düşük ({qv:,.0f} USDT)")
    if live.get("stale"):
        blockers.append("Veri güncel değil (son kapanan mum gecikmeli)")
    det["likidite"] = pts - det.get("funding", 0) - det.get("oi", 0)
    return pts, det, missing, blockers


def decide(f: pd.DataFrame, live: dict, threshold: float | None = None, risk_state: dict | None = None) -> dict:
    """Son kapanan mum için LONG / SHORT / İŞLEM YOK kararı ve sinyal kartı verisi."""
    threshold = C.SCORE_THRESHOLD if threshold is None else threshold
    row = f.iloc[-1]
    best = None
    for name, sd in SIDES.items():
        P = name[0]
        lp, ldet, missing, blockers = live_score(sd, live)
        total = float(row[f"{P}_tech"]) + lp
        failed = [GATE_TEXT[g].format(dir="yükseliş" if sd == 1 else "düşüş") for g in GATE_NAMES if not bool(row[f"{P}_{g}"])]
        cand = {"side": name, "sd": sd, "P": P, "score": total, "tech": float(row[f"{P}_tech"]), "live": lp,
                "comps": {k: float(row[f"{P}_{k}"]) for k in ("trend", "yapi", "hacim", "momentum", "piyasa", "vol_rr")} | ldet,
                "failed": failed, "blockers": blockers, "missing": missing}
        if risk_state:
            if risk_state.get("daily_loss_pct", 0) >= C.DAILY_MAX_LOSS_PCT * 100:
                cand["blockers"].append("Günlük maksimum zarar limiti doldu")
            if risk_state.get("open_risk_pct", 0) + risk_state.get("risk_pct", C.RISK_PCT * 100) > C.MAX_OPEN_RISK_PCT * 100 + 1e-9:
                cand["blockers"].append("Toplam açık pozisyon riski limiti (%2) aşılır")
        cand["ok"] = not failed and not blockers and total >= threshold
        if best is None or (cand["ok"], -len(cand["failed"]), cand["score"]) > (best["ok"], -len(best["failed"]), best["score"]):
            best = cand
    best["threshold"] = threshold
    best["row"] = row
    if not best["ok"] and not best["failed"] and not best["blockers"]:
        best["failed"] = [f"Sinyal puanı eşiğin altında ({best['score']:.0f} < {threshold})"]
    best["decision"] = best["side"] if best["ok"] else "İŞLEM YOK"
    return best


def trade_plan(d: dict, capital: float | None, risk_pct: float, market: dict | None = None) -> dict:
    """Sinyal kartı için entry/stop/TP/miktar/kaldıraç hesabı (sadece karar LONG/SHORT ise)."""
    r, P, sd = d["row"], d["P"], d["sd"]
    entry = float(r["close"])
    stop, risk = float(r[f"{P}_stop"]), float(r[f"{P}_risk"])
    cpu = float(cost_per_unit(entry))
    plan = {
        "price": entry, "zone": (float(r[f"{P}_zone_lo"]), float(r[f"{P}_zone_hi"])),
        "alt_trigger": entry + sd * 0.25 * float(r["atr"]),
        "invalid": float(r[f"{P}_invalid"]), "chase": entry + sd * C.CHASE_LIMIT_ATR * float(r["atr"]),
        "stop": stop, "stop_pct": risk / entry * 100,
        "tps": [(float(r[f"{P}_tp{k}"]), float(r[f"{P}_r{k}"]), C.TP_SPLIT[k - 1]) for k in (1, 2, 3)],
        "rr": float(r[f"{P}_rr"]), "cost_r": cpu / risk,
    }
    stop_frac = risk / entry
    lev = max(1, min(C.MAX_LEVERAGE_CAP, int(1 / (2 * stop_frac + 0.005))))  # likidasyon ≈ stop mesafesinin 2 katı ötede
    plan["max_leverage"] = lev
    plan["liq_est"] = entry - sd * entry * (1 / lev - 0.005)
    if capital:
        risk_amt = capital * risk_pct
        qty = risk_amt / (risk + cpu)
        cs = (market or {}).get("contract_size") or 1.0
        step = (market or {}).get("amount_step") or 1.0
        contracts = np.floor(qty / cs / step) * step
        plan.update({"risk_amt": risk_amt, "qty": contracts * cs, "contracts": contracts,
                     "notional": contracts * cs * entry, "loss_at_stop": contracts * cs * (risk + cpu)})
        min_amt = (market or {}).get("min_amount")
        if min_amt and contracts < min_amt:
            plan["size_note"] = f"Hesaplanan miktar borsanın minimum emir miktarının ({min_amt} kontrat) altında."
    return plan
