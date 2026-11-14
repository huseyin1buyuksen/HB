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
    return {"1d": 365, "4h": 365 * 6, "1h": 365 * 24}.get(C.TIMEFRAME, 365)


def run_backtest(data: dict[str, pd.DataFrame], btc: pd.DataFrame, params: dict | None = None):
    p = {**S.default_params(), **(params or {})}
    ind = {s: S.add_indicators(df, p) for s, df in data.items()}
    regime = S.regime_series(btc, p)
    index = sorted(set().union(*[df.index for df in ind.values()]))
    index = pd.DatetimeIndex(index)
    regime = regime.reindex(index).ffill().fillna(False).astype(bool)

    cash = C.INITIAL_CAPITAL
    pos: dict[str, dict] = {}
    pending_entry: list[str] = []
    pending_exit: set[str] = set()
    last_close: dict[str, float] = {}
    trades, equity = [], []

    for t in index:
        # 1) Bekleyen çıkışlar: açılıştan sat
        for s in list(pending_exit):
            df = ind[s]
            if t not in df.index:
                continue
            px = df.at[t, "open"] * (1 - C.SLIPPAGE)
            ps = pos.pop(s)
            proceeds = ps["qty"] * px * (1 - C.FEE_RATE)
            cash += proceeds
            trades.append({
                "symbol": s, "entry_time": ps["entry_time"], "exit_time": t,
                "entry_price": ps["entry_price"], "exit_price": px,
                "pnl": proceeds - ps["cost"], "ret": proceeds / ps["cost"] - 1,
                "reason": ps.get("exit_reason", ""),
            })
            pending_exit.discard(s)

        # 2) Bekleyen girişler: açılıştan al
        if pending_entry:
            mark = cash + sum(ps["qty"] * last_close.get(s, ps["entry_price"]) for s, ps in pos.items())
            for s in pending_entry:
                df = ind[s]
                if t not in df.index or s in pos or len(pos) >= C.MAX_OPEN_POSITIONS:
                    continue
                px = df.at[t, "open"] * (1 + C.SLIPPAGE)
                sig_atr = df["atr"].loc[:t].iloc[-2] if len(df.loc[:t]) > 1 else np.nan
                qty = S.position_size(mark, px, sig_atr, p["atr_stop_mult"])
                cost = qty * px * (1 + C.FEE_RATE)
                if cost > cash:
                    qty = cash / (px * (1 + C.FEE_RATE))
                    cost = cash
                if qty <= 0 or cost < 10:  # 10 USDT altı emir anlamsız
                    continue
                cash -= cost
                pos[s] = {
                    "qty": qty, "entry_price": px, "entry_time": t, "cost": cost,
                    "highest": px, "stop": px - p["atr_stop_mult"] * sig_atr,
                }
            pending_entry = []

        # 3) Kapanışta: stop güncelle, çıkış/giriş sinyallerini topla
        for s, ps in pos.items():
            df = ind[s]
            if t not in df.index:
                continue
            row = df.loc[t]
            last_close[s] = row["close"]
            ps["highest"] = max(ps["highest"], row["close"])
            ps["stop"] = max(ps["stop"], ps["highest"] - p["atr_stop_mult"] * row["atr"])
            if row["close"] < ps["stop"]:
                ps["exit_reason"] = "ATR stop"
                pending_exit.add(s)
            elif row["exit_signal"]:
                ps["exit_reason"] = f"{p['exit_lookback']} mum dibi kırıldı"
                pending_exit.add(s)

        for s, df in ind.items():
            if t in df.index:
                last_close[s] = df.at[t, "close"]

        if regime.loc[t]:
            free = C.MAX_OPEN_POSITIONS - (len(pos) - len(pending_exit))
            if free > 0:
                cands = []
                for s, df in ind.items():
                    if s in pos or t not in df.index:
                        continue
                    if bool(df.at[t, "entry_signal"]):
                        cands.append((df.at[t, "mom"], s))
                cands.sort(reverse=True)  # en güçlü momentum önce
                pending_entry = [s for _, s in cands[:free]]

        eq = cash + sum(ps["qty"] * last_close.get(s, ps["entry_price"]) for s, ps in pos.items())
        equity.append((t, eq))

    eq = pd.Series(dict(equity)).sort_index()
    tr = pd.DataFrame(trades)
    open_pos = {s: {k: v for k, v in ps.items()} for s, ps in pos.items()}
    return eq, tr, open_pos


def metrics(eq: pd.Series, tr: pd.DataFrame) -> dict:
    if len(eq) < 2:
        return {}
    rets = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25 or float("nan")
    total = eq.iloc[-1] / eq.iloc[0] - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1 if years and years > 0 else float("nan")
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
        by_sym = tr.groupby("symbol").pnl.agg(["count", "sum"]).sort_values("sum", ascending=False)
        print("\nCoin bazında (işlem sayısı, toplam kâr/zarar USDT):")
        print(by_sym.round(1).to_string())
    if open_pos:
        print(f"\nTest sonunda açık pozisyonlar: {', '.join(open_pos)}")
    plot(eq, bh, cut)


if __name__ == "__main__":
    main()
