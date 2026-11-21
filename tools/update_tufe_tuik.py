#!/usr/bin/env python3
"""TÜİK SDMX servisinden TÜFE (aylık ve yıllık % değişim) okuyup tufe.json üretir.

Kullanım:
    TUIK_API_KEY=... python3 tools/update_tufe_tuik.py <çıktı.json> [--compare-tcmb]
    python3 tools/update_tufe_tuik.py <çıktı.json> --from-file tuik_out/history.xml   # geliştirme

Kimlik: TUIK_API_KEY (TÜİK Veri Portalı'nda SMS doğrulamasıyla üretilen kişisel anahtar)
ortam değişkeni; ya da hazır TUIK_TOKEN. Anahtar ve token hiçbir yere yazdırılmaz.
GitHub Actions'ta anahtar bir şifreli *secret* olarak verilir.

Kaynak: TÜİK (https://veriportali.tuik.gov.tr). TÜİK'in Yasal Uyarı sayfası, verilerin
kaynak gösterilerek izne gerek duyulmadan yeniden kullanılabileceğini söyler.

Seri: veri akışı DF_TUFE_SDMX_TT01, TR / aylık / TÜFE genel, COICOP_2018=0, F_TFE,
DEGISIM=2 (bir önceki aya göre %) ve DEGISIM=4 (bir önceki yılın aynı ayına göre %).
Seri anahtarı kullanılmaz: anahtarsız istek tüm serileri döndürür, burada süzülür
(sunucu kısmi anahtarlara 404 verdi). Yanıt SDMX-ML Generic Data (XML).

Doğrulama başarısız olursa HATA ile çıkar ve çıktı dosyasına dokunmaz; yayındaki son iyi
dosya olduğu gibi kalır. TCMB'ye geri DÖNÜLMEZ (TCMB'nin ticari kullanım koşulu ayrıdır).
Yalnızca standart kütüphane kullanır.
"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_tufe as base  # noqa: E402  (doğrulama kuralları ve TCMB karşılaştırması)

TOKEN_URL = "https://giris.tuik.gov.tr/realms/web/protocol/openid-connect/token"
DATA_URL = "https://nsiws.tuik.gov.tr/rest/data/TR,DF_TUFE_SDMX_TT01,1.0"
SOURCE_URL = "https://veriportali.tuik.gov.tr/tr"
START = "2005-01"
G = "{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic}"
WANT = {"REF_AREA": "TR", "FREQ": "M", "SINIFLAMA_DUZEYI": "TUFE", "OZEL_KAPSAM_TUFE": "_Z",
        "COICOP_2018": "0", "INDICATOR": "F_TFE"}
MONTHLY, ANNUAL = "2", "4"


def http(url, headers=None, data=None, timeout=120):
    req = urllib.request.Request(url, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def get_token():
    token = os.environ.get("TUIK_TOKEN", "").strip()
    if token:
        return token
    key = os.environ.get("TUIK_API_KEY", "").strip()
    if not key:
        raise base.DataError("TUIK_API_KEY (ya da TUIK_TOKEN) tanımlı değil")
    body = urllib.parse.urlencode([("grant_type", "password"), ("client_id", "nsi-ws-consumer"),
                                   ("api_key", key)]).encode()
    status, raw = http(TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    if status != 200:
        # Anahtar ya da gövde YAZDIRILMAZ. Sunucu geçersiz anahtara 500 ile yanıt verir.
        raise base.DataError(f"TÜİK token alınamadı (HTTP {status}); anahtar geçersiz/iptal olabilir")
    token = json.loads(raw).get("access_token")
    if not token:
        raise base.DataError("token yanıtında access_token yok")
    return token


def fetch_xml(token, retries=3):
    q = urllib.parse.urlencode({"startPeriod": START, "endPeriod": f"{datetime.date.today().year}-12"})
    last = None
    for attempt in range(retries):
        try:
            status, raw = http(f"{DATA_URL}?{q}", {"Authorization": f"Bearer {token}"})
        except OSError as e:
            last = f"ağ hatası: {e}"
        else:
            if status == 200:
                return raw
            last = f"HTTP {status}"
            if status < 500:  # istemci hatası tekrarla düzelmez
                break
        time.sleep(5 * (attempt + 1))
    raise base.DataError(f"TÜİK verisi alınamadı ({last})")


def parse_generic(xml_bytes):
    """[{'key': {boyut: kod}, 'obs': [(dönem, değer), ...]}, ...]"""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise base.DataError(f"yanıt XML değil: {e}")
    out = []
    for s in root.iter(G + "Series"):
        key = {v.get("id"): v.get("value") for v in s.findall(f"{G}SeriesKey/{G}Value")}
        obs = []
        for o in s.findall(G + "Obs"):
            d, v = o.find(G + "ObsDimension"), o.find(G + "ObsValue")
            if d is not None and v is not None and v.get("value") not in (None, ""):
                obs.append((d.get("value"), v.get("value")))
        out.append({"key": key, "obs": obs})
    return out


def select_months(series):
    """TÜFE genel serilerinden [(yıl, ay, yıllık, aylık), ...] (en yeniden eskiye).

    Aynı dönem birden çok seride varsa (taban/yayın dönemi değişirse) YAYIM_DONEMI'si
    en yeni olan seri kazanır. Aylık ya da yıllık değeri eksik bir ay HATADIR.
    """
    best = {}  # (degisim, dönem) -> (yayim_donemi, değer)
    for s in series:
        k = s["key"]
        if any(k.get(d) != v for d, v in WANT.items()) or k.get("DEGISIM") not in (MONTHLY, ANNUAL):
            continue
        for period, value in s["obs"]:
            cur = best.get((k["DEGISIM"], period))
            yayim = k.get("YAYIM_DONEMI") or ""
            if cur is None or yayim > cur[0]:
                best[(k["DEGISIM"], period)] = (yayim, value)
    periods = sorted({p for _, p in best})
    if not periods:
        raise base.DataError("TÜFE serisi bulunamadı: boyut kodları değişmiş olabilir")
    rows = []
    for p in periods:
        m, a = best.get((MONTHLY, p)), best.get((ANNUAL, p))
        if m is None or a is None:
            raise base.DataError(f"{p}: aylık ya da yıllık değer eksik")
        try:
            year, month = int(p[:4]), int(p[5:7])
            rows.append((year, month, float(a[1]), float(m[1])))
        except ValueError:
            raise base.DataError(f"{p}: sayı/dönem okunamadı")
    rows.sort(reverse=True)
    return rows


def build(months, now):
    asc = list(reversed(months))
    return {
        "source": "TÜİK",
        "sourceUrl": SOURCE_URL,
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lastMonth": f"{asc[-1][0]}-{asc[-1][1]:02d}",
        # [ay, aylık % değişim, yıllık % değişim], eskiden yeniye
        "months": [[f"{y}-{m:02d}", monthly, annual] for y, m, annual, monthly in asc],
    }


def compare_with_tcmb(months):
    """TCMB sayfasıyla ay ay karşılaştırır (yalnızca doğrulama; çıktıya girmez)."""
    tcmb = {(y, m): (a, mo) for y, m, a, mo in base.parse(base.fetch(base.URL))}
    mine = {(y, m): (a, mo) for y, m, a, mo in months}
    common = sorted(set(mine) & set(tcmb))
    diffs = [(k, mine[k], tcmb[k]) for k in common
             if abs(mine[k][0] - tcmb[k][0]) > 0.005 or abs(mine[k][1] - tcmb[k][1]) > 0.005]
    print(f"TCMB karşılaştırması: {len(common)} ortak ay, {len(diffs)} farklı; "
          f"yalnız TÜİK'te {len(set(mine) - set(tcmb))}, yalnız TCMB'de {len(set(tcmb) - set(mine))}")
    for (y, m), mi, tc in diffs[:10]:
        print(f"  {y}-{m:02d}: TÜİK yıllık/aylık {mi}  TCMB {tc}")
    return not diffs


def selftest():
    def ser(degisim, yayim, obs, **over):
        k = dict(WANT, DEGISIM=degisim, YAYIM_DONEMI=yayim, BASE_PER="2025", COICOP_1999="_Z")
        k.update(over)
        vals = "".join(f'<g:Value id="{a}" value="{b}"/>' for a, b in k.items())
        o = "".join(f'<g:Obs><g:ObsDimension id="TIME_PERIOD" value="{p}"/><g:ObsValue value="{v}"/></g:Obs>'
                    for p, v in obs)
        return f"<g:Series><g:SeriesKey>{vals}</g:SeriesKey>{o}</g:Series>"

    def doc(*series):
        return ('<m:GenericData xmlns:m="http://x" xmlns:g="%s"><m:DataSet>' % G[1:-1]
                + "".join(series) + "</m:DataSet></m:GenericData>").encode()

    # 1) temel seçim ve sıralama
    s = parse_generic(doc(ser("2", "2026_01", [("2026-09", "1.84"), ("2026-10", "2.58")]),
                          ser("4", "2026_01", [("2026-09", "29.73"), ("2026-10", "29.77")])))
    rows = select_months(s)
    assert rows == [(2026, 10, 29.77, 2.58), (2026, 9, 29.73, 1.84)], rows
    # 2) başka boyutlar (ana gruplar, başka INDICATOR, başka DEGISIM) yok sayılır
    noise = [ser("2", "2026_01", [("2026-10", "9.99")], SINIFLAMA_DUZEYI="2"),
             ser("2", "2026_01", [("2026-10", "8.88")], COICOP_2018="01"),
             ser("1", "2026_01", [("2026-10", "140.77")]),
             ser("2", "2026_01", [("2026-10", "7.77")], FREQ="A")]
    s2 = parse_generic(doc(ser("2", "2026_01", [("2026-10", "2.58")]),
                           ser("4", "2026_01", [("2026-10", "29.77")]), *noise))
    assert select_months(s2) == [(2026, 10, 29.77, 2.58)]
    # 3) çakışmada en yeni YAYIM_DONEMI kazanır (sıradan bağımsız)
    for order in (("2025_12", "2026_01"), ("2026_01", "2025_12")):
        parts = []
        for y in order:
            v = "1.00" if y == "2025_12" else "1.50"
            parts += [ser("2", y, [("2026-10", v)]), ser("4", y, [("2026-10", "30.0")])]
        assert select_months(parse_generic(doc(*parts))) == [(2026, 10, 30.0, 1.50)], order
    # 4) eksik yıllık değer HATA
    try:
        select_months(parse_generic(doc(ser("2", "2026_01", [("2026-10", "2.58")]))))
        raise AssertionError("eksik yıllık değer reddedilmeliydi")
    except base.DataError:
        pass
    # 5) hiç TÜFE serisi yoksa HATA; bozuk XML HATA
    for bad in (doc(ser("2", "2026_01", [("2026-10", "1")], SINIFLAMA_DUZEYI="2")), b"<html>bakim"):
        try:
            select_months(parse_generic(bad))
            raise AssertionError("reddedilmeliydi")
        except base.DataError:
            pass
    # 6) çıktı şeması uygulamanın beklediğiyle aynı
    now = datetime.datetime(2026, 11, 21, 12, 0, tzinfo=datetime.timezone.utc)
    out = build(rows, now)
    assert out["months"] == [["2026-09", 1.84, 29.73], ["2026-10", 2.58, 29.77]] and out["lastMonth"] == "2026-10"
    print("selftest geçti")


def main(argv):
    if "--selftest" in argv:
        return selftest() or 0
    # Konumsal argüman = çıktı yolu; "--from-file"ın değeri konumsal sayılmaz.
    args, skip = [], False
    for i, a in enumerate(argv[1:], start=1):
        if skip:
            skip = False
        elif a == "--from-file":
            skip = True
        elif not a.startswith("--"):
            args.append(a)
    if len(args) != 1:
        print(__doc__)
        return 2
    out_path = args[0]
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        if "--from-file" in argv:
            path = argv[argv.index("--from-file") + 1]
            xml = open(path, "rb").read()
            base.MIN_MONTHS = 60  # yalnızca geliştirme: kısa dosyalarla deneme
            print(f"yerel dosya kullanılıyor: {path} (en az ay sayısı 60'a indirildi)")
        else:
            xml = fetch_xml(get_token())
        months = select_months(parse_generic(xml))
        base.validate(months, now.date())
        if "--compare-tcmb" in argv and not compare_with_tcmb(months):
            raise base.DataError("TCMB sayfasıyla uyuşmayan aylar var")
    except (base.DataError, OSError) as e:
        print(f"HATA: {e}", file=sys.stderr)
        return 1
    data = build(months, now)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print(f"{len(months)} ay yazıldı, son ay {data['lastMonth']} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
