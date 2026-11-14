"""
MEXC altcoin sinyal sitesi (Streamlit) — LONG + SHORT.
Yerelde:   streamlit run app.py
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config as C
import data as D
import signals as SG
import strategy as S

st.set_page_config(page_title="MEXC Sinyal Paneli", page_icon="📈", layout="wide")
TFS = ["15m", "1h", "4h", "1d"]

# ---------------------------------------------------------------- veri katmanı (önbellekli)


@st.cache_resource(show_spinner=False)
def exchange():
    return D.get_exchange()


@st.cache_data(ttl=3600, show_spinner=False)
def universe(top_n: int) -> list[str]:
    C.TOP_N = top_n
    return D.pick_universe(exchange())


@st.cache_data(ttl=60, show_spinner=False)
def recent(symbol: str, bars: int, tf: str) -> pd.DataFrame:
    C.TIMEFRAME = tf
    return D.fetch_ohlcv(exchange(), symbol, limit_bars=bars)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def history(symbol: str, tf: str, start: str) -> pd.DataFrame:
    C.TIMEFRAME = tf
    return D.fetch_ohlcv(exchange(), symbol, since=start)


def parse_positions(text: str) -> dict:
    """Her satır: 'SOL LONG 2026-11-14 10:30' veya 'ETH SHORT 2026-11-14' (yön ve tarih isteğe bağlı)."""
    out = {}
    for line in text.splitlines():
        parts = line.replace(",", " ").split()
        if not parts:
            continue
        sym = parts[0].upper()
        if "/" not in sym:
            sym = f"{sym}/{C.QUOTE}"
        side, rest = "LONG", parts[1:]
        if rest and rest[0].upper() in ("LONG", "SHORT"):
            side, rest = rest[0].upper(), rest[1:]
        out[sym] = {"side": side}
        if rest:
            out[sym]["entry_date"] = " ".join(rest)
    return out


# ---------------------------------------------------------------- kenar çubuğu

with st.sidebar:
    st.header("Ayarlar")
    tf = st.selectbox("Zaman dilimi", TFS, index=TFS.index(C.TIMEFRAME) if C.TIMEFRAME in TFS else 0,
                      help="15 dakikalık grafik daha çok sinyal üretir ama gürültü ve komisyon etkisi de artar.")
    top_n = st.slider("Taranacak coin sayısı (hacme göre)", 10, 60, C.TOP_N, 5)
    btc_filter = st.toggle("BTC filtresi", value=C.USE_BTC_FILTER,
                           help="Açıksa: long sadece BTC yükselişteyken, short sadece BTC düşüşteyken verilir. "
                                "Kapalıysa: her coinin kendi kırılımı yeterli (daha çok fırsat, daha çok risk).")
    st.subheader("Açık pozisyonlarım")
    pos_text = st.text_area(
        "Her satıra: coin, yön, giriş zamanı", key="positions", height=110,
        placeholder="SOL LONG 2026-11-14 10:30\nDOGE SHORT 2026-11-14 11:15",
        help="Bu coinler için çıkış sinyali ve güncel stop gösterilir. Sayfa yenilenince silinebilir.",
    )
    if st.button("🔄 Verileri yenile", use_container_width=True):
        recent.clear()
        universe.clear()
    st.caption("Fiyat verisi dakikada bir yenilenir. Sinyaller sadece KAPANMIŞ mumlardan hesaplanır; "
               "yeni sinyaller için sayfayı mum kapanışından sonra yenile.")

C.TIMEFRAME = tf
C.USE_BTC_FILTER = btc_filter
held = parse_positions(pos_text)

st.title("📈 MEXC Altcoin Sinyal Paneli")
st.caption(f"{tf} grafik · LONG + SHORT · trend + momentum + Donchian kırılımı + ATR stop. "
           "Bu panel bir test/öğrenme aracıdır, yatırım tavsiyesi değildir.")

tab_sig, tab_chart, tab_bt, tab_info = st.tabs(["Sinyaller", "Grafik", "Backtest", "Nasıl çalışır?"])

# ---------------------------------------------------------------- tarama

try:
    with st.spinner("MEXC verisi taranıyor..."):
        symbols = universe(top_n)
        res = SG.scan(lambda s, n: recent(s, n, tf), symbols, held)
except Exception as e:
    st.error(f"MEXC'ye bağlanılamadı: {e}")
    st.stop()

rows = pd.DataFrame(res["rows"])
REGIME_TXT = {"BOĞA": "🟢 Yükselişte", "AYI": "🔴 Düşüşte", "NÖTR": "⚪ Yatay"}

with tab_sig:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BTC durumu", REGIME_TXT[res["regime"]],
              help="BTC EMA üstünde ve momentumu pozitifse yükselişte, tersi düşüşte sayılır.")
    n_long = int(((rows["Durum"] == "GİRİŞ") & (rows["Yön"] == "LONG")).sum()) if len(rows) else 0
    n_short = int(((rows["Durum"] == "GİRİŞ") & (rows["Yön"] == "SHORT")).sum()) if len(rows) else 0
    c2.metric("🟢 Long sinyali", n_long)
    c3.metric("🔴 Short sinyali", n_short)
    c4.metric("Son kapanan mum (UTC)", f"{res['bar_time']:%d.%m %H:%M}")

    if btc_filter and res["regime"] == "NÖTR":
        st.info("BTC filtresi açık ve BTC yatayda: şu an yeni giriş verilmiyor. Filtreyi soldan kapatabilirsin.")

    def show(df: pd.DataFrame, title: str, empty: str):
        if title:
            st.subheader(title)
        if df.empty:
            st.caption(empty)
            return
        st.dataframe(
            df[["Coin", "Yön", "Durum", "Not", "Kapanış", "Stop", "Momentum", "Kırılıma uzaklık", "Ort. hacim (USDT)"]],
            hide_index=True, use_container_width=True,
            column_config={
                "Kapanış": st.column_config.NumberColumn(format="%.6g"),
                "Stop": st.column_config.NumberColumn(format="%.6g"),
                "Momentum": st.column_config.NumberColumn(f"Momentum ({C.MOMENTUM_LOOKBACK} mum)", format="percent"),
                "Kırılıma uzaklık": st.column_config.NumberColumn(format="percent"),
                "Ort. hacim (USDT)": st.column_config.NumberColumn("Ort. hacim / mum", format="compact"),
            },
        )

    if len(rows):
        if held:
            show(rows[rows.Coin.isin(held)], "📌 Açık pozisyonlarım", "")
        show(rows[(rows.Durum == "GİRİŞ") & (rows.Yön == "LONG")], "🟢 Long sinyalleri", "Şu an long sinyali yok.")
        show(rows[(rows.Durum == "GİRİŞ") & (rows.Yön == "SHORT")], "🔴 Short sinyalleri", "Şu an short sinyali yok.")
        show(rows[rows.Durum.isin(["İZLE", "FİLTRELENDİ"])], "👀 İzleme listesi (kırılıma yakın)", "Kırılıma yakın coin yok.")
        with st.expander(f"Taranan tüm coinler ({len(rows)})"):
            show(rows, "", "")
    if res["errors"]:
        with st.expander(f"Veri alınamayan coinler ({len(res['errors'])})"):
            st.write("\n".join(res["errors"]))

# ---------------------------------------------------------------- grafik

with tab_chart:
    choices = [r["Coin"] for r in res["rows"]]
    if choices:
        sym = st.selectbox("Coin", choices)
        df = res["frames"][sym].tail(150)
        fig = go.Figure()
        fig.add_candlestick(x=df.index, open=df.open, high=df.high, low=df.low, close=df.close, name=sym,
                            increasing_line_color="#1a7f37", decreasing_line_color="#cf222e")
        fig.add_scatter(x=df.index, y=df.ema, name=f"EMA {C.TREND_EMA}", line=dict(color="#1f6feb", width=1.5))
        fig.add_scatter(x=df.index, y=df.hh, name=f"{C.BREAKOUT_LOOKBACK} mum zirvesi (long giriş)",
                        line=dict(color="#1a7f37", width=1, dash="dot"))
        fig.add_scatter(x=df.index, y=df.lo, name=f"{C.BREAKOUT_LOOKBACK} mum dibi (short giriş)",
                        line=dict(color="#cf222e", width=1, dash="dot"))
        L, Sh = df[df.entry_long], df[df.entry_short]
        fig.add_scatter(x=L.index, y=L.low * 0.995, mode="markers", name="Long sinyali",
                        marker=dict(symbol="triangle-up", size=10, color="#1a7f37"))
        fig.add_scatter(x=Sh.index, y=Sh.high * 1.005, mode="markers", name="Short sinyali",
                        marker=dict(symbol="triangle-down", size=10, color="#cf222e"))
        fig.update_layout(height=560, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10),
                          legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Yeşil üçgen: geçmişteki long sinyalleri · Kırmızı üçgen: geçmişteki short sinyalleri.")

# ---------------------------------------------------------------- backtest

with tab_bt:
    import backtest as B

    start = D.history_start()
    st.write(f"Stratejiyi {start} tarihinden bugüne, {tf} grafikte, taranan {len(symbols)} coin üzerinde "
             f"LONG + SHORT test eder. Komisyon %{C.FEE_RATE * 100:.2f} + kayma %{C.SLIPPAGE * 100:.2f} (her yön). "
             f"Son %{int(C.OUT_OF_SAMPLE_FRACTION * 100)} dönem out-of-sample olarak ayrıca raporlanır.")
    st.info("Sadece bugün listede olan coinler test edilir; short'ta fonlama ücreti ve kaldıraç hesaba katılmaz. "
            "Bu yüzden sonuçlar gerçeğe göre iyimserdir.")
    if st.button("▶️ Backtest'i çalıştır (birkaç dakika sürebilir)"):
        hist, prog = {}, st.progress(0.0, "Geçmiş veri indiriliyor...")
        for i, s in enumerate(symbols + [C.REGIME_SYMBOL]):
            try:
                hist[s] = history(s, tf, start)
            except Exception:
                pass
            prog.progress((i + 1) / (len(symbols) + 1), f"İndiriliyor: {s}")
        prog.empty()
        btc = hist.pop(C.REGIME_SYMBOL)
        hist = {s: d for s, d in hist.items() if len(d) > 100}
        with st.spinner("Simülasyon çalışıyor..."):
            eq, tr, _ = B.run_backtest(hist, btc)
        cut = eq.index[int(len(eq) * (1 - C.OUT_OF_SAMPLE_FRACTION))]

        parts = {
            "Tüm dönem": (eq, tr),
            "In-sample": (eq[:cut], tr[tr.exit_time <= cut] if len(tr) else tr),
            "Out-of-sample": (eq[cut:], tr[tr.exit_time > cut] if len(tr) else tr),
        }
        if len(tr):
            parts["Sadece LONG"] = (eq, tr[tr.side == "LONG"])
            parts["Sadece SHORT"] = (eq, tr[tr.side == "SHORT"])
        mt = pd.DataFrame({k: B.metrics(e, t) for k, (e, t) in parts.items()})
        for k in ("Sadece LONG", "Sadece SHORT"):  # bu sütunlarda sermaye metrikleri anlamsız
            if k in mt:
                mt.loc[["Toplam getiri", "Yıllık getiri (CAGR)", "Maks. düşüş", "Sharpe"], k] = float("nan")
        pct = ["Toplam getiri", "Yıllık getiri (CAGR)", "Maks. düşüş", "Kazanma oranı"]
        shown = mt.copy().astype(object)
        for r in mt.index:
            shown.loc[r] = ["—" if pd.isna(v) else (f"{v:+.1%}" if r in pct else (f"{int(v)}" if r == "İşlem sayısı" else f"{v:.2f}"))
                            for v in mt.loc[r]]
        st.subheader("Sonuçlar")
        st.dataframe(shown, use_container_width=True)
        if len(parts["Out-of-sample"][1]) < 30:
            st.warning("Out-of-sample dönemde 30'dan az işlem var; sonuç istatistiksel olarak güvenilir değil.")
        bh = B.buy_hold(btc, eq)
        fig = go.Figure()
        fig.add_scatter(x=eq.index, y=eq.values, name="Strateji", line=dict(color="#1f6feb", width=2))
        fig.add_scatter(x=bh.index, y=bh.values, name="BTC al-tut", line=dict(color="#999", width=1.3))
        fig.add_vline(x=cut, line_dash="dash", line_color="#d29922")
        fig.update_layout(height=420, yaxis_title="Sermaye (USDT)",
                          margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig, use_container_width=True)
        if len(tr):
            st.subheader("İşlemler")
            st.dataframe(tr.sort_values("exit_time", ascending=False), hide_index=True, use_container_width=True,
                         column_config={"ret": st.column_config.NumberColumn("Getiri", format="percent"),
                                        "pnl": st.column_config.NumberColumn("Kâr/zarar (USDT)", format="%.2f")})

# ---------------------------------------------------------------- açıklama

with tab_info:
    st.markdown(f"""
