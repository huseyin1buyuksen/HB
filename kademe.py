"""
KADEME DEMO — ayrı 100$'lık sanal kasa (gerçek para yok).
Kurallar:
  • Ani düşüş sinyali gelen coin sıraya alınır; düşüşten 4 SAAT SONRA 2$ marjinle 10x LONG açılır.
  • 6 saatte bir: açık pozisyonlar içinden EN ÇOK ZARARDA olana 50$ marjin EKLEME yapılır
    (her pozisyona en fazla 1 ekleme, boşta en az 50$ varsa).
  • Çıkış: fiyat ortalama maliyetin %3 üstüne gelince pozisyonun tamamı kapanır (10x'te marjine göre ≈ +%30).
  • Stop yok; fiyat likidasyona gelirse pozisyonun marjini tamamen gider.
  • Komisyon ve funding düşülür. Pozisyonlar 5 dakikalık kapanmış mumlarla takip edilir.
Bu "maliyet düşürme (ortalama alma)" stratejisi küçük kazançları sık, büyük kayıpları seyrek ama sert yaşatır.
"""
from __future__ import annotations

import time

import config as C
import paper as PP

START = 100.0
FIRST = 2.0
ADD = 50.0
LEV = 10
OPEN_DELAY_H = 4
ADD_EVERY_H = 6
TP = 0.03
MMR = 0.005
FEE = C.FEE_RATE
FUND_8H = C.FUNDING_PER_8H


def now_ms() -> int:
    return int(time.time() * 1000)


def fmt(x):
    return PP.fmt(x)


def load(path: str) -> dict:
    st = PP.load(path)
    st.setdefault("pending", [])
    st.setdefault("next_add", None)
    st.setdefault("kind", "kademe")
    return st


def liq_px(avg: float) -> float:
    return avg * (1 - 1 / LEV + MMR)


def queue(st: dict, d: dict) -> str | None:
    sym = d["symbol"]
    if any(p["symbol"] == sym for p in st["positions"]) or any(q["symbol"] == sym for q in st["pending"]):
        return None
    t_open = now_ms() + OPEN_DELAY_H * 3_600_000
    st["pending"].append({"symbol": sym, "base": d["base"], "drop": d["drop"], "dump_px": d["price"], "open_at": t_open})
    return (f"⏳ KADEME: {d['base']} %{d['drop'] * 100:.1f} düştü → sıraya alındı. "
            f"{PP.tsi(t_open)} TSİ'de (4 saat sonra) {FIRST:.0f}$ marjinle {LEV}x LONG açılacak.")


def process_pending(st: dict, price_fn) -> list[str]:
    msgs, keep = [], []
    for q in st["pending"]:
        if now_ms() < q["open_at"]:
            keep.append(q)
            continue
        if st["cash"] < FIRST + 0.1:
            msgs.append(f"ℹ️ KADEME: {q['base']} için açılış zamanı geldi ama boşta {st['cash']:.2f}$ var, işlem açılmadı.")
            continue
        px = price_fn(q["symbol"])
        if not px:
            keep.append(q)
            continue
        qty = FIRST * LEV / px
        fee = qty * px * FEE
        p = {"id": st["next_id"], "symbol": q["symbol"], "base": q["base"], "side": 1, "lev": LEV, "opened": now_ms(),
             "last_ms": now_ms() - now_ms() % 300_000, "entry": px, "avg": px, "qty": qty, "qty0": qty, "margin": FIRST,
             "add_px": None, "add_q": 0.0, "add_margin": ADD, "add_done": False, "add_cancel": False,
             "stop": None, "stop0": None, "tps": [px * (1 + TP)], "tp_done": [False], "liq": liq_px(px),
             "realized": -fee, "fees": fee, "funding": 0.0, "score": None, "last_px": px, "drop": q["drop"]}
        st["next_id"] += 1
        st["cash"] -= FIRST + fee
        st["positions"].append(p)
        if not st["next_add"]:
            st["next_add"] = now_ms() + ADD_EVERY_H * 3_600_000
        chg = px / q["dump_px"] - 1
        msgs.append(f"📒 KADEME İŞLEM AÇILDI #{p['id']} — {q['base']}USDT LONG 🟢\n\n"
                    f"Neden: 4 saat önce %{q['drop'] * 100:.1f} ani düşüş (o andan beri fiyat %{chg * 100:+.1f})\n"
                    f"Giriş: {fmt(px)} · {FIRST:.0f}$ marjin · {LEV}x → {FIRST * LEV:.0f}$ pozisyon\n"
                    f"Kâr al: {fmt(p['tps'][0])} (ortalama +%{TP * 100:.0f}) → tamamı kapanır\n"
                    f"Stop: yok · Likidasyon: {fmt(p['liq'])}\n"
                    f"Ekleme: 6 saatte bir en çok zarardaki pozisyona {ADD:.0f}$ (sıradaki kontrol {PP.tsi(st['next_add'])} TSİ)\n"
                    f"Kasa (boşta): {st['cash']:.2f}$")
    st["pending"] = keep
    return msgs


