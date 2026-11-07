import json, os, re, sys, requests
from pathlib import Path
from bs4 import BeautifulSoup

NTFY_TOPIC = os.environ.get("NTFY_TOPIC")
MAX_HUSLEJE = 13000
MIN_VAERELSER = 2
SEEN = Path("seen.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (privat boligagent)"}
URL = "https://kereby.dk/bolig/"
PN = r"\b(\d{4})\s+(?:København|Frederiksberg|Valby)"

def postnr_ok(p):
    return 1000 <= p <= 1999 or p in (2000, 2100, 2200, 2300, 2500)

def tal(m):
    return int(m.group(1).replace(".", "")) if m else None

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
        requests.get(URL, headers=HEADERS, timeout=20).text, "html.parser")
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
        link = requests.compat.urljoin(URL, a["href"]) if a else URL
        opslag.append({
            "id": noegle, "url": link, "tekst": t,
            "husleje": husleje, "vaerelser": vaerelser, "postnr": postnr,
            "ledig": not ("Reserveret" in t or "Udlejet" in t),
        })
    return opslag

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
    try:
        fundet = hent_kereby()
    except Exception as e:
        print("Fejl:", e)
        sys.exit(1)
    for o in fundet:
        ok = matcher(o)
        if test:
            print("MATCH" if ok else "-----",
                  "LEDIG" if o["ledig"] else "optaget",
                  o["husleje"], o["vaerelser"], o["postnr"], o["tekst"][:50])
        elif ok and o["id"] not in seen:
            requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                data=o["tekst"][:200].encode("utf-8"),
                headers={"Title": f"Ny bolig: {o['husleje']} kr",
                         "Click": o["url"]})
            seen.add(o["id"])
    antal = sum(matcher(o) for o in fundet)
    print(f"{len(fundet)} opslag fundet, {antal} match")
    if not test:
        SEEN.write_text(json.dumps(list(seen)))

main()
