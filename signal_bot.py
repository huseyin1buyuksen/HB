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


def tg(method: str, token: str, _timeout: int = 20, **params):
    body = urllib.parse.urlencode(params).encode() if params else None
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", body, timeout=_timeout) as r:
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


def listen(token, chat, state, ex, seconds: int, capital: float, paper_tick=None, paper_status=None, extra_poll=None):
    """Telegram'dan gelen 'AKE kontrol et' gibi mesajları dinler ve analizle cevaplar (yalnızca sahibinin sohbetinde)."""
    import time
    bases = {m["base"] for m in ex.markets.values() if D.is_usdt_perp(m) and not D.is_tradfi(m)}
    started = time.time()
    end = started + seconds
    offset = state.get("offset")
    last_tick = time.time()
    while time.time() < end - 5:
        if extra_poll:
            try:
                extra_poll()
            except Exception as e:
                print("ikinci bot hatası:", type(e).__name__)
        if paper_tick and time.time() - last_tick >= 120:
            last_tick = time.time()
            try:
                paper_tick()
            except Exception as e:
                print("demo takip hatası:", type(e).__name__, e)
        wait = int(min(20 if extra_poll else 50, max(1, end - time.time() - 5)))
        try:
            params = {"timeout": wait, "allowed_updates": json.dumps(["message"])}
            if offset:
                params["offset"] = offset
            upd = tg("getUpdates", token, _timeout=wait + 15, **params).get("result", [])
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


