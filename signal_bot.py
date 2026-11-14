"""
Telegram sinyal botu (QUANT FUTURES). İşlem YAPMAZ; web sitesiyle aynı tarama kodunu çalıştırır ve
LONG/SHORT sinyal kartlarını Telegram'a (yoksa ekrana) gönderir.

    python signal_bot.py                 # tek tarama
    python signal_bot.py --capital 1000  # pozisyon miktarını da hesapla

Her 15 dakikada bir, mum kapanışından ~1 dk sonra çalıştır (örn. cron: 1,16,31,46 * * * *).
Ortam değişkenleri: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
import argparse
import os
import urllib.parse
import urllib.request

import config as C
import data as D
import scanner as SC


def send(text: str):
    token, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    print(text, "\n")
    if not (token and chat):
        return
    for i in range(0, len(text), 3800):  # Telegram mesaj sınırı
        body = urllib.parse.urlencode({"chat_id": chat, "text": text[i:i + 3800]}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=20)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=None)
    ap.add_argument("--threshold", type=float, default=C.SCORE_THRESHOLD)
    ap.add_argument("--daily-loss", type=float, default=0.0, help="bugünkü gerçekleşen zarar (%)")
    ap.add_argument("--open-risk", type=float, default=0.0, help="açık pozisyonların toplam riski (%)")
    a = ap.parse_args()

    ex = D.get_exchange()
    syms, tickers = D.pick_universe(ex)
    out = SC.scan(
        fetch=lambda s, tf, n: D.fetch_ohlcv(ex, s, tf, n),
        live_fn=lambda s: D.live_snapshot(ex, s, tickers.get(s)),
        rules_fn=lambda s: D.market_rules(ex, s),
        symbols=syms, threshold=a.threshold, capital=a.capital, risk_pct=C.RISK_PCT,
        risk_state={"daily_loss_pct": a.daily_loss, "open_risk_pct": a.open_risk, "risk_pct": C.RISK_PCT * 100},
        now_ms=ex.milliseconds(), stale_fn=D.is_stale,
    )
    sig = [r for r in out["results"] if r["decision"] != "İŞLEM YOK"]
    if not sig:
        print(f"İŞLEM YOK — {len(out['results'])} kontrat tarandı, şartları sağlayan yok.")
        return  # boş tarama için Telegram'a mesaj atma
    for r in sorted(sig, key=lambda r: -r["score"]):
        send(SC.card(r, C.RISK_PCT) + "\n\nBu çıktı bir test aracıdır, yatırım tavsiyesi değildir.")


if __name__ == "__main__":
    main()
