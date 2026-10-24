"""The running-ads email's phone picture, made from Joey's real iPhone screenshot (Joey 10/24: "the text images
look so fake... keep literally everything about the image exactly the same and only change the code and the name").

phone_shot/base.png is that screenshot (the agent's draft to Linda R., YE9). Everything in it stays as it is: the
status bar, the header and avatar, the bubble shapes and tails, the faded earlier message, the + and the input bar.
Only the words are redrawn: the gray bubble's text (it grows or shrinks a line at a time when a home's name runs
long), the green reply code and the "Sent to" line. The font is Inter (free, phone_shot/Inter.ttf), sized and spaced
to match the iPhone's text in the screenshot within a few pixels a line. Pillow only, about 0.1 s a picture."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent / "phone_shot"
BASE = HERE / "base.png"
FONT = HERE / "Inter.ttf"
SIZE, WEIGHT, TRACK = 39, 600, -0.34  # px, Inter weight (stroke as thick as the screenshot's), letter spacing
WRAP = 565                     # widest line in the gray bubble
PITCH, BASELINE0 = 47.3, 388   # line spacing and the first baseline in the screenshot
LINES0 = 25                    # lines in the screenshot's bubble (blank lines between paragraphs count)
TEXT_X = 75                    # left edge of the letters
SPLIT = 1040                   # a blank row inside the gray bubble, where it grows or shrinks
CLEAR = (60, 348, 648)         # x0, first row, x1 of the gray bubble's text area (inside its round corners)
LAST_ROW = 1540                # last text row of the gray bubble (before growing)
GREEN = {"box": (744, 1584, 884, 1692), "baseline": 1645, "color": (0, 108, 36)}
SENT = {"box": (38, 1704, 398, 1812), "baseline": 1765}
CAP = 48                       # width of a bubble's round end, copied as is when a bubble gets wider or narrower
_font: dict = {}


def font() -> ImageFont.FreeTypeFont:
    if "f" not in _font:
        f = ImageFont.truetype(str(FONT), SIZE)
        f.set_variation_by_axes([14, WEIGHT])
        _font["f"] = f
    return _font["f"]


def width(text: str) -> float:
    return font().getlength(text) + TRACK * max(0, len(text) - 1)


def clear(im: Image.Image, x0: int, x1: int, y0: int, y1: int, sample_x: int) -> None:
    """Paint over old words row by row with the bubble's own color on that row (taken from its empty margin), so the
    faint shading at the top of the bubble and its soft edges stay exactly as in the screenshot."""
    d = ImageDraw.Draw(im)
    for y in range(y0, y1 + 1):
        d.line((x0, y, x1, y), fill=im.getpixel((sample_x, y)))


def draw_text(d: ImageDraw.ImageDraw, x: float, baseline: float, text: str, fill=(255, 255, 255)) -> None:
    """Letter by letter so the spacing matches; each letter sits where the whole run would put it (kerning kept)."""
    f = font()
    x -= f.getbbox(text[:1] or "H")[0]  # line up the ink, not the letter box, like the screenshot
    for i, ch in enumerate(text):
        d.text((x + f.getlength(text[:i]) + TRACK * i, baseline), ch, font=f, fill=fill, anchor="ls")


def wrap(text: str) -> list[str]:
    """The bubble's lines: each paragraph wrapped to the bubble, a blank line between paragraphs."""
    out: list[str] = []
    for p, para in enumerate(text.split("\n\n")):
        if p:
            out.append("")
        for raw in para.split("\n"):
            line = ""
            for w in raw.split(" "):
                cand = f"{line} {w}".strip()
                if line and width(cand) > WRAP:
                    out.append(line)
                    line = w
                else:
                    line = cand
            out.append(line)
    return out