def add_cycle(st: dict, price_fn) -> list[str]:
    if not st["next_add"] or now_ms() < st["next_add"]:
        return []
    st["next_add"] = now_ms() + ADD_EVERY_H * 3_600_000 if st["positions"] or st["pending"] else None
    cands = []
    for p in st["positions"]:
        px = price_fn(p["symbol"])
        if px and not p["add_done"] and px < p["avg"]:
            cands.append((px / p["avg"] - 1, p, px))
    if not cands:
        return [f"ℹ️ KADEME ekleme kontrolü: zararda olan (ve daha önce ekleme yapılmamış) pozisyon yok. "
                f"Sıradaki kontrol {PP.tsi(st['next_add']) if st['next_add'] else '—'} TSİ."]
    loss, p, px = min(cands, key=lambda c: c[0])
    if st["cash"] < ADD + 0.5:
        return [f"ℹ️ KADEME: en çok zarardaki {p['base']} (%{loss * 100:.1f}) ama boşta {st['cash']:.2f}$ var, {ADD:.0f}$ ekleme yapılamadı."]
    q2 = ADD * LEV / px
    fee = q2 * px * FEE
    old_avg = p["avg"]
    p["avg"] = (p["avg"] * p["qty"] + px * q2) / (p["qty"] + q2)
    p["qty"] += q2
    p["qty0"] += q2
    p["margin"] += ADD
    p["realized"] -= fee
    p["fees"] += fee
    p["add_done"] = True
    p["add_px"] = px
    p["last_px"] = px
    p["liq"] = liq_px(p["avg"])
    p["tps"] = [p["avg"] * (1 + TP)]
    st["cash"] -= ADD + fee
    return [f"➕ KADEME EKLEME #{p['id']} {p['base']}: en çok zarardaki pozisyon (%{loss * 100:.1f}). "
            f"{fmt(px)}'den {ADD:.0f}$ marjin eklendi ({ADD * LEV:.0f}$ pozisyon).\n"
            f"Ortalama {fmt(old_avg)} → {fmt(p['avg'])} · yeni kâr al {fmt(p['tps'][0])} · likidasyon {fmt(p['liq'])}\n"
            f"Toplam marjin {p['margin']:.2f}$ · Kasa (boşta): {st['cash']:.2f}$"]


def _close_all(st: dict, p: dict, px: float, reason: str, ts: int) -> float:
    pnl = (px - p["avg"]) * p["qty"]
    fee = p["qty"] * px * FEE
    fund = p["qty"] * p["avg"] * FUND_8H * ((now_ms() - p["opened"]) / 3_600_000) / 8
    st["cash"] += p["margin"] + pnl - fee - fund
    p["realized"] += pnl - fee - fund
    p["fees"] += fee
    p["funding"] += fund
    p["qty"], p["margin"] = 0.0, 0.0
    if reason.startswith("TP"):
        p["tp_done"] = [True]
    PP._finish(st, p, reason, px, ts)
    return pnl - fee - fund


def update(st: dict, fetch_5m) -> list[str]:
    msgs = []
    for p in list(st["positions"]):
        try:
            df = fetch_5m(p["symbol"], p["last_ms"])
        except Exception as e:
            print("kademe fiyat hatası", p["symbol"], e)
            continue
        df = df[df.index.as_unit("ms").asi8 >= p["last_ms"]]
        for ts, bar in df.iterrows():
            t = int(ts.value // 10**6)
            if float(bar["low"]) <= p["liq"]:
                lost = p["margin"]
                p["realized"] -= lost
                p["qty"], p["margin"] = 0.0, 0.0
                PP._finish(st, p, "LİKİDASYON", p["liq"], t)
                msgs.append(f"💥 KADEME #{p['id']} {p['base']} LİKİDE OLDU ({fmt(p['liq'])}). "
                            f"Marjin {lost:.2f}$ gitti · ekleme {'yapılmıştı' if p['add_done'] else 'yapılmamıştı'} · "
                            f"işlem sonucu {p['realized']:+.2f}$ · Kasa {PP.equity(st):.2f}$")
                break
            if float(bar["high"]) >= p["tps"][0]:
                res = _close_all(st, p, p["tps"][0], "TP", t)
                msgs.append(f"✅ KADEME #{p['id']} {p['base']} KÂR ALINDI ({fmt(p['tps'][0])}): {res:+.2f}$ · "
                            f"ekleme {'yapılmıştı' if p['add_done'] else 'yapılmamıştı'} · işlem sonucu {p['realized']:+.2f}$ · "
                            f"Kasa {PP.equity(st):.2f}$")
                break
            p["last_px"] = float(bar["close"])
            p["last_ms"] = t + 300_000
    return msgs


def status_text(st: dict) -> str:
    txt = PP.status_text(st).replace("📒 DEMO KASA", "🪜 KADEME DEMO KASA")
    lines = [txt]
    if st["pending"]:
        lines.append("Sırada (4 saat bekleyen): " + ", ".join(f"{q['base']} → {PP.tsi(q['open_at'])}" for q in st["pending"]))
    if st["next_add"]:
        lines.append(f"Sıradaki 50$ ekleme kontrolü: {PP.tsi(st['next_add'])} TSİ")
    return "\n".join(lines)
