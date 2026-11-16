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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=C.DEFAULT_MODE, choices=list(C.MODES))
    ap.add_argument("--capital", type=float, default=float(os.getenv("CAPITAL") or 0) or None)
    ap.add_argument("--state", default=None, help="gönderilen sinyallerin kaydı (tekrar göndermemek için)")
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


if __name__ == "__main__":
    main()
