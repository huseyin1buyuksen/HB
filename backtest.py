"""
Portföy backtest'i: birden fazla altcoin, ortak sermaye, maksimum açık pozisyon sınırı,
komisyon + kayma, sinyal kapanışta / işlem bir sonraki açılışta.

Kullanım:
    python backtest.py              # backtest + rapor + grafik
    python backtest.py --refresh    # veriyi MEXC'den yeniden indir
    python backtest.py --grid       # parametre sağlamlık tablosu (plato mu tepe mi?)
"""
import argparse
import itertools
import math

import numpy as np
import pandas as pd

import config as C
import strategy as S


def bars_per_year() -> float:
    return 365 * S.bars_per_day()


def run_backtest(data: dict[str, pd.DataFrame], btc: pd.DataFrame, params: dict | None = None):
    """LONG + SHORT portföy simülasyonu. side: +1 long, -1 short.
    Short'ta teminat = pozisyon büyüklüğü (kaldıraçsız varsayım)."""
    p = {**S.default_params(), **(params or {})}
    m = p["atr_stop_mult"]
    index = pd.DatetimeIndex(sorted(set().union(*[df.index for df in data.values()])))
    cols = ["open", "close", "atr", "mom", "entry_long", "entry_short", "exit_long", "exit_short"]
    A = {}
    for s, df in data.items():
        d = S.add_indicators(df, p).reindex(index)
        A[s] = {c: d[c].to_numpy(dtype=float) for c in cols}
    bull, bear = S.regime_series(btc, p)
    bull = bull.reindex(index).ffill().fillna(False).astype(bool).to_numpy()
    bear = bear.reindex(index).ffill().fillna(False).astype(bool).to_numpy()
    if not C.USE_BTC_FILTER:  # filtre kapalı: her iki yöne de her zaman izin ver
        bull = np.ones(len(index), dtype=bool)
        bear = np.ones(len(index), dtype=bool)

    cash = C.INITIAL_CAPITAL
    pos: dict[str, dict] = {}
    pending_entry: list[tuple[str, int]] = []
    pending_exit: set[str] = set()
    last: dict[str, float] = {}
    trades, equity = [], []

    def mark_equity():
        return cash + sum(ps["notional"] + ps["side"] * ps["qty"] * (last.get(s, ps["entry_price"]) - ps["entry_price"])
                          for s, ps in pos.items())

    for i, t in enumerate(index):
        # 1) Bekleyen çıkışlar: açılışta kapat
        for s in list(pending_exit):
            o = A[s]["open"][i]
            if not np.isfinite(o):
                continue
            ps = pos.pop(s)
            px = o * (1 - ps["side"] * C.SLIPPAGE)
            gross = ps["side"] * ps["qty"] * (px - ps["entry_price"])
            exit_fee = ps["qty"] * px * C.FEE_RATE
            cash += ps["notional"] + gross - exit_fee
            pnl = gross - exit_fee - ps["entry_fee"]
            trades.append({
                "symbol": s, "side": "LONG" if ps["side"] > 0 else "SHORT",
                "entry_time": ps["entry_time"], "exit_time": t,
                "entry_price": ps["entry_price"], "exit_price": px,
                "pnl": pnl, "ret": pnl / ps["notional"], "reason": ps.get("exit_reason", ""),
            })
            pending_exit.discard(s)

        # 2) Bekleyen girişler: açılışta aç
        if pending_entry:
            eq_now = mark_equity()
            for s, side in pending_entry:
                o = A[s]["open"][i]
                if not np.isfinite(o) or s in pos or len(pos) >= C.MAX_OPEN_POSITIONS:
                    continue
                px = o * (1 + side * C.SLIPPAGE)
                sig_atr = A[s]["atr"][i - 1] if i > 0 else np.nan
                qty = S.position_size(eq_now, px, sig_atr, m)
                notional = qty * px
                fee = notional * C.FEE_RATE
                if notional + fee > cash:
                    notional = cash / (1 + C.FEE_RATE)
                    qty, fee = notional / px, notional * C.FEE_RATE
                if qty <= 0 or notional < 10:  # 10 USDT altı emir anlamsız
                    continue
                cash -= notional + fee
                pos[s] = {"side": side, "qty": qty, "entry_price": px, "entry_time": t, "notional": notional,
                          "entry_fee": fee, "extreme": px, "stop": px - side * m * sig_atr}
            pending_entry = []

        # 3) Kapanış: fiyatları güncelle, stop'ları kaydır, sinyalleri topla
        for s in A:
            c = A[s]["close"][i]
            if np.isfinite(c):
                last[s] = c
        for s, ps in pos.items():
            c, a = A[s]["close"][i], A[s]["atr"][i]
            if not np.isfinite(c):
                continue
            if ps["side"] > 0:
                ps["extreme"] = max(ps["extreme"], c)
                ps["stop"] = max(ps["stop"], ps["extreme"] - m * a)
                hit, sig = c < ps["stop"], A[s]["exit_long"][i] == 1
            else:
                ps["extreme"] = min(ps["extreme"], c)
                ps["stop"] = min(ps["stop"], ps["extreme"] + m * a)
                hit, sig = c > ps["stop"], A[s]["exit_short"][i] == 1
            if hit:
                ps["exit_reason"] = "ATR stop"
                pending_exit.add(s)
            elif sig:
                ps["exit_reason"] = f"{p['exit_lookback']} mum kanalı ters kırıldı"
                pending_exit.add(s)

        free = C.MAX_OPEN_POSITIONS - (len(pos) - len(pending_exit))
        if free > 0 and (bull[i] or bear[i]):
            cands = []
            for s, a in A.items():
                if s in pos:
                    continue
                if bull[i] and a["entry_long"][i] == 1:
                    cands.append((a["mom"][i], s, 1))
                elif bear[i] and a["entry_short"][i] == 1:
                    cands.append((-a["mom"][i], s, -1))
            cands.sort(reverse=True)  # en güçlü momentum önce
            pending_entry = [(s, side) for _, s, side in cands[:free]]

        equity.append((t, mark_equity()))

    eq = pd.Series(dict(equity)).sort_index()
    tr = pd.DataFrame(trades)
    return eq, tr, dict(pos)


