"""Pick one real photo of a care home for its free ad (Joey 10/24: "no AI images actually please", "match the
location", under 1% on the stock photo).

Where it looks, in order, keeping the first outside shot of the building it finds:
  1. the home's Google Maps listing, checked to be the same address (maps.py): owners' and visitors' photos
  2. its Facebook Page cover (the Page the Ad Library matched, or the one its website links to)
  3. its own website (biggest pictures, house and residents first, stock and aerials last)
With no outside shot anywhere, a real inside photo from Maps or Facebook comes next, then the website's best.
Every picture must be big, a photo (not a logo, flyer or graphic) and carry no AI tag (C2PA / IPTC "made by AI",
ChatGPT, Midjourney, Firefly...). Nothing at all -> the stock smiling-resident photo shipped next to this file."""
from __future__ import annotations

import io
import re
import urllib.request
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

HERE = Path(__file__).resolve().parent
STOCK = HERE / "stock_resident.jpg"  # Unsplash License (free), Vitaly Gariev, "Smiling elderly woman in a pink cardigan"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
FREEMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "msn.com", "live.com",
            "comcast.net", "att.net", "sbcglobal.net", "bellsouth.net", "verizon.net", "me.com", "ymail.com", "cox.net",
            "charter.net", "earthlink.net", "mindspring.com", "protonmail.com", "mail.com", "gmx.com", "frontier.com",
            "windstream.net", "rocketmail.com", "juno.com", "netzero.net", "q.com", "centurylink.net", "embarqmail.com"}
# 10/24: whole words where a name could hold them ("Maple Ridge", "Yarrow Gardens", "social hour" photos)
SKIP = re.compile(r"logo|\bicons?\b|favicon|sprite|badge|award|certif|\bmaps?\b|placeholder|avatar|headshot|"
                  r"button|\barrows?\b|social-?(?:icon|media|share)|facebook|instagram|twitter|linkedin|youtube|google|yelp|bbb|banner-ad|pixel|spacer|loader|"
                  r"fractal|pattern|texture|illustration|graphic|globe|world|testimonial|quote|review|flyer|brochure|"
                  r"menu|calendar|newsletter|infographic|\.svg|\.gif|\.png|data:", re.I)
# 10/24: Sunstar Village's own site used a ChatGPT picture; its file said so in a C2PA tag. An "enhanced" (upscaled)
# real photo is fine; one made or filled in by AI is not.
AI_TAG = re.compile(rb"trainedAlgorithmicMedia|compositeSynthetic|Midjourney|DALL.?E|Stable Diffusion|ChatGPT|OpenAI|"
                    rb"Google Imagen|Adobe Firefly(?! Upscal)|Generative Fill|ai.generated", re.I)
AI_NAME = re.compile(r"midjourney|dall-?e|chatgpt|openai|stable-?diffusion|ai[-_ ]?generated|(?<!up)generated|"
                     r"firefly(?!.{0,12}upscal)", re.I)
OUTSIDE = 0.12  # sky above, grass or trees below: an outside shot of the building


def site_for(website: str, email: str) -> str:
    """The home's own site: the one we have, else the domain of a business email address."""
    if website:
        return website if website.startswith("http") else "https://" + website
    dom = (email or "").split("@")[-1].lower().strip()
    return "" if not dom or dom in FREEMAIL else f"https://{dom}"


def _get(url: str, limit: int, timeout: int = 15) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(limit), r.geturl()


def _file(u: str) -> str:
    return unquote(urlparse(u).path.rsplit("/", 1)[-1])


