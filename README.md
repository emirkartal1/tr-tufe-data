# tr-tufe-data

Küçük, doğrulanmış iki veri dosyasını günde bir kez üretip GitHub Pages'ten yayınlar:

| Dosya | İçerik | Kaynak |
| --- | --- | --- |
| `tufe.json` | Türkiye TÜFE, aylık ve yıllık % değişim | TÜİK [Veri Portalı](https://veriportali.tuik.gov.tr/tr) SDMX web servisi |
| `kur.json` | Günlük USD/TRY (çapraz) ve EUR/TRY | Avrupa Merkez Bankası (ECB) euro referans kurları |

## Kaynak ve değişiklik bildirimi

- **TÜFE:** Kaynak TÜİK. TÜİK'in [Yasal Uyarı](https://www.tuik.gov.tr/Kurumsal/Yasal_Uyari)
  sayfası, verilerin kaynak gösterilerek izne gerek duyulmadan yeniden kullanılabileceğini
  söyler. Bu depo yalnızca yeniden biçimlendirir (yalnızca TR/aylık/TÜFE genel; aylık ve yıllık % değişim).
- **Kur:** Kaynak ECB ([koşullar](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html)).
  ECB doğrudan USD/TRY yayınlamaz: **USD/TRY, ECB'nin EUR/TRY ve EUR/USD kurlarından
  çapraz hesaplanmıştır**, değerler 4 ondalığa yuvarlanmıştır ve 2020'den itibaren
  günlük alınmıştır. Bu bir değişikliktir ve ECB'nin yayınladığı biçim değildir.
  Veri ücretsiz olarak ECB sitesinden de alınabilir.

Doğruluk garantisi verilmez; resmî bir kur ya da endeks değildir.

## Dosya biçimleri

```json
// tufe.json
{ "source": "TCMB", "lastMonth": "2026-10",
  "months": [["2005-01", 0.55, 9.24], "..."] }   // [ay, aylık %, yıllık %]

// kur.json
{ "source": "ECB", "lastDate": "2026-11-20", "note": "...",
  "days": [["2020-01-02", 5.9, 6.6], "..."] }    // [tarih, USD/TRY, EUR/TRY]
```

Satırlar eskiden yeniye, ardışık ve tekrarsızdır.

## Güvenlik ağı

Her üretici (`tools/update_tufe_tuik.py`, `tools/update_kur.py`) doğrulamada takılırsa hata
ile çıkar ve çıktı dosyasına dokunmaz; yayındaki son iyi dosya kalır.
- **TÜFE:** tablo düzeni değişmiş, ay boşluğu, çok az ay, 4 aydan eski son veri, makul
  aralık dışı değer, 12 aylık bileşik değişim yayınlanan yıllık orana uymuyor.
- **Kur:** biçim değişmiş, TRY/USD eksik, çok az gün, tekrarlayan tarih, sıfır ya da
  negatif kur, tek günde %30'dan büyük hareket, 10 günden eski son veri.

İki üretici bağımsızdır: biri takılırsa diğeri yine yayınlanır, iş yine de başarısız görünür.

## Yerelde çalıştırma

```bash
TUIK_API_KEY=... python3 tools/update_tufe_tuik.py /tmp/tufe.json   # kişisel TÜİK API anahtarı
python3 tools/update_kur.py /tmp/kur.json
```

Yalnızca Python standart kütüphanesi kullanılır.

## TÜİK API anahtarı (GitHub secret)

TÜİK SDMX servisi kişisel bir API anahtarı ister (Veri Portalı'nda SMS doğrulamasıyla
üretilir). Anahtar depoya YAZILMAZ; `TUIK_API_KEY` adıyla şifreli bir secret olarak verilir:

```bash
gh secret set TUIK_API_KEY --repo emirkartal1/tr-tufe-data
```

`tools/update_tufe.py` (TCMB sayfası okuyucusu) yalnızca `--compare-tcmb` ile çapraz
doğrulama içindir; yayına girmez. `tools/tuik_discover.py` bir keşif aracıdır.
