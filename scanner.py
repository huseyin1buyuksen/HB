"""
Canlı tarama: her coin için 15m/1h/4h veri + canlı piyasa verisi -> LONG / SHORT / İŞLEM YOK kararı ve sinyal kartı.
Hem web sitesi (app.py) hem Telegram botu (signal_bot.py) bunu kullanır.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config as C
import quant as Q

BARS = {"15m": 400, "1h": 300, "4h": 300}


def fmt(x, nd=6):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    return f"{x:.{nd}g}"


def scan(fetch, live_fn, rules_fn, symbols: list[str], threshold: float, capital: float | None,
         risk_pct: float, risk_state: dict, now_ms: int, stale_fn, progress=None) -> dict:
    """fetch(sym, tf, bars) -> DataFrame; live_fn(sym) -> canlı veri sözlüğü; rules_fn(sym) -> kontrat kuralları."""
    btc15, btc1h, eth1h = fetch(C.BTC, "15m", BARS["15m"]), fetch(C.BTC, "1h", BARS["1h"]), fetch(C.ETH, "1h", BARS["1h"])
    results, errors = [], []
    for n, sym in enumerate(symbols):
        if progress:
            progress(n / max(1, len(symbols)), sym)
        try:
            d15, d1h, d4h = fetch(sym, "15m", BARS["15m"]), fetch(sym, "1h", BARS["1h"]), fetch(sym, "4h", BARS["4h"])
            if len(d15) < 250:
                errors.append(f"{sym}: yetersiz geçmiş ({len(d15)} mum)")
                continue
            f = Q.build_features(d15, d1h, d4h, btc15=btc15, btc1h=btc1h, eth1h=eth1h)
        except Exception as e:
            errors.append(f"{sym}: {e}")
            continue
        row = f.iloc[-1]
        stale = stale_fn(d15, "15m", now_ms)
        # canlı veriyi yalnızca teknik olarak aday olanlar için çek (API yükünü azaltır)
        need_live = any(bool(row[f"{P}_gates_ok"]) and row[f"{P}_tech"] + 15 >= threshold for P in ("L", "S"))
        live = {"stale": stale}
        if need_live:
            try:
                live.update(live_fn(sym))
            except Exception as e:
                live["live_error"] = str(e)
        d = Q.decide(f, live, threshold, risk_state)
        d.update({"symbol": sym, "live_data": live, "features": f, "bar_time": f.index[-1]})
        if d["decision"] != "İŞLEM YOK":
            d["plan"] = Q.trade_plan(d, capital, risk_pct, rules_fn(sym))
        results.append(d)

    # aynı yöndeki yüksek korelasyonlu sinyallerden zayıf olanı ele; toplam açık risk limitini uygula
    sig = sorted([r for r in results if r["decision"] != "İŞLEM YOK"], key=lambda r: -r["score"])
    slots = int(np.floor((C.MAX_OPEN_RISK_PCT * 100 - risk_state.get("open_risk_pct", 0)) / (risk_pct * 100) + 1e-9))
    kept = []
    for r in sig:
        rets = r["features"]["close"].pct_change().tail(96)
        clash = next((k for k in kept if k["side"] == r["side"] and
                      rets.corr(k["features"]["close"].pct_change().tail(96)) > C.MAX_CORR), None)
        if clash is not None:
            _demote(r, f"{clash['symbol']} ile yüksek korelasyon (aynı yön) — portföy riski sınırlandı")
        elif len(kept) >= slots:
            _demote(r, "Toplam açık risk limiti (%2) dolu — daha yüksek puanlı sinyaller öncelikli")
        else:
            kept.append(r)
    return {"results": results, "errors": errors, "btc15": btc15, "btc_dir": _btc_dir(btc1h), "scanned_at": pd.Timestamp.now(tz="UTC")}


def _demote(r, reason):
    r["decision"] = "İŞLEM YOK"
    r["blockers"] = r.get("blockers", []) + [reason]
    r.pop("plan", None)


def _btc_dir(btc1h):
    import indicators as I
    t = I.htf_trend(btc1h).iloc[-1]
    return int(t["dir"])


def card(r: dict, risk_pct: float) -> str:
    """Dokümandaki ZORUNLU SİNYAL ÇIKTISI formatı."""
    row, live = r["row"], r["live_data"]
    base = r["symbol"].split("/")[0]
    ts = r["bar_time"] + pd.Timedelta(minutes=15)
    src = f"MEXC USDT-M Futures (ccxt) · 15m mum kapanışı {ts:%Y-%m-%d %H:%M} UTC ({(ts + pd.Timedelta(hours=3)):%H:%M} TSİ)"
    if live.get("ts") is not None:
        src += f" · canlı veri {live['ts']:%H:%M:%S} UTC"
    lines = [f"COIN: {base}USDT", f"KARAR: {r['decision']}",
             f"SİNYAL PUANI: {r['score']:.0f}/100 (teknik {r['tech']:.0f}/85 + canlı {r['live']:.0f}/15; eşik {r['threshold']})",
             f"GÜNCEL FİYAT: {fmt(float(row['close']))} USDT (son kapanan 15m mum)"]
    if r["decision"] == "İŞLEM YOK":
        reasons = r["blockers"] + r["failed"]
        lines += ["", "NEDEN İŞLEM YOK:"] + [f"- {x}" for x in reasons[:6]]
        lines += ["", f"BEKLENEN TETİKLEYİCİ: {_awaited(r)}"]
        if r.get("missing"):
            lines.append(f"EKSİK CANLI VERİ: {', '.join(r['missing'])}")
        lines += [f"VERİ KAYNAĞI VE SAATİ: {src}", "SONUÇ: Şartlar oluşana kadar işlem açılmaz."]
        return "\n".join(lines)
    p = r["plan"]
    sd = r["sd"]
    tp_lines = [f"TP{k + 1}: {fmt(px)} — çıkış %{int(w * 100)} (net {rn:.2f} R)" for k, (px, rn, w) in enumerate(p["tps"])]
    size = (f"{p['contracts']:.4g} kontrat ≈ {p['qty']:.6g} {base} (~{p['notional']:,.0f} USDT nominal; "
            f"stopta beklenen net zarar ≈ {p['loss_at_stop']:,.2f} USDT)") if "contracts" in p else "Sermaye girilmedi"
    if p.get("size_note"):
        size += f" — {p['size_note']}"
    comps = r["comps"]
    lines += [
        f"ENTRY BÖLGESİ: {fmt(min(p['zone']))}–{fmt(max(p['zone']))} (limit) · alternatif tetikleyici: sonraki 15m mumu "
        f"{fmt(p['alt_trigger'])} {'altında' if sd == 1 else 'üstünde'} açılırsa piyasa emri",
        f"STOP LOSS: {fmt(p['stop'])} (yapısal swing ± {C.STOP_ATR_BUFFER} ATR tampon; mesafe %{p['stop_pct']:.2f})",
        *tp_lines,
        f"RİSK/GETİRİ: Net {p['rr']:.2f} R (ağırlıklı; maliyet {p['cost_r']:.2f} R dahil)",
        f"ÖNERİLEN RİSK: Sermayenin %{risk_pct * 100:.1f}'i · en fazla {p['max_leverage']}x izole kaldıraç "
        f"(tahmini likidasyon {fmt(p['liq_est'])}, stopun ötesinde)",
        f"POZİSYON MİKTARI: {size}",
        f"SİNYAL GEÇERLİLİĞİ: {C.SIGNAL_VALID_BARS} mum (~{C.SIGNAL_VALID_BARS * 15} dk). İptal: 15m kapanış "
        f"{fmt(p['invalid'])} {'altında' if sd == 1 else 'üstünde'} ya da fiyat {fmt(p['chase'])} seviyesini girişsiz geçerse",
        f"{r['side']} GEREKÇESİ: " + "; ".join(_evidence(r)),
        "RİSKLER: " + "; ".join(_risks(r)),
        f"PUAN DÖKÜMÜ: trend {comps['trend']:.0f}/20 · yapı/SMC {comps['yapi']:.0f}/20 · hacim {comps['hacim']:.0f}/10 + "
        f"OI/funding {comps.get('funding', 0) + comps.get('oi', 0):.0f}/5 · momentum {comps['momentum']:.0f}/15 · "
        f"likidite {comps.get('likidite', 0):.0f}/10 · BTC/ETH {comps['piyasa']:.0f}/10 · volatilite+R/R {comps['vol_rr']:.0f}/10",
        f"VERİ KAYNAĞI VE SAATİ: {src}",
        f"SONUÇ: {base}USDT için {r['side']} adayı; giriş yalnızca bölge içinde ve iptal koşulu oluşmadan, "
        f"stop emriyle birlikte. Kesin kazanç değildir.",
    ]
    if r.get("missing"):
        lines.insert(-2, f"EKSİK CANLI VERİ: {', '.join(r['missing'])} (bu kalemlerden puan verilmedi)")
    return "\n".join(lines)


def _awaited(r):
    row, P = r["row"], r["P"]
    if r["sd"] == 1:
        return f"15m kapanışın son swing tepesi {fmt(float(row['sh']))} üstünde gelmesi (BOS) ve diğer şartların sağlanması"
    return f"15m kapanışın son swing dibi {fmt(float(row['sl']))} altında gelmesi (BOS) ve diğer şartların sağlanması"


def _evidence(r):
    row, sd, P = r["row"], r["sd"], r["P"]
    ev = []
    if row["h4_dir"] == sd and row["h1_dir"] == sd:
        ev.append("4H ve 1H trend aynı yönde (EMA21/50)")
    elif row["h1_dir"] == sd:
        ev.append("1H trend aynı yönde")
    ev.append(("CHoCH" if bool(row["choch_up" if sd == 1 else "choch_dn"]) else "BOS") +
              f": 15m kapanış {fmt(float(row['break_level_up' if sd == 1 else 'break_level_dn']))} seviyesini kırdı")
    if row["rel_vol"] >= 1.2:
        ev.append(f"hacim ortalamanın {row['rel_vol']:.1f} katı")
    ev.append(f"RSI14 {row['rsi14']:.0f}, MACD histogram {'pozitif' if row['macd_hist'] > 0 else 'negatif'}")
    if bool(row["sweep_bull" if sd == 1 else "sweep_bear"]):
        ev.append("yakın zamanda likidite süpürmesi")
    return ev[:3]


def _risks(r):
    row, sd, live = r["row"], r["sd"], r["live_data"]
    rk = []
    room = float(row[f"{r['P']}_room"])
    if np.isfinite(room):
        rk.append(f"{'üstte' if sd == 1 else 'altta'} {fmt(float(row['close']) + sd * room)} likidite/direnç seviyesi ({room / float(row['atr']):.1f} ATR)")
    if r["comps"]["piyasa"] < 5:
        rk.append("BTC/ETH tam destek vermiyor")
    fr = live.get("funding")
    if fr is not None and sd * fr > 0:
        rk.append(f"{'long' if sd == 1 else 'short'} pozisyonlar funding ödüyor (%{abs(fr) * 100:.3f}/8s)")
    rk.append("sahte kırılım (fiyat kırılan seviyenin gerisine kapanırsa sinyal geçersiz)")
    rk.append("likidasyon/haber verisi kontrol edilmedi")
    return rk[:3]