**LONG girişi (mum kapanışında, hepsi aynı anda):**
coin {C.TREND_EMA} EMA'nın üstünde · {C.MOMENTUM_LOOKBACK} mumluk getiri pozitif ·
kapanış önceki {C.BREAKOUT_LOOKBACK} mumun **zirvesini** kırıyor · yeterli hacim

**SHORT girişi:** coin {C.TREND_EMA} EMA'nın altında · {C.MOMENTUM_LOOKBACK} mumluk getiri negatif ·
kapanış önceki {C.BREAKOUT_LOOKBACK} mumun **dibini** kırıyor · yeterli hacim

**BTC filtresi:** {"açık — long sadece BTC yükselişte, short sadece BTC düşüşteyken" if btc_filter else "kapalı — BTC'ye bakılmaz, her coinin kendi kırılımı yeterli"}.

**Çıkış:** LONG'da kapanış önceki {C.EXIT_LOOKBACK} mumun dibinin altına, SHORT'ta zirvesinin üstüne çıkarsa;
ya da iz süren ATR stop'u ({C.ATR_STOP_MULT}×ATR) kapanışla geçerse.

**Risk:** stop'a kadar olan kayıp sermayenin %{C.RISK_PER_TRADE * 100:.0f}'i, tek coine en fazla
%{C.MAX_POSITION_PCT * 100:.0f}, aynı anda en fazla {C.MAX_OPEN_POSITIONS} pozisyon.

**Short notu:** Spot piyasada short açılmaz; short için MEXC vadeli (futures) gerekir. Vadelide kaldıraç,
fonlama ücreti ve likidasyon riski vardır, backtest bunları hesaba katmaz.

**15 dakikalık grafik notu:** Kısa zaman diliminde sinyal sayısı artar ama sahte kırılımlar ve komisyonun
payı da artar. Hangi zaman diliminin daha iyi çalıştığını Backtest sekmesinde karşılaştırabilirsin.
""")
