"""Real photos of the home itself from its Google Maps listing (Joey 10/24: "no AI images actually please... match
the location", and under 1% of ads on the stock photo). Free: the public Maps page in the run's browser, no key.

  find(page, name, address)  opens the listing, checks it is the same address (street number + ZIP or city), then
                             opens its photo gallery and returns every photo's address, best size later.
Owners and visitors upload these, so they show the real building, rooms and residents; the photo pick (photo.py)
then prefers an outside shot."""
from __future__ import annotations

import re
from urllib.parse import quote

# Every Maps photo on screen: <img> tags and the gallery tiles, which are CSS backgrounds.
_PICS = r"""() => {
  const out = [];
  for (const i of document.images) out.push(i.src);
  for (const e of document.querySelectorAll('div, button, a')) {
    const b = getComputedStyle(e).backgroundImage;
    const m = b && b.match(/url\("?(.*?)"?\)/);
    if (m) out.push(m[1]);
  }
  return out.filter(u => /googleusercontent\.com\/(grass-cs|gps-cs|p\/)/.test(u));
}"""


_SV = r"""() => [...document.images].map(i => i.src).filter(u => /streetviewpixels-pa\.googleapis\.com\/v1\/thumbnail.*panoid=/.test(u))"""


def street_view(u: str, w: int = 1600, h: int = 1000) -> str:
    """The listing's Street View picture (it faces the building) at full size."""
    u = re.sub(r"([?&])w=\d+", rf"\g<1>w={w}", u)
    return re.sub(r"([?&])h=\d+", rf"\g<1>h={h}", u)


def _num_zip(addr: str) -> tuple[str, str]:
    a = (addr or "").split(";")[0]
    n = re.match(r"\s*(\d+)", re.sub(r"^\W+", "", a))
    z = re.findall(r"\b(\d{5})(?:-\d{4})?\b", a)
    return (n.group(1) if n else ""), (z[-1] if z else "")


def _street(addr: str) -> str:
    """First word of the street name: "3160 Gracefield Road" -> "gracefield", "865 E Iron Ave" -> "iron"."""
    words = re.findall(r"[a-z]+", re.sub(r"^\W*\d+\S*", "", (addr or "").split(",")[0].lower()))
    words = [w for w in words if w not in {"n", "s", "e", "w", "north", "south", "east", "west", "ne", "nw", "se", "sw"}]
    return words[0] if words else ""


def _name_words(s: str) -> set[str]:
    stop = {"the", "of", "at", "and", "in", "on", "senior", "living", "assisted", "care", "home", "llc", "inc", "alf",
            "community", "memory", "residence", "residences", "center", "retirement", "facility"}
    return {w for w in re.findall(r"[a-z]+", (s or "").lower()) if w not in stop and len(w) > 2}


def _parts(addr: str) -> list[tuple[str, str]]:
    """(street number, street word) for every numbered part: "off of Hwy 129 N, 201 Ranchera St" has two."""
    out = []
    for part in (addr or "").split(";")[0].split(","):
        m = re.match(r"\s*(?:\W*)(\d+)\b", part)
        if m:
            out.append((m.group(1), _street(part)))
    return out


def same_place(ours: str, theirs: str, city: str = "", name: str = "", title: str = "") -> bool:
    """Same street number, and the same ZIP, city or street; or, for a big campus listed at a neighbor number
    (Riderwood 3160 vs 3140 Gracefield Rd, Asbury 333 vs 201 Russell Ave), the same street and the same name.
    Our address comes from the state license list."""
    n1, z1 = _num_zip(ours)
    _, z2 = _num_zip(theirs)
    s1 = _street(ours)
    if not n1:
        return False
    same_area = bool((z1 and z1 == z2) or (city and city.lower() in theirs.lower()))
    w1, w2 = _name_words(name), _name_words(title)
    named = bool(w1 & w2)
    all_named = bool(w1) and w1 <= w2 and len(w1) >= 2
    for n2, s2 in _parts(theirs):
        same_street = bool(s1 and s1 == s2)
        if n2 == n1 and (same_area or same_street):
            return True
        gap = abs(int(n1) - int(n2))
        if same_street and (same_area or not z1) and ((named and gap <= 100) or (all_named and gap <= 400)):
            return True
    return False


