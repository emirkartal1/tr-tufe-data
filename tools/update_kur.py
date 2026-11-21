#!/usr/bin/env python3
"""ECB euro referans kurlarından günlük USD/TRY ve EUR/TRY serisi üretir (kur.json).

Kullanım:  python3 tools/update_kur.py <çıktı.json>

Kaynak: Avrupa Merkez Bankası (ECB) euro foreign exchange reference rates.
ECB koşulları: bilgi ücretsiz kullanılabilir; ECB kaynak gösterilmeli; veri
değiştirilmişse bu açıkça belirtilmeli. Burada iki değişiklik yapılır ve dosyada
"note" alanında belirtilir:
  1. USD/TRY, ECB'nin EUR/TRY ve EUR/USD kurlarından ÇAPRAZ hesaplanır
     (ECB doğrudan USD/TRY yayınlamaz).
  2. Yalnızca 2020'den bugüne günlük değerler alınır ve 4 ondalığa yuvarlanır.

Veri tutarsızsa script HATA ile çıkar ve çıktı dosyasına dokunmaz; yayındaki son iyi
dosya olduğu gibi kalır. Yalnızca standart kütüphane kullanır.
"""
import datetime
import json
import re
import sys
import urllib.request

URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml"
SOURCE_URL = ("https://www.ecb.europa.eu/stats/policy_and_exchange_rates/"
              "euro_reference_exchange_rates/html/index.en.html")
FROM = "2020-01-01"
MIN_DAYS = 1500           # 2020'den beri ~1.700 iş günü var
MAX_AGE_DAYS = 10         # son veri bundan eskiyse dosya güncellenmiyor demektir
MAX_DAILY_MOVE = 0.30     # bir günde ±%30'dan büyük hareket veri hatasıdır
NOTE = ("Kaynak: Avrupa Merkez Bankası (ECB) euro referans kurları. USD/TRY, ECB'nin "
        "EUR/TRY ve EUR/USD kurlarından çapraz hesaplanmıştır; 2020'den itibaren günlük "
        "değerler 4 ondalığa yuvarlanmıştır. Bu veri ECB tarafından değiştirilmiş "
        "biçimiyle yayınlanmaz ve resmi bir kur değildir.")


class DataError(Exception):
    pass


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "kur-data-updater"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", errors="replace")


def _rate(body: str, code: str):
    m = re.search(r"currency=[\"']%s[\"']\s+rate=[\"']([0-9.]+)[\"']" % code, body)
    return float(m.group(1)) if m else None


def parse(xml: str):
    """[(tarih, usdtry, eurtry), ...] eskiden yeniye, FROM'dan itibaren."""
    days = re.findall(r"<Cube time=[\"'](\d{4}-\d{2}-\d{2})[\"']>(.*?)</Cube>", xml, flags=re.S)
    if not days:
        raise DataError("ECB dosyasında günlük kur bulunamadı: biçim değişmiş olabilir")
    out = []
    for date, body in days:
        if date < FROM:
            continue
        eur_try, eur_usd = _rate(body, "TRY"), _rate(body, "USD")
        if eur_try is None or eur_usd is None:
            raise DataError(f"{date}: TRY ya da USD kuru yok")
        out.append((date, round(eur_try / eur_usd, 4), round(eur_try, 4)))
    out.sort()
    return out


def validate(rows, today: datetime.date):
    if len(rows) < MIN_DAYS:
        raise DataError(f"çok az gün okundu: {len(rows)}")
    dates = [r[0] for r in rows]
    if len(set(dates)) != len(dates):
        raise DataError("tekrarlayan tarih var")
    for date, usd, eur in rows:
        if usd <= 0 or eur <= 0:
            raise DataError(f"{date}: sıfır ya da negatif kur")
    for a, b in zip(rows, rows[1:]):
        for i in (1, 2):
            move = abs(b[i] / a[i] - 1)
            if move > MAX_DAILY_MOVE:
                raise DataError(f"{b[0]}: tek günde %{move * 100:.0f} hareket")
    last = datetime.date.fromisoformat(rows[-1][0])
    if (today - last).days > MAX_AGE_DAYS:
        raise DataError(f"son veri {last}, {(today - last).days} gün eski")


def build(rows, now: datetime.datetime):
    return {
        "source": "ECB",
        "sourceUrl": SOURCE_URL,
        "note": NOTE,
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lastDate": rows[-1][0],
        # [tarih, USD/TRY (çapraz), EUR/TRY], eskiden yeniye
        "days": [list(r) for r in rows],
    }


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        rows = parse(fetch(URL))
        validate(rows, now.date())
    except (DataError, OSError) as e:
        print(f"HATA: {e}", file=sys.stderr)
        return 1
    data = build(rows, now)
    with open(argv[1], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print(f"{len(rows)} gün yazıldı, son gün {data['lastDate']} -> {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
