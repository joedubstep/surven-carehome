"""Render a free 1080x1080 Facebook still-image ad for one care home (or a CSV of homes).

The layout is template.html (modeled on Wisdom First's US client ads, pulled 2026-10-23).
Words default to Codex "Ad 1":
    headline  You can start with a visit
    line      [Care] for your parent near [City].
    button    Book a tour

Brand color, in order: --color, else the main color of --logo, else of the Facebook Page's public
profile picture (--fb-page-id, usually the logo), else of the logo / theme-color on --site, else a
calm navy. Light colors are darkened until white text on the band passes WCAG AA (4.5:1).

Usage:
  python tools/free_ad/render.py --name "The Retreat at Conyers" --city Conyers --state GA \
      --care "assisted living" --photo https://example.com/building.jpg --site https://example.com \
      --out retreat.png
  python tools/free_ad/render.py --csv homes.csv --outdir out/
      (columns: name,city,state,care,photo,color,logo,site,fb_page_id,focus,band,headline,line,cta,out;
       only name,city,care,photo needed)
Other flags: --focus "50% 30%" (which part of the photo to keep), --band/--headline/--line/--cta to
change words, --cta-style text (the button becomes a plain line with a short accent underline),
--use-logo (put the logo in a white tag instead of the text wordmark).
"""
from __future__ import annotations

import argparse
import base64
import colorsys
import csv
import json
import mimetypes
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template.html"
DEFAULT_COLOR = "#24435E"  # calm navy
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

# Dominant "brand" color of an image: skip near-white, near-black and grey pixels, bucket the rest.
DOMINANT_JS = r"""
async (src) => {
  const img = new Image();
  await new Promise((ok, bad) => { img.onload = ok; img.onerror = () => bad(new Error('img')); img.src = src; });
  const c = document.createElement('canvas'); c.width = 96; c.height = 96;
  const x = c.getContext('2d'); x.drawImage(img, 0, 0, 96, 96);
  const d = x.getImageData(0, 0, 96, 96).data;
  const buckets = {};
  for (let i = 0; i < d.length; i += 4) {
    const [r, g, b, a] = [d[i], d[i+1], d[i+2], d[i+3]];
    if (a < 200) continue;
    const mx = Math.max(r, g, b) / 255, mn = Math.min(r, g, b) / 255, l = (mx + mn) / 2;
    const s = mx === mn ? 0 : (l > .5 ? (mx - mn) / (2 - mx - mn) : (mx - mn) / (mx + mn));
    if (l > .9 || l < .08 || s < .22) continue;
    const k = (r >> 5) + ',' + (g >> 5) + ',' + (b >> 5);
    const e = buckets[k] || (buckets[k] = {n: 0, r: 0, g: 0, b: 0, w: 0});
    e.n++; e.r += r; e.g += g; e.b += b; e.w += s;
  }
  let best = null;
  for (const e of Object.values(buckets)) if (!best || e.w > best.w) best = e;
  if (!best || best.n < 20) return null;
  const h = v => Math.round(v / best.n).toString(16).padStart(2, '0');
  return '#' + h(best.r) + h(best.g) + h(best.b);
}
"""

LOGO_JS = r"""
() => {
  const meta = n => (document.querySelector(`meta[name="${n}"],meta[property="${n}"]`) || {}).content || '';
  const cands = [...document.querySelectorAll('img')].filter(i =>
      /logo/i.test((i.currentSrc || i.src) + ' ' + i.alt + ' ' + i.className + ' ' + i.id + ' ' +
                   ((i.parentElement || {}).className || '')))
    .map(i => i.currentSrc || i.src);
  const og = meta('og:image');
  if (/logo/i.test(og)) cands.push(og);
  const icon = document.querySelector('link[rel="apple-touch-icon"],link[rel~="icon"]');
  if (icon) cands.push(icon.href);
  return {theme: meta('theme-color'), logos: cands.slice(0, 4)};
}
"""


# ---------- color helpers ----------
def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_: str) -> float:
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def contrast_with_white(hex_: str) -> float:
    return 1.05 / (luminance(hex_) + 0.05)


