"""
Telegram sinyal botu (QUANT FUTURES). İşlem YAPMAZ; web sitesiyle aynı tarama kodunu çalıştırır ve
YENİ LONG/SHORT sinyal kartlarını Telegram'a (yoksa ekrana) gönderir. Aynı sinyali iki kez göndermez.

    python signal_bot.py                          # tek tarama (varsayılan mod: config.DEFAULT_MODE)
    python signal_bot.py --mode "Dengeli (doküman)"
    python signal_bot.py --capital 1000           # pozisyon miktarını da hesapla

GitHub Actions ile her 15 dakikada bir otomatik çalışır (.github/workflows/sinyal.yml).
Ortam değişkenleri: TELEGRAM_BOT_TOKEN (zorunlu), TELEGRAM_CHAT_ID (isteğe bağlı — yoksa bota son
mesaj atan sohbet otomatik bulunur; bunun için Telegram'da bota bir kez /start yazman yeterli).
"""
import argparse
import json
import os
import urllib.parse
import urllib.request

import pandas as pd

import config as C
import data as D
import scanner as SC


def tg(method: str, token: str, **params):
    body = urllib.parse.urlencode(params).encode() if params else None
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", body, timeout=20) as r:
        return json.loads(r.read().decode())


def find_chat(token: str):
    """Bota en son mesaj atan sohbetin kimliği (kullanıcı Telegram'da bota /start yazmış olmalı)."""
    upd = tg("getUpdates", token).get("result", [])
    for u in reversed(upd):
        msg = u.get("message") or u.get("my_chat_member") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            return str(chat["id"])
    return None


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    if not path:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


STOP_WORDS = {"USDT", "USDC", "PERP", "KONTROL", "ET", "ANALIZ", "ANALİZ", "ANALIZI", "YAP", "LONG", "SHORT", "BAK",
              "NE", "DURUMU", "GIR", "GİR", "KASA", "X", "MI", "MU", "COIN", "LÜTFEN", "LUTFEN", "KRAL", "REIS", "REİS"}

HELP = ("Coin analizi için coinin adını yaz, örnek:\n"
        "• AKE\n• AKE USDT kontrol et\n• SOL 20x\n• BTC short kasa 200\n• /kasa → demo hesabın durumu\n\n"
        "Varsayılan: kasa {cap:.0f}$, 10x, ilk giriş %50, ekleme %25. Kaldıraç (20x), kasa (kasa 200) ve yön (long/short) yazabilirsin.")


def parse_request(text: str, bases: set):
    """'AKE USDT kontrol et 20x kasa 200 long' -> ('AKE', 20, 200, 'LONG')"""
    import re
    t = text.upper().replace("/", " ").replace(",", " ")
    lev = re.search(r"(\d{1,3})\s*X\b", t)
    cap = re.search(r"KASA\s*(\d+(?:\.\d+)?)", t)
    side = "LONG" if re.search(r"\bLONG\b", t) else "SHORT" if re.search(r"\bSHORT\b", t) else "Otomatik"
    base = None
    for w in re.findall(r"[A-Z0-9]+", t):
        w2 = w[:-4] if w.endswith("USDT") and len(w) > 4 else w
        if w2 in STOP_WORDS or w2.isdigit():
            continue
        if w2 in bases:
            base = w2
            break
    return base, (int(lev.group(1)) if lev else None), (float(cap.group(1)) if cap else None), side


def html_escape(x: str) -> str:
    return x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def run_analysis(ex, sym: str, capital: float, leverage: int, side: str):
    import analysis as A
    d15 = D.fetch_ohlcv(ex, sym, "15m", 400)
    h1 = D.fetch_ohlcv(ex, sym, "1h", 1500)
    h4 = D.fetch_ohlcv(ex, sym, "4h", 600)
    d1 = D.fetch_ohlcv(ex, sym, "1d", 200)
    btc1h = D.fetch_ohlcv(ex, C.BTC, "1h", 300)
    if len(h1) < 120:
        return None
    try:
        t = ex.fetch_ticker(sym)
    except Exception:
        t = None
    live = D.live_snapshot(ex, sym, t)
    if t and t.get("last"):
        live["last"] = float(t["last"])
    return A.analyze(sym, d15, h1, h4, d1, btc1h, live, capital, leverage, 0.5, 0.25, side)