def base(u: str) -> str:
    """A Maps photo address without its size, so one photo found twice counts once."""
    return re.sub(r"=[swh]\d[^/]*$", "", u)


def sized(u: str, w: int = 1600, h: int = 1200) -> str:
    return f"{base(u)}=w{w}-h{h}-k-no"


def find(page, name: str, address: str, city: str = "") -> dict:
    """-> {"match": bool, "title", "address", "photos": [photo addresses], "url", "website", "street_view"}
    Searches the name with the address, then the name with the city, and checks each listing it lands on or that
    the results list shows (a sponsored listing comes first sometimes)."""
    last = {"match": False, "title": "", "address": "", "photos": [], "url": "", "website": "", "street_view": ""}
    for q in (f"{name} {address.split(';')[0]}", f"{name} {city}" if city else ""):
        if not q.strip():
            continue
        got = _search(page, q, name, address, city)
        if got["match"]:
            return _details(page, got)
        last = got
    return last


def _search(page, q: str, name: str, address: str, city: str) -> dict:
    page.goto("https://www.google.com/maps/search/" + quote(q.strip()) + "?hl=en", wait_until="domcontentloaded",
              timeout=45000)
    try:
        page.wait_for_selector('h1, a[href*="/maps/place/"]', timeout=15000)
    except Exception:
        return {"match": False, "title": "", "address": "", "photos": [], "url": page.url}
    page.wait_for_timeout(1500)
    res = {"match": False, "title": _title(page), "address": _addr(page), "photos": [], "url": page.url}
    if res["address"] and same_place(address, res["address"], city, name, res["title"]):
        return {**res, "match": True}
    hrefs = page.evaluate("""() => [...new Set([...document.querySelectorAll('a[href*="/maps/place/"]')]
                                     .map(a => a.href))].slice(0, 6)""")
    for h in hrefs:
        try:
            page.goto(h + ("&" if "?" in h else "?") + "hl=en", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector('button[data-item-id="address"]', timeout=10000)
            page.wait_for_timeout(1200)
        except Exception:
            continue
        got = {"match": False, "title": _title(page), "address": _addr(page), "photos": [], "url": page.url}
        if same_place(address, got["address"], city, name, got["title"]):
            return {**got, "match": True}
    return res


def _details(page, res: dict) -> dict:
    res = {**res, "website": "", "street_view": ""}
    page.wait_for_timeout(1000)
    site = page.locator('a[data-item-id="authority"]')
    res["website"] = (site.first.get_attribute("href") or "") if site.count() else ""
    sv = page.evaluate(_SV)
    res["street_view"] = street_view(sv[0]) if sv else ""
    photos = list(dict.fromkeys(base(u) for u in page.evaluate(_PICS)))
    btn = page.locator('button[aria-label^="Photo of"]')
    if btn.count():
        try:
            btn.first.click()
            page.wait_for_timeout(2500)
            for _ in range(4):  # the gallery loads more tiles as it scrolls
                page.mouse.move(220, 500)
                page.mouse.wheel(0, 1400)
                page.wait_for_timeout(700)
            photos += [base(u) for u in page.evaluate(_PICS)]
        except Exception:
            pass
    res["photos"] = list(dict.fromkeys(photos))
    return res


def _title(page) -> str:
    return page.locator("h1").first.inner_text().strip() if page.locator("h1").count() else ""


def _addr(page) -> str:
    loc = page.locator('button[data-item-id="address"]')
    if not loc.count():
        return ""
    return re.sub(r"^[^\w]+", "", loc.first.inner_text()).strip()
