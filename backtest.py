"""
QUANT FUTURES backtest: walk-forward, train/validation/out-of-sample, eşik optimizasyonu, R metrikleri.

Varsayımlar (hepsi raporda yazılır):
- Sinyal 15m mum kapanışında; giriş bir sonraki mumun açılışında (alternatif tetikleyici), kayma dahil.
- Aynı mumda hem stop hem hedef görülürse önce STOP varsayılır (muhafazakâr).
- TP1/TP2/TP3 kısmi çıkışlar %30/%40/%30; TP1 sonrası stop girişe çekilir.
- 24 saatte (96 mum) sonuçlanmayan işlem kapanışta kapatılır.
- Komisyon + kayma her iki yönde; funding 8 saatte bir varsayılan oranda.
- Portföy kuralları: seçili moddaki işlem başı risk, toplam açık risk ve günlük zarar limiti; coin başına tek pozisyon.
- Canlı verili puanlar (OI/funding, emir defteri) geçmişte yok; eşik teknik puanın 100'e ölçeklenmiş hali üzerinde test edilir.

Kullanım:  python backtest.py --days 730 --coins 20
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import config as C
import quant as Q



def simulate_signals(sym: str, f: pd.DataFrame, min_score: float) -> list[dict]:
    """Bir coinin tüm aday sinyallerini (eşikten bağımsız) tek tek simüle eder."""
    out = []
    O, H, Lo, Cl = (f[k].to_numpy() for k in ("open", "high", "low", "close"))
    atr = f["atr"].to_numpy()
    idx = f.index
    n = len(f)
    btc_dir = f["btc_dir"].to_numpy()
    for name, sd in Q.SIDES.items():
        P = name[0]
        ok = f[f"{P}_gates_ok"].to_numpy(dtype=bool) & (f[f"{P}_tech100"].to_numpy() >= min_score)
        stop0, tp = f[f"{P}_stop"].to_numpy(), [f[f"{P}_tp{k}"].to_numpy() for k in (1, 2, 3)]
        inval = f[f"{P}_invalid"].to_numpy()
        score = f[f"{P}_tech100"].to_numpy()
        for i in np.flatnonzero(ok):
            j = i + 1
            if j >= n:
                continue
            o = O[j]
            if sd * (o - Cl[i]) > C.CHASE_LIMIT_ATR * atr[i] or sd * (o - inval[i]) < 0:
                continue  # fiyat giriş bölgesinden uzaklaştı ya da sinyal geçersizleşti
            entry = o * (1 + sd * C.SLIPPAGE)
            R = sd * (entry - stop0[i])
            if R <= 0:
                continue
            stop, rem, pnl, hit = stop0[i], 1.0, 0.0, 0
            exit_k = min(n - 1, j + C.MAX_HOLD_BARS)
            k_end = exit_k
            for k in range(j, exit_k + 1):
                if sd * (Lo[k] if sd == 1 else H[k]) <= sd * stop:          # stop önce
                    px = stop * (1 - sd * C.SLIPPAGE)
                    pnl += rem * (sd * (px - entry) - px * C.FEE_RATE)
                    rem, k_end = 0.0, k
                    break
                ext = H[k] if sd == 1 else Lo[k]
                while hit < 3 and sd * ext >= sd * tp[hit][i]:
                    w = C.TP_SPLIT[hit]
                    px = tp[hit][i]
                    pnl += w * (sd * (px - entry) - px * C.FEE_RATE)
                    rem -= w
                    hit += 1
                    if hit == 1:
                        stop = entry                                       # TP1 sonrası başa baş
                if rem <= 1e-9:
                    k_end = k
                    break
            if rem > 1e-9:
                px = Cl[k_end] * (1 - sd * C.SLIPPAGE)
                pnl += rem * (sd * (px - entry) - px * C.FEE_RATE)
            bars = k_end - j + 1
            pnl -= entry * C.FEE_RATE + entry * C.FUNDING_PER_8H * (bars * 15 / 480)
            reg = btc_dir[i]
            out.append({
                "symbol": sym, "side": name, "signal_time": idx[i], "entry_time": idx[j], "exit_time": idx[k_end],
                "score": float(score[i]), "entry": entry, "stop": stop0[i], "R": pnl / R, "tp_hits": hit, "bars": bars,
                "cost_R": (entry * (2 * C.FEE_RATE + 2 * C.SLIPPAGE) + entry * C.FUNDING_PER_8H * bars * 15 / 480) / R,
                "regime": "Boğa" if reg == 1 else ("Ayı" if reg == -1 else "Yatay"),
            })
    return out


def portfolio_filter(cands: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """Eşik + portföy kuralları: en fazla MAX_CONCURRENT açık işlem, coin başına tek pozisyon, günlük R limiti."""
    if cands.empty:
        return cands
    MAX_CONCURRENT = max(1, int(round(C.MAX_OPEN_RISK_PCT / C.RISK_PCT)))
    DAILY_R_LIMIT = -C.DAILY_MAX_LOSS_PCT / C.RISK_PCT
    c = cands[cands.score >= threshold].sort_values(["entry_time", "score"], ascending=[True, False])
    taken, open_ = [], []
    day_R: dict = {}
    for r in c.itertuples():
        open_ = [t for t in open_ if t.exit_time > r.entry_time]
        if len(open_) >= MAX_CONCURRENT or any(t.symbol == r.symbol for t in open_):
            continue
        d = r.entry_time.floor("1D")
        realized = sum(t.R for t in taken if t.exit_time.floor("1D") == d and t.exit_time <= r.entry_time)
        if realized <= DAILY_R_LIMIT:
            continue
        taken.append(r)
        open_.append(r)
    return pd.DataFrame(taken).drop(columns="Index", errors="ignore") if taken else c.iloc[0:0]


def metrics(t: pd.DataFrame) -> dict:
    if t is None or t.empty:
        return {"İşlem": 0}
    R = t.sort_values("exit_time")["R"].to_numpy()
    wins, losses = R[R > 0], R[R <= 0]
    eq = np.cumsum(R)
    dd = (eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min() if len(eq) else 0
    streak = best = 0
    for x in R:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    return {
        "İşlem": len(R),
        "Win rate": len(wins) / len(R),
        "Profit factor": wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf"),
        "Expectancy (R)": R.mean(),
        "Ort. kazanç (R)": wins.mean() if len(wins) else 0.0,
        "Ort. zarar (R)": losses.mean() if len(losses) else 0.0,
        "Maks. düşüş (R)": dd,
        "Maks. düşüş (%)": dd * C.RISK_PCT * 100,
        "Ardışık maks. kayıp": best,
        "Net getiri (%)": R.sum() * C.RISK_PCT * 100,
        "İşlem başı maliyet (R)": t["cost_R"].mean(),
    }


def split_bounds(start, end, fracs):
    span = end - start
    b, acc = [start], 0.0
    for fr in fracs:
        acc += fr
        b.append(start + span * acc)
    return b


def choose_threshold(cands: pd.DataFrame) -> tuple[float, pd.DataFrame]:
    rows = []
    for th in C.BT_THRESHOLDS:
        m = metrics(portfolio_filter(cands, th))
        rows.append({"Eşik": th, "İşlem": m["İşlem"], "Expectancy (R)": m.get("Expectancy (R)", np.nan),
                     "Profit factor": m.get("Profit factor", np.nan), "Win rate": m.get("Win rate", np.nan)})
    tab = pd.DataFrame(rows)
    ok = tab[tab["İşlem"] >= C.BT_MIN_TRADES]
    best = ok.sort_values("Expectancy (R)", ascending=False).iloc[0]["Eşik"] if len(ok) else C.SCORE_THRESHOLD
    return float(best), tab


def run(features: dict[str, pd.DataFrame]) -> dict:
    """features: {sembol: quant.build_features çıktısı}. Tam rapor sözlüğü döndürür."""
    allc = []
    for sym, f in features.items():
        allc += simulate_signals(sym, f, min(C.BT_THRESHOLDS))
    cands = pd.DataFrame(allc)
    start = min(f.index[0] for f in features.values()) + pd.Timedelta(days=3)   # gösterge ısınma süresi
    end = max(f.index[-1] for f in features.values())
    if not cands.empty:
        cands = cands[cands.entry_time >= start]
    rep = {"start": start, "end": end, "n_candidates": len(cands), "candidates": cands}
    if cands.empty:
        return rep

    # 1) Train / validation / out-of-sample
    b = split_bounds(start, end, C.BT_SPLIT)
    seg = {name: cands[(cands.entry_time >= b[i]) & (cands.entry_time < b[i + 1])]
           for i, name in enumerate(["Train", "Validation", "Out-of-sample"])}
    th, sweep = choose_threshold(seg["Train"])
    rep["threshold"], rep["sweep_train"] = th, sweep
    rep["sweep_all"] = choose_threshold(cands)[1]
    rep["splits"] = {k: metrics(portfolio_filter(v, th)) for k, v in seg.items()}
    rep["split_bounds"] = b

    # 2) Walk-forward: her dilimde eşik önceki dilimde seçilir, sonraki dilimde test edilir
    wb = split_bounds(start, end, [1 / (C.BT_WF_FOLDS + 1)] * (C.BT_WF_FOLDS + 1))
    wf_trades, wf_rows = [], []
    for i in range(C.BT_WF_FOLDS):
        tr_c = cands[(cands.entry_time >= wb[0]) & (cands.entry_time < wb[i + 1])]
        te_c = cands[(cands.entry_time >= wb[i + 1]) & (cands.entry_time < wb[i + 2])]
        th_i, _ = choose_threshold(tr_c)
        t = portfolio_filter(te_c, th_i)
        m = metrics(t)
        wf_rows.append({"Test dönemi": f"{wb[i + 1]:%Y-%m-%d} → {wb[i + 2]:%Y-%m-%d}", "Seçilen eşik": th_i,
                        "İşlem": m["İşlem"], "Expectancy (R)": m.get("Expectancy (R)", np.nan),
                        "Net getiri (%)": m.get("Net getiri (%)", np.nan)})
        if len(t):
            wf_trades.append(t)
    rep["walk_forward"] = pd.DataFrame(wf_rows)
    wft = pd.concat(wf_trades) if wf_trades else cands.iloc[0:0]
    rep["walk_forward_total"] = metrics(wft)

    # 3) Seçilen eşikle tüm dönem: rejim, yön ve coin kırılımları
    final = portfolio_filter(cands, th)
    rep["trades"] = final
    rep["total"] = metrics(final)
    if len(final):
        rep["by_regime"] = pd.DataFrame({k: metrics(g) for k, g in final.groupby("regime")}).T
        rep["by_side"] = pd.DataFrame({k: metrics(g) for k, g in final.groupby("side")}).T
        rep["by_symbol"] = pd.DataFrame({k: metrics(g) for k, g in final.groupby("symbol")}).T.sort_values("Expectancy (R)", ascending=False)
    return rep


def lookahead_check(df15: pd.DataFrame, btc15: pd.DataFrame, eth15: pd.DataFrame | None, points: int = 6) -> tuple[bool, list]:
    """Geleceği görme kontrolü: t anındaki puan, verinin t'den sonrası silinerek yeniden hesaplanınca aynı mı?"""
    full = Q.build_features(df15, btc15=btc15, eth1h=_eth1h(eth15))
    cols = ["L_tech", "S_tech", "L_gates_ok", "S_gates_ok", "L_stop", "S_stop"]
    rng = np.random.default_rng(0)
    idxs = rng.choice(np.arange(len(df15) // 2, len(df15) - 1), size=min(points, len(df15) // 2), replace=False)
    diffs = []
    for i in sorted(idxs):
        t = df15.index[i]
        part = Q.build_features(df15.loc[:t], btc15=btc15.loc[:t], eth1h=_eth1h(eth15.loc[:t] if eth15 is not None else None))
        a, b = full.loc[t, cols], part.loc[t, cols]
        same = all((pd.isna(x) and pd.isna(y)) or (x == y) or (isinstance(x, float) and abs(x - y) < 1e-9 * max(1, abs(x)))
                   for x, y in zip(a, b))
        if not same:
            diffs.append(str(t))
    return not diffs, diffs


def _eth1h(eth15):
    import indicators as I
    return I.resample(eth15, "1h") if eth15 is not None else None


def build_all(hist: dict[str, pd.DataFrame], btc15: pd.DataFrame, eth15: pd.DataFrame | None) -> dict[str, pd.DataFrame]:
    eth1h = _eth1h(eth15)
    return {s: Q.build_features(df, btc15=btc15, eth1h=eth1h) for s, df in hist.items() if len(df) > 500}


def print_report(rep: dict):
    print(f"\nDönem: {rep['start']:%Y-%m-%d} → {rep['end']:%Y-%m-%d} · aday sinyal: {rep['n_candidates']}")
    if "threshold" not in rep:
        print("Hiç aday sinyal yok.")
        return
    print(f"Train'de seçilen eşik (teknik puan /100): {rep['threshold']}")
    print("\n" + pd.DataFrame(rep["splits"]).round(3).to_string())
    print("\nWalk-forward:\n" + rep["walk_forward"].round(3).to_string(index=False))
    print("\nWalk-forward toplam:", {k: round(v, 3) if isinstance(v, float) else v for k, v in rep["walk_forward_total"].items()})
    if "by_regime" in rep:
        print("\nRejime göre:\n" + rep["by_regime"].round(3).to_string())
        print("\nYöne göre:\n" + rep["by_side"].round(3).to_string())


def main():
    import data as D
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=730)
    ap.add_argument("--coins", type=int, default=20)
    a = ap.parse_args()
    ex = D.get_exchange()
    syms, _ = D.pick_universe(ex, a.coins)
    since = ex.milliseconds() - a.days * 86_400_000
    get = lambda s: D.fetch_ohlcv(ex, s, C.BASE_TF, since_ms=since)
    print(f"{len(syms)} coin, {a.days} gün indiriliyor...")
    btc, eth = get(C.BTC), get(C.ETH)
    hist = {}
    for s in syms:
        try:
            hist[s] = get(s)
            print("  ", s, len(hist[s]))
        except Exception as e:
            print("  !", s, e)
    first = next(iter(hist.values()))
    ok, bad = lookahead_check(first, btc, eth)
    print("Geleceği görme kontrolü:", "GEÇTİ" if ok else f"SORUN {bad}")
    rep = run(build_all(hist, btc, eth))
    print_report(rep)
    if "trades" in rep and len(rep["trades"]):
        rep["trades"].to_csv("islemler.csv", index=False)
        print("\nİşlemler: islemler.csv")


if __name__ == "__main__":
    main()
