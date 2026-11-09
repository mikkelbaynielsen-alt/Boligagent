import json, os, re, sys, requests
from pathlib import Path
from bs4 import BeautifulSoup

NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
MAX_HUSLEJE = 13000
MIN_VAERELSER = 2
SEEN = Path("seen.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (privat boligagent)"}
KEREBY_URL = "https://kereby.dk/bolig/"
EDC_URL = "https://www.edc.dk/lejebolig/lejlighed/koebenhavn/?side={}"
PN = r"\b(\d{4})\s+(?:København|Frederiksberg|Valby)"

def postnr_ok(p):
    return 1000 <= p <= 1999 or p in (2000, 2100, 2200, 2300, 2500, 2150)

def tal(m):
    return int(m.group(1).replace(".", "")) if m else None

# ---------- Kilde 1: Kereby ----------
def kort_for(node):
    el, best = node.parent, None
    for _ in range(10):
        if el is None:
            break
        t = el.get_text(" ", strip=True)
        if len(set(re.findall(r"[\d.]+\s*kr\./md", t))) > 1:
            break
        if re.search(PN, t) and re.search(r"vær", t, re.I):
            best = el
        el = el.parent
    return best

def hent_kereby():
    soup = BeautifulSoup(
        requests.get(KEREBY_URL, headers=HEADERS, timeout=20).text, "html.parser")
    opslag, set_ = [], set()
    for node in soup.find_all(string=re.compile(r"kr\./md")):
        kort = kort_for(node)
        if kort is None:
            continue
        t = kort.get_text(" ", strip=True)
        husleje = tal(re.search(r"([\d.]+)\s*kr\./md", t))
        vaerelser = tal(re.search(r"(\d+)\s*værelser", t, re.I)
                        or re.search(r"(\d+)-?\s*vær", t, re.I))
        m2 = tal(re.search(r"(\d+)\s*m2", t))
        pn = re.search(PN, t)
        postnr = int(pn.group(1)) if pn else None
        noegle = f"{postnr}-{husleje}-{vaerelser}-{m2}"
        if noegle in set_:
            continue
        set_.add(noegle)
        a = kort.select_one('a[href*="/bolig/"]') or kort.find_parent("a", href=True)
        link = requests.compat.urljoin(KEREBY_URL, a["href"]) if a else KEREBY_URL
        opslag.append({
            "id": noegle, "url": link, "tekst": t,
            "husleje": husleje, "vaerelser": vaerelser, "postnr": postnr,
            "ledig": not ("Reserveret" in t or "Udlejet" in t),
        })
    return opslag

# ---------- Kilde 2: EDC ----------
def hent_edc():
    opslag, set_ = [], set()
    for side in (1, 2, 3):
        html = requests.get(EDC_URL.format(side), headers=HEADERS, timeout=20).text
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select('a[href*="/leje/lejlighed/"]'):
            href = a["href"]
            t = a.get_text(" ", strip=True)
            m = re.search(r"(\d+)\s*rum,\s*\d+\s*m[²2]?\s*([\d.]+)\s*kr\./md", t)
            pn = re.search(r"/leje/lejlighed/(\d{4})-", href)
            nr = re.search(r"/(\d+)/?$", href)
            if not (m and pn and nr) or nr.group(1) in set_:
                continue
            set_.add(nr.group(1))
            opslag.append({
                "id": "edc-" + nr.group(1),
                "url": requests.compat.urljoin("https://www.edc.dk", href),
                "tekst": t,
                "husleje": int(m.group(2).replace(".", "")),
                "vaerelser": int(m.group(1)),
                "postnr": int(pn.group(1)),
                "ledig": not ("Reserveret" in t or "Udlejet" in t),
            })
    return opslag
# ---------- Kilde 3: City Apartment ----------
CITY_URL = "https://cityapartment.dk/da/lejeboliger-koebenhavn/"

def hent_city():
    soup = BeautifulSoup(
        requests.get(CITY_URL, headers=HEADERS, timeout=20).text, "html.parser")
    opslag, set_ = [], set()
    for node in soup.find_all(string=re.compile(r"DKK\s*/\s*pr\.\s*måned")):
        el, kort = node.parent, None
        for _ in range(10):
            if el is None:
                break
            t = el.get_text(" ", strip=True)
            if len(re.findall(r"DKK\s*/\s*pr\.", t)) > 1:
                break
            if "Post nr" in t and "værelses" in t:
                kort = el
            el = el.parent
        if kort is None:
            continue
        t = kort.get_text(" ", strip=True)
        a = (kort.select_one('h3 a[href*="/cityapartments/"]')
             or kort.select_one('a[href*="/cityapartments/"]'))
        titel = a.get_text(" ", strip=True) if a else "City Apartment"
        link = requests.compat.urljoin(CITY_URL, a["href"]) if a else CITY_URL
        pn = re.search(r"Post nr\.\s*(\d{4})", t)
        husleje = tal(re.search(r"([\d.]+)\s*DKK", t))
        vaerelser = tal(re.search(r"(\d+)-værelses", t))
        m2 = tal(re.search(r"(\d+)\s*m²", t))
        postnr = int(pn.group(1)) if pn else None
        noegle = "city-" + link.rstrip("/").split("/")[-1]
        if noegle in set_:
            continue
        set_.add(noegle)
        opslag.append({
            "id": noegle, "url": link,
            "tekst": f"{titel}, {postnr}: {vaerelser} vær., {m2} m², {husleje} kr./md.",
            "husleje": husleje, "vaerelser": vaerelser, "postnr": postnr,
            "ledig": not ("Reserveret" in t or "Udlejet" in t),
        })
    return opslag
    
KILDER = [("Kereby", hent_kereby), ("EDC", hent_edc), ("City Apartment", hent_city)]

def matcher(o):
    return bool(o["ledig"]
                and o["husleje"] and o["husleje"] <= MAX_HUSLEJE
                and o["vaerelser"] and o["vaerelser"] >= MIN_VAERELSER
                and o["postnr"] and postnr_ok(o["postnr"]))

def main():
    test = "--test" in sys.argv
    if not test and not NTFY_TOPIC:
        print("NTFY_TOPIC mangler")
        sys.exit(1)
    seen = set(json.loads(SEEN.read_text())) if SEEN.exists() else set()
    fejl = 0
    for navn, kilde in KILDER:
        try:
            fundet = kilde()
        except Exception as e:
            print(f"{navn}: FEJL {e}")
            fejl += 1
            continue
        for o in fundet:
            ok = matcher(o)
            if test:
                print(navn, "MATCH" if ok else "-----",
                      o["husleje"], o["vaerelser"], o["postnr"], o["tekst"][:50])
            elif ok and o["id"] not in seen:
                requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                    data=o["tekst"][:200].encode("utf-8"),
                    headers={"Title": f"Ny bolig ({navn}): {o['husleje']} kr",
                             "Click": o["url"]})
                seen.add(o["id"])
        antal = sum(matcher(o) for o in fundet)
        eks = fundet[0]["tekst"][:70] if fundet else "-"
        print(f"{navn}: {len(fundet)} opslag, {antal} match. Eksempel: {eks}")
    if not test:
        SEEN.write_text(json.dumps(list(seen)))
    if fejl == len(KILDER):
        sys.exit(1)

main()
