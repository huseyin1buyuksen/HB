"""
QUANT FUTURES — tüm ayarlar burada. Değerler dokümandaki kurallardan alındı.
"""

# ---------------- Borsa / veri ----------------
EXCHANGE = "mexc"
MARKET_TYPE = "swap"          # MEXC USDT-M sürekli vadeli (futures)
QUOTE = "USDT"
BASE_TF = "15m"               # sinyal ve tetikleyici zaman dilimi
TOP_N = 30                    # 24s hacmine göre taranacak coin sayısı
EXCLUDE_BASES = {"BTC", "USDC", "FDUSD", "TUSD", "DAI", "USDE", "USD1", "PYUSD"}
BTC = "BTC/USDT:USDT"
ETH = "ETH/USDT:USDT"
MAX_STALE_BARS = 2            # son kapanan mum bundan daha eskiyse veri bayat sayılır -> İŞLEM YOK

# ---------------- Göstergeler ----------------
EMAS = (9, 21, 50, 100, 200)
RSI_FAST, RSI_SLOW = 7, 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
ADX_LEN = 14
ATR_LEN = 14
BB_LEN, BB_STD = 20, 2.0
ST_LEN, ST_MULT = 10, 3.0     # Supertrend
SWING_K = 3                   # swing noktası: solunda ve sağında 3 mum daha düşük/yüksek (3 mum sonra doğrulanır)
RECENT_BARS = 12              # sweep / FVG için geriye bakış (15m'de 3 saat)

# ---------------- Sinyal ----------------
SCORE_THRESHOLD = 85          # 100 üzerinden aday sinyal eşiği (85 puan = %85 başarı DEMEK DEĞİLDİR)
MIN_NET_RR = 1.5              # ağırlıklı ortalama net R/R bundan düşükse İŞLEM YOK
STOP_ATR_BUFFER = 0.2         # yapısal stop'a eklenen ATR tamponu
STOP_MIN_ATR, STOP_MAX_ATR = 0.5, 4.0   # stop mesafesi bu aralıkta değilse stop "mantıklı değil"
SIGNAL_VALID_BARS = 3         # sinyal en fazla 3 mum (45 dk) geçerli
CHASE_LIMIT_ATR = 1.0         # fiyat girişten 1 ATR'den fazla uzaklaşırsa sinyal iptal
TP_SPLIT = (0.30, 0.40, 0.30) # TP1 / TP2 / TP3 kısmi çıkış oranları
MAX_HOLD_BARS = 96            # backtest: 24 saatte hedef/stop yoksa kapat

# ---------------- Canlı veri kapıları ----------------
MAX_SPREAD_PCT = 0.15         # % — üstündeyse İŞLEM YOK
GOOD_SPREAD_PCT = 0.05
MIN_DEPTH_USDT = 50_000       # fiyatın ±%0.5'i içindeki emir defteri derinliği
MAX_FUNDING_WITH = 0.0005     # long için funding > +%0.05 (short için < -%0.05) = kalabalık -> puan yok
EXTREME_FUNDING = 0.001       # |funding| > %0.1 -> İŞLEM YOK
MIN_QUOTE_VOL_24H = 2_000_000 # 24s hacim (USDT) altındaysa likidite yetersiz

# ---------------- Risk ----------------
RISK_PCT = 0.005              # işlem başına sermayenin %0.5'i
DAILY_MAX_LOSS_PCT = 0.02     # günlük maksimum zarar %2
MAX_OPEN_RISK_PCT = 0.02      # toplam açık pozisyon riski en fazla %2
MAX_CORR = 0.85               # aynı yöndeki sinyaller arasında getiri korelasyonu bundan yüksekse zayıf olan elenir
MAX_LEVERAGE_CAP = 10         # önerilen kaldıraç için üst sınır (kaldıraç kâr artırıcı değildir)

# ---------------- Maliyet varsayımları (MEXC futures; hesabından kontrol et) ----------------
FEE_RATE = 0.0004             # taker komisyonu (tek yön)
SLIPPAGE = 0.0005             # tek yön kayma
FUNDING_PER_8H = 0.0001       # backtest'te varsayılan funding maliyeti (8 saatte bir)

# ---------------- Backtest ----------------
BT_DAYS_DEFAULT = 180
BT_COINS_DEFAULT = 12
BT_THRESHOLDS = (55, 60, 65, 70, 75, 80, 85, 90)
BT_MIN_TRADES = 30
BT_SPLIT = (0.50, 0.25, 0.25)  # train / validation / out-of-sample
BT_WF_FOLDS = 4

# ---------------- Telegram ----------------
# Ortam değişkeni olarak ver: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
