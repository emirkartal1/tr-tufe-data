# tr-tufe-data

Türkiye TÜFE (aylık ve yıllık % değişim) verisini, TCMB'nin herkese açık
[Tüketici Fiyatları](https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/Istatistikler/Enflasyon+Verileri/Tuketici+Fiyatlari)
sayfasından günde bir kez okuyup küçük bir JSON dosyası olarak yayınlar.

Kaynak: Türkiye Cumhuriyet Merkez Bankası (TCMB). Veri TÜİK tarafından açıklanan
TÜFE'dir; bu depo yalnızca yeniden biçimlendirir.

## Dosya

`tufe.json`, GitHub Pages üzerinden yayınlanır:

```json
{
  "source": "TCMB",
  "sourceUrl": "...",
  "generatedAt": "2026-11-21T13:45:07Z",
  "lastMonth": "2026-10",
  "months": [["2005-01", 0.55, 9.24], ["2005-02", 0.02, 8.69], "..."]
}
```

`months` satırları `[ay, aylık % değişim, yıllık % değişim]`, eskiden yeniye ve ardışıktır.

## Güvenlik ağı

`tools/update_tufe.py` şunlardan biri varsa hata ile çıkar ve yayına gitmez
(Pages'teki son iyi dosya kalır): tablo düzeni değişmiş, ay boşluğu, çok az ay,
4 aydan eski son veri, makul aralık dışı değer, 12 aylık bileşik değişim
yayınlanan yıllık orana uymuyor.

## Yerelde çalıştırma

```bash
python3 tools/update_tufe.py /tmp/tufe.json
```

Yalnızca Python standart kütüphanesi kullanılır.