class SideBot:
    """Ek Telegram botu: kendi sohbeti, /kasa komutu; token yoksa mesajları ana bota etiketli gönderir."""

    def __init__(self, env, key, title, state, fallback, welcome, status_fn, help_text):
        import time
        self.time = time
        self.token = os.getenv(env)
        self.key, self.title, self.state, self.fallback = key, title, state, fallback
        self.welcome, self.status_fn, self.help_text = welcome, status_fn, help_text
        self.chat = os.getenv(env.replace("TOKEN", "CHAT_ID")) or state.get(key + "_chat")
        self.ensure()

    def ensure(self):
        if not self.token or self.chat or self.time.time() - self.state.get(self.key + "_try", 0) < 30:
            return
        self.state[self.key + "_try"] = self.time.time()
        try:
            self.chat = find_chat(self.token)
        except Exception as e:
            print(self.title, "botuna ulaşılamadı:", type(e).__name__)
            return
        if self.chat:
            self.state[self.key + "_chat"] = self.chat
            tg("sendMessage", self.token, chat_id=self.chat, text=self.welcome)
        else:
            print(self.title, "sohbeti bulunamadı: Telegram'da bota /start yaz.")

    def say(self, msg):
        print(f"[{self.title}]", msg, "\n")
        if self.token and self.chat:
            send_html(self.token, self.chat, html_escape(msg))
        else:
            self.fallback(f"[{self.title}] " + msg)

    def poll(self):
        if not self.token:
            return
        self.ensure()
        if not self.chat:
            return
        params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
        if self.state.get(self.key + "_offset"):
            params["offset"] = self.state[self.key + "_offset"]
        for u in tg("getUpdates", self.token, **params).get("result", []):
            self.state[self.key + "_offset"] = u["update_id"] + 1
            m = u.get("message") or {}
            if str((m.get("chat") or {}).get("id", "")) != str(self.chat) or m.get("date", 0) < self.time.time() - 900:
                continue
            t = (m.get("text") or "").lower().strip("/ ")
            if t in ("kasa", "demo", "durum", "bakiye"):
                send_html(self.token, self.chat, html_escape(self.status_fn()))
            else:
                send_html(self.token, self.chat, html_escape(self.help_text))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default=C.DEFAULT_MODE, choices=list(C.MODES))
    ap.add_argument("--capital", type=float, default=float(os.getenv("CAPITAL") or 0) or None)
    ap.add_argument("--state", default=None, help="gönderilen sinyallerin kaydı (tekrar göndermemek için)")
    ap.add_argument("--listen", type=int, default=0, help="tarama sonrası bu kadar saniye Telegram mesajlarını dinle ve cevapla")
    ap.add_argument("--loop", type=int, default=0, help="bu kadar saniye çalış: her 15m mumda tara, arada mesajları cevapla")
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

    import time
    import paper as PP
    ex = D.get_exchange()
    ppath = os.path.join(os.path.dirname(a.state) if a.state else ".", "paper.json")
    if not os.path.exists(ppath):
        restore_paper(ppath)
    pst = PP.load(ppath)
    # ---- EK BOTLAR: Ani Demo (ani düşüş → LONG) ve Kademe Demo (2$ giriş + 50$ ekleme); her birinin ayrı kasası var
    import dump as DU
    import kademe as KD
    sdir = os.path.dirname(a.state) if a.state else "."
    p2path = os.path.join(sdir, "paper_ani_v2.json")          # yeni strateji: kasa sıfırdan (100$)
    if not os.path.exists(p2path):
        restore_paper(p2path, "paper_ani_v2.json")
    pst2 = PP.load(p2path)
    p3path = os.path.join(sdir, "kademe.json")
    if not os.path.exists(p3path):
        restore_paper(p3path, "kademe.json")
    pst3 = KD.load(p3path)
    deadline = time.time() + a.loop if a.loop else 0

    def say(msg):
        print(msg, "\n")
        if token and chat:
            send_html(token, chat, html_escape(msg))

    ani = SideBot("TELEGRAM_ANI_TOKEN", "ani", "ANİ DEMO", state, say,
                  welcome="✅ ANİ DEMO bağlandı. Son 4 saatte zirvesinden %15+ ani düşen coinleri ~2 dakikada bir bulup "
                          "LONG giriş sinyali yazacağım; ayrı 100$'lık demo kasa bu sinyallere girer. /kasa ile durumu görebilirsin.",
                  status_fn=lambda: PP.status_text(pst2).replace("📒 DEMO KASA", "🚀 ANİ DEMO KASA"),
                  help_text="ANİ DEMO: son 4 saatte %15+ ani düşen coinler için LONG sinyali ve ayrı demo kasa.\n/kasa → kasanın durumu")
    kad = SideBot("TELEGRAM_KADEME_TOKEN", "kademe", "KADEME DEMO", state, say,
                  welcome=f"✅ KADEME DEMO bağlandı. Kasa {KD.START:.0f}$. Ani düşen coinlere düşüşten {KD.OPEN_DELAY_H} saat sonra "
                          f"{KD.FIRST:.0f}$ ile {KD.LEV}x LONG açar; {KD.ADD_EVERY_H} saatte bir en çok zarardaki pozisyona "
                          f"{KD.ADD:.0f}$ ekler; ortalamanın %{KD.TP * 100:.0f} üstünde tamamını kapatır. /kasa ile durumu görebilirsin.",
                  status_fn=lambda: KD.status_text(pst3),
                  help_text="KADEME DEMO: 2$ giriş + en çok zarardakine 50$ ekleme stratejisinin ayrı demo kasası.\n/kasa → kasanın durumu")
    if ani.chat and not state.get("ani_v2_notice"):
        ani.say("🔄 ANİ DEMO GÜNCELLENDİ: artık son 4 saatte zirvesinden %15+ ani düşen coinleri ~2 dakikada bir bulup "
                "LONG giriş sinyali yazıyorum. Demo kasa sıfırlandı, 100$'dan yeniden başlıyor.")
        state["ani_v2_notice"] = True
    watch = DU.DumpWatch(ex)
    dump_last = state.setdefault("dump_last", {})

    def side_polls():
        for b in (ani, kad):
            try:
                b.poll()
            except Exception as e:
                print(b.title, "bot hatası:", type(e).__name__)

    def dump_tick():
        """~2 dakikada bir: ani düşüş ara → Ani Demo'ya sinyal + LONG giriş, Kademe Demo'ya sıraya al."""
        try:
            hits = watch.poll()
        except Exception as e:
            print("ani düşüş taraması hatası:", type(e).__name__, e)
            hits = []
        for d in hits[:3]:
            if time.time() - dump_last.get(d["symbol"], 0) < DU.WINDOW_H * 3600:
                continue
            dump_last[d["symbol"]] = time.time()
            ani.say(DU.describe(d))
            r = run_analysis(ex, d["symbol"], pst2["cash"], PP.MAX_LEV, "LONG")
            if r is not None:
                sigd = {"symbol": d["symbol"], "decision": "LONG", "score": abs(d["drop"]) * 100}
                _, msg = PP.open_position(pst2, sigd, r)
                ani.say(msg.replace("DEMO İŞLEM AÇILDI", "ANİ DEMO İŞLEM AÇILDI").replace(
                    "Neden: sinyal puanı", f"Neden: %{d['drop'] * 100:.1f} ani düşüş · puan"))
            m = KD.queue(pst3, d)
            if m:
                kad.say(m)
        for m in KD.process_pending(pst3, watch.price) + KD.add_cycle(pst3, watch.price):
            kad.say(m)
        for k in [k for k, v in dump_last.items() if time.time() - v > 86400]:
            del dump_last[k]

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
        dump_tick()
        for m in PP.update(pst2, fetch_5m):
            ani.say(m)
        rep = PP.roll_day(pst2)
        if rep:
            ani.say(rep.replace("GÜN SONU RAPORU", "ANİ DEMO GÜN SONU RAPORU"))
        PP.snapshot(pst2)
        PP.save(p2path, pst2)
        for m in KD.update(pst3, fetch_5m):
            kad.say(m)
        rep = PP.roll_day(pst3)
        if rep:
            kad.say(rep.replace("GÜN SONU RAPORU", "KADEME DEMO GÜN SONU RAPORU"))
        PP.snapshot(pst3)
        PP.save(p3path, pst3)

    def cycle():
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
        print(f"{pd.Timestamp.now(tz='UTC'):%H:%M} · {len(out['results'])} kontrat tarandı · aktif sinyal {len(sig)} · yeni {len(new)}")
        for r in sorted(new, key=lambda r: -r["score"]):
            text = (f"{'🟢' if r['decision'] == 'LONG' else '🔴'} YENİ SİNYAL\n\n" + SC.card(r, C.RISK_PCT)
                    + "\n\nhttps://hb-mexc-sinyal.streamlit.app\nBu çıktı bir test aracıdır, yatırım tavsiyesi değildir.")
            print(text, "\n")
            if token and chat:
                for k in range(0, len(text), 3800):  # Telegram mesaj sınırı
                    tg("sendMessage", token, chat_id=chat, text=text[k:k + 3800])
            sent.add(f"{r['symbol']}|{r['bar_time']:%Y%m%d%H%M}|{r['decision']}")
        state["sent"] = sorted(sent)[-500:]
        state["last_run"] = str(pd.Timestamp.now(tz="UTC"))
        save_state(a.state, state)
        # ---- DEMO HESAP: yeni sinyallere sanal giriş + açık pozisyon takibi
        try:
            tick()
            for r in sorted(new, key=lambda r: -r["score"]):
                res = run_analysis(ex, r["symbol"], pst["cash"], PP.MAX_LEV, r["decision"])
                if res is None:
                    continue
                _, msg = PP.open_position(pst, r, res)
                say(msg)
            PP.snapshot(pst)
            PP.save(ppath, pst)
        except Exception as e:
            print("demo hesap hatası:", type(e).__name__, e)
        publish_paper([ppath, p2path, p3path])

    while True:
        try:
            cycle()
        except Exception as e:
            print("tarama hatası:", type(e).__name__, e)
        if not (token and chat) or (a.listen <= 0 and not a.loop):
            break
        now = time.time()
        if a.loop:
            end = min((now // 900 + 1) * 900 + 60, deadline)    # bir sonraki 15m mum kapanışı + 1 dk
        else:
            end = now + a.listen
        if end - now > 10:
            listen(token, chat, state, ex, int(end - now), a.capital or 100.0, paper_tick=tick,
                   paper_status=lambda: PP.status_text(pst), extra_poll=side_polls)
        save_state(a.state, state)
        try:
            tick()
        except Exception as e:
            print("demo takip hatası:", type(e).__name__, e)
        if not a.loop or time.time() > deadline - 90:
            break
    publish_paper([ppath, p2path, p3path])
    save_state(a.state, state)


PAPER_RAW = "https://raw.githubusercontent.com/{repo}/demo-data/{name}"


def restore_paper(path, name="paper.json"):
    """Önbellek kaybolursa demo hesabı yayınlanan son kopyadan geri yükle."""
    repo = os.getenv("GITHUB_REPOSITORY")
    if not repo:
        return
    try:
        with urllib.request.urlopen(PAPER_RAW.format(repo=repo, name=name), timeout=20) as r:
            data = r.read()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        print("Demo hesap yayınlanan kopyadan geri yüklendi.")
    except Exception as e:
        print("Demo hesap geri yüklenemedi (ilk çalışma olabilir):", type(e).__name__)


def publish_paper(paths):
    """Demo hesapları siteye yayınla: json dosyalarını 'demo-data' dalına tek commit olarak yazar."""
    tok, repo = os.getenv("GH_TOKEN"), os.getenv("GITHUB_REPOSITORY")
    paths = [p for p in paths if os.path.exists(p)]
    if not (tok and repo and paths):
        return
    import shutil
    import subprocess
    import tempfile
    d = tempfile.mkdtemp()
    for p in paths:
        shutil.copy(p, os.path.join(d, os.path.basename(p)))
    cmds = [["git", "init", "-q", "-b", "demo-data"], ["git", "add", "-A"],
            ["git", "-c", "user.name=demo-bot", "-c", "user.email=demo-bot@users.noreply.github.com", "commit", "-q", "-m", "demo hesap"],
            ["git", "push", "-q", "-f", f"https://x-access-token:{tok}@github.com/{repo}.git", "demo-data"]]
    try:
        for c in cmds:
            subprocess.run(c, cwd=d, check=True, capture_output=True, timeout=60)
    except Exception as e:
        print("Demo hesap yayınlanamadı:", type(e).__name__)      # komut satırı token içerdiği için yazdırılmaz
    finally:
        shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    main()