def candidates(html: str, base: str) -> list[str]:
    """Picture URLs in page order, og:image first, biggest srcset entry for each img."""
    out: list[str] = []
    for m in re.finditer(r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*>', html, re.I):
        c = re.search(r'content=["\']([^"\']+)', m.group(0))
        if c:
            out.append(c.group(1))
    for tag in re.findall(r"<img\b[^>]*>", html, re.I):
        best = None
        ss = re.search(r'(?:data-)?srcset=["\']([^"\']+)', tag, re.I)
        if ss:
            parts = [p.strip().split() for p in ss.group(1).split(",") if p.strip()]
            parts = [(p[0], int(re.sub(r"\D", "", p[1]) or 0) if len(p) > 1 else 0) for p in parts if p]
            if parts:
                best = max(parts, key=lambda x: x[1])[0]
        src = re.search(r'(?:data-lazy-src|data-src|src)=["\']([^"\']+)', tag, re.I)
        alt = re.search(r'alt=["\']([^"\']*)', tag, re.I)
        if alt and SKIP.search(alt.group(1)):
            continue
        if best or src:
            out.append(best or src.group(1))
    out += re.findall(r"background(?:-image)?\s*:\s*url\(['\"]?([^'\")]+)", html, re.I)
    seen, clean = set(), []
    for u in out:
        u = urljoin(base, unescape(u.strip()))
        if u.startswith("http") and not SKIP.search(_file(u)) and not AI_NAME.search(_file(u)) and u not in seen:
            seen.add(u)
            clean.append(u)
    return clean


def ai_made(data: bytes) -> bool:
    return bool(AI_TAG.search(data))


def _pixels(im):
    """Pillow 13 renamed getdata (removed in Pillow 14)."""
    f = getattr(im, "get_flattened_data", None)
    return f() if f else im.getdata()


def good_photo(data: bytes, small: bool = False) -> tuple[int, int] | None:
    """(w, h) if this is a real, big photo; None for logos, flat graphics, small images or AI pictures.
    small: a Maps, Facebook or Street View photo of the place itself may be smaller (754x738 still looks fine)."""
    from PIL import Image, ImageStat
    try:
        if ai_made(data):
            return None
        im = Image.open(io.BytesIO(data))
        # 10/24: on a website a PNG is almost always a drawn graphic (Morningstar's world map). Google Maps serves
        # some owners' building photos as PNG, so there the color test below decides.
        if im.format == "PNG" and not small:
            return None
        w, h = im.size
        # 10/24 volume test: the ad crops to a near-square, so a tall 800x1000 resident photo works too
        big, side = (700, 450) if small else (800, 600)
        if max(w, h) < big or min(w, h) < side or not (0.75 <= w / h <= 2.4):
            return None
        if small and 1.95 <= w / h <= 2.05:  # a 360 degree Maps panorama: bent walls once cropped
            return None
        g = im.convert("L").resize((64, 64))
        st = ImageStat.Stat(g)
        if st.stddev[0] < 35:  # flat graphic or mostly white
            return None
        if sum(1 for v in _pixels(g) if v > 235) / 4096 > 0.25:  # a big white area: a cut-out graphic, not a photo
            return None
        colors = len(set(_pixels(im.convert("RGB").resize((48, 48)))))
        return (w, h) if colors > 600 else None  # a photo has many distinct colors; a drawn graphic has few
    except Exception:
        return None


def outside(data: bytes) -> float:
    """How much this looks like an outside shot: blue sky in the top third plus green (lawn, trees) in the bottom
    half. An inside room scores near 0."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB").resize((96, 96))
    except Exception:
        return 0.0
    px = list(_pixels(im))
    top, bottom = px[: 96 * 32], px[96 * 48:]
    sky = sum(1 for r, g, b in top if b > 110 and b > r + 25 and b > g + 5) / len(top)
    green = sum(1 for r, g, b in bottom if g > r + 10 and g > b and g > 60) / len(bottom)
    return round(sky + 0.6 * green, 3)


def _try(req, url: str, src: str, final_url: str = "", small: bool = False) -> dict | None:
    try:
        r = req.get(url, timeout=15000)
        data = r.body() if r.ok else b""
    except Exception:
        return None
    wh = good_photo(data, small)
    if not wh:
        return None
    return {"photo": final_url or url, "from": src, "w": wh[0], "h": wh[1], "outside": outside(data)}


def best_of(req, urls: list[str], src: str, n: int = 14, size=None, small: bool = False) -> dict | None:
    """The first outside shot among the first n pictures, else the first good photo. size(u) -> (test, final) URLs."""
    first = None
    for u in urls[:n]:
        test, final = size(u) if size else (u, u)
        got = _try(req, test, src, final, small)
        if not got:
            continue
        if got["outside"] >= OUTSIDE:
            return got
        first = first or got
    return first


def pick_maps(ctx, req, place: tuple) -> tuple[dict | None, dict]:
    """-> (best Maps photo or None, the listing: its website and Street View picture too)."""
    from . import maps
    page = ctx.new_page()
    try:
        m = maps.find(page, *place)
    except Exception:
        return None, {}
    finally:
        page.close()
    if not m["match"]:
        return None, {}
    got = best_of(req, m["photos"], "google maps", size=lambda u: (maps.sized(u), maps.sized(u)), small=True)
    if got:
        got["listing"] = m["title"]
    return got, m


_FB_PICS = r"""() => ({
  title: (document.querySelector('meta[property="og:title"]') || {}).content || document.title,
  pics: [...document.images].filter(i => /fbcdn/.test(i.src) && i.naturalWidth >= 500)
          .sort((a, b) => (b.getAttribute('data-imgperflogname') === 'profileCoverPhoto') -
                          (a.getAttribute('data-imgperflogname') === 'profileCoverPhoto'))
          .map(i => i.src)
})"""


def _words(s: str) -> set[str]:
    stop = {"the", "of", "at", "and", "in", "on", "senior", "living", "assisted", "care", "home", "llc", "inc",
            "community", "memory", "residence", "residences", "center", "retirement", "facebook"}
    return {w for w in re.findall(r"[a-z]+", (s or "").lower()) if w not in stop and len(w) > 2}


def pick_facebook(ctx, req, fb: str, home: str = "") -> dict | None:
    """The Page's cover photo, then its recent post photos. Public, no login. A Page whose name shares no word
    with the home's name (a chain's main Page) is skipped."""
    url = fb if fb.startswith("http") else f"https://www.facebook.com/{fb}"
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(4000)
        got = page.evaluate(_FB_PICS)
    except Exception:
        return None
    finally:
        page.close()
    if home and _words(home) and not (_words(home) & _words(got["title"])):
        return None
    return best_of(req, got["pics"], "facebook", n=6, small=True)


# Every picture on the page as the browser drew it: og:image first, then the biggest images and backgrounds.
_PAGE_PICS = r"""() => {
  const out = [];
  for (const m of document.querySelectorAll('meta[property="og:image"],meta[name="twitter:image"]'))
    if (m.content) out.push({u: m.content, a: 1e12, alt: ''});
  for (const i of document.images) {
    const u = i.currentSrc || i.src;
    if (u) out.push({u, a: i.naturalWidth * i.naturalHeight, alt: i.alt || ''});
  }
  for (const e of document.querySelectorAll('body *')) {
    const b = getComputedStyle(e).backgroundImage;
    const m = b && b.match(/url\(["']?(.*?)["']?\)/);
    if (m) { const r = e.getBoundingClientRect(); out.push({u: m[1], a: r.width * r.height, alt: ''}); }
  }
  return out;
}"""
_FB_LINK = r"""() => [...document.querySelectorAll('a[href*="facebook.com/"]')].map(a => a.href)
  .filter(h => !/sharer|share\.php|plugins|dialog|\/groups\/|\/events\//.test(h))"""


# What a picture shows, from its file name and alt text. Joey 10/24: the ad needs the house or smiling residents
# ("there's no house in that ad"); a stock aerial of the town ranks last.
GOOD = re.compile(r"resident|senior|elder|grand|smil|happy|laugh|exterior|building|entrance|front|outside|outdoor|"
                  r"courtyard|garden|porch|patio|house|campus|caregiver|staff|nurse|activit|dining|family|friends|"
                  r"couple|community", re.I)
STOCKY = re.compile(r"adobestock|shutterstock|istock|getty|unsplash|nsplsh|pexels|depositphotos|dreamstime|"
                    r"stock-photo|-utc\.|image by ", re.I)
FAR = re.compile(r"aerial|drone|skyline|sunset|landscape|downtown|cityscape|abstract", re.I)
BUILDING = re.compile(r"exterior|building|entrance|front|outside|outdoor|courtyard|campus|facade|porch|patio|"
                      r"grounds|garden|house|community", re.I)


def _full_size(u: str) -> str:
    """Wix serves each picture at the size it is drawn (460x460); the original sits before "/v1/"."""
    if "wixstatic.com/media/" in u and "/v1/" in u:
        return u.split("/v1/")[0]
    return u


def rank(pics: list[dict], base: str) -> list[str]:
    """Picture URLs, best first: what it shows (house, residents), then how big it is drawn."""
    seen, out = set(), []
    for x in pics:
        u = _full_size(urljoin(base, unescape(x["u"].strip())))
        name, alt = _file(u), x.get("alt") or ""
        if not u.startswith("http") or u in seen or SKIP.search(name) or SKIP.search(alt) or AI_NAME.search(name):
            continue
        seen.add(u)
        words = f"{name} {alt}"
        score = (2 if GOOD.search(words) else 0) - (2 if STOCKY.search(words) else 0) - (2 if FAR.search(words) else 0)
        out.append((score, x["a"] if x["a"] < 1e11 else 5e5, u))  # og:image counts as one mid-size picture
    return [u for _, _, u in sorted(out, key=lambda t: (-t[0], -t[1]))]


def pick_browser(ctx, req, site: str, max_tries: int = 14) -> dict | None:
    """The home's own website in a real browser (gets past JavaScript-drawn pictures, 403s and bad certificates).
    -> {"photo"...} or {"photo": ""}, plus "fb": the Facebook Page the site links to."""
    page = ctx.new_page()
    try:
        page.goto(site, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        for _ in range(3):  # wake lazy-loaded pictures
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(400)
        final = page.url
        pics = page.evaluate(_PAGE_PICS)
        fb = next(iter(page.evaluate(_FB_LINK)), "")
    except Exception:
        return None
    finally:
        page.close()
    got = best_of(req, rank(pics, final), "website", n=max_tries) or {"photo": "", "from": "stock"}
    return {**got, "site": final, "fb": fb}


def pick(website: str, email: str, max_tries: int = 12, browser=None, place: tuple | None = None, fb: str = "",
         home: str = "") -> dict:
    """-> {"photo": url or "", "site": url, "from": "google maps" | "facebook" | "website" | "stock", "w", "h"}
    browser: (playwright context, request context). place: (name, address, city) for Google Maps. fb: Page id or
    link from the Ad Library."""
    site = site_for(website, email)
    if not browser:
        return _pick_quick(site, max_tries)
    ctx, req = browser
    later, listing = [], {}
    if place:
        m, listing = pick_maps(ctx, req, place)
        if m and m["outside"] >= OUTSIDE:
            return {**m, "site": site}
        later.append(m)
    web = pick_browser(ctx, req, site) if site else None
    other = listing.get("website") or ""
    if (not web or not web.get("photo")) and other and domain_of(other) != domain_of(site):
        web = pick_browser(ctx, req, other) or web  # 10/24: the Maps listing often links a newer site than ours
    link = fb or (web or {}).get("fb", "")
    if link:
        f = pick_facebook(ctx, req, link, home)
        if f and f["outside"] >= OUTSIDE:
            return {**f, "site": site}
        later.append(f)
    if listing.get("street_view"):  # the building itself, from the street
        sv = _try(req, listing["street_view"], "street view", small=True)
        if sv:
            return {**sv, "site": site}
    if later and later[0] and later[0].get("photo"):  # a real inside photo of the place, from its Maps listing
        return {**later[0], "site": site}
    # 10/24 volume test: website pictures of people outdoors are often stock (couples, a staff headshot); only a
    # picture named as the building counts as an outside shot of the home.
    if web and web.get("photo") and web["outside"] >= OUTSIDE and BUILDING.search(_file(web["photo"])):
        return {**web, "site": web["site"]}
    for got in later[1:] + [web]:
        if got and got.get("photo"):
            return {**got, "site": (web or {}).get("site") or site}
    return {"photo": "", "site": (web or {}).get("site") or site, "from": "stock"}


def _pick_quick(site: str, max_tries: int) -> dict:
    """No browser: plain downloads of the website only."""
    found = {"photo": "", "site": site, "from": "stock"}
    if not site:
        return found
    for url in (site, site.replace("https://", "https://www.", 1) if "://www." not in site else ""):
        if not url:
            continue
        try:
            body, final = _get(url, 3_000_000)
        except Exception:
            continue
        found["site"] = final
        for c in candidates(body.decode("utf-8", "replace"), final)[:max_tries]:
            try:
                data, _ = _get(c, 8_000_000)
            except Exception:
                continue
            wh = good_photo(data)
            if wh:
                return {"photo": c, "site": final, "from": "website", "w": wh[0], "h": wh[1]}
        break
    return found


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()
