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
SKIP = re.compile(r"logo|icon|favicon|sprite|badge|award|seal|certif|map|placeholder|avatar|headshot|button|arrow|"
                  r"social|facebook|instagram|twitter|linkedin|youtube|google|yelp|bbb|banner-ad|pixel|spacer|loader|"
                  r"fractal|pattern|texture|illustration|graphic|globe|world|\.svg|\.gif|\.png|data:", re.I)


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
        if u.startswith("http") and not SKIP.search(u) and u not in seen:
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
        if w < 900 or h < 500 or not (1.0 <= w / h <= 2.4):
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


def pick(website: str, email: str, max_tries: int = 8) -> dict:
    """-> {"photo": url or "", "site": url or "", "from": "website" | "stock", "w": .., "h": ..}"""
    site = site_for(website, email)
    if site:
        for url in (site, site.replace("https://", "https://www.", 1) if "://www." not in site else ""):
            if not url:
                continue
            try:
                body, final = _get(url, 3_000_000)
            except Exception:
                continue
            for c in candidates(body.decode("utf-8", "replace"), final)[:max_tries]:
                try:
                    data, _ = _get(c, 8_000_000)
                except Exception:
                    continue
                wh = good_photo(data)
                if wh:
                    return {"photo": c, "site": final, "from": "website", "w": wh[0], "h": wh[1]}
            return {"photo": "", "site": final, "from": "stock"}
    return {"photo": "", "site": site, "from": "stock"}


def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()