def _resize_bubble(im: Image.Image, box: tuple, new_w: int, anchor: str) -> tuple:
    """Make a one-line bubble wider or narrower: the round end with the tail stays put, the other round end moves,
    and the middle is filled with copies of the bubble's own middle column (so its edges look exactly the same)."""
    x0, y0, x1, y1 = box
    r0, r1 = y0 - 4, y1 + 16  # the tail hangs a little below the bubble
    if abs(new_w - (x1 - x0)) <= 3:
        return box
    mid = im.crop(((x0 + x1) // 2, r0, (x0 + x1) // 2 + 1, r1))
    d = ImageDraw.Draw(im)
    if anchor == "right":  # the green bubble: its tail is on the right
        cap = im.crop((x0 - 4, r0, x0 + CAP, r1))
        nx0 = x1 - new_w
        d.rectangle((min(x0, nx0) - 4, r0, x0 + CAP, r1), fill=(0, 0, 0))
        for x in range(nx0 + CAP - 2, x0 + CAP + 2):
            im.paste(mid, (x, r0))
        im.paste(cap, (nx0 - 4, r0))
        return (nx0, y0, x1, y1)
    cap = im.crop((x1 - CAP, r0, x1 + 4, r1))  # the "Sent to" bubble: its tail is on the left
    nx1 = x0 + new_w
    d.rectangle((x1 - CAP, r0, max(x1, nx1) + 4, r1), fill=(0, 0, 0))
    for x in range(x1 - CAP - 2, nx1 - CAP + 2):
        im.paste(mid, (x, r0))
    im.paste(cap, (nx1 - CAP, r0))
    return (x0, y0, nx1, y1)


def render(draft: str, code: str, who: str) -> bytes:
    """draft: the agent's message (phone.draft_text); code: "E38"; who: "Kevin T." -> PNG bytes."""
    im = Image.open(BASE).convert("RGB")
    # a text message shows straight quotes, as in the screenshot ("yes", I'll)
    draft = draft.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    lines = wrap(draft)
    grow = round((len(lines) - LINES0) * PITCH)
    if grow:
        w, h = im.size
        top, bottom = im.crop((0, 0, w, SPLIT)), im.crop((0, SPLIT - min(0, grow), w, h))
        out = Image.new("RGB", (w, h + grow))
        out.paste(top, (0, 0))
        if grow > 0:
            row = im.crop((0, SPLIT, w, SPLIT + 1))
            for y in range(grow):
                out.paste(row, (0, SPLIT + y))
        out.paste(bottom, (0, SPLIT + max(0, grow)))
        im = out
    d = ImageDraw.Draw(im)
    clear(im, CLEAR[0], CLEAR[2], CLEAR[1], LAST_ROW + grow, 56)
    for k, line in enumerate(lines):
        if line:
            draw_text(d, TEXT_X, BASELINE0 + PITCH * k, line)

    reply = f"Y{code}"
    gx0, gy0, gx1, gy1 = (v + (grow if i % 2 else 0) for i, v in enumerate(GREEN["box"]))
    pad = (gx1 - gx0 - width("YE9")) / 2
    gx0, gy0, gx1, gy1 = _resize_bubble(im, (gx0, gy0, gx1, gy1), round(width(reply) + 2 * pad), "right")
    clear(im, gx0 + 20, gx1 - 22, GREEN["baseline"] + grow - 34, GREEN["baseline"] + grow + 10, gx0 + 12)
    draw_text(d, gx0 + (gx1 - gx0 - width(reply)) / 2, GREEN["baseline"] + grow, reply)

    sent = f"Sent to {who}."
    sx0, sy0, sx1, sy1 = (v + (grow if i % 2 else 0) for i, v in enumerate(SENT["box"]))
    spad = sx1 - (TEXT_X + width("Sent to Linda R."))
    sx0, sy0, sx1, sy1 = _resize_bubble(im, (sx0, sy0, sx1, sy1), round(TEXT_X - sx0 + width(sent) + spad), "left")
    clear(im, TEXT_X - 4, sx1 - 24, SENT["baseline"] + grow - 36, SENT["baseline"] + grow + 10, sx0 + 14)
    draw_text(d, TEXT_X, SENT["baseline"] + grow, sent)

    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()