def norm_hex(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", s) or re.fullmatch(r"#?([0-9a-fA-F]{3})", s)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return "#" + h.upper()


def accent_of(hex_: str) -> str:
    """Light tint of the brand color (for the text-style button underline on the dark fade)."""
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    rr, gg, bb = colorsys.hls_to_rgb(h, max(l, 0.72), min(max(s, 0.45), 0.8))
    return "#%02X%02X%02X" % (round(rr * 255), round(gg * 255), round(bb * 255))


def band_safe(hex_: str) -> str:
    """Darken (keep hue) until white text passes 4.5:1; also tame neon saturation."""
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    s = min(s, 0.75)
    out = hex_
    for _ in range(60):
        rr, gg, bb = colorsys.hls_to_rgb(h, l, s)
        out = "#%02X%02X%02X" % (round(rr * 255), round(gg * 255), round(bb * 255))
        if contrast_with_white(out) >= 4.5:
            break
        l -= 0.02
    return out


# ---------- fetching ----------
def to_data_uri(req, src: str) -> tuple[str, bytes]:
    """Local path or URL -> (data URI, raw bytes)."""
    if re.match(r"https?://", src, re.I):
        r = req.get(src, headers={"User-Agent": UA}, timeout=45000)
        if not r.ok:
            raise RuntimeError(f"download failed ({r.status}): {src}")
        body = r.body()
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
        if not ctype.startswith("image/"):
            ctype = mimetypes.guess_type(src.split("?")[0])[0] or "image/jpeg"
    else:
        p = Path(src)
        body = p.read_bytes()
        ctype = mimetypes.guess_type(p.name)[0] or "image/jpeg"
    return f"data:{ctype};base64,{base64.b64encode(body).decode()}", body


def dominant(page, data_uri: str) -> str | None:
    try:
        return page.evaluate(DOMINANT_JS, data_uri)
    except Exception:
        return None


def site_logo_and_theme(ctx, site: str) -> tuple[str | None, list[str]]:
    p = ctx.new_page()
    try:
        p.goto(site, wait_until="domcontentloaded", timeout=45000)
        p.wait_for_timeout(2500)
        info = p.evaluate(LOGO_JS)
        logos = [urljoin(p.url, u) for u in info.get("logos") or [] if u]
        return norm_hex(info.get("theme")), logos
    except Exception:
        return None, []
    finally:
        p.close()


def pick_color(ctx, page, req, args) -> tuple[str, str, str | None]:
    """-> (band color, where it came from, logo data URI if we have one)."""
    if norm_hex(args.get("color")):
        return band_safe(norm_hex(args["color"])), "given", None
    tries: list[tuple[str, str]] = []
    if args.get("logo"):
        tries.append((args["logo"], "logo file"))
    if args.get("fb_page_id"):
        tries.append((f"https://graph.facebook.com/{args['fb_page_id']}/picture?width=500&height=500",
                      "Facebook profile picture"))
    theme = None
    if args.get("site"):
        theme, logos = site_logo_and_theme(ctx, args["site"])
        tries += [(u, "website logo") for u in logos]
    for src, label in tries:
        try:
            uri, _ = to_data_uri(req, src)
        except Exception:
            continue
        col = dominant(page, uri)
        if col:
            return band_safe(norm_hex(col)), f"{label} ({col})", uri if label != "Facebook profile picture" else None
    if theme and contrast_with_white(theme) < 21:
        return band_safe(theme), f"website theme-color ({theme})", None
    return DEFAULT_COLOR, "default", None


# ---------- words ----------
def cap_first(s: str) -> str:
    s = s.strip()
    return s[:1].upper() + s[1:]


def build_words(a: dict) -> dict:
    care = (a.get("care") or "assisted living").strip()
    city = (a.get("city") or "").strip()
    state = (a.get("state") or "").strip()
    line = a.get("line") or (f"{cap_first(care)} for your parent near {city}." if city
                             else f"{cap_first(care)} for your parent.")
    place = f"{city}, {state}" if city and state else city
    band = a.get("band") or (f"{care} · {place}" if place else care)
    return {"band": band.upper(), "headline": a.get("headline") or "You can start with a visit",
            "line": line, "cta": a.get("cta") or "Book a tour", "name": a["name"].strip()}


# ---------- render ----------
def render_one(ctx, req, a: dict, out: Path) -> dict:
    page = ctx.new_page()
    try:
        page.add_init_script("window.__RENDER__ = true;")
        page.goto(TEMPLATE.as_uri(), wait_until="load")
        brand, source, logo_uri = pick_color(ctx, page, req, a)
        photo_uri, _ = to_data_uri(req, a["photo"])
        data = {**build_words(a), "brand": brand, "accent": accent_of(brand),
                "focus": a.get("focus") or "50% 50%",
                "ctaStyle": a.get("cta_style") or "button", "shade": a.get("shade") or "dark",
                "photo": photo_uri, "logo": logo_uri if a.get("use_logo") else None}
        info = page.evaluate("d => window.fill(d)", data)
        out.parent.mkdir(parents=True, exist_ok=True)
        page.locator("#ad").screenshot(path=str(out))
        res = {"home": a["name"], "out": str(out), "color": brand, "color_from": source, **info}
        if info["upscale"] > 1.6:
            res["warning"] = f"photo is small ({info['photoW']}x{info['photoH']}); it is stretched {info['upscale']}x and may look soft"
        return res
    finally:
        page.close()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "ad"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name"); ap.add_argument("--city"); ap.add_argument("--state", default="")
    ap.add_argument("--care", default="assisted living", help='e.g. "assisted living", "memory care"')
    ap.add_argument("--photo", help="photo path or URL (building in good light, or a smiling resident)")
    ap.add_argument("--color", help="brand color hex, e.g. #1F6F8B")
    ap.add_argument("--logo", help="logo path or URL (used for the color, and with --use-logo)")
    ap.add_argument("--site", help="home's website (logo / theme-color for the color)")
    ap.add_argument("--fb-page-id", dest="fb_page_id", help="Facebook Page id (public profile picture for the color)")
    ap.add_argument("--focus", help='photo crop point, CSS object-position, default "50%% 50%%"')
    ap.add_argument("--band", help='top band text (default: "CARE TYPE · CITY, ST")')
    ap.add_argument("--headline"); ap.add_argument("--line"); ap.add_argument("--cta")
    ap.add_argument("--cta-style", dest="cta_style", choices=["button", "text"], default="button")
    ap.add_argument("--use-logo", dest="use_logo", action="store_true")
    ap.add_argument("--shade", choices=["dark", "corner"], default="dark", help="corner = brand-color fade from the bottom-left at 45 degrees")
    ap.add_argument("--out", help="output PNG (single home)")
    ap.add_argument("--csv", help="batch: CSV of homes"); ap.add_argument("--outdir", default="free_ads")
    a = vars(ap.parse_args())

    jobs: list[tuple[dict, Path]] = []
    if a["csv"]:
        with open(a["csv"], newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
                job = {**{k: a[k] for k in ("band", "headline", "line", "cta", "cta_style", "use_logo", "shade")},
                       **{k: v for k, v in row.items() if v}}
                out = Path(row.get("out") or Path(a["outdir"]) / f"{slug(row['name'])}.png")
                jobs.append((job, out))
    else:
        if not (a["name"] and a["photo"]):
            ap.error("give --name and --photo (and --city), or --csv")
        jobs.append((a, Path(a["out"] or f"{slug(a['name'])}.png")))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1080, "height": 1080}, device_scale_factor=1,
                                  user_agent=UA)
        req = pw.request.new_context(user_agent=UA, ignore_https_errors=True)
        try:
            for job, out in jobs:
                try:
                    print(json.dumps(render_one(ctx, req, job, out)), flush=True)
                except Exception as e:
                    print(json.dumps({"home": job.get("name"), "error": f"{type(e).__name__}: {e}"}), flush=True)
        finally:
            req.dispose()
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
