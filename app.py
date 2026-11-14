"""
QUANT FUTURES — MEXC USDT-M LONG/SHORT sinyal paneli (Streamlit).
Yerelde:  streamlit run app.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import backtest as B
import config as C
import data as D
import indicators as I
import scanner as SC

st.set_page_config(page_title="Quant Futures · MEXC", page_icon="📈", layout="wide")


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


# ---------------------------------------------------------------- kenar çubuğu

with st.sidebar:
    st.header("Ayarlar")
    threshold = st.slider("Sinyal eşiği (puan /100)", 50, 95, C.SCORE_THRESHOLD, 5,
                          help="Doküman: 85. Not: 85 puan, %85 başarı ihtimali demek değildir. Backtest sekmesinde eşikleri karşılaştırabilirsin.")
    top_n = st.slider("Taranacak kontrat sayısı (24s hacme göre)", 10, 60, C.TOP_N, 5)
    st.subheader("Risk")
    capital = st.number_input("Sermaye (USDT, isteğe bağlı)", min_value=0.0, value=0.0, step=100.0,
                              help="Girilirse pozisyon miktarı kontrat cinsinden hesaplanır.")
    risk_pct = st.number_input("İşlem başı risk (%)", 0.1, 2.0, C.RISK_PCT * 100, 0.1) / 100
    daily_loss = st.number_input("Bugünkü gerçekleşen zarar (%)", 0.0, 10.0, 0.0, 0.1,
                                 help=f"%{C.DAILY_MAX_LOSS_PCT * 100:.0f} limitine ulaşınca yeni sinyal verilmez.")
    open_risk = st.number_input("Açık pozisyonlarının toplam riski (%)", 0.0, 10.0, 0.0, 0.1,
                                help=f"Toplam açık risk %{C.MAX_OPEN_RISK_PCT * 100:.0f}'yi geçecekse yeni sinyal verilmez.")
    if st.button("🔄 Şimdi yeniden tara", use_container_width=True):
        candles.clear()
    st.caption("Veri: MEXC USDT-M Futures (herkese açık API). Sinyaller sadece kapanmış 15m mumlardan üretilir; "
               "yeni sinyal için mum kapanışından (:00, :15, :30, :45) sonra yenile.")

risk_state = {"daily_loss_pct": daily_loss, "open_risk_pct": open_risk, "risk_pct": risk_pct * 100}

st.title("📈 Quant Futures — MEXC LONG/SHORT")
st.caption("Çoklu zaman dilimi trend · SMC piyasa yapısı · hacim/OI/funding · momentum · likidite · BTC/ETH uyumu · "
           "100 puan + zorunlu şartlar. İşlem açmak zorunda değil: kanıt yetersizse İŞLEM YOK. Yatırım tavsiyesi değildir.")

tab_sig, tab_all, tab_chart, tab_bt, tab_info = st.tabs(["Sinyaller", "Tüm tarama", "Grafik", "Backtest", "Yöntem"])

# ---------------------------------------------------------------- tarama

try:
    ex = exchange()
    symbols, tickers = universe(top_n)
except Exception as e:
    st.error(f"MEXC Futures'a bağlanılamadı: {e}")
    st.stop()

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

with tab_sig:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟢 LONG", sum(r["decision"] == "LONG" for r in signals))
    c2.metric("🔴 SHORT", sum(r["decision"] == "SHORT" for r in signals))
    c3.metric("BTC 1H trend", {1: "Yukarı", -1: "Aşağı", 0: "Kararsız"}[out["btc_dir"]])
    c4.metric("Son kapanan 15m mum", f"{(last_bar + pd.Timedelta(minutes=15) + pd.Timedelta(hours=3)):%H:%M} TSİ" if last_bar is not None else "—")
    if risk_state["daily_loss_pct"] >= C.DAILY_MAX_LOSS_PCT * 100:
        st.error("Günlük maksimum zarar limitine ulaşıldı: bugün yeni sinyal verilmez.")
    if not signals:
        st.info(f"Şu an {len(res)} kontrattan hiçbiri tüm zorunlu şartları ve {threshold} puan eşiğini sağlamıyor → **İŞLEM YOK**. "
                "Bu normaldir; sistem fırsat kovalamaz, kanıt bekler. En yakın adaylar aşağıda.")
    for r in sorted(signals, key=lambda r: -r["score"]):
        with st.container(border=True):
            st.markdown(f"### {'🟢' if r['decision'] == 'LONG' else '🔴'} {r['symbol'].split(':')[0]} — {r['decision']} · {r['score']:.0f}/100")
            st.code(SC.card(r, risk_pct), language=None)

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
                            increasing_line_color="#1a7f37", decreasing_line_color="#cf222e")
        for col, nm, colr in (("ema21", "EMA21", "#8250df"), ("ema50", "EMA50", "#1f6feb"), ("vwap", "VWAP", "#bf8700")):
            fig.add_scatter(x=f.index, y=f[col], name=nm, line=dict(color=colr, width=1.2))
        fig.add_scatter(x=f.index, y=f.sh, name="Son swing tepe", line=dict(color="#57606a", width=1, dash="dot"))
        fig.add_scatter(x=f.index, y=f.sl, name="Son swing dip", line=dict(color="#57606a", width=1, dash="dash"))
        for P, sym_, colr, y in (("L", "triangle-up", "#1a7f37", f.low * 0.997), ("S", "triangle-down", "#cf222e", f.high * 1.003)):
            m = f[f"{P}_gates_ok"]
            fig.add_scatter(x=f.index[m], y=y[m], mode="markers", name=f"{'LONG' if P == 'L' else 'SHORT'} şartları",
                            marker=dict(symbol=sym_, size=11, color=colr))
        if "plan" in r:
            p = r["plan"]
            fig.add_hline(y=p["stop"], line_color="#cf222e", annotation_text="STOP")
            for k, (px, _, _) in enumerate(p["tps"]):
                fig.add_hline(y=px, line_color="#1a7f37", line_dash="dot", annotation_text=f"TP{k + 1}")
        fig.update_layout(height=600, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10),
                          legend=dict(orientation="h", y=1.06))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Üçgenler: geçmişte tüm zorunlu teknik şartların oluştuğu mumlar (puan eşiği ve canlı veri hariç).")

# ---------------------------------------------------------------- backtest

with tab_bt:
    st.write("Stratejiyi MEXC Futures 15m geçmiş verisinde test eder: **train / validation / out-of-sample** ayrımı, "
             "**walk-forward**, eşik optimizasyonu, rejim (boğa/ayı/yatay) ve yön kırılımı, komisyon + kayma + funding dahil.")
    cc1, cc2 = st.columns(2)
    days = cc1.slider("Gün", 30, 730, C.BT_DAYS_DEFAULT, 30,
                      help="Doküman en az 2 yıl ister. Bulut sunucuda uzun testler yavaştır; 730 gün için bilgisayarında "
                           "`python backtest.py --days 730 --coins 20` önerilir.")
    ncoins = cc2.slider("Coin sayısı", 3, 30, C.BT_COINS_DEFAULT)
    with st.expander("Varsayımlar"):
        st.markdown(B.__doc__)
    if st.button("▶️ Backtest'i çalıştır"):
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
        hist = {s: d for s, d in hist.items() if s not in (C.BTC, C.ETH)}
        with st.spinner("Geleceği görme kontrolü..."):
            ok, bad = B.lookahead_check(next(iter(hist.values())), btc, eth, points=4)
        (st.success if ok else st.error)("Geleceği görme kontrolü: " + ("GEÇTİ — t anındaki puan, t sonrası veri silinince değişmiyor."
                                                                         if ok else f"SORUN: {bad}"))
        with st.spinner("Göstergeler ve simülasyon..."):
            rep = B.run(B.build_all(hist, btc, eth))
        st.session_state["bt"] = rep

    rep = st.session_state.get("bt")
    if rep:
        st.write(f"Dönem: {rep['start']:%Y-%m-%d} → {rep['end']:%Y-%m-%d} · aday sinyal: {rep['n_candidates']}")
        if "threshold" not in rep:
            st.warning("Bu dönemde zorunlu şartları sağlayan aday sinyal oluşmadı.")
        else:
            def show_metrics(d):
                df = pd.DataFrame(d)
                return df.apply(lambda col: col.map(lambda v: f"{v:.3f}" if isinstance(v, (float, np.floating)) else v))
            st.subheader(f"Train / Validation / Out-of-sample (eşik train'de seçildi: {rep['threshold']:.0f})")
            st.dataframe(show_metrics(rep["splits"]), use_container_width=True)
            st.subheader("Walk-forward")
            st.dataframe(rep["walk_forward"], hide_index=True, use_container_width=True)
            st.write("Walk-forward toplam:", {k: (round(v, 3) if isinstance(v, (float, np.floating)) else v)
                                              for k, v in rep["walk_forward_total"].items()})
            st.subheader("Eşik duyarlılığı (tüm dönem) — plato mu, tek tepe mi?")
            st.dataframe(rep["sweep_all"], hide_index=True, use_container_width=True)
            if "by_regime" in rep:
                cA, cB = st.columns(2)
                cA.subheader("Rejime göre")
                cA.dataframe(show_metrics(rep["by_regime"].T).T, use_container_width=True)
                cB.subheader("Yöne göre")
                cB.dataframe(show_metrics(rep["by_side"].T).T, use_container_width=True)
                st.subheader("Coin bazında (dayanıklılık)")
                st.dataframe(show_metrics(rep["by_symbol"].T).T, use_container_width=True)
                t = rep["trades"].sort_values("exit_time")
                fig = go.Figure()
                fig.add_scatter(x=t.exit_time, y=t.R.cumsum(), name="Kümülatif R", line=dict(color="#1f6feb", width=2))
                for bnd in rep["split_bounds"][1:-1]:
                    fig.add_vline(x=bnd, line_dash="dash", line_color="#d29922")
                fig.update_layout(height=380, yaxis_title="Kümülatif R", margin=dict(l=10, r=10, t=30, b=10))
                st.plotly_chart(fig, use_container_width=True)
                st.dataframe(t, hide_index=True, use_container_width=True)
            st.caption("Hayatta kalma yanlılığı: sadece bugün listede olan kontratlar test edilir. OI/funding/emir defteri "
                       "puanlarının geçmişi yoktur; eşik teknik puanın 100'e ölçeklenmiş hali üzerinde test edilir. "
                       "Backtest sonucu canlı performans garantisi değildir.")

# ---------------------------------------------------------------- yöntem

with tab_info:
    st.markdown(f"""
