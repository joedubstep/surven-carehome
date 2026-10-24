"""Pick one good photo of a care home from its own website for the free ad (Joey 10/24: "automatic photo pick").

Free and automatic: read the home page, collect the big pictures (og:image, img src/srcset, CSS backgrounds), skip
logos, icons and badges, download the likeliest few and keep the first that is large, landscape-ish and a real
photo (not a flat graphic). No good photo -> the stock smiling-resident photo shipped next to this file."""
from __future__ import annotations

import io
import re
import urllib.request
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

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
        if u.startswith("http") and not SKIP.search(_file(u)) and u not in seen:
            seen.add(u)
            clean.append(u)
    return clean


def good_photo(data: bytes) -> tuple[int, int] | None:
    """(w, h) if this is a real, big, landscape-ish photo; None for logos, flat graphics or small images."""
    from PIL import Image, ImageStat
    try:
        im = Image.open(io.BytesIO(data))
        if im.format == "PNG":  # 10/24: a PNG is almost always a drawn graphic (Morningstar's world map), not a photo
            return None
        w, h = im.size
        # 10/24 volume test: the ad crops to a near-square, so a tall 800x1000 resident photo works too
        if max(w, h) < 800 or min(w, h) < 600 or not (0.75 <= w / h <= 2.4):
            return None
        g = im.convert("L").resize((64, 64))
        st = ImageStat.Stat(g)
        if st.stddev[0] < 35:  # flat graphic or mostly white
            return None
        if sum(1 for v in g.getdata() if v > 235) / 4096 > 0.25:  # a big white area: a cut-out graphic, not a photo
            return None
        colors = len(set(im.convert("RGB").resize((48, 48)).getdata()))
        return (w, h) if colors > 600 else None  # a photo has many distinct colors; a drawn graphic has few
    except Exception:
        return None


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


# What a picture shows, from its file name and alt text. Joey 10/24: the ad needs the house or smiling residents
# ("there's no house in that ad"); a stock aerial of the town ranks last.
GOOD = re.compile(r"resident|senior|elder|grand|smil|happy|laugh|exterior|building|entrance|front|outside|outdoor|"
                  r"courtyard|garden|porch|patio|house|campus|caregiver|staff|nurse|activit|dining|family|friends|"
                  r"couple|community", re.I)
STOCKY = re.compile(r"adobestock|shutterstock|istock|getty|unsplash|nsplsh|pexels|depositphotos|dreamstime|"
                    r"stock-photo|-utc\.|image by ", re.I)
FAR = re.compile(r"aerial|drone|skyline|sunset|landscape|downtown|cityscape|abstract", re.I)


def _file(u: str) -> str:
    from urllib.parse import unquote
    path = urlparse(u).path
    return unquote(path.rsplit("/", 1)[-1])


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
        if not u.startswith("http") or u in seen or SKIP.search(name) or SKIP.search(alt):
            continue
        seen.add(u)
        words = f"{name} {alt}"
        score = (2 if GOOD.search(words) else 0) - (2 if STOCKY.search(words) else 0) - (2 if FAR.search(words) else 0)
        out.append((score, x["a"] if x["a"] < 1e11 else 5e5, u))  # og:image counts as one mid-size picture
    return [u for _, _, u in sorted(out, key=lambda t: (-t[0], -t[1]))]


def pick_browser(ctx, req, site: str, max_tries: int = 14) -> dict | None:
    """10/24 volume test: half the homes fell back to stock because their sites draw pictures with JavaScript, refuse
    plain downloads (403) or have broken certificates. A real browser page gets past all three."""
    page = ctx.new_page()
    try:
        page.goto(site, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        for _ in range(3):  # wake lazy-loaded pictures
            page.mouse.wheel(0, 900)
            page.wait_for_timeout(400)
        final = page.url
        pics = page.evaluate(_PAGE_PICS)
    except Exception:
        return None
    finally:
        page.close()
    for u in rank(pics, final)[:max_tries]:
        try:
            r = req.get(u, timeout=15000)
            data = r.body() if r.ok else b""
        except Exception:
            continue
        wh = good_photo(data)
        if wh:
            return {"photo": u, "site": final, "from": "website", "w": wh[0], "h": wh[1]}
    return {"photo": "", "site": final, "from": "stock"}


def pick(website: str, email: str, max_tries: int = 12, browser=None) -> dict:
    """-> {"photo": url or "", "site": url or "", "from": "website" | "stock", "w": .., "h": ..}
    browser: optional (playwright context, request context), tried when the quick download finds no photo."""
    site = site_for(website, email)
    if not site:
        return {"photo": "", "site": "", "from": "stock"}
    if browser:
        got = pick_browser(browser[0], browser[1], site)
        if got and got["photo"]:
            return got
    found = {"photo": "", "site": site, "from": "stock"}
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