def metrics(eq: pd.Series, tr: pd.DataFrame) -> dict:
    if len(eq) < 2:
        return {}
    rets = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25 or float("nan")
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years and years >= 0.5 else float("nan")  # 6 aydan kısa dönemde yıllıklandırma yanıltıcı
    dd = (eq / eq.cummax() - 1).min()
    sharpe = rets.mean() / rets.std() * math.sqrt(bars_per_year()) if rets.std() > 0 else float("nan")
    m = {"Toplam getiri": total, "Yıllık getiri (CAGR)": cagr, "Maks. düşüş": dd, "Sharpe": sharpe,
         "İşlem sayısı": 0, "Kazanma oranı": float("nan"), "Profit factor": float("nan"),
         "Ort. kazanç / ort. kayıp": float("nan")}
    if len(tr):
        wins, losses = tr[tr.pnl > 0], tr[tr.pnl <= 0]
        m["İşlem sayısı"] = len(tr)
        m["Kazanma oranı"] = len(wins) / len(tr)
        m["Profit factor"] = wins.pnl.sum() / -losses.pnl.sum() if len(losses) and losses.pnl.sum() < 0 else float("inf")
        if len(wins) and len(losses):
            m["Ort. kazanç / ort. kayıp"] = wins.ret.mean() / -losses.ret.mean()
    return m


def fmt(m: dict) -> str:
    lines = []
    for k, v in m.items():
        if k in ("Sharpe", "Profit factor", "Ort. kazanç / ort. kayıp"):
            lines.append(f"  {k:<26} {v:.2f}")
        elif k == "İşlem sayısı":
            lines.append(f"  {k:<26} {v}")
        else:
            lines.append(f"  {k:<26} {v:+.1%}")
    return "\n".join(lines)


def split_report(eq: pd.Series, tr: pd.DataFrame):
    cut = eq.index[int(len(eq) * (1 - C.OUT_OF_SAMPLE_FRACTION))]
    parts = {
        f"TÜM DÖNEM ({eq.index[0]:%Y-%m-%d} → {eq.index[-1]:%Y-%m-%d})": (eq, tr),
        f"IN-SAMPLE (→ {cut:%Y-%m-%d})": (eq[:cut], tr[tr.exit_time <= cut] if len(tr) else tr),
        f"OUT-OF-SAMPLE ({cut:%Y-%m-%d} →)": (eq[cut:], tr[tr.exit_time > cut] if len(tr) else tr),
    }
    for name, (e, t) in parts.items():
        print(f"\n=== {name} ===")
        print(fmt(metrics(e, t)))
        if 0 < len(t) < 30:
            print("  ⚠ 30'dan az işlem: bu dönemin sonuçları istatistiksel olarak güvenilir değil.")
    return cut