def analysis_message(ex, sym: str, capital: float, leverage: int, side: str) -> str:
    import re
    import analysis as A
    r = run_analysis(ex, sym, capital, leverage, side)
    if r is None:
        return f"{sym.split('/')[0]}: yeterli geçmiş veri yok. Yeni listelenmiş olabilir."
    base = sym.split("/")[0]
    head = {1: "🟢 LONG eğilimli", -1: "🔴 SHORT eğilimli", 0: "⚪ Net yön yok — BEKLE"}[r["direction"]]
    tf = " · ".join(f"{k} {'▲' if v['dir'] == 1 else '▼' if v['dir'] == -1 else '■'}" for k, v in r["tfs"].items())
    p = r["prob"]["dir"]
    lines = [f"<b>{base}USDT — {head}</b>", f"Fiyat {A.fmt_px(r['price'])} · 1H ATR %{r['atr_pct']:.2f}", f"Trend: {tf}",
             f"Olasılık (24s, ±{A.DIR_ATR:g} ATR): yukarı %{p['up'] * 100:.0f} · aşağı %{p['down'] * 100:.0f} · yatay %{p['range'] * 100:.0f} "
             f"({r['prob']['n']} benzer durum)", ""]
    body = html_escape(r["comment"])
    body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", body)
    lines.append(body)
    lines.append("\nDetaylı grafik ve likidasyon haritası: https://hb-mexc-sinyal.streamlit.app")
    return "\n".join(lines)


def send_html(token, chat, text):
    for i in range(0, len(text), 3800):
        part = text[i:i + 3800]
        try:
            tg("sendMessage", token, chat_id=chat, text=part, parse_mode="HTML", disable_web_page_preview="true")
        except Exception:
            tg("sendMessage", token, chat_id=chat, text=part.replace("<b>", "").replace("</b>", ""))


