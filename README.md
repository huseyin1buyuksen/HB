# MEXC Altcoin Sinyal Botu (trend + momentum)

Bu araç **işlem yapmaz**. Sadece sinyal üretir ve stratejiyi geçmiş veride test eder. API anahtarı gerekmez, çünkü sadece herkese açık fiyat verisini kullanır. Çıktıları yatırım tavsiyesi değildir.

## Strateji (araştırmadan çıkan yapı)

| Katman | Kural |
|---|---|
| BTC rejim filtresi | BTC 50 EMA'nın üstünde ve 20 mumluk getirisi pozitifse yeni girişe izin var |
| Trend | Coin 50 EMA'nın üstünde |
| Momentum | 20 mumluk getiri pozitif |
| Giriş | Kapanış, önceki 20 mumun en yükseğini kırar (Donchian kırılımı) |
| Likidite | 20 mumluk ortalama hacim ≥ 1M USDT |
| Çıkış | Kapanış önceki 10 mumun dibinin altına düşer **veya** iz süren ATR stop'un altına düşer (en yüksek kapanış − 3×ATR) |
| Pozisyon | Stop'a kadar olan kayıp sermayenin %1'i, tek coin en fazla %20, en fazla 5 açık pozisyon |
| Maliyet | İşlem başına %0.1 komisyon + %0.1 kayma |

Sinyal mum kapanışında üretilir, işlem bir sonraki mumun açılışında varsayılır. Tüm ayarlar `config.py` dosyasında.

## Kurulum

```bash
pip install -r requirements.txt
```

## 0. Sinyal sitesi (web paneli)

`app.py` bir web sitesi: sinyalleri MEXC'den canlı hesaplar, grafik çizer, backtest'i tek tuşla çalıştırır.

**Kendi bilgisayarında aç:**
```bash
pip install -r requirements.txt
streamlit run app.py
```
Tarayıcıda `http://localhost:8501` adresi açılır.

**Siteyi canlıya alma (ücretsiz, her yerden erişilebilir link):**

*Yol A — Streamlit Community Cloud (en kolayı):*
1. github.com'da ücretsiz hesap aç → "New repository" → isim ver (örn. `mexc-sinyal`) → oluştur.
2. Repo sayfasında "uploading an existing file" linkine tıkla, `mexc_bot` klasörünün İÇİNDEKİ bütün dosyaları sürükle → "Commit changes".
3. share.streamlit.io adresine gir, GitHub ile giriş yap → "Create app" → repoyu seç, Main file: `app.py` → "Deploy".
4. Birkaç dakika sonra `https://....streamlit.app` linkin hazır.

*Yol B — Render, Frankfurt sunucusu (A'da MEXC bağlantı hatası alırsan):*
Streamlit Cloud sunucuları ABD'de ve MEXC bazı ülkelerden gelen istekleri engelleyebiliyor. Öyle olursa:
1. render.com'da GitHub ile giriş yap → "New" → "Blueprint" → aynı repoyu seç.
2. Render, repodaki `render.yaml` dosyasını okuyup siteyi Frankfurt'ta kurar. Link `https://....onrender.com` olur.
3. Ücretsiz planda site bir süre ziyaret edilmezse uyur; ilk açılış 30-60 saniye sürebilir.

Not: Link herkese açıktır ama içinde şifre ya da API anahtarı yoktur, sadece herkese açık fiyat verisini gösterir.

## 1. Backtest (komut satırından)

```bash
python backtest.py --refresh   # MEXC'den veriyi indirir (ilk sefer birkaç dakika sürer)
python backtest.py             # sonraki çalıştırmalarda önbellekten okur
python backtest.py --grid      # parametre sağlamlık tablosu
```

Çıktılar:
- Ekranda tüm dönem, in-sample ve **out-of-sample** metrikleri, ayrıca BTC al-tut karşılaştırması
- `backtest_sonuc.png`: sermaye eğrisi ve düşüş grafiği
- `islemler.csv`: her işlem tek tek

**Sonuçları nasıl okumalısın:**
- Asıl bakılacak yer OUT-OF-SAMPLE bölümü. In-sample'da harika görünüp out-of-sample'da çöküyorsa strateji geçmişe uydurulmuş demektir.
- 30'dan az işlem varsa sonuç güvenilir değildir.
- Trend stratejilerinde kazanma oranının %35–45 civarında olması normaldir. Önemli olan "ort. kazanç / ort. kayıp" oranının 2'nin üstünde olması.
- `--grid` tablosunda tek bir hücre parlıyor, komşuları kötüyse o parametreye güvenme. Geniş bir iyi bölge (plato) arıyoruz.
- Sadece bugün listede olan coinler test ediliyor. Batıp listeden kalkanlar hesaba girmediği için gerçek sonuçlar backtest'ten daha kötü olacaktır.

## 2. Sinyal botu

```bash
python signal_bot.py
```

Telegram'a göndermek için:
1. Telegram'da @BotFather ile bot oluştur, token'ı al.
2. Botuna bir mesaj at, sonra `https://api.telegram.org/bot<TOKEN>/getUpdates` adresinden `chat.id` değerini bul.
3. Ortam değişkenlerini ayarla:
   - Windows: `setx TELEGRAM_BOT_TOKEN "..."` ve `setx TELEGRAM_CHAT_ID "..."`
   - Mac/Linux: `export TELEGRAM_BOT_TOKEN=...` ve `export TELEGRAM_CHAT_ID=...`

Elindeki coinleri `positions.json` dosyasına yazarsan bot onlar için çıkış sinyali ve güncel stop seviyesi verir (`positions.example.json` dosyasına bak).

**Otomatik çalıştırma:** Günlük mumlar 03:00 TSİ'de (00:00 UTC) kapanır. Botu her gün 03:05'te çalıştır:
- Windows: Görev Zamanlayıcı → "python signal_bot.py"
- Mac/Linux: `crontab -e` → `5 0 * * * cd /yol/mexc_bot && python signal_bot.py` (sunucu saati UTC ise)

## Önerilen yol haritası

1. Backtest'i çalıştır ve sonuçları birlikte yorumlayalım.
2. Out-of-sample makulse: 1–2 ay boyunca sinyalleri sadece kağıt üzerinde takip et (paper trading).
3. Sinyaller backtest'le tutarlı çıkarsa sonraki adımı o zaman konuşuruz.
