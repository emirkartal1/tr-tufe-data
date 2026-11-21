#!/usr/bin/env python3
"""TCMB "Tüketici Fiyatları" sayfasından TÜFE değişimini okuyup tufe.json üretir.

Kullanım:  python3 tools/update_tufe.py <çıktı.json>

Uygulama bu dosyayı kendi barındırmamızdan çeker, TCMB sayfasını doğrudan okumaz.
Sayfa düzeni değişirse ya da veri tutarsızsa script HATA ile çıkar ve çıktı dosyasına
dokunmaz; böylece yayındaki son iyi dosya olduğu gibi kalır.

Yalnızca standart kütüphane kullanır.
"""
import datetime
import html
import json
import re
import sys
import urllib.request

URL = ("https://www.tcmb.gov.tr/wps/wcm/connect/TR/TCMB+TR/Main+Menu/"
       "Istatistikler/Enflasyon+Verileri/Tuketici+Fiyatlari")
EXPECTED_HEADER = ["Ay-Yıl", "TÜFE (Yıllık % Değişim)", "TÜFE (Aylık % Değişim)"]
MIN_MONTHS = 240          # 2005'ten beri ~260 ay var
MAX_AGE_MONTHS = 4        # son veri bundan eskiyse sayfa güncellenmiyor demektir
CHAIN_TOLERANCE_PP = 0.15  # 12 aylık zincir ile yayınlanan yıllık oran farkı


class DataError(Exception):
    pass


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "tufe-data-updater"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def _cells(row_html: str):
    cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, flags=re.S)
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in cells]


def parse(page: str):
    """[(yıl, ay, yıllık, aylık), ...] en yeniden eskiye."""
    page = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    rows = [_cells(r) for r in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.S)]
    if EXPECTED_HEADER not in rows:
        raise DataError("tablo başlığı bulunamadı: sayfa düzeni değişmiş olabilir")
    out = []
    for c in rows:
        if len(c) == 3 and re.fullmatch(r"\d{2}-\d{4}", c[0]):
            try:
                out.append((int(c[0][3:]), int(c[0][:2]), float(c[1]), float(c[2])))
            except ValueError:
                raise DataError(f"sayı okunamadı: {c}")
    return out


def validate(months, today: datetime.date):
    if len(months) < MIN_MONTHS:
        raise DataError(f"çok az ay okundu: {len(months)}")
    idx = [y * 12 + (m - 1) for y, m, _, _ in months]
    for a, b in zip(idx, idx[1:]):
        if a - b != 1:
            raise DataError("aylar ardışık değil (boşluk ya da tekrar var)")
    for y, m, annual, monthly in months:
        if not (-20 <= monthly <= 60) or not (-50 <= annual <= 500):
            raise DataError(f"makul aralık dışı değer: {y}-{m:02d} {annual} {monthly}")
    y, m, annual, _ = months[0]
    age = (today.year * 12 + today.month - 1) - (y * 12 + m - 1)
    if age > MAX_AGE_MONTHS:
        raise DataError(f"son veri {y}-{m:02d}, {age} ay eski")
    factor = 1.0
    for _, _, _, monthly in months[:12]:
        factor *= 1 + monthly / 100
    chained = (factor - 1) * 100
    if abs(chained - annual) > CHAIN_TOLERANCE_PP:
        raise DataError(f"12 aylık zincir %{chained:.2f}, yayınlanan yıllık %{annual:.2f}")


def build(months, now: datetime.datetime):
    asc = list(reversed(months))
    return {
        "source": "TCMB",
        "sourceUrl": URL,
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lastMonth": f"{asc[-1][0]}-{asc[-1][1]:02d}",
        # [ay, aylık % değişim, yıllık % değişim], eskiden yeniye
        "months": [[f"{y}-{m:02d}", monthly, annual] for y, m, annual, monthly in asc],
    }


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        months = parse(fetch(URL))
        validate(months, now.date())
    except (DataError, OSError) as e:
        print(f"HATA: {e}", file=sys.stderr)
        return 1
    data = build(months, now)
    with open(argv[1], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print(f"{len(months)} ay yazıldı, son ay {data['lastMonth']} -> {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
