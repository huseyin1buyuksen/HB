"""
ANİ DÜŞÜŞ AVCISI — son 4 saat içinde zirvesinden %15+ düşen coinleri ~2 dakikada bir bulur.

Nasıl:
  • Her turda MEXC'den TEK çağrıyla tüm vadeli fiyatlar (tickers) alınır ve hafızada 4 saatlik fiyat geçmişi tutulur.
  • Hafızadaki zirveden %10+ düşmüş ya da 24 saatte %12+ düşmüş coinler aday olur (açılışta hafıza boş olduğu için).
  • Aday, 15m mumlarla doğrulanır: son 4 saatin en yüksek fiyatından şu anki fiyata düşüş %15 ve üzeriyse SİNYAL.
Sinyal: "düşen coine LONG (tepki alımı)". Eğitim/deneme amaçlıdır, yatırım tavsiyesi değildir.
"""
from __future__ import annotations

import time
from collections import deque

import pandas as pd

import config as C

DROP = 0.15            # zirveden en az bu kadar düşüş
WINDOW_H = 4           # zirvenin aranacağı süre (saat)
PRE_DROP = 0.10        # hafıza ön-eleme eşiği
PRE_24H = -12.0        # 24s değişim ön-eleme eşiği (%)
MIN_QVOL = 1_000_000   # 24s hacmi bunun altındaki coinler izlenmez
RECHECK_S = 600        # aynı coin en erken 10 dk sonra yeniden doğrulanır
MAX_CHECKS = 15        # bir turda en fazla bu kadar coin doğrulanır


class DumpWatch:
    def __init__(self, ex):
        self.ex = ex
        self.hist: dict[str, deque] = {}
        self.checked: dict[str, float] = {}
        self.last_px: dict[str, float] = {}

    def _ok(self, sym: str, t: dict) -> bool:
        import data as D
        m = self.ex.markets.get(sym)
        if (not m or not D.is_usdt_perp(m) or m["base"] in C.EXCLUDE_BASES
                or m["base"].endswith(C.EXCLUDE_SUFFIXES) or D.is_tradfi(m)):
            return False
        return D.quote_volume(t) >= MIN_QVOL

    def poll(self) -> list[dict]:
        import data as D
        tickers = self.ex.fetch_tickers(params={"type": C.MARKET_TYPE})
        now = time.time()
        cands = []
        for sym, t in tickers.items():
            px = t.get("last")
            if not px or not self._ok(sym, t):
                continue
            px = float(px)
            self.last_px[sym] = px
            h = self.hist.setdefault(sym, deque())
            h.append((now, px))
            while h and h[0][0] < now - WINDOW_H * 3600:
                h.popleft()
            mx = max(p for _, p in h)
            pct24 = float(t.get("percentage") or 0)
            if (px / mx - 1 <= -PRE_DROP or pct24 <= PRE_24H) and now - self.checked.get(sym, 0) > RECHECK_S:
                cands.append((px / mx - 1, sym, px, D.quote_volume(t), pct24))
        out = []
        for _, sym, px, qv, pct24 in sorted(cands)[:MAX_CHECKS]:
            self.checked[sym] = now
            try:
                df = D.fetch_ohlcv(self.ex, sym, "15m", WINDOW_H * 4 + 4)
            except Exception:
                continue
            seg = df.tail(WINDOW_H * 4)
            if seg.empty:
                continue
            hi_t = seg["high"].idxmax()
            hi = float(seg["high"].max())
            hi = max(hi, max(p for _, p in self.hist[sym]))
            drop = px / hi - 1
            if drop <= -DROP:
                hours = max((pd.Timestamp.now(tz="UTC") - hi_t).total_seconds() / 3600, 0.25)
                out.append({"symbol": sym, "base": self.ex.markets[sym]["base"], "price": px, "high": hi,
                            "high_time": hi_t, "drop": drop, "hours": hours, "qvol": qv, "pct24": pct24})
        return sorted(out, key=lambda d: d["drop"])

    def price(self, sym: str) -> float | None:
        if sym in self.last_px:
            return self.last_px[sym]
        try:
            return float(self.ex.fetch_ticker(sym)["last"])
        except Exception:
            return None


def describe(d: dict) -> str:
    return (f"🔻 ANİ DÜŞÜŞ — {d['base']}USDT son {d['hours']:.1f} saatte %{d['drop'] * 100:.1f} düştü\n"
            f"Zirve {d['high']:.6g} → şimdi {d['price']:.6g} · 24s değişim %{d['pct24']:+.1f} · 24s hacim {d['qvol'] / 1e6:.1f}M$\n\n"
            f"🟢 LONG GİRİŞ SİNYALİ (tepki alımı, deneysel)\n"
            f"Giriş bölgesi: {d['price']:.6g} civarı\n"
            f"Not: Düşen bıçağı tutmak çok risklidir; düşüş devam edebilir. Bu bir eğitim/deneme sinyalidir, yatırım tavsiyesi değildir.")