### Puanlama (100)
| Kriter | Maks. | Nasıl hesaplanır |
|---|---|---|
| Çoklu zaman dilimi trend | 20 | 4H ve 1H EMA21/50 yönü, 15m EMA9>21>50 dizilimi, 1H ADX>20 ve DI yönü |
| Piyasa yapısı / SMC | 20 | HH+HL (LH+LL) yapı, BOS/CHoCH tetikleyicisi, likidite süpürmesi, FVG, karşı seviyeye ≥1 ATR alan |
| Hacim + OI | 15 | Göreli hacim (10) · funding kalabalık değil (3) · OI artışı (2, önceki taramaya göre) |
| Momentum | 15 | RSI14 bölgesi, RSI7, MACD histogram ve kesişim, Supertrend(10,3) |
| Likidite / emir defteri | 10 | Spread, ±%0.5 derinlik, defter dengesizliği |
| BTC/ETH uyumu | 10 | BTC ve ETH 1H trendi, BTC son 1 saat |
| Volatilite + R/R | 10 | ATR% bandı, Bollinger genişlemesi, VWAP tarafı, net R/R ≥ 2 |

### Zorunlu şartlar (biri bile yoksa İŞLEM YOK)
4H veya 1H trend aynı yönde · 15m yapı (HH/HL ya da CHoCH) · mum kapanışıyla BOS tetikleyicisi · RSI/MACD desteği ·
hacim ortalamanın üstünde · BTC 1H ters yönde değil · yapısal stop {C.STOP_MIN_ATR}–{C.STOP_MAX_ATR} ATR · net R/R ≥ {C.MIN_NET_RR} ·
spread ≤ %{C.MAX_SPREAD_PCT} · funding aşırı değil · yeterli derinlik · veri güncel · günlük ve toplam risk limitleri.

### Entry / Stop / TP
Entry: kırılan seviye ile kapanış arası limit bölge; alternatif: sonraki mumda piyasa emri. Stop: son doğrulanmış swing ± {C.STOP_ATR_BUFFER} ATR.
TP1/TP2/TP3 ≈ net 1R/2R/3R; karşıdaki likidite seviyesi (önceki gün/hafta yüksek-düşük, son swing) önce gelirse hedef onun önüne çekilir.
Kısmi çıkış %30/%40/%30. Miktar ≈ risk tutarı / (|giriş − stop| + birim maliyet), kontrat adımına aşağı yuvarlanır.

### Kontrol EDİLEMEYEN kalemler (bilerek puan verilmez)
Likidasyon verisi (güvenilir ücretsiz kaynak yok) · taker buy/sell hacmi ve long/short oranı (MEXC herkese açık API'de yok;
mum yönüne göre hacim payı kullanılır) · haber/manipülasyon riski · order block (kural tanımı tartışmalı olduğu için puana katılmadı).
OI değişimi yalnızca site açıkken önceki taramayla karşılaştırılabilir.
""")
