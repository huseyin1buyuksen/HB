"""
Telegram sinyal botu. İşlem YAPMAZ, sadece sinyal üretir ve Telegram'a (yoksa ekrana) yazar.
Web sitesiyle (app.py) aynı tarama kodunu kullanır. Her mum kapanışından sonra bir kez çalıştır.

    python signal_bot.py

Elindeki coinleri positions.json dosyasına yazarsan çıkış sinyallerini ve güncel stop seviyesini de verir:
    {"SOL/USDT": {"entry_date": "2026-11-01"}}
"""
import json
import os
import urllib.parse
import urllib.request

import config as C
import data as D
import signals as SG

ICON = {"ÇIKIŞ": "🔴", "GİRİŞ": "🟢", "TUT": "🟡", "İZLE": "👀"}


def send(text: str):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    print(text)
    if not (token and chat):
        print("\n(Telegram ayarlı değil, sadece ekrana yazıldı.)")
        return
    body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=20)


def load_positions() -> dict:
    if os.path.exists(C.POSITIONS_FILE):
        with open(C.POSITIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def main():
    ex = D.get_exchange()
    res = SG.scan(lambda s, n: D.fetch_ohlcv(ex, s, limit_bars=n), D.pick_universe(ex), load_positions())
    lines = [
        f"📊 MEXC sinyal raporu — {res['bar_time']:%Y-%m-%d %H:%M} UTC mumu ({C.TIMEFRAME})",
        f"BTC rejimi: {'✅ trend var, yeni giriş serbest' if res['regime_ok'] else '⛔ trend yok, yeni giriş YOK'}",
        "",
    ]
    for r in res["rows"]:
        if r["Durum"] in ICON:
            stop = f", stop {r['Stop']:.6g}" if r["Stop"] == r["Stop"] else ""
            lines.append(f"{ICON[r['Durum']]} {r['Coin']} {r['Durum']} — {r['Not']} (kapanış {r['Kapanış']:.6g}{stop})")
    if not any(r["Durum"] == "GİRİŞ" for r in res["rows"]):
        lines.append("Yeni giriş sinyali yok.")
    lines += ["", "Bu rapor bir test aracının çıktısıdır, yatırım tavsiyesi değildir."]
    send("\n".join(lines))


if __name__ == "__main__":
    main()
