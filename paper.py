"""
DEMO HESAP (paper trading) — gerçek para ve borsa hesabı KULLANMAZ.
Gerçek MEXC fiyatlarıyla sanal bir kasayı yönetir:
  • Her yeni sinyalde: boştaki kasanın %50'si marjinle giriş, planlı ekleme seviyesinde boştaki kasanın %25'i
  • Kaldıraç 10x; likidasyon stoptan önce gelecekse stopun çalışabileceği güvenli kaldıraca düşürülür
  • TP1 %30 · TP2 %40 · TP3 kalan; TP1 sonrası stop girişe çekilir ve bekleyen ekleme iptal edilir
  • Komisyon (giriş/çıkış) ve funding maliyeti düşülür; aynı mumda hem aleyhte hem lehte seviye varsa önce aleyhte sayılır
Pozisyonlar 5 dakikalık KAPANMIŞ mumlarla takip edilir.
"""
from __future__ import annotations

import json
import os
import time

import pandas as pd

import config as C

START = 100.0
FIRST_PCT, ADD_PCT = 0.50, 0.25
MAX_LEV = 10
MIN_MARGIN = 2.0
FEE = C.FEE_RATE
FUND_8H = C.FUNDING_PER_8H
TSI = pd.Timedelta(hours=3)


def fmt(x):
    import analysis as A
    return A.fmt_px(x)


def now_ms() -> int:
    return int(time.time() * 1000)


def tsi(ms: int) -> str:
    return (pd.Timestamp(ms, unit="ms", tz="UTC") + TSI).strftime("%d.%m %H:%M")


# ------------------------------------------------------------------ durum

def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st.setdefault("start", START)
    st.setdefault("cash", START)
    st.setdefault("positions", [])
    st.setdefault("closed", [])
    st.setdefault("equity_curve", [])
    st.setdefault("day", None)
    st.setdefault("day_start_equity", START)
    st.setdefault("created", now_ms())
    st.setdefault("next_id", 1)
    return st


