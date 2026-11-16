"""
QUANT FUTURES — MEXC USDT-M analiz ve sinyal paneli (Streamlit).
Yerelde:  streamlit run app.py
Sayfalar: Coin Analizi · Demo Kasa · Sinyal Tarayıcı · Backtest · Yöntem
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from plotly.subplots import make_subplots

import analysis as A
import backtest as B
import config as C
import data as D
import indicators as I
import scanner as SC

st.set_page_config(page_title="Quant Futures · MEXC", page_icon="📈", layout="wide")

GREEN, RED, GOLD, GREY = "#16c784", "#ea3943", "#f0b90b", "#8b949e"

st.markdown("""
<style>
.block-container {padding-top: 1.6rem; max-width: 1400px;}
.hero {padding: 18px 22px; border-radius: 16px; margin-bottom: 14px;
       background: linear-gradient(120deg, #161b22 0%, #1f2630 60%, #2a2412 100%); border: 1px solid #30363d;}
.hero h1 {font-size: 1.7rem; margin: 0; color: #f0f6fc;}
.hero p {margin: 4px 0 0 0; color: #8b949e; font-size: .92rem;}
.card {background: #161b22; border: 1px solid #30363d; border-radius: 14px; padding: 14px 16px; height: 100%;}
.card h4 {margin: 0 0 6px 0; font-size: .82rem; color: #8b949e; font-weight: 600; letter-spacing: .04em; text-transform: uppercase;}
.card .big {font-size: 1.55rem; font-weight: 700; color: #f0f6fc; line-height: 1.2;}
.card .sub {font-size: .85rem; color: #8b949e; margin-top: 4px;}
.verdict {border-radius: 16px; padding: 18px 22px; margin: 6px 0 14px 0; border: 1px solid #30363d;}
.verdict .lbl {font-size: .8rem; letter-spacing: .08em; text-transform: uppercase; opacity: .8;}
.verdict .dir {font-size: 2.1rem; font-weight: 800; line-height: 1.15;}
.badge {display: inline-block; padding: 3px 10px; border-radius: 999px; font-size: .78rem; font-weight: 700; margin: 2px 4px 2px 0;}
.b-up {background: rgba(22,199,132,.15); color: #16c784; border: 1px solid rgba(22,199,132,.4);}
.b-dn {background: rgba(234,57,67,.15); color: #ea3943; border: 1px solid rgba(234,57,67,.4);}
.b-nt {background: rgba(139,148,158,.15); color: #c9d1d9; border: 1px solid rgba(139,148,158,.4);}
.b-gd {background: rgba(240,185,11,.15); color: #f0b90b; border: 1px solid rgba(240,185,11,.4);}
.pbar {margin: 8px 0 12px 0;}
.pbar .row {display: flex; justify-content: space-between; font-size: .88rem; color: #c9d1d9; margin-bottom: 3px;}
.pbar .track {background: #21262d; border-radius: 8px; height: 12px; overflow: hidden;}
.pbar .fill {height: 12px; border-radius: 8px;}
.pbar .base {font-size: .76rem; color: #8b949e; margin-top: 2px;}
.plan td, .plan th {padding: 6px 10px; border-bottom: 1px solid #21262d; font-size: .9rem;}
.plan table {width: 100%; border-collapse: collapse;}
.plan th {color: #8b949e; text-align: left; font-weight: 600;}
.warnbox {background: rgba(240,185,11,.08); border: 1px solid rgba(240,185,11,.35); border-radius: 12px; padding: 10px 14px;
          color: #e3b341; font-size: .88rem; margin: 6px 0 12px 0;}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- veri (önbellekli)

@st.cache_resource(show_spinner=False)
def exchange():
    return D.get_exchange()


@st.cache_resource(show_spinner=False)
def oi_store():
    return {}


@st.cache_data(ttl=600, show_spinner=False)
def universe(n: int):
    syms, tickers = D.pick_universe(exchange(), n)
    return syms, {s: tickers.get(s) for s in syms}


@st.cache_data(ttl=60, show_spinner=False)
def candles(sym: str, tf: str, bars: int) -> pd.DataFrame:
    return D.fetch_ohlcv(exchange(), sym, tf, bars)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def history(sym: str, days: int) -> pd.DataFrame:
    ex = exchange()
    return D.fetch_ohlcv(ex, sym, C.BASE_TF, since_ms=ex.milliseconds() - days * 86_400_000)


@st.cache_data(ttl=60, show_spinner=False)
def live_for(sym: str) -> dict:
    ex = exchange()
    try:
        t = ex.fetch_ticker(sym)
    except Exception:
        t = None
    out = D.live_snapshot(ex, sym, t, oi_store())
    if t and t.get("last"):
        out["last"] = float(t["last"])
        out["change_24h"] = t.get("percentage")
    return out


# ---------------------------------------------------------------- yardımcı HTML

def badge(text, kind):
    return f'<span class="badge b-{kind}">{text}</span>'


def dir_badge(d):
    return badge({1: "▲ Yukarı", -1: "▼ Aşağı", 0: "■ Kararsız"}[d], {1: "up", -1: "dn", 0: "nt"}[d])


def card(title, big, sub="", color="#f0f6fc"):
    return f'<div class="card"><h4>{title}</h4><div class="big" style="color:{color}">{big}</div><div class="sub">{sub}</div></div>'


def pbar(label, p, color, base=None, note=""):
    if p is None or not np.isfinite(p):
        return ""
    b = f'<div class="base">Tüm geçmiş ortalaması: %{base * 100:.0f} {note}</div>' if base is not None else (
        f'<div class="base">{note}</div>' if note else "")
    return (f'<div class="pbar"><div class="row"><span>{label}</span><b>%{p * 100:.0f}</b></div>'
            f'<div class="track"><div class="fill" style="width:{max(p * 100, 1):.1f}%;background:{color}"></div></div>{b}</div>')


# ---------------------------------------------------------------- sayfa seçimi

PAGES = ["🔍 Coin Analizi", "💰 Demo Kasa", "📡 Sinyal Tarayıcı", "🧪 Backtest", "📘 Yöntem"]
qp = st.query_params.get("p", "0") if hasattr(st, "query_params") else "0"
try:
    default_page = min(max(int(qp), 0), len(PAGES) - 1)
except (TypeError, ValueError):
    default_page = 0

with st.sidebar:
    st.markdown("### 📈 Quant Futures")
    page = st.radio("Sayfa", PAGES, index=default_page, label_visibility="collapsed")
    try:
        st.query_params["p"] = str(PAGES.index(page))
    except Exception:
        pass
    st.divider()

st.markdown("""<div class="hero"><h1>📈 Quant Futures · MEXC</h1>
<p>Coin analizi · geçmişten ölçülen olasılıklar · tahmini likidasyon haritası · kasa planı · LONG/SHORT sinyal tarayıcı.
Eğitim amaçlıdır, yatırım tavsiyesi değildir.</p></div>""", unsafe_allow_html=True)

try:
    ex = exchange()
except Exception as e:
    st.error(f"MEXC Futures'a bağlanılamadı: {e}")
    st.stop()


# ================================================================= 1) COİN ANALİZİ

def page_analysis():
    try:
        coins, tick = universe(400)
    except Exception as e:
        st.error(f"Kontrat listesi alınamadı: {e}")
        return
    names = [s.split("/")[0] for s in coins]
    default = names.index("SOL") if "SOL" in names else 0
    c1, c2, c3, c4, c5, c6 = st.columns([1.4, 1, 1, 1, 1, 1])
    base = c1.selectbox("Coin (MEXC USDT-M vadeli, hacme göre sıralı)", names, index=default)
    side_choice = c2.selectbox("Yön", ["Otomatik", "LONG", "SHORT"],
                               help="Otomatik: plan, analizin gösterdiği yöne göre kurulur.")
    capital = c3.number_input("Kasa ($)", min_value=10.0, value=100.0, step=10.0)
    leverage = int(c4.number_input("Kaldıraç (x)", min_value=1, max_value=125, value=10, step=1))
    first_pct = c5.number_input("İlk giriş (kasanın %)", min_value=5, max_value=100, value=50, step=5) / 100
    add_pct = c6.number_input("Planlı ekleme (%)", min_value=0, max_value=95, value=25, step=5) / 100
    if first_pct + add_pct > 1:
        st.error("İlk giriş + ekleme kasanın %100'ünü geçemez.")
        return
    sym = coins[names.index(base)]

    with st.spinner(f"{base} analiz ediliyor: 15m / 1h / 4h / 1g veri, olasılıklar, likidasyon haritası..."):
        try:
            d15 = candles(sym, "15m", 400)
            h1 = candles(sym, "1h", 1500)
            h4 = candles(sym, "4h", 600)
            d1 = candles(sym, "1d", 200)
            btc1h = candles(C.BTC, "1h", 300)
            live = live_for(sym)
        except Exception as e:
            st.error(f"{base} verisi alınamadı: {e}")
            return
        if len(h1) < 300:
            st.warning(f"{base} için yeterli geçmiş yok ({len(h1)} adet 1H mum). Yeni listelenmiş olabilir; olasılıklar güvenilmez.")
            if len(h1) < 120:
                return
        r = A.analyze(sym, d15, h1, h4, d1, btc1h, live, capital, leverage, first_pct, add_pct, side_choice)

    # ---- karar bandı
    d, s = r["direction"], r["side"]
    col = {1: GREEN, -1: RED, 0: GREY}[d]
    dtxt = {1: "LONG eğilimli", -1: "SHORT eğilimli", 0: "Net yön yok — BEKLE"}[d]
    chg = live.get("change_24h")
    st.markdown(f"""<div class="verdict" style="background:linear-gradient(120deg,{col}22,#161b22 70%);border-color:{col}66">
<div class="lbl" style="color:{col}">{base}USDT · yön tahmini</div>
<div class="dir" style="color:{col}">{dtxt}</div>
<div style="color:#c9d1d9;margin-top:6px">Güven: <b>{r['confidence']}</b></div>
<div style="margin-top:8px">{badge(f"Gösterge eğilimi {r['score']:+.0f}/100", 'up' if r['score'] >= 25 else 'dn' if r['score'] <= -25 else 'nt')}
{badge(f"Plan yönü: {'LONG' if s == 1 else 'SHORT'}{' (senin seçimin)' if r['side_forced'] else ''}", 'up' if s == 1 else 'dn')}
{badge(f"BTC 1H: {A.DIR_TXT[r['btc_dir']]}", {1: 'up', -1: 'dn', 0: 'nt'}[r['btc_dir']])}</div></div>""", unsafe_allow_html=True)

    k = st.columns(5)
    k[0].markdown(card("Fiyat", A.fmt_px(r["price"]), f"24s: {chg:+.2f}%" if isinstance(chg, (int, float)) else "son işlem",
                       GREEN if (chg or 0) >= 0 else RED), unsafe_allow_html=True)
    k[1].markdown(card("1H ATR (oynaklık)", f"%{r['atr_pct']:.2f}", f"≈ {A.fmt_px(r['atr1h'])} / saat"), unsafe_allow_html=True)
    fr = r["funding"]
    k[2].markdown(card("Funding", f"{fr * 100:+.4f}%" if fr is not None else "—",
                       "pozitif: longlar shortlara öder" if (fr or 0) > 0 else "negatif: shortlar longlara öder",
                       RED if (fr or 0) > 0.0005 else GREEN if (fr or 0) < -0.0005 else "#f0f6fc"), unsafe_allow_html=True)
    oi = live.get("oi_usdt")
    k[3].markdown(card("Açık pozisyon (OI)", f"{oi / 1e6:,.1f}M$" if oi else "—",
                       f"değişim {live['oi_change'] * 100:+.1f}%" if live.get("oi_change") is not None else "önceki ölçüm yok"),
                  unsafe_allow_html=True)
    sp = live.get("spread_pct")
    k[4].markdown(card("Spread / derinlik", f"%{sp:.3f}" if sp is not None else "—",
                       f"±%0.5 derinlik ≈ {live['depth_usdt'] / 1e3:,.0f}k$" if live.get("depth_usdt") else ""), unsafe_allow_html=True)

    # ---- zaman dilimleri
    st.markdown("#### ⏱️ Zaman dilimleri")
    tcols = st.columns(len(r["tfs"]))
    for col_, (tf, t) in zip(tcols, [(x, r["tfs"][x]) for x in ("15m", "1h", "4h", "1g") if x in r["tfs"]]):
        rsi_k = "dn" if t["rsi"] >= 70 else "up" if t["rsi"] <= 30 else "nt"
        col_.markdown(f"""<div class="card"><h4>{tf}</h4>{dir_badge(t['dir'])}
<div class="sub">RSI {badge(f"{t['rsi']:.0f}", rsi_k)} MACD {badge('+' if t['macd_up'] else '−', 'up' if t['macd_up'] else 'dn')}</div>
<div class="sub">ADX {t['adx']:.0f} · EMA200 {'üstünde' if t['above200'] == 1 else 'altında'} · son 4 mum {t['ret']:+.1f}%</div></div>""",
                      unsafe_allow_html=True)

    # ---- olasılıklar + plan
    left, right = st.columns([1, 1.25])
    with left:
        pr = r["prob"]
        st.markdown("#### 🎲 Olasılıklar (geçmişten ölçülen)")
        st.caption(f"{base}'in kendi 1H geçmişinde bugüne benzeyen **{pr['n']}** an bulundu "
                   f"(eşleşen: {', '.join(pr['keys']) or 'hepsi'}). Hata payı ±{pr['moe']:.0f} puan. "
                   f"Ardışık saatler birbirine benzediği için gerçek bağımsız örnek sayısı daha azdır.")
        html = ""
        for sc, colr, key in zip(r["scenarios"], (GREEN, RED, GOLD), ("up", "down", "range")):
            html += pbar(f"{sc['name']} — tetik: {sc['trigger']}", sc["p"], colr, pr["dir_base"][key],
                         f"· hedef: {sc['targets']}")
        st.markdown(html, unsafe_allow_html=True)
        st.markdown("#### 📐 Planın tutma olasılığı")
        for nm, sd in (("LONG", 1), ("SHORT", -1)):
            p = pr["long" if sd == 1 else "short"]
            st.markdown(pbar(f"{nm}: TP1 stoptan önce", p["tp1"]["win"], GREEN if sd == 1 else RED, None,
                             f"stop önce %{p['tp1']['loss'] * 100:.0f} · TP2 önce %{p['tp2']['win'] * 100:.0f} · "
                             f"{pr['horizon_h']} saatte sonuçsuz %{p['tp1']['none'] * 100:.0f}"), unsafe_allow_html=True)
        with st.expander("Gösterge eğilimi nasıl hesaplandı?"):
            st.dataframe(pd.DataFrame([{"Bileşen": a, "Puan": round(b, 1)} for a, b in r["parts"]]),
                         hide_index=True, use_container_width=True)

    with right:
        z, p = r["sized"], r["plan"]
        st.markdown(f"#### 🧭 Kasa planı — {'🟢 LONG' if s == 1 else '🔴 SHORT'} · {z['leverage']}x · kasa {z['capital']:.0f}\\$")
        if z["liq_before_stop_a"] or z["liq_before_stop_b"]:
            st.markdown(f'<div class="warnbox">🚨 {z["leverage"]}x kaldıraçta likidasyon stoptan önce geliyor. '
                        f'Bu stop için en fazla <b>{z["safe_lev"]}x</b>.</div>', unsafe_allow_html=True)
        rows = [("1. Giriş", A.fmt_px(p["entry"]), f"{z['m1']:.2f}$ marjin · {z['notional1']:.0f}$ pozisyon"),
                ("2. Planlı ekleme", A.fmt_px(p["add"]), f"{z['m2']:.2f}$ marjin · %{z['add_pct_move']:.1f} aleyhte" if z["m2"] > 0 else "yok"),
                ("Ortalama (ekleme dolarsa)", A.fmt_px(z["avg_b"]), ""),
                ("Yedek (girmez)", "—", f"{z['reserve']:.2f}$"),
                ("STOP", A.fmt_px(p["stop"]), f"−{z['loss_a']:.2f}$ (%{z['loss_a_pct']:.0f}) · eklemeyle −{z['loss_b']:.2f}$ (%{z['loss_b_pct']:.0f})"),
                ("Likidasyon", A.fmt_px(z["liq_a"]), f"eklemeyle {A.fmt_px(z['liq_b'])}")]
        for i_, t in enumerate(z["tps"]):
            rows.append((f"TP{i_ + 1} (%{int(C.TP_SPLIT[i_] * 100)} kapat)", A.fmt_px(t["price"]),
                         f"+{t['gain_a']:.2f}$ · eklemeyle +{t['gain_b']:.2f}$ · %{t['pct_move']:.1f} hareket"))
        colors = {"STOP": RED, "Likidasyon": "#ff7b72"}
        trs = "".join(f"<tr><td style='color:{colors.get(a, GREEN if a.startswith('TP') else '#c9d1d9')}'><b>{a}</b></td>"
                      f"<td><b>{b}</b></td><td style='color:#8b949e'>{c}</td></tr>" for a, b, c in rows)
        st.markdown(f'<div class="card plan"><table><tr><th>Adım</th><th>Fiyat</th><th>Kasa etkisi</th></tr>{trs}</table></div>',
                    unsafe_allow_html=True)
        st.markdown("#### 💬 Yorum")
        with st.container(border=True):
            st.markdown(r["comment"].replace("$", "\\$"))

    # ---- grafik + likidasyon haritası
    st.markdown("#### 🗺️ 1H grafik + tahmini likidasyon haritası")
    f = h1.tail(240)
    lm = r["liq"]
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, column_widths=[0.78, 0.22], horizontal_spacing=0.01)
    fig.add_trace(go.Candlestick(x=f.index, open=f.open, high=f.high, low=f.low, close=f.close, name="1H",
                                 increasing_line_color=GREEN, decreasing_line_color=RED), row=1, col=1)
    fig.add_trace(go.Scatter(x=f.index, y=I.ema(h1["close"], 50).tail(240), name="EMA50", line=dict(color="#58a6ff", width=1.2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=f.index, y=I.ema(h1["close"], 200).tail(240), name="EMA200", line=dict(color="#bc8cff", width=1.2)), row=1, col=1)
    lines = [(p["entry"], "GİRİŞ", "#c9d1d9", "solid"), (p["add"], "EKLE", GOLD, "dash"), (p["stop"], "STOP", RED, "solid"),
             (z["liq_a"], "LİKİDASYON", "#ff7b72", "dot")] + [(t["price"], f"TP{i_ + 1}", GREEN, "dot") for i_, t in enumerate(z["tps"])]
    for y, nm, colr, dash in lines:
        fig.add_hline(y=y, line_color=colr, line_dash=dash, line_width=1, annotation_text=nm,
                      annotation_position="top left", annotation_font_color=colr, row=1, col=1)
    lo_y, hi_y = min(f.low.min(), p["stop"], z["liq_a"]) * 0.995, max(f.high.max(), z["tps"][-1]["price"]) * 1.005
    m = (lm["mids"] >= lo_y) & (lm["mids"] <= hi_y)
    fig.add_trace(go.Bar(y=lm["mids"][m], x=lm["long"][m], orientation="h", name="Long likidasyon (tahmini)",
                         marker_color="rgba(22,199,132,.65)"), row=1, col=2)
    fig.add_trace(go.Bar(y=lm["mids"][m], x=lm["short"][m], orientation="h", name="Short likidasyon (tahmini)",
                         marker_color="rgba(234,57,67,.65)"), row=1, col=2)
    fig.update_yaxes(range=[lo_y, hi_y])
    fig.update_layout(height=640, template="plotly_dark", xaxis_rangeslider_visible=False, bargap=0.05,
                      margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.06),
                      paper_bgcolor="#0d1117", plot_bgcolor="#0d1117")
    st.plotly_chart(fig, use_container_width=True)
    a1, a2 = st.columns(2)
    a1.markdown("**Üstte (short likidasyonları — yukarı çeker):** " + (
        " · ".join(f"{A.fmt_px(c['price'])} ({c['dist_pct']:+.1f}%)" for c in lm["above"]) or "—"))
    a2.markdown("**Altta (long likidasyonları — aşağı çeker):** " + (
        " · ".join(f"{A.fmt_px(c['price'])} ({c['dist_pct']:+.1f}%)" for c in lm["below"]) or "—"))
    st.caption("Likidasyon haritası bir **modeldir**, gerçek veri değildir: son 30 günün 1H hacmi, varsayılan kaldıraç dağılımı "
               f"({', '.join(f'{k}x %{int(v * 100)}' for k, v in A.LEV_MIX.items())}) ve %{A.MMR * 100:.1f} bakım marjiniyle "
               "hesaplanır; fiyatın sonradan geçtiği seviyeler patlamış sayılıp silinir. Coinglass gibi ücretli kaynaklarla "
               "karşılaştırarak kullan.")

    with st.expander("📍 Önemli seviyeler"):
        lv = [{"Seviye": nm, "Fiyat": A.fmt_px(v), "Uzaklık": f"{(v / r['price'] - 1) * 100:+.2f}%", "Taraf": "Direnç"}
              for v, nm in r["levels"]["up"]] + \
             [{"Seviye": nm, "Fiyat": A.fmt_px(v), "Uzaklık": f"{(v / r['price'] - 1) * 100:+.2f}%", "Taraf": "Destek"}
              for v, nm in r["levels"]["down"]]
        st.dataframe(pd.DataFrame(lv), hide_index=True, use_container_width=True)
    st.caption(f"Veri: MEXC USDT-M Futures · son kapanan 15m mum {(r['bar_time'] + pd.Timedelta(minutes=15) + pd.Timedelta(hours=3)):%d.%m %H:%M} TSİ. "
               "Olasılıklar geçmişe dayanır ve geleceği garanti etmez. Yatırım tavsiyesi değildir.")


# ================================================================= 2) DEMO KASA

def page_demo():
    st.markdown("#### 💰 Demo kasa: 100$ → 1000$ hedefi gerçekçi mi?")
    st.markdown('<div class="warnbox">Bu sayfa hedefe ulaşmanın <b>matematiğini</b> gösterir. Kendi isabet oranını '
                've R/R değerini girersen, binlerce olası 10 günü simüle eder ve hedefe ulaşma / kasayı kaybetme '
                'olasılığını hesaplar.</div>', unsafe_allow_html=True)
    c = st.columns(4)
    start = c[0].number_input("Başlangıç ($)", min_value=10.0, value=100.0, step=10.0)
    target = c[1].number_input("Hedef ($)", min_value=20.0, value=1000.0, step=50.0)
    days = int(c[2].number_input("Gün", min_value=1, max_value=90, value=10, step=1))
    tpd = int(c[3].number_input("Günlük işlem sayısı", min_value=1, max_value=20, value=3, step=1))
    c = st.columns(3)
    risk = c[0].number_input("İşlem başı risk (bakiyenin %)", min_value=0.5, max_value=100.0, value=10.0, step=0.5) / 100
    win = c[1].number_input("İsabet oranı (%)", min_value=5.0, max_value=95.0, value=50.0, step=1.0) / 100
    rr = c[2].number_input("Ortalama kazanç / kayıp (R/R)", min_value=0.2, max_value=10.0, value=2.0, step=0.1)

    path, g = A.target_path(start, target, days)
    mc = A.monte_carlo(start, target, days, tpd, risk, win, rr)
    k = st.columns(5)
    k[0].markdown(card("Gereken günlük büyüme", f"%{g * 100:.1f}", f"{days} gün boyunca her gün", GOLD), unsafe_allow_html=True)
    k[1].markdown(card("Hedefe ulaşma", f"%{mc['p_target'] * 100:.1f}", f"{mc['n_trades']} işlemde", GREEN), unsafe_allow_html=True)
    k[2].markdown(card("Kasayı kaybetme", f"%{mc['p_bust'] * 100:.1f}", "bakiye 10$ altına iner", RED), unsafe_allow_html=True)
    k[3].markdown(card("Ortanca sonuç", f"{mc['median']:.0f}$", f"%10: {mc['p10']:.0f}$ · %90: {mc['p90']:.0f}$"), unsafe_allow_html=True)
    k[4].markdown(card("Kasayı bitiren seri", f"{mc['streak_to_bust']} kayıp", "üst üste bu kadar stop → 10$ altı", RED), unsafe_allow_html=True)

    st.write("")
    note = (f"İşlem başı beklenti **{mc['exp_r']:+.2f}R**. Kelly formülüne göre bu isabet ve R/R için teorik üst sınır bakiyenin "
            f"**%{mc['kelly'] * 100:.0f}**'i; pratikte bunun yarısı bile çok agresiftir.")
    if mc["exp_r"] <= 0:
        note += " ⚠️ Beklenti negatif: bu değerlerle risk ne olursa olsun uzun vadede kasa erir."
    st.markdown(note)

    left, right = st.columns([1.4, 1])
    with left:
        cur = mc["curve"]
        x = np.arange(len(cur)) / tpd
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=x, y=cur[:, 2], line=dict(width=0), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=x, y=cur[:, 0], fill="tonexty", fillcolor="rgba(88,166,255,.15)", line=dict(width=0),
                                 name="Olası sonuçların %80'i"))
        fig.add_trace(go.Scatter(x=x, y=cur[:, 1], name="Ortanca bakiye", line=dict(color="#58a6ff", width=2.5)))
        fig.add_trace(go.Scatter(x=path["Gün"], y=path["Hedef bakiye ($)"], name="Hedef yolu", line=dict(color=GOLD, dash="dash")))
        fig.update_layout(height=420, template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                          xaxis_title="Gün", yaxis_title="Bakiye ($)", yaxis_type="log",
                          margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig, use_container_width=True)
    with right:
        st.markdown("**Günlük hedef tablosu**")
        st.dataframe(path, hide_index=True, use_container_width=True, height=420)

    st.markdown("#### Risk seviyesine göre (aynı isabet ve R/R ile)")
    rows = []
    for rp in (0.02, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50):
        m = A.monte_carlo(start, target, days, tpd, rp, win, rr, paths=6000)
        rows.append({"İşlem başı risk": f"%{rp * 100:.0f}", "Hedefe ulaşma": f"%{m['p_target'] * 100:.1f}",
                     "Kasayı kaybetme": f"%{m['p_bust'] * 100:.1f}", "Ortanca sonuç ($)": round(m["median"]),
                     "Kasayı bitiren kayıp serisi": m["streak_to_bust"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    st.markdown("#### Kasanın %50'si marjin olursa: stopta kasanın yüzde kaçı gider?")
    levs, stops = (3, 5, 10, 20, 25, 50), (0.5, 1, 2, 3, 5)
    tbl = {}
    for L in levs:
        col_ = {}
        for sp in stops:
            liq_d = (1 / L - A.MMR) * 100
            col_[f"stop %{sp:g}"] = "💥 likidasyon" if sp >= liq_d else f"%{min(50 * L * sp / 100, 50):.0f}"
        tbl[f"{L}x"] = col_
    st.dataframe(pd.DataFrame(tbl).T, use_container_width=True)
    st.caption("Örnek: %50 marjin + 20x + %3 stop → pozisyon kasanın 10 katı; %3 ters hareket kasanın %30'unu götürür. "
               "Likidasyon: izole marjinde yaklaşık 1/kaldıraç − bakım marjini kadar ters harekette pozisyon kapanır. "
               "Simülasyonda komisyon ve kayma yoktur; gerçekte sonuçlar biraz daha kötüdür.")


# ================================================================= 3) SİNYAL TARAYICI

def page_scanner():
    with st.sidebar:
        st.subheader("Tarayıcı ayarları")
        mode = st.radio("Mod", list(C.MODES), index=list(C.MODES).index(C.DEFAULT_MODE), horizontal=True,
                        help="Agresif: eşik 65, hacim ve BTC şartı yok, net R/R ≥ 1.2, işlem başı %1 risk. "
                             "Dengeli: dokümandaki kurallar (eşik 85, tüm şartlar, %0.5 risk).")
        C.apply_mode(mode)
        threshold = st.slider("Sinyal eşiği (puan /100)", 50, 95, C.SCORE_THRESHOLD, 5, key=f"thr_{mode}")
        top_n = st.slider("Taranacak kontrat sayısı", 10, 60, C.TOP_N, 5)
        capital = st.number_input("Sermaye (USDT, isteğe bağlı)", min_value=0.0, value=0.0, step=100.0)
        risk_pct = st.number_input("İşlem başı risk (%)", 0.1, 3.0, C.RISK_PCT * 100, 0.1, key=f"risk_{mode}") / 100
        C.RISK_PCT = risk_pct
        daily_loss = st.number_input("Bugünkü gerçekleşen zarar (%)", 0.0, 10.0, 0.0, 0.1)
        open_risk = st.number_input("Açık pozisyonların toplam riski (%)", 0.0, 10.0, 0.0, 0.1)
        if st.button("🔄 Şimdi yeniden tara", use_container_width=True):
            candles.clear()
        st.caption(f"Sinyaller kapanmış 15m mumlardan üretilir ve {C.SIGNAL_VALID_BARS} mum listede kalır. "
                   "Bu sayfa her mum kapanışından ~1 dk sonra kendini yeniler.")
    risk_state = {"daily_loss_pct": daily_loss, "open_risk_pct": open_risk, "risk_pct": risk_pct * 100}
    components.html("""<script>
const now = new Date(); const ms = 15 * 60 * 1000;
const wait = ms - (now.getTime() % ms) + 60 * 1000;
setTimeout(() => { try { window.parent.location.reload(); } catch (e) { location.reload(); } }, wait);
</script>""", height=0)

    try:
        symbols, tickers = universe(top_n)
    except Exception as e:
        st.error(f"Kontrat listesi alınamadı: {e}")
        return
    bar = st.progress(0.0, "Taranıyor...")
    out = SC.scan(
        fetch=lambda s, tf, n: candles(s, tf, n),
        live_fn=lambda s: D.live_snapshot(ex, s, tickers.get(s), oi_store()),
        rules_fn=lambda s: D.market_rules(ex, s),
        symbols=symbols, threshold=threshold, capital=capital or None, risk_pct=risk_pct, risk_state=risk_state,
        now_ms=ex.milliseconds(), stale_fn=D.is_stale, progress=lambda p, s: bar.progress(p, f"Taranıyor: {s}"),
    )
    bar.empty()
    res = out["results"]
    signals = [r for r in res if r["decision"] != "İŞLEM YOK"]
    last_bar = max((r["bar_time"] for r in res), default=None)

    k = st.columns(4)
    k[0].markdown(card("LONG sinyal", sum(r["decision"] == "LONG" for r in signals), f"mod: {mode}", GREEN), unsafe_allow_html=True)
    k[1].markdown(card("SHORT sinyal", sum(r["decision"] == "SHORT" for r in signals), f"eşik {threshold}", RED), unsafe_allow_html=True)
    k[2].markdown(card("BTC 1H trend", {1: "Yukarı", -1: "Aşağı", 0: "Kararsız"}[out["btc_dir"]], "",
                       {1: GREEN, -1: RED, 0: GREY}[out["btc_dir"]]), unsafe_allow_html=True)
    k[3].markdown(card("Son kapanan 15m mum",
                       f"{(last_bar + pd.Timedelta(minutes=15) + pd.Timedelta(hours=3)):%H:%M} TSİ" if last_bar is not None else "—",
                       f"{len(res)} kontrat tarandı"), unsafe_allow_html=True)
    st.write("")
    tab_sig, tab_all, tab_chart = st.tabs(["Sinyaller", "Tüm tarama", "Grafik"])
    with tab_sig:
        if risk_state["daily_loss_pct"] >= C.DAILY_MAX_LOSS_PCT * 100:
            st.error("Günlük maksimum zarar limitine ulaşıldı: bugün yeni sinyal verilmez.")
        if not signals:
            st.info(f"Şu an {len(res)} kontrattan hiçbiri tüm zorunlu şartları ve {threshold} puan eşiğini sağlamıyor → **İŞLEM YOK**. "
                    "En yakın adaylar ve son 24 saatin sinyalleri aşağıda. İstediğin coini **🔍 Coin Analizi** sayfasında inceleyebilirsin.")
        for r in sorted(signals, key=lambda r: -r["score"]):
            colr = GREEN if r["decision"] == "LONG" else RED
            age = f"{r['age']} mum önce oluştu, hâlâ geçerli" if r.get("age") else "yeni"
            st.markdown(f"""<div class="verdict" style="background:linear-gradient(120deg,{colr}22,#161b22 70%);border-color:{colr}66;padding:12px 18px">
<span class="dir" style="font-size:1.4rem;color:{colr}">{'▲' if r['decision'] == 'LONG' else '▼'} {r['symbol'].split(':')[0]} — {r['decision']}</span>
&nbsp; {badge(f"{r['score']:.0f}/100", 'gd')} {badge(age, 'nt')}</div>""", unsafe_allow_html=True)
            st.code(SC.card(r, risk_pct), language=None)
        st.subheader("🕘 Son 24 saatte verilen sinyaller")
        hist_df = SC.recent_signals(res, threshold)
        if hist_df.empty:
            st.caption("Son 24 saatte bu modun şartlarını sağlayan sinyal yok.")
        else:
            done = hist_df[hist_df["Durum"] != "Devam ediyor"]
            st.caption(f"{len(hist_df)} sinyal · kapanan {len(done)} işlemin toplamı {done['Sonuç (R)'].sum():+.2f} R "
                       "(1R = işlem başı risk; backtest kurallarıyla, teknik puanla hesaplanır).")
            st.dataframe(hist_df, hide_index=True, use_container_width=True)
        near = sorted([r for r in res if r["decision"] == "İŞLEM YOK"], key=lambda r: (len(r["failed"]) + len(r["blockers"]), -r["score"]))[:5]
        if near:
            st.subheader("👀 En yakın adaylar (henüz İŞLEM YOK)")
            for r in near:
                with st.expander(f"{r['symbol'].split(':')[0]} · {r['side']} · {r['score']:.0f}/100 · eksik şart: {len(r['failed']) + len(r['blockers'])}"):
                    st.code(SC.card(r, risk_pct), language=None)
    with tab_all:
        rows = [{"Kontrat": r["symbol"].split(":")[0], "Karar": r["decision"], "En iyi yön": r["side"], "Puan": round(r["score"]),
                 "Teknik /85": round(r["tech"]), "Trend /20": r["comps"]["trend"], "Yapı /20": r["comps"]["yapi"],
                 "Momentum /15": r["comps"]["momentum"], "BTC/ETH /10": r["comps"]["piyasa"],
                 "Eksik şart": len(r["failed"]) + len(r["blockers"]),
                 "İlk eksik": (r["blockers"] + r["failed"])[0] if (r["blockers"] + r["failed"]) else "—"} for r in res]
        if rows:
            st.dataframe(pd.DataFrame(rows).sort_values(["Karar", "Eksik şart", "Puan"], ascending=[True, True, False]),
                         hide_index=True, use_container_width=True)
        if out["errors"]:
            with st.expander(f"Veri alınamayan kontratlar ({len(out['errors'])})"):
                st.write("\n".join(out["errors"]))
    with tab_chart:
        if res:
            pick = st.selectbox("Kontrat", [r["symbol"] for r in sorted(res, key=lambda r: -r["score"])])
            r = next(x for x in res if x["symbol"] == pick)
            f = r["features"].tail(160)
            fig = go.Figure()
            fig.add_candlestick(x=f.index, open=f.open, high=f.high, low=f.low, close=f.close, name="15m",
                                increasing_line_color=GREEN, decreasing_line_color=RED)
            for col, nm, colr in (("ema21", "EMA21", "#bc8cff"), ("ema50", "EMA50", "#58a6ff"), ("vwap", "VWAP", GOLD)):
                fig.add_scatter(x=f.index, y=f[col], name=nm, line=dict(color=colr, width=1.2))
            for P, sym_, colr, y in (("L", "triangle-up", GREEN, f.low * 0.997), ("S", "triangle-down", RED, f.high * 1.003)):
                msk = f[f"{P}_gates_ok"]
                fig.add_scatter(x=f.index[msk], y=y[msk], mode="markers", name=f"{'LONG' if P == 'L' else 'SHORT'} şartları",
                                marker=dict(symbol=sym_, size=11, color=colr))
            if "plan" in r:
                p = r["plan"]
                fig.add_hline(y=p["stop"], line_color=RED, annotation_text="STOP")
                for i_, (px, _, _) in enumerate(p["tps"]):
                    fig.add_hline(y=px, line_color=GREEN, line_dash="dot", annotation_text=f"TP{i_ + 1}")
            fig.update_layout(height=600, template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                              xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.06))
            st.plotly_chart(fig, use_container_width=True)


# ================================================================= 4) BACKTEST

def page_backtest():
    with st.sidebar:
        mode = st.radio("Mod", list(C.MODES), index=list(C.MODES).index(C.DEFAULT_MODE), horizontal=True, key="bt_mode")
        C.apply_mode(mode)
    st.write("Tarayıcı stratejisini MEXC Futures 15m geçmiş verisinde test eder: **train / validation / out-of-sample**, "
             "**walk-forward**, eşik optimizasyonu, rejim ve yön kırılımı; komisyon + kayma + funding dahil.")
    cc1, cc2 = st.columns(2)
    days = cc1.slider("Gün", 30, 730, C.BT_DAYS_DEFAULT, 30)
    ncoins = cc2.slider("Coin sayısı", 3, 30, C.BT_COINS_DEFAULT)
    with st.expander("Varsayımlar"):
        st.markdown(B.__doc__)
    if st.button("▶️ Backtest'i çalıştır"):
        symbols, _ = universe(max(ncoins, 10))
        syms = symbols[:ncoins]
        prog = st.progress(0.0, "Geçmiş veri indiriliyor...")
        hist = {}
        allsyms = [C.BTC, C.ETH] + syms
        for i, s in enumerate(allsyms):
            try:
                hist[s] = history(s, days)
            except Exception as e:
                st.warning(f"{s}: {e}")
            prog.progress((i + 1) / len(allsyms), f"İndiriliyor: {s}")
        prog.empty()
        btc, eth = hist.pop(C.BTC), hist.pop(C.ETH, None)
        with st.spinner("Geleceği görme kontrolü..."):
            ok, bad = B.lookahead_check(next(iter(hist.values())), btc, eth, points=4)
        (st.success if ok else st.error)("Geleceği görme kontrolü: " + ("GEÇTİ" if ok else f"SORUN: {bad}"))
        with st.spinner("Göstergeler ve simülasyon..."):
            st.session_state["bt"] = B.run(B.build_all(hist, btc, eth))
    rep = st.session_state.get("bt")
    if not rep:
        return
    st.write(f"Dönem: {rep['start']:%Y-%m-%d} → {rep['end']:%Y-%m-%d} · aday sinyal: {rep['n_candidates']}")
    if "threshold" not in rep:
        st.warning("Bu dönemde zorunlu şartları sağlayan aday sinyal oluşmadı.")
        return

    def show_metrics(d):
        df = pd.DataFrame(d)
        return df.apply(lambda col: col.map(lambda v: f"{v:.3f}" if isinstance(v, (float, np.floating)) else v))
    st.subheader(f"Train / Validation / Out-of-sample (eşik train'de seçildi: {rep['threshold']:.0f})")
    st.dataframe(show_metrics(rep["splits"]), use_container_width=True)
    st.subheader("Walk-forward")
    st.dataframe(rep["walk_forward"], hide_index=True, use_container_width=True)
    st.subheader("Eşik duyarlılığı")
    st.dataframe(rep["sweep_all"], hide_index=True, use_container_width=True)
    if "by_regime" in rep:
        cA, cB = st.columns(2)
        cA.subheader("Rejime göre")
        cA.dataframe(show_metrics(rep["by_regime"].T).T, use_container_width=True)
        cB.subheader("Yöne göre")
        cB.dataframe(show_metrics(rep["by_side"].T).T, use_container_width=True)
        st.subheader("Coin bazında")
        st.dataframe(show_metrics(rep["by_symbol"].T).T, use_container_width=True)
        t = rep["trades"].sort_values("exit_time")
        fig = go.Figure()
        fig.add_scatter(x=t.exit_time, y=t.R.cumsum(), name="Kümülatif R", line=dict(color="#58a6ff", width=2))
        for bnd in rep["split_bounds"][1:-1]:
            fig.add_vline(x=bnd, line_dash="dash", line_color=GOLD)
        fig.update_layout(height=380, template="plotly_dark", paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                          yaxis_title="Kümülatif R", margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(t, hide_index=True, use_container_width=True)
    st.caption("Hayatta kalma yanlılığı: sadece bugün listede olan kontratlar test edilir. Backtest sonucu canlı performans garantisi değildir.")


# ================================================================= 5) YÖNTEM

def page_method():
    st.markdown(f"""
### 🔍 Coin Analizi nasıl çalışır?
1. **Zaman dilimleri:** 15m, 1h, 4h, 1g için EMA21/50 trend yönü, RSI14, MACD, ADX, EMA200 konumu.
2. **Gösterge eğilimi (−100…+100):** 1g %15 · 4h %20 · 1h %15 · 15m %10 trend ağırlığı + 1H momentum (10) + 1H yapı HH/HL (10)
   + BTC 1H (10) + likidasyon mıknatısı (5) + funding kalabalığı (5, ters yönde).
3. **Geçmişten ölçülen olasılık:** coinin son ~60 günlük 1H verisinde bugünkü durumla (4H trend, 1H trend, RSI bölgesi,
   EMA9/21) aynı olan anlar bulunur; her birinde fiyatın {A.DIR_H} saat içinde önce **+{A.DIR_ATR:g} ATR**'ye mi **−{A.DIR_ATR:g} ATR**'ye mi
   gittiği sayılır. Örnek 80'den azsa şartlar sırayla gevşetilir. Aynı mumda iki seviye de görülürse aleyhte sayılır.
4. **Yön tahmini:** gösterge eğilimi ve geçmiş istatistik aynı yönü gösterirse güven orta-yüksek; çelişirse "net yön yok".
5. **Plan:** giriş = güncel fiyat; ekleme = ilk anlamlı destek/direnç ya da likidite kümesi (0.6–3 ATR); stop = eklemenin ötesindeki
   bir sonraki yapı − 0.3 ATR (1.8–6 ATR); TP'ler = lehteki seviyeler, yaklaşık 1R / 2R / 3.2R. Likidasyon ≈ giriş × (1 ∓ 1/kaldıraç ± %{A.MMR * 100:.1f}).
6. **Likidasyon haritası (tahmini):** her 1H mumda hacimle orantılı pozisyon açıldığı ve kaldıraç dağılımının
   {', '.join(f'{k}x %{int(v * 100)}' for k, v in A.LEV_MIX.items())} olduğu varsayılır; fiyatın geçtiği seviyeler silinir, eski
   pozisyonlar {A.LIQ_HALF_LIFE_H} saat yarılanma süresiyle azaltılır.

### 📡 Sinyal tarayıcı puanlaması (100)
| Kriter | Maks. | Nasıl hesaplanır |
|---|---|---|
| Çoklu zaman dilimi trend | 20 | 4H ve 1H EMA21/50 yönü, 15m EMA9>21>50, 1H ADX>20 ve DI yönü |
| Piyasa yapısı / SMC | 20 | HH+HL (LH+LL), BOS/CHoCH, likidite süpürmesi, FVG, karşı seviyeye ≥1 ATR alan |
| Hacim + OI | 15 | Göreli hacim · funding · OI değişimi |
| Momentum | 15 | RSI14, RSI7, MACD, Supertrend(10,3) |
| Likidite / emir defteri | 10 | Spread, ±%0.5 derinlik, defter dengesizliği |
| BTC/ETH uyumu | 10 | BTC ve ETH 1H trendi |
| Volatilite + R/R | 10 | ATR%, Bollinger, VWAP tarafı, net R/R ≥ 2 |

### Sınırlar
Gerçek likidasyon verisi ücretsiz değil (harita modeldir) · taker al/sat hacmi ve long/short oranı MEXC herkese açık API'de yok ·
haber riski ölçülmez · geçmiş olasılıklar geleceği garanti etmez · 10 günde 10x hedefi yüksek kaldıraç gerektirir ve kasayı
kaybetme olasılığı yüksektir (Demo Kasa sayfasında hesaplanır). Bu site eğitim amaçlıdır, yatırım tavsiyesi değildir.
""")


{PAGES[0]: page_analysis, PAGES[1]: page_demo, PAGES[2]: page_scanner, PAGES[3]: page_backtest, PAGES[4]: page_method}[page]()