def listen(token, chat, state, ex, seconds: int, capital: float, paper_tick=None, paper_status=None):
    """Telegram'dan gelen 'AKE kontrol et' gibi mesajları dinler ve analizle cevaplar (yalnızca sahibinin sohbetinde)."""
    import time
    bases = {m["base"] for m in ex.markets.values() if D.is_usdt_perp(m) and not D.is_tradfi(m)}
    started = time.time()
    end = started + seconds
    offset = state.get("offset")
    last_tick = time.time()
    while time.time() < end - 5:
        if paper_tick and time.time() - last_tick >= 120:
            last_tick = time.time()
            try:
                paper_tick()
            except Exception as e:
                print("demo takip hatası:", type(e).__name__, e)
        wait = int(min(50, max(1, end - time.time() - 5)))
        try:
            params = {"timeout": wait, "allowed_updates": json.dumps(["message"])}
            if offset:
                params["offset"] = offset
            upd = tg("getUpdates", token, **params).get("result", [])
        except Exception as e:
            print("getUpdates hatası:", type(e).__name__)
            time.sleep(5)
            continue
        for u in upd:
            offset = u["update_id"] + 1
            state["offset"] = offset
            m = u.get("message") or {}
            cid = str((m.get("chat") or {}).get("id", ""))
            text = (m.get("text") or "").strip()
            if not text or cid != str(chat) or m.get("date", 0) < started - 900:
                continue                      # yalnızca sahibinin yeni mesajları
            if paper_status and text.lower().strip("/ ") in ("kasa", "demo", "durum", "bakiye"):
                send_html(token, chat, html_escape(paper_status()))
                continue
            if text.lower() in ("/start", "/help", "yardım", "yardim"):
                send_html(token, chat, html_escape(HELP.format(cap=capital)))
                continue
            base, lev, cap, side = parse_request(text, bases)
            if not base:
                send_html(token, chat, html_escape("Bu mesajda MEXC vadeli coin adı bulamadım.\n\n" + HELP.format(cap=capital)))
                continue
            sym = f"{base}/USDT:USDT"
            tg("sendMessage", token, chat_id=chat, text=f"🔍 {base}USDT analiz ediliyor (15m/1h/4h/1g, olasılıklar, likidasyon haritası)...")
            try:
                send_html(token, chat, analysis_message(ex, sym, cap or capital, lev or 10, side))
                print("Cevaplandı:", base)
            except Exception as e:
                send_html(token, chat, html_escape(f"{base} analiz edilemedi: {type(e).__name__}: {e}"[:500]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=C.DEFAULT_MODE, choices=list(C.MODES))
    ap.add_argument("--capital", type=float, default=float(os.getenv("CAPITAL") or 0) or None)
    ap.add_argument("--state", default=None, help="gönderilen sinyallerin kaydı (tekrar göndermemek için)")
    ap.add_argument("--listen", type=int, default=0, help="tarama sonrası bu kadar saniye Telegram mesajlarını dinle ve cevapla")
    a = ap.parse_args()
    C.apply_mode(a.mode)

    state = load_state(a.state) if a.state else {}
    sent = set(state.get("sent", []))
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID") or state.get("chat_id")
    if token and not chat:
        try:
            print("Bot:", "@" + tg("getMe", token)["result"]["username"])
        except Exception as e:
            print("Token geçersiz ya da Telegram'a ulaşılamadı:", type(e).__name__)
        chat = find_chat(token)
        if chat:
            tg("sendMessage", token, chat_id=chat,
               text=f"✅ Quant Futures sinyal botu bağlandı ({a.mode} mod). Yeni sinyal çıktığında buraya yazacağım.")
        else:
            print("Telegram sohbeti bulunamadı: Telegram'da bota /start yaz ve tekrar dene.")
    state["chat_id"] = chat

    ex = D.get_exchange()
    syms, tickers = D.pick_universe(ex)
    out = SC.scan(
        fetch=lambda s, tf, n: D.fetch_ohlcv(ex, s, tf, n),
        live_fn=lambda s: D.live_snapshot(ex, s, tickers.get(s)),
        rules_fn=lambda s: D.market_rules(ex, s),
        symbols=syms, threshold=C.SCORE_THRESHOLD, capital=a.capital, risk_pct=C.RISK_PCT,
        risk_state={"daily_loss_pct": 0, "open_risk_pct": 0, "risk_pct": C.RISK_PCT * 100},
        now_ms=ex.milliseconds(), stale_fn=D.is_stale,
    )
    sig = [r for r in out["results"] if r["decision"] != "İŞLEM YOK"]
    new = [r for r in sig if f"{r['symbol']}|{r['bar_time']:%Y%m%d%H%M}|{r['decision']}" not in sent]
    print(f"{len(out['results'])} kontrat tarandı · aktif sinyal {len(sig)} · yeni {len(new)}")
    for r in sorted(new, key=lambda r: -r["score"]):
        text = (f"{'🟢' if r['decision'] == 'LONG' else '🔴'} YENİ SİNYAL\n\n" + SC.card(r, C.RISK_PCT)
                + "\n\nhttps://hb-mexc-sinyal.streamlit.app\nBu çıktı bir test aracıdır, yatırım tavsiyesi değildir.")
        print(text, "\n")
        if token and chat:
            for i in range(0, len(text), 3800):  # Telegram mesaj sınırı
                tg("sendMessage", token, chat_id=chat, text=text[i:i + 3800])
        sent.add(f"{r['symbol']}|{r['bar_time']:%Y%m%d%H%M}|{r['decision']}")
    state["sent"] = sorted(sent)[-500:]
    state["last_run"] = str(pd.Timestamp.now(tz="UTC"))
    save_state(a.state, state)

    # ---- DEMO HESAP: yeni sinyallere sanal giriş + açık pozisyon takibi
    import paper as PP
    ppath = os.path.join(os.path.dirname(a.state) if a.state else ".", "paper.json")
    pst = PP.load(ppath)

    def say(msg):
        print(msg, "\n")
        if token and chat:
            send_html(token, chat, html_escape(msg))

    def fetch_5m(sym, since_ms):
        return D.fetch_ohlcv(ex, sym, "5m", since_ms=since_ms)

    def tick():
        for m in PP.update(pst, fetch_5m):
            say(m)
        rep = PP.roll_day(pst)
        if rep:
            say(rep)
        PP.snapshot(pst)
        PP.save(ppath, pst)

    try:
        tick()
        for r in sorted(new, key=lambda r: -r["score"]):
            side = r["decision"]
            res = run_analysis(ex, r["symbol"], pst["cash"], PP.MAX_LEV, side)
            if res is None:
                continue
            _, msg = PP.open_position(pst, r, res)
            say(msg)
        PP.snapshot(pst)
        PP.save(ppath, pst)
    except Exception as e:
        print("demo hesap hatası:", type(e).__name__, e)

    if token and chat and a.listen > 0:
        listen(token, chat, state, ex, a.listen, a.capital or 100.0, paper_tick=tick,
               paper_status=lambda: PP.status_text(pst))
        save_state(a.state, state)
        tick()


if __name__ == "__main__":
    main()
