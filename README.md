# Quant Futures — MEXC USDT-M LONG/SHORT sinyal sistemi

Canlı site: https://hb-mexc-sinyal.streamlit.app

Bu araç **işlem yapmaz**; MEXC vadeli (futures) piyasasının herkese açık verisiyle sinyal üretir ve stratejiyi geçmişte test eder.
Temel ilke: işlem açmak zorunlu değil — kanıt yetersizse **İŞLEM YOK**. Çıktılar yatırım tavsiyesi değildir.

## Dosyalar
| Dosya | Görev |
|---|---|
| `config.py` | Tüm ayarlar (eşik, risk, maliyet, gösterge parametreleri) |
| `indicators.py` | EMA, RSI, MACD, ADX, ATR, Bollinger, VWAP, Supertrend; swing, BOS/CHoCH, FVG, likidite süpürmesi, önceki gün/hafta seviyeleri |
| `quant.py` | 100 puanlık skor, zorunlu şartlar, İŞLEM YOK kuralları, entry/stop/TP ve pozisyon hesabı |
| `data.py` | MEXC Futures verisi: çoklu zaman dilimi mumlar, funding, OI, emir defteri, kontrat kuralları |
| `scanner.py` | Canlı tarama ve zorunlu sinyal kartı formatı |
| `backtest.py` | Train/validation/OOS, walk-forward, eşik optimizasyonu, rejim/yön/coin kırılımı |
| `app.py` | Web sitesi (Streamlit) |
| `signal_bot.py` | Telegram botu |

## Çalıştırma
```bash
pip install -r requirements.txt
streamlit run app.py                         # siteyi bilgisayarında aç
python backtest.py --days 730 --coins 20     # 2 yıllık tam backtest (uzun sürer)
python signal_bot.py --capital 1000          # tek tarama, sinyal varsa Telegram'a gönderir
```

## Geleceği görme ve diğer yanlılıklar
- Swing noktaları ancak sağ tarafı tamamlanınca (3 mum sonra) kullanılır; 1H/4H değerleri o mum kapanınca 15m'ye aktarılır.
- `backtest.lookahead_check` t anındaki puanı, t sonrası veri silinerek yeniden hesaplar; sonuç aynı olmalı.
- Rastgele (trendsiz) veride sistemin negatif beklenti vermesi kontrol edildi.
- Hayatta kalma yanlılığı: sadece bugün listede olan kontratlar test edilir — sonuçlar iyimserdir.
- OI/funding/emir defteri geçmişi olmadığı için backtest teknik puanı (85) 100'e ölçekleyerek test eder.

## Kontrol edilemeyenler
Likidasyon verisi, taker buy/sell hacmi, long/short oranı, haber/manipülasyon riski. Sinyal kartında belirtilir.
