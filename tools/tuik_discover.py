#!/usr/bin/env python3
"""TÜİK SDMX servisinde TÜFE veri akışını keşfeder (yalnızca okur).

Kullanım (anahtar ve token ekrana basılmaz, dosyaya yazılmaz; yalnızca ortam değişkeninden okunur):

    read -rs TUIK_API_KEY        # İstemde TÜİK API ANAHTARINI yapıştır (bilgisayar şifresini DEĞİL!)
    python3 tools/tuik_discover.py              # 1. aşama: yapı + son iki ay örnek veri
    python3 tools/tuik_discover.py --history    # 2. aşama: 2020'den beri aylık/yıllık değişim serileri
    unset TUIK_API_KEY

İsteğe bağlı: TUIK_TOKEN doluysa token isteği atılmaz (örn. curl ile alınmış token).

Çıktılar ./tuik_out/ klasörüne yazılır (anahtar ve token içermez). Ekrandaki özeti
paylaşmak güvenlidir. 1. aşama 3, 2. aşama en çok 3 istek atar.

  python3 tools/tuik_discover.py --selftest   # ağ ve anahtar olmadan ayrıştırıcı testi

Belge: https://veriportali.tuik.gov.tr/en/sdmx-web-service-documentation
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

TOKEN_URL = "https://giris.tuik.gov.tr/realms/web/protocol/openid-connect/token"
BASE = "https://nsiws.tuik.gov.tr/rest"
FLOW = "DF_TUFE_SDMX_TT01"
OUT = "tuik_out"
NS = {
    "m": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "s": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure",
    "c": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common",
    "g": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic",
}
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
INTERESTING = ("FREQ", "REF_AREA", "DEGISIM", "SINIFLAMA_DUZEYI")
SHOW_CODELISTS = {"FREQ", "DEGISIM", "SINIFLAMA_DUZEYI"}  # REF_AREA ~1.000 satır, basılmaz
# Dataflow boyut sırası (keşifte doğrulandı): anahtar bu sırayla kurulur.
DIM_ORDER = ["REF_AREA", "FREQ", "SINIFLAMA_DUZEYI", "DEGISIM", "OZEL_KAPSAM_TUFE",
             "BASE_PER", "YAYIM_DONEMI", "COICOP_1999", "COICOP_2018", "INDICATOR"]


def die(msg):
    print(f"HATA: {msg}", file=sys.stderr)
    sys.exit(1)


def http(url, headers=None, data=None, timeout=90):
    req = urllib.request.Request(url, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except OSError as e:
        die(f"{url.split('?')[0]} -> {e}")


def key_diagnostics(raw_key):
    """Anahtarın İÇERİĞİNİ göstermeden yapıştırma sorunlarını yakalar."""
    stripped = raw_key.strip()
    return {
        "uzunluk": len(stripped),
        "bas_son_bosluk_vardi": raw_key != stripped,
        "icinde_bosluk_ya_da_satir_sonu": any(ch.isspace() for ch in stripped),
        "harf_rakam_disi_karakter_sayisi": sum(1 for ch in stripped if not ch.isalnum()),
        "ascii_disi_karakter_var": any(ord(ch) > 127 for ch in stripped),
    }


def get_token(api_key):
    body = urllib.parse.urlencode([
        ("grant_type", "password"), ("client_id", "nsi-ws-consumer"), ("api_key", api_key),
    ]).encode()
    status, raw = http(TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    print(f"token isteği: HTTP {status}")
    if status != 200:
        die(f"token alınamadı (HTTP {status}): {raw[:200].decode('utf-8', 'replace')}\n"
            "Not: sunucu geçersiz anahtarı 500 ile reddediyor. İstemte TÜİK API anahtarını "
            "yapıştırdığından emin ol (bilgisayar şifresini değil).")
    token = json.loads(raw).get("access_token")
    if not token:
        die("yanıtta access_token yok")
    return token


def name_of(el):
    """tr adı, yoksa en adı, yoksa ilk ad."""
    names = {n.get(XML_LANG): (n.text or "") for n in el.findall("c:Name", NS)}
    return names.get("tr") or names.get("en") or next(iter(names.values()), "")


def parse_structures(xml_bytes):
    """Dataflow + DSD + kod listelerinden {flows, dsd, dims:[...], codelists:{id:{kod:ad}}}."""
    root = ET.fromstring(xml_bytes)
    out = {"flows": [], "dsd": None, "dims": [], "codelists": {}}
    for f in root.iter("{%s}Dataflow" % NS["s"]):
        out["flows"].append({"id": f.get("id"), "version": f.get("version"), "name": name_of(f)})
    for cl in root.iter("{%s}Codelist" % NS["s"]):
        out["codelists"][cl.get("id")] = {
            c.get("id"): name_of(c) for c in cl.findall("s:Code", NS)
        }
        out["codelists"][cl.get("id")]["__version"] = cl.get("version")
    for dsd in root.iter("{%s}DataStructure" % NS["s"]):
        out["dsd"] = {"id": dsd.get("id"), "version": dsd.get("version")}
        for d in dsd.iter("{%s}Dimension" % NS["s"]):
            ref = d.find("s:LocalRepresentation/s:Enumeration/Ref", NS)
            out["dims"].append({
                "id": d.get("id"),
                "position": int(d.get("position", "0")),
                "codelist": ref.get("id") if ref is not None else None,
            })
        out["dims"].sort(key=lambda x: x["position"])
        break
    return out


def parse_generic(xml_bytes):
    """SDMX-ML 2.1 Generic Data -> [{'key': {boyut: kod}, 'obs': [(dönem, değer), ...]}, ...]."""
    root = ET.fromstring(xml_bytes)
    series = []
    for s in root.iter("{%s}Series" % NS["g"]):
        key = {v.get("id"): v.get("value") for v in s.findall("g:SeriesKey/g:Value", NS)}
        obs = []
        for o in s.findall("g:Obs", NS):
            period = o.find("g:ObsDimension", NS).get("value")
            val = o.find("g:ObsValue", NS)
            obs.append((period, val.get("value") if val is not None else None))
        series.append({"key": key, "obs": obs})
    return series


def build_key(filters):
    """Boyut sırasına göre seri anahtarı; filtresiz boyutlar boş bırakılır."""
    return ".".join(filters.get(d, "") for d in DIM_ORDER)


def summarize_history(series):
    """Aylık değişim (DEGISIM=2) ve yıllık (4) serilerinin kapsamını ve çakışmalarını özetler."""
    lines = []
    groups = defaultdict(list)
    for s in series:
        k = s["key"]
        g = (k.get("DEGISIM"), k.get("BASE_PER"), k.get("YAYIM_DONEMI"), k.get("COICOP_2018"), k.get("INDICATOR"))
        groups[g].append(s)
    lines.append(f"{len(series)} seri, {len(groups)} farklı (DEGISIM, BASE_PER, YAYIM_DONEMI, COICOP_2018, INDICATOR) kombinasyonu:")
    for g, ss in sorted(groups.items(), key=lambda x: (str(x[0][0]), str(x[0][1]), str(x[0][2]))):
        periods = sorted(p for s in ss for p, _ in s["obs"])
        span = f"{periods[0]} → {periods[-1]} ({len(periods)} gözlem)" if periods else "boş"
        lines.append(f"  DEGISIM={g[0]} BASE_PER={g[1]} YAYIM_DONEMI={g[2]} COICOP_2018={g[3]} IND={g[4]}  {span}")
    # Aynı dönem birden fazla seride varsa değerleri aynı mı?
    for degisim in sorted({s["key"].get("DEGISIM") for s in series}):
        byp = defaultdict(set)
        for s in series:
            if s["key"].get("DEGISIM") == degisim:
                for p, v in s["obs"]:
                    byp[p].add(v)
        conflicts = {p: sorted(v) for p, v in byp.items() if len(v) > 1}
        lines.append(f"DEGISIM={degisim}: {len(byp)} farklı dönem, çakışan (farklı değerli) dönem: {len(conflicts)}"
                     + (f"  örnek: {dict(list(sorted(conflicts.items()))[:3])}" if conflicts else ""))
    return lines


def selftest():
    xml = """<m:Structure xmlns:m="%(m)s" xmlns:s="%(s)s" xmlns:c="%(c)s"><m:Structures>
    <s:Dataflows><s:Dataflow id="DF_X" agencyID="TR" version="1.2"><c:Name xml:lang="tr">TÜFE</c:Name><c:Name xml:lang="en">CPI</c:Name></s:Dataflow></s:Dataflows>
    <s:Codelists><s:Codelist id="CL_DEGISIM" version="3.0"><c:Name xml:lang="en">Change</c:Name>
      <s:Code id="1"><c:Name xml:lang="tr">Aylık değişim</c:Name></s:Code>
      <s:Code id="2"><c:Name xml:lang="en">Annual change</c:Name></s:Code></s:Codelist></s:Codelists>
    <s:DataStructures><s:DataStructure id="DSD_X" version="1.0"><s:DataStructureComponents><s:DimensionList>
      <s:Dimension id="DEGISIM" position="2"><s:LocalRepresentation><s:Enumeration><Ref id="CL_DEGISIM"/></s:Enumeration></s:LocalRepresentation></s:Dimension>
      <s:Dimension id="FREQ" position="1"><s:LocalRepresentation><s:Enumeration><Ref id="CL_SIKLIK"/></s:Enumeration></s:LocalRepresentation></s:Dimension>
    </s:DimensionList></s:DataStructureComponents></s:DataStructure></s:DataStructures></m:Structures></m:Structure>""" % NS
    p = parse_structures(xml.encode())
    assert p["flows"] == [{"id": "DF_X", "version": "1.2", "name": "TÜFE"}], p["flows"]
    assert p["dsd"] == {"id": "DSD_X", "version": "1.0"}
    assert [d["id"] for d in p["dims"]] == ["FREQ", "DEGISIM"], p["dims"]  # konuma göre
    assert p["codelists"]["CL_DEGISIM"]["1"] == "Aylık değişim"
    assert p["codelists"]["CL_DEGISIM"]["2"] == "Annual change"  # tr yoksa en

    def ser(degisim, base, per, obs):
        vals = "".join(f'<g:Value id="{k}" value="{v}"/>' for k, v in
                       [("REF_AREA", "TR"), ("FREQ", "M"), ("DEGISIM", degisim), ("BASE_PER", base), ("YAYIM_DONEMI", per)])
        o = "".join(f'<g:Obs><g:ObsDimension id="TIME_PERIOD" value="{p}"/><g:ObsValue value="{v}"/></g:Obs>' for p, v in obs)
        return f"<g:Series><g:SeriesKey>{vals}</g:SeriesKey>{o}</g:Series>"
    data = ('<message:GenericData xmlns:message="%(m)s" xmlns:g="%(g)s"><message:DataSet>' % NS
            + ser("2", "2003", "2025_12", [("2025-12", "0.9"), ("2025-11", "0.8")])
            + ser("2", "2025", "2026_01", [("2025-12", "0.9"), ("2026-01", "4.8")])
            + ser("4", "2025", "2026_01", [("2026-01", "30.1")])
            + "</message:DataSet></message:GenericData>").encode()
    gs = parse_generic(data)
    assert len(gs) == 3 and gs[0]["key"]["BASE_PER"] == "2003" and gs[1]["obs"][1] == ("2026-01", "4.8")
    text = "\n".join(summarize_history(gs))
    assert "DEGISIM=2: 3 farklı dönem, çakışan (farklı değerli) dönem: 0" in text, text  # 2025-12 iki seride aynı değer
    gs[1]["obs"][0] = ("2025-12", "1.0")  # çakışma yarat
    assert "çakışan (farklı değerli) dönem: 1" in "\n".join(summarize_history(gs))
    k = build_key({"REF_AREA": "TR", "FREQ": "M", "SINIFLAMA_DUZEYI": "TUFE", "DEGISIM": "2+4", "COICOP_2018": "0", "INDICATOR": "F_TFE"})
    # 4 boş boyut (OZEL_KAPSAM, BASE_PER, YAYIM_DONEMI, COICOP_1999) = 5 nokta arası
    assert k == "TR.M.TUFE.2+4.....0.F_TFE", k
    assert k.count(".") == len(DIM_ORDER) - 1
    d = key_diagnostics("  ab-cd 12\n")
    assert d["uzunluk"] == 8 and d["bas_son_bosluk_vardi"] and d["icinde_bosluk_ya_da_satir_sonu"], d
    assert d["harf_rakam_disi_karakter_sayisi"] == 2 and not d["ascii_disi_karakter_var"], d
    assert "ab-cd" not in json.dumps(d)  # özet anahtarın içeriğini taşımaz
    print("selftest geçti")


def acquire_auth():
    # Hazır token varsa (örn. curl ile alınmış) token isteği ATILMAZ.
    token = os.environ.get("TUIK_TOKEN", "").strip()
    if token:
        print(f"TUIK_TOKEN kullanılıyor (uzunluk {len(token)}, içerik gösterilmez); token isteği atılmadı\n")
    else:
        key = os.environ.get("TUIK_API_KEY", "").strip()
        if not key:
            die("TUIK_TOKEN ya da TUIK_API_KEY ortam değişkeni gerekli. Başlıktaki kullanıma bak.")
        print("anahtar özeti (içerik gösterilmez):", key_diagnostics(os.environ["TUIK_API_KEY"]))
        token = get_token(key)
        print("token alındı (içerik gösterilmez)\n")
    return {"Authorization": f"Bearer {token}"}


def stage_structure(auth):
    url = f"{BASE}/dataflow/TR/{FLOW}/latest?references=descendants"
    status, raw = http(url, auth)
    if status != 200:
        die(f"veri akışı alınamadı (HTTP {status}): {raw[:300].decode('utf-8', 'replace')}")
    open(f"{OUT}/dataflow.xml", "wb").write(raw)
    p = parse_structures(raw)
    flow = next((f for f in p["flows"] if f["id"] == FLOW), None)
    if not flow:
        die(f"{FLOW} yanıtta yok: {[f['id'] for f in p['flows']]}")
    print(f"Veri akışı: {flow['id']} v{flow['version']} · {flow['name']}")
    print(f"DSD: {p['dsd']}")
    print("Boyutlar (sıra önemli):")
    for d in p["dims"]:
        print(f"  {d['position']:>2}. {d['id']:<22} kod listesi: {d['codelist']}")
    for d in p["dims"]:
        if d["id"] in SHOW_CODELISTS and d["codelist"] in p["codelists"]:
            cl = p["codelists"][d["codelist"]]
            print(f"\n{d['id']} ({d['codelist']} v{cl.get('__version')}):")
            for code, nm in cl.items():
                if code != "__version":
                    print(f"  {code:<8} {nm}")
    return flow


def fetch_data(auth, version, key, start, end):
    q = urllib.parse.urlencode({"startPeriod": start, "endPeriod": end})
    url = f"{BASE}/data/TR,{FLOW},{version}/{key}?{q}" if key else f"{BASE}/data/TR,{FLOW},{version}?{q}"
    status, raw = http(url, auth)
    return status, raw


def stage_sample(auth, version):
    status, raw = fetch_data(auth, version, "", "2026-09", "2026-10")
    if status != 200:
        die(f"örnek veri alınamadı (HTTP {status}): {raw[:300].decode('utf-8', 'replace')}")
    open(f"{OUT}/data_sample.xml", "wb").write(raw)
    series = parse_generic(raw)
    print(f"\nÖrnek veri: {len(series)} seri (dosya: {OUT}/data_sample.xml)")


def stage_history(auth, version):
    """2020 sonrası TR/aylık/TÜFE genel için aylık (2) ve yıllık (4) değişim serileri."""
    # Seri anahtarı KULLANILMAZ: önceki denemede kısmi anahtarlar 404 verdi (IIS URL yolundaki
    # '+' işaretini reddediyor olabilir; R istemcisi de kısmi anahtarların kabul edilmediğini not
    # ediyor). Anahtarsız istek örnekte çalıştı: tüm seriler gelir, burada süzülür.
    # 2020'den başlar: uygulamanın kur verisiyle aynı ve 5 yıllık tarih seçicisini kapsar.
    print("\nİstek: anahtarsız, 2020-01 → 2026-12 (tüm seriler; birkaç MB, bir dakika sürebilir)")
    status, raw = fetch_data(auth, version, "", "2020-01", "2026-12")
    print(f"  HTTP {status}, {len(raw)} bayt")
    if status != 200:
        die(f"geçmiş verisi alınamadı: {raw[:300].decode('utf-8', 'replace')}")
    open(f"{OUT}/history.xml", "wb").write(raw)
    series = parse_generic(raw)
    print(f"  toplam {len(series)} seri okundu (dosya: {OUT}/history.xml)")
    wanted = [s for s in series
              if s["key"].get("REF_AREA") == "TR" and s["key"].get("FREQ") == "M"
              and s["key"].get("SINIFLAMA_DUZEYI") == "TUFE" and s["key"].get("COICOP_2018") == "0"
              and s["key"].get("INDICATOR") == "F_TFE" and s["key"].get("DEGISIM") in ("1", "2", "4")]
    print(f"  bunlardan TR/aylık/TÜFE genel (endeks, aylık %, yıllık %): {len(wanted)} seri\n")
    if not wanted:
        die("beklenen TÜFE serisi bulunamadı; boyut kodları değişmiş olabilir")
    for line in summarize_history(wanted):
        print(line)


def main():
    if "--selftest" in sys.argv:
        return selftest()
    os.makedirs(OUT, exist_ok=True)
    auth = acquire_auth()
    # Sürüm 1.0 keşifte doğrulandı; geçmiş aşaması yapı isteğini atlar (2,8 MB tasarruf).
    if "--history" in sys.argv:
        return stage_history(auth, "1.0")
    flow = stage_structure(auth)
    stage_sample(auth, flow["version"])


if __name__ == "__main__":
    main()
