"""
Botun tüm ayarları burada. Parametre sayısını bilerek az tuttuk:
araştırmada en büyük tehlike çıkan şey overfitting (geçmişe aşırı uydurma).
"""

# ---------------- Piyasa / veri ----------------
EXCHANGE = "mexc"
QUOTE = "USDT"
TIMEFRAME = "15m"           # "15m", "1h", "4h" veya "1d"
HISTORY_START = "2021-01-01"  # günlük grafikte backtest veri başlangıcı
# Kısa zaman dilimlerinde backtest için kaç günlük veri indirilecek (15m'de yıllarca veri çok büyük olur)
HISTORY_DAYS = {"15m": 60, "1h": 240, "4h": 900}

# Yönler: long = yükselişte al, short = düşüşte aç (short için MEXC vadeli/futures gerekir)
ENABLE_LONG = True
ENABLE_SHORT = True
DATA_DIR = "data"

# Coin evreni: boş bırakılırsa MEXC'de USDT hacmine göre en büyük TOP_N altcoin otomatik seçilir.
SYMBOLS = []  # örn: ["SOL/USDT", "AVAX/USDT", "LINK/USDT"]
TOP_N = 30
EXCLUDE_BASES = {
    "BTC", "USDC", "FDUSD", "TUSD", "DAI", "USDE", "USDD", "BUSD", "PYUSD", "EUR", "USD1",
}
MIN_AVG_QUOTE_VOLUME = 1_000_000  # GÜNLÜK ortalama USDT hacmi bunun altındaysa işlem açılmaz (zaman dilimine göre ölçeklenir)

# BTC rejim filtresi: altcoinler BTC'yi takip eder.
# BTC yükseliş trendindeyse sadece LONG, düşüş trendindeyse sadece SHORT girişine izin verilir.
REGIME_SYMBOL = "BTC/USDT"
# False: BTC'nin durumuna bakmadan her coinin kendi kırılımında sinyal verir (daha çok fırsat, daha çok risk).
# True : sadece BTC aynı yöndeyken giriş verir (araştırmada düşüşleri azaltan ayar).
USE_BTC_FILTER = False

# ---------------- Strateji ----------------
TREND_EMA = 50          # fiyat bu EMA'nın üstünde olmalı
MOMENTUM_LOOKBACK = 20  # bu kadar mumluk getiri pozitif olmalı
BREAKOUT_LOOKBACK = 20  # önceki N mumun en yükseğini kapanışla kırmalı (Donchian)
EXIT_LOOKBACK = 10      # önceki N mumun en düşüğünün altında kapanış -> çıkış
ATR_PERIOD = 14
ATR_STOP_MULT = 3.0     # iz süren stop: giriş sonrası en yüksek kapanış - 3*ATR

# ---------------- Risk ----------------
INITIAL_CAPITAL = 1000.0
RISK_PER_TRADE = 0.01       # stop'a kadar olan kayıp sermayenin %1'i
MAX_POSITION_PCT = 0.20     # tek coine en fazla sermayenin %20'si
MAX_OPEN_POSITIONS = 5

# ---------------- Maliyet ----------------
# MEXC spot komisyonlarını kendi hesabından kontrol et; muhafazakâr varsayım:
FEE_RATE = 0.001        # işlem başına %0.1
SLIPPAGE = 0.001        # altcoinlerde kayma için %0.1

# ---------------- Doğrulama ----------------
OUT_OF_SAMPLE_FRACTION = 0.30  # verinin son %30'u strateji hiç görmemiş gibi ayrı raporlanır

# ---------------- Telegram (sinyal botu) ----------------
# Anahtarları buraya yazma; ortam değişkeni olarak ver:
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
POSITIONS_FILE = "positions.json"  # elinde olan coinler (çıkış sinyali için)
