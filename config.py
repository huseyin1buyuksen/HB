"""
Botun tüm ayarları burada. Parametre sayısını bilerek az tuttuk:
araştırmada en büyük tehlike çıkan şey overfitting (geçmişe aşırı uydurma).
"""

# ---------------- Piyasa / veri ----------------
EXCHANGE = "mexc"
QUOTE = "USDT"
TIMEFRAME = "1d"            # "1d" veya "4h"
HISTORY_START = "2021-01-01"  # backtest için veri başlangıcı
DATA_DIR = "data"

# Coin evreni: boş bırakılırsa MEXC'de USDT hacmine göre en büyük TOP_N altcoin otomatik seçilir.
SYMBOLS = []  # örn: ["SOL/USDT", "AVAX/USDT", "LINK/USDT"]
TOP_N = 30
EXCLUDE_BASES = {
    "BTC", "USDC", "FDUSD", "TUSD", "DAI", "USDE", "USDD", "BUSD", "PYUSD", "EUR", "USD1",
}
MIN_AVG_QUOTE_VOLUME = 1_000_000  # 20 mumluk ortalama USDT hacmi bunun altındaysa işlem açılmaz

# BTC rejim filtresi: altcoinler BTC'yi takip eder, BTC trendde değilse yeni giriş yok
REGIME_SYMBOL = "BTC/USDT"

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
