"""
MEXC altcoin sinyal sitesi (Streamlit).
Yerelde:   streamlit run app.py
Canlıda:   README.md -> "Siteyi canlıya alma"
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

# ---------------------------------------------------------------- veri katmanı (önbellekli)


@st.cache_resource(show_spinner=False)
def exchange():
    return D.get_exchange()


@st.cache_data(ttl=3600, show_spinner=False)
def universe(top_n: int) -> list[str]:
    C.TOP_N = top_n
    return D.pick_universe(exchange())


@st.cache_data(ttl=600, show_spinner=False)
def recent(symbol: str, bars: int, tf: str) -> pd.DataFrame:
    C.TIMEFRAME = tf
    return D.fetch_ohlcv(exchange(), symbol, limit_bars=bars)


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def history(symbol: str, tf: str, start: str) -> pd.DataFrame:
    C.TIMEFRAME = tf
    return D.fetch_ohlcv(exchange(), symbol, since=start)


def parse_positions(text: str) -> dict:
    """Her satır: 'SOL/USDT 2026-11-01' (tarih isteğe bağlı)."""
    out = {}
    for line in text.splitlines():
        parts = line.replace(",", " ").split()
        if not parts:
            continue
        sym = parts[0].upper()
        if "/" not in sym:
            sym = f"{sym}/{C.QUOTE}"
        out[sym] = {"entry_date": parts[1]} if len(parts) > 1 else {}
    return out


# ---------------------------------------------------------------- kenar çubuğu

with st.sidebar:
    st.header("Ayarlar")
    tf = st.selectbox("Zaman dilimi", ["1d", "4h"], index=0 if C.TIMEFRAME == "1d" else 1,
                      help="Strateji günlük grafik için tasarlandı; 4 saatlik daha çok sinyal ama daha çok gürültü üretir.")
    top_n = st.slider("Taranacak coin sayısı (hacme göre)", 10, 60, C.TOP_N, 5)
    st.subheader("Elimdeki coinler")
    pos_text = st.text_area(
        "Her satıra bir coin ve giriş tarihi", key="positions", height=110,
        placeholder="SOL/USDT 2026-11-01\nLINK 2026-11-10",
        help="Bu coinler için çıkış sinyali ve güncel stop gösterilir. Sayfa yenilenince silinebilir.",
    )
    if st.button("🔄 Verileri yenile", use_container_width=True):
        recent.clear()
        universe.clear()
    st.caption("Fiyat verisi 10 dakikada bir yenilenir. Sinyaller sadece KAPANMIŞ mumlardan hesaplanır.")

C.TIMEFRAME = tf
held = parse_positions(pos_text)

st.title("📈 MEXC Altcoin Sinyal Paneli")
st.caption("Trend + momentum + Donchian kırılımı, BTC rejim filtresi ve ATR stop ile. "
           "Bu panel bir test/öğrenme aracıdır, yatırım tavsiyesi değildir.")

tab_sig, tab_chart, tab_bt, tab_info = st.tabs(["Sinyaller", "Grafik", "Backtest", "Nasıl çalışır?"])

# ---------------------------------------------------------------- tarama

try:
    with st.spinner("MEXC verisi taranıyor..."):
        symbols = universe(top_n)
        res = SG.scan(lambda s, n: recent(s, n, tf), symbols, held)
except Exception as e:
    st.error(f"MEXC'ye bağlanılamadı: {e}\n\nSunucunun bulunduğu ülke MEXC tarafından engelleniyor olabilir "
             "(README'deki 'Sorun giderme' bölümüne bak).")
    st.stop()

rows = pd.DataFrame(res["rows"])

with tab_sig:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BTC rejimi", "✅ Trend var" if res["regime_ok"] else "⛔ Trend yok",
              help="BTC 50 EMA üstünde ve 20 mumluk getirisi pozitifse yeni girişlere izin verilir.")
    c2.metric("Giriş sinyali", int((rows["Durum"] == "GİRİŞ").sum()) if len(rows) else 0)
    c3.metric("Çıkış sinyali", int((rows["Durum"] == "ÇIKIŞ").sum()) if len(rows) else 0)
    c4.metric("Son kapanan mum (UTC)", f"{res['bar_time']:%d.%m.%Y %H:%M}")

    if not res["regime_ok"]:
        st.warning("BTC trendde değil: strateji bu dönemde yeni pozisyon açmıyor. Kırılım yapan coinler 'FİLTRELENDİ' olarak gösterilir.")

    def show(df: pd.DataFrame, title: str, empty: str):
        st.subheader(title)
        if df.empty:
            st.caption(empty)
            return
        st.dataframe(
            df[["Coin", "Durum", "Not", "Kapanış", "Stop", "Momentum (20)", "Kırılıma uzaklık", "Ort. hacim (USDT)"]],
            hide_index=True, use_container_width=True,
            column_config={
                "Kapanış": st.column_config.NumberColumn(format="%.6g"),
                "Stop": st.column_config.NumberColumn(format="%.6g"),
                "Momentum (20)": st.column_config.NumberColumn(format="percent"),
                "Kırılıma uzaklık": st.column_config.NumberColumn(format="percent"),
                "Ort. hacim (USDT)": st.column_config.NumberColumn(format="compact"),
            },
        )

    if len(rows):
        if held:
            show(rows[rows.Coin.isin(held)], "Elimdeki coinler", "")
        show(rows[rows.Durum == "GİRİŞ"], "🟢 Giriş sinyalleri", "Şu an giriş sinyali yok.")
        show(rows[rows.Durum.isin(["İZLE", "FİLTRELENDİ"])], "👀 İzleme listesi", "Kırılıma yakın coin yok.")
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
        fig.add_scatter(x=df.index, y=df.hh, name=f"{C.BREAKOUT_LOOKBACK} mum zirvesi (giriş)",
                        line=dict(color="#8250df", width=1, dash="dot"))
        fig.add_scatter(x=df.index, y=df.ll, name=f"{C.EXIT_LOOKBACK} mum dibi (çıkış)",
                        line=dict(color="#d29922", width=1, dash="dot"))
        sig = df[df.entry_signal]
        fig.add_scatter(x=sig.index, y=sig.low * 0.97, mode="markers", name="Giriş sinyali",
                        marker=dict(symbol="triangle-up", size=10, color="#1a7f37"))
        fig.update_layout(height=560, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=30, b=10),
                          legend=dict(orientation="h", y=1.04))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Yeşil üçgenler geçmişte giriş şartlarının oluştuğu mumlar (BTC filtresi hariç).")

# ---------------------------------------------------------------- backtest

with tab_bt:
    import backtest as B

    st.write(f"Stratejiyi {C.HISTORY_START} tarihinden bugüne, taranan {len(symbols)} coin üzerinde test eder. "
             f"Komisyon %{C.FEE_RATE * 100:.2f} + kayma %{C.SLIPPAGE * 100:.2f}. Son %{int(C.OUT_OF_SAMPLE_FRACTION * 100)} "
             "dönem out-of-sample olarak ayrıca raporlanır.")
    st.info("Sadece bugün listede olan coinler test edilir; batıp listeden kalkanlar hesaba girmez. "
            "Bu yüzden sonuçlar gerçeğe göre iyimserdir.")
    if st.button("▶️ Backtest'i çalıştır (ilk sefer birkaç dakika sürebilir)"):
        hist, prog = {}, st.progress(0.0, "Geçmiş veri indiriliyor...")
        for i, s in enumerate(symbols + [C.REGIME_SYMBOL]):
            try:
                hist[s] = history(s, tf, C.HISTORY_START)
            except Exception:
                pass
            prog.progress((i + 1) / (len(symbols) + 1), f"İndiriliyor: {s}")
        prog.empty()
        btc = hist.pop(C.REGIME_SYMBOL)
        hist = {s: d for s, d in hist.items() if len(d) > 60}
        with st.spinner("Simülasyon çalışıyor..."):
            eq, tr, _ = B.run_backtest(hist, btc)
        cut = eq.index[int(len(eq) * (1 - C.OUT_OF_SAMPLE_FRACTION))]
        parts = {
            "Tüm dönem": (eq, tr),
            "In-sample": (eq[:cut], tr[tr.exit_time <= cut] if len(tr) else tr),
            "Out-of-sample": (eq[cut:], tr[tr.exit_time > cut] if len(tr) else tr),
        }
        mt = pd.DataFrame({k: B.metrics(e, t) for k, (e, t) in parts.items()})
        pct = ["Toplam getiri", "Yıllık getiri (CAGR)", "Maks. düşüş", "Kazanma oranı"]
        shown = mt.copy().astype(object)
        for r in mt.index:
            shown.loc[r] = [f"{v:+.1%}" if r in pct else (f"{int(v)}" if r == "İşlem sayısı" else f"{v:.2f}")
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
        fig.update_layout(height=420, yaxis_type="log", yaxis_title="Sermaye (USDT, log)",
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
**Giriş şartları (hepsi aynı anda, mum kapanışında):**
1. BTC kendi {C.TREND_EMA} EMA'sının üstünde ve {C.MOMENTUM_LOOKBACK} mumluk getirisi pozitif (rejim filtresi)
2. Coin {C.TREND_EMA} EMA'nın üstünde
3. Coinin {C.MOMENTUM_LOOKBACK} mumluk getirisi pozitif
4. Kapanış önceki {C.BREAKOUT_LOOKBACK} mumun en yükseğini kırıyor
5. 20 mumluk ortalama hacim en az {C.MIN_AVG_QUOTE_VOLUME:,.0f} USDT

**Çıkış:** kapanış önceki {C.EXIT_LOOKBACK} mumun dibinin altına düşerse ya da iz süren stop'un
(girişten sonraki en yüksek kapanış − {C.ATR_STOP_MULT}×ATR) altında kapanırsa.

**Risk:** stop'a kadar olan kayıp sermayenin %{C.RISK_PER_TRADE * 100:.0f}'i, tek coine en fazla
%{C.MAX_POSITION_PCT * 100:.0f}, aynı anda en fazla {C.MAX_OPEN_POSITIONS} pozisyon.

**Durumlar:** GİRİŞ = bütün şartlar oluştu · İZLE = trendde ve kırılıma %3'ten az kaldı ·
FİLTRELENDİ = kırılım var ama BTC filtresi kapalı · TUT / ÇIKIŞ = elindeki coinler için.

Trend stratejilerinde işlemlerin çoğu küçük zararla kapanır; kâr, az sayıdaki büyük trendden gelir.
Bu yüzden tek tek sinyallere değil, kurala uzun süre sadık kalmaya bakılır.
""")