def save(path: str, st: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    st["closed"] = st["closed"][-300:]
    st["equity_curve"] = st["equity_curve"][-3000:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


def unrealized(p: dict, px: float) -> float:
    return p["side"] * (px - p["avg"]) * p["qty"]


def equity(st: dict, prices: dict | None = None) -> float:
    eq = st["cash"]
    for p in st["positions"]:
        px = (prices or {}).get(p["symbol"], p.get("last_px", p["avg"]))
        eq += p["margin"] + max(unrealized(p, px), -p["margin"])
    return eq


# ------------------------------------------------------------------ açılış

def open_position(st: dict, sig: dict, analysis_result: dict) -> tuple[dict | None, str]:
    """sig: tarayıcı sonucu (symbol, decision, score); analysis_result: analysis.analyze(...) çıktısı (plan aynı yönde)."""
    import analysis as A
    sym, sd = sig["symbol"], (1 if sig["decision"] == "LONG" else -1)
    base = sym.split("/")[0]
    if any(p["symbol"] == sym for p in st["positions"]):
        return None, f"ℹ️ DEMO: {base} {sig['decision']} sinyali geldi ama bu coinde zaten açık demo pozisyon var, yeni işlem açılmadı."
    cash = st["cash"]
    if cash * FIRST_PCT < MIN_MARGIN:
        return None, (f"ℹ️ DEMO: {base} {sig['decision']} sinyali atlandı: boştaki kasa {cash:.2f}$, "
                      f"%50'si ({cash * FIRST_PCT:.2f}$) minimum {MIN_MARGIN:.0f}$ marjinin altında.")
    plan = analysis_result["plans"][sd]
    probe = A.size_plan(plan, cash, MAX_LEV, FIRST_PCT, ADD_PCT)
    lev = MAX_LEV if not (probe["liq_before_stop_a"] or probe["liq_before_stop_b"]) else max(1, min(MAX_LEV, probe["safe_lev"]))
    z = A.size_plan(plan, cash, lev, FIRST_PCT, ADD_PCT)
    e1 = plan["entry"]
    fee = z["q1"] * e1 * FEE
    p = {"id": st["next_id"], "symbol": sym, "base": base, "side": sd, "lev": lev, "opened": now_ms(),
         "last_ms": now_ms() - now_ms() % 300_000, "entry": e1, "avg": e1, "qty": z["q1"], "qty0": z["q1"],
         "margin": z["m1"], "add_px": plan["add"], "add_q": z["q2"], "add_margin": z["m2"], "add_done": False,
         "add_cancel": False, "stop": plan["stop"], "stop0": plan["stop"], "tps": plan["tps"], "tp_done": [False] * 3,
         "liq": z["liq_a"], "realized": -fee, "fees": fee, "funding": 0.0, "score": sig.get("score"),
         "dir_conf": analysis_result["confidence"], "last_px": e1}
    st["next_id"] += 1
    st["cash"] -= z["m1"] + fee
    st["positions"].append(p)
    word = "LONG 🟢" if sd == 1 else "SHORT 🔴"
    lev_note = "" if lev == MAX_LEV else f" (10x'te likidasyon stoptan önce geliyordu → güvenli {lev}x kullanıldı)"
    loss_a = z["loss_a"]
    tps = "\n".join(f"  TP{k + 1}: {fmt(tp)} → pozisyonun %{int(w * 100)}'ini kapat (+{t['gain_a']:.2f}$)"
                    for k, (tp, w, t) in enumerate(zip(plan["tps"], C.TP_SPLIT, z["tps"])))
    msg = (f"📒 DEMO İŞLEM AÇILDI #{p['id']} — {base}USDT {word}\n\n"
           f"Neden: sinyal puanı {sig.get('score', 0):.0f}/100 · analiz güveni: {analysis_result['confidence'].split(' (')[0]}\n"
           f"Giriş: {fmt(e1)} · kasanın %50'si = {z['m1']:.2f}$ marjin · {lev}x{lev_note} → {z['notional1']:.0f}$ pozisyon\n"
           f"Planlı ekleme: {fmt(plan['add'])}'{'e düşerse' if sd == 1 else 'e yükselirse'} boştaki kasanın %25'i = {z['m2']:.2f}$ "
           f"(ortalama {fmt(z['avg_b'])} olur)\n"
           f"Yedek: {cash - z['m1'] - z['m2']:.2f}$ işleme girmez\n"
           f"Stop: {fmt(plan['stop'])} (girişten %{z['stop_pct']:.2f}) → stopta kayıp ≈ {loss_a:.2f}$, ekleme dolarsa ≈ {z['loss_b']:.2f}$\n"
           f"Hedefler:\n{tps}\n"
           f"TP1 gelince stop girişe çekilir.\n"
           f"Likidasyon: {fmt(z['liq_a'])}\n"
           f"Komisyon: {fee:.3f}$ · Kasa (boşta): {st['cash']:.2f}$")
    return p, msg


# ------------------------------------------------------------------ takip

def _close(st: dict, p: dict, q: float, px: float, why: str) -> float:
    q = min(q, p["qty"])
    pnl = p["side"] * (px - p["avg"]) * q
    fee = q * px * FEE
    hours = (now_ms() - p["opened"]) / 3_600_000
    fund = q * p["avg"] * FUND_8H * hours / 8
    rel = p["margin"] * q / p["qty"] if p["qty"] > 0 else p["margin"]
    st["cash"] += rel + pnl - fee - fund
    p["margin"] -= rel
    p["qty"] -= q
    p["realized"] += pnl - fee - fund
    p["fees"] += fee
    p["funding"] += fund
    return pnl - fee - fund


def _finish(st: dict, p: dict, reason: str, px: float, ts: int):
    rec = {k: p[k] for k in ("id", "symbol", "base", "side", "lev", "opened", "entry", "avg", "stop0", "tps", "tp_done",
                             "add_done", "realized", "fees", "funding", "score")}
    rec.update({"closed": ts, "reason": reason, "exit": px})
    st["closed"].append(rec)
    st["positions"] = [x for x in st["positions"] if x["id"] != p["id"]]


def update(st: dict, fetch_5m) -> list[str]:
    """Açık pozisyonları son kontrolden bu yana kapanan 5m mumlarla işler. fetch_5m(symbol, since_ms) -> DataFrame."""
    msgs = []
    for p in list(st["positions"]):
        try:
            df = fetch_5m(p["symbol"], p["last_ms"])
        except Exception as e:
            print("demo fiyat hatası", p["symbol"], e)
            continue
        df = df[df.index.as_unit("ms").asi8 >= p["last_ms"]]
        word, sd, b = ("LONG" if p["side"] == 1 else "SHORT"), p["side"], p["base"]
        for ts, bar in df.iterrows():
            t = int(ts.value // 10**6)
            hi, lo, op = float(bar["high"]), float(bar["low"]), float(bar["open"])
            adverse = lo if sd == 1 else hi
            favor = hi if sd == 1 else lo
            hit = lambda lvl, x: (x <= lvl) if sd == 1 else (x >= lvl)       # aleyhte seviyeye değdi mi
            reach = lambda lvl, x: (x >= lvl) if sd == 1 else (x <= lvl)     # lehte seviyeye değdi mi
            # 1) planlı ekleme
            if not p["add_done"] and not p["add_cancel"] and hit(p["add_px"], adverse):
                if st["cash"] >= p["add_margin"] + 0.01 and p["add_margin"] >= 0.5:
                    q2, px2 = p["add_q"], p["add_px"]
                    fee = q2 * px2 * FEE
                    p["avg"] = (p["avg"] * p["qty"] + px2 * q2) / (p["qty"] + q2)
                    p["qty"] += q2
                    p["qty0"] += q2
                    p["margin"] += p["add_margin"]
                    st["cash"] -= p["add_margin"] + fee
                    p["realized"] -= fee
                    p["fees"] += fee
                    p["liq"] = p["avg"] * (1 - sd * (1 / p["lev"] - 0.005))
                    p["add_done"] = True
                    msgs.append(f"➕ DEMO #{p['id']} {b} {word}: fiyat {fmt(px2)}'e geldi, planlı EKLEME yapıldı "
                                f"({p['add_margin']:.2f}$ marjin). Yeni ortalama {fmt(p['avg'])}, likidasyon {fmt(p['liq'])}, "
                                f"stop {fmt(p['stop'])}. Boştaki kasa {st['cash']:.2f}$")
                else:
                    p["add_cancel"] = True
                    msgs.append(f"ℹ️ DEMO #{p['id']} {b}: ekleme seviyesi geldi ama boşta yeterli kasa yok, ekleme yapılmadı.")
            # 2) likidasyon / stop
            if hit(p["liq"], adverse) and (sd * (p["liq"] - p["stop"]) > 0):
                lost = p["margin"]
                p["realized"] -= lost
                p["margin"], p["qty"] = 0.0, 0.0
                _finish(st, p, "LİKİDASYON", p["liq"], t)
                msgs.append(f"💥 DEMO #{p['id']} {b} {word} LİKİDE OLDU ({fmt(p['liq'])}). Kalan marjin {lost:.2f}$ gitti. "
                            f"İşlem sonucu {p['realized']:+.2f}$ · Kasa {equity(st):.2f}$")
                break
            if hit(p["stop"], adverse):
                px = op if hit(p["stop"], op) else p["stop"]         # boşlukla açıldıysa açılıştan
                res = _close(st, p, p["qty"], px, "stop")
                be = any(p["tp_done"])
                _finish(st, p, "STOP (giriş)" if be else "STOP", px, t)
                msgs.append(f"{'⚪' if be else '🛑'} DEMO #{p['id']} {b} {word} {'başa baş STOP' if be else 'STOP oldu'} "
                            f"({fmt(px)}). Bu kapanış {res:+.2f}$ · işlem toplamı {p['realized']:+.2f}$ · "
                            f"ekleme {'yapılmıştı' if p['add_done'] else 'yapılmamıştı'} · Kasa {equity(st):.2f}$")
                break
            # 3) hedefler
            done = False
            for k in range(3):
                if p["tp_done"][k] or not reach(p["tps"][k], favor):
                    continue
                rem_w = sum(w for j, w in enumerate(C.TP_SPLIT) if not p["tp_done"][j])
                q = p["qty"] if k == 2 or rem_w <= C.TP_SPLIT[k] + 1e-9 else p["qty"] * C.TP_SPLIT[k] / rem_w
                res = _close(st, p, q, p["tps"][k], f"TP{k + 1}")
                p["tp_done"][k] = True
                extra = ""
                if k == 0:
                    p["stop"] = p["avg"]
                    if not p["add_done"]:
                        p["add_cancel"] = True
                    extra = f" Stop girişe çekildi ({fmt(p['stop'])}){', bekleyen ekleme iptal' if not p['add_done'] else ''}."
                msgs.append(f"✅ DEMO #{p['id']} {b} {word} TP{k + 1} alındı ({fmt(p['tps'][k])}): pozisyonun "
                            f"{'kalanı' if p['qty'] <= 1e-12 else '%' + str(int(C.TP_SPLIT[k] * 100))}'i kapandı, {res:+.2f}$.{extra} "
                            f"Kasa {equity(st):.2f}$")
                if p["qty"] <= 1e-12:
                    _finish(st, p, "TP3", p["tps"][k], t)
                    msgs.append(f"🏁 DEMO #{p['id']} {b} {word} tamamlandı · işlem sonucu {p['realized']:+.2f}$ · Kasa {equity(st):.2f}$")
                    done = True
                    break
            if done:
                break
            p["last_px"] = float(bar["close"])
            p["last_ms"] = t + 300_000
    return msgs


# ------------------------------------------------------------------ raporlar

def status_text(st: dict, prices: dict | None = None) -> str:
    eq = equity(st, prices)
    lines = [f"📒 DEMO KASA: {eq:.2f}$ (başlangıç {st['start']:.0f}$, {(eq / st['start'] - 1) * 100:+.1f}%)",
             f"Boşta: {st['cash']:.2f}$ · açık pozisyon: {len(st['positions'])} · kapanan işlem: {len(st['closed'])}"]
    days = max((now_ms() - st["created"]) / 86_400_000, 1e-9)
    target = st["start"] * 10 ** (min(days, 10) / 10)
    lines.append(f"100$→1000$ hedef yolunda bugün olması gereken: {target:.0f}$")
    for p in st["positions"]:
        px = (prices or {}).get(p["symbol"], p.get("last_px", p["avg"]))
        u = max(unrealized(p, px), -p["margin"])
        tp = "/".join("✓" if d else "·" for d in p["tp_done"])
        lines.append(f"• #{p['id']} {p['base']} {'LONG' if p['side'] == 1 else 'SHORT'} {p['lev']}x · ort. {fmt(p['avg'])} · "
                     f"fiyat {fmt(px)} · stop {fmt(p['stop'])} · TP {tp} · ekleme {'✓' if p['add_done'] else '—'} · {u:+.2f}$")
    return "\n".join(lines)


def daily_report(st: dict, day_key: str, prices: dict | None = None) -> str:
    rows = [c for c in st["closed"] if (pd.Timestamp(c["closed"], unit="ms", tz="UTC") + TSI).strftime("%Y-%m-%d") == day_key]
    wins = [c for c in rows if c["realized"] > 0]
    pnl = sum(c["realized"] for c in rows)
    eq = equity(st, prices)
    lines = [f"🌙 GÜN SONU RAPORU — {pd.Timestamp(day_key):%d.%m.%Y} (demo hesap)",
             f"Gün başı kasa: {st['day_start_equity']:.2f}$ → şimdi: {eq:.2f}$ ({eq - st['day_start_equity']:+.2f}$, "
             f"{(eq / st['day_start_equity'] - 1) * 100:+.1f}%)",
             f"Başlangıçtan beri: {st['start']:.0f}$ → {eq:.2f}$ ({(eq / st['start'] - 1) * 100:+.1f}%)",
             f"Bugün kapanan işlem: {len(rows)} · kazanan {len(wins)} · kaybeden {len(rows) - len(wins)} · "
             f"gerçekleşen {pnl:+.2f}$"]
    for c in rows:
        tp = "".join(f"TP{k + 1}✓ " for k, d in enumerate(c["tp_done"]) if d)
        lines.append(f"• #{c['id']} {c['base']} {'LONG' if c['side'] == 1 else 'SHORT'} {c['lev']}x · giriş {fmt(c['entry'])} → "
                     f"{c['reason']} {fmt(c['exit'])} · {tp}{'ekleme✓ ' if c['add_done'] else ''}· {c['realized']:+.2f}$")
    if st["positions"]:
        lines.append(f"Açık kalan pozisyon: {len(st['positions'])}")
        lines.append(status_text(st, prices).split("\n", 3)[-1])
    lines.append("Not: demo hesap, gerçek para yok. Eğitim amaçlıdır, yatırım tavsiyesi değildir.")
    return "\n".join(lines)


def roll_day(st: dict, prices: dict | None = None) -> str | None:
    """TSİ gün değiştiyse önceki günün raporunu döndürür."""
    today = (pd.Timestamp.now(tz="UTC") + TSI).strftime("%Y-%m-%d")
    msg = None
    if st["day"] and st["day"] != today:
        msg = daily_report(st, st["day"], prices)
    if st["day"] != today:
        st["day"] = today
        st["day_start_equity"] = equity(st, prices)
    return msg


def snapshot(st: dict, prices: dict | None = None):
    st["equity_curve"].append([now_ms(), round(equity(st, prices), 4)])