def buy_hold(btc: pd.DataFrame, eq: pd.Series) -> pd.Series:
    b = btc["close"].reindex(eq.index).ffill().dropna()
    return b / b.iloc[0] * C.INITIAL_CAPITAL


def plot(eq: pd.Series, bh: pd.Series, cut, path="backtest_sonuc.png"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    a1.plot(eq.index, eq.values, label="Strateji", color="#1f6feb", lw=1.6)
    a1.plot(bh.index, bh.values, label="BTC al-tut", color="#999999", lw=1.2)
    a1.axvline(cut, color="#d29922", ls="--", lw=1)
    a1.text(cut, a1.get_ylim()[1], "  out-of-sample →", va="top", color="#d29922", fontsize=9)
    a1.set_yscale("log")
    a1.set_ylabel("Sermaye (USDT, log)")
    a1.legend(frameon=False)
    a1.spines[["top", "right"]].set_visible(False)
    dd = eq / eq.cummax() - 1
    a2.fill_between(dd.index, dd.values * 100, 0, color="#cf222e", alpha=0.35, lw=0)
    a2.set_ylabel("Düşüş %")
    a2.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    print(f"\nGrafik kaydedildi: {path}")


def grid(data, btc):
    """Parametre sağlamlığı: komşu değerlerde de iyi mi? Tek bir tepe değil, geniş bir plato arıyoruz."""
    cut = None
    bl = [10, 15, 20, 30, 40]
    te = [30, 50, 100]
    table = pd.DataFrame(index=[f"EMA {x}" for x in te], columns=[f"Kırılım {x}" for x in bl], dtype=float)
    for e_, b_ in itertools.product(te, bl):
        eq, tr, _ = run_backtest(data, btc, {"trend_ema": e_, "breakout_lookback": b_})
        cut = cut or eq.index[int(len(eq) * (1 - C.OUT_OF_SAMPLE_FRACTION))]
        m = metrics(eq[:cut], tr[tr.exit_time <= cut] if len(tr) else tr)
        table.loc[f"EMA {e_}", f"Kırılım {b_}"] = m.get("Sharpe", np.nan)
        print(f"  EMA {e_:>3} / kırılım {b_:>2}: Sharpe {m.get('Sharpe', float('nan')):.2f}")
    print("\nIn-sample Sharpe tablosu (komşu hücreler de iyiyse strateji sağlam):")
    print(table.round(2).to_string())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--grid", action="store_true")
    args = ap.parse_args()

    import data as D
    ex = D.get_exchange() if (args.refresh or not C.SYMBOLS) else None
    symbols = D.pick_universe(ex) if ex else list(C.SYMBOLS)
    print(f"{len(symbols)} coin: {', '.join(symbols)}")
    hist = D.load_history(symbols + [C.REGIME_SYMBOL], refresh=args.refresh)
    btc = hist.pop(C.REGIME_SYMBOL)
    run(hist, btc, grid_mode=args.grid)


def run(hist, btc, grid_mode=False):
    print("\nNOT: Sadece bugün listede olan coinler test ediliyor. Listeden kalkıp batan coinler yok,")
    print("bu yüzden sonuçlar gerçeğe göre iyimserdir (survivorship bias).")
    if grid_mode:
        grid(hist, btc)
        return
    eq, tr, open_pos = run_backtest(hist, btc)
    cut = split_report(eq, tr)
    bh = buy_hold(btc, eq)
    print(f"\nKarşılaştırma — BTC al-tut toplam getiri: {bh.iloc[-1] / bh.iloc[0] - 1:+.1%}, "
          f"maks. düşüş: {(bh / bh.cummax() - 1).min():+.1%}")
    if len(tr):
        tr.to_csv("islemler.csv", index=False)
        print("Tüm işlemler: islemler.csv")
        by_sym = tr.groupby(["symbol", "side"]).pnl.agg(["count", "sum"]).sort_values("sum", ascending=False)
        print("\nCoin bazında (işlem sayısı, toplam kâr/zarar USDT):")
        print(by_sym.round(1).to_string())
    if open_pos:
        print(f"\nTest sonunda açık pozisyonlar: {', '.join(open_pos)}")
    plot(eq, bh, cut)


if __name__ == "__main__":
    main()
