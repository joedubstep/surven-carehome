"""Read what a person actually wrote in a reply, all of it.

2026-10-01: Danielle at CiminoCare answered a cold email with "Hello / Luceli is no longer with CiminoCare / Thank you,
Danielle / Cell phone (916)600-0734 is a 24/7 line". The scanner kept only the first line, so Joey got "Luceli at
Grand River Villa said "Hello". Call them now." The real news (Luceli left, Danielle answered, a 24/7 cell) never
reached him, and the alert named the person we emailed, not the person who wrote. So:
  - the whole new text is read: the plain part, or the HTML part turned to text when there is no plain part,
  - the quoted original below it is cut ("-----Original Message-----", "On ... wrote:", an Outlook "From: ... Sent:"
    header, "> " lines), so only what they wrote is kept, signature and phone number included,
  - the sender's own name comes from the From header.
"""
import html
import re
from email.utils import parseaddr

# Where the quoted original starts, in the clients we see: Outlook, Gmail, Apple Mail, Yahoo, phones.
_CUT = [
    re.compile(r"^\s*-{2,}\s*original message\s*-{2,}\s*$", re.I),
    re.compile(r"^\s*-{2,}\s*forwarded message\s*-{2,}\s*$", re.I),
    re.compile(r"^\s*_{8,}\s*$"),
    re.compile(r"^\s*from:\s.+", re.I),          # Outlook puts the old header right under the reply
    re.compile(r"^\s*on\s.+\bwrote:\s*$", re.I),  # Gmail / Apple Mail, on one line
    re.compile(r"^\s*>"),
]
_ON_START = re.compile(r"^\s*on\s.+", re.I)


def _html_to_text(s: str) -> str:
    s = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", "", s)
    s = re.sub(r"(?is)<blockquote\b.*", "", s)  # Gmail's quoted original sits in a blockquote
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|tr|li|h\d)>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", "", s)
    return html.unescape(s).replace("\xa0", " ")


def body_text(m) -> str:
    """The plain-text part, or the HTML part as text when there is no plain part."""
    plain, htm = None, None
    for part in (m.walk() if m.is_multipart() else [m]):
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        try:
            raw = part.get_payload(decode=True)
            text = raw.decode(part.get_content_charset() or "utf-8", errors="ignore") if raw else ""
        except Exception:
            continue
        if part.get_content_type() == "text/plain" and plain is None:
            plain = text
        elif part.get_content_type() == "text/html" and htm is None:
            htm = text
    return plain if plain and plain.strip() else _html_to_text(htm or "")


def new_text(m_or_text) -> str:
    """Only what the sender wrote this time: everything above the quoted original, blank runs squeezed."""
    text = m_or_text if isinstance(m_or_text, str) else body_text(m_or_text)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    for i, line in enumerate(lines):
        if any(p.match(line) for p in _CUT):
            break
        # "On Wed, Sep 30, 2026 at 9:00 AM Surven <joey@" / "surven.ai> wrote:" wraps over two or three lines
        if _ON_START.match(line) and any(re.search(r"\bwrote:\s*$", l, re.I) for l in lines[i:i + 3]):
            break
        out.append(line.rstrip())
    joined = "\n".join(out).strip()
    return re.sub(r"\n\s*\n+", "\n", joined)


def sender_name(m) -> str:
    """The From display name, or the address when there is none."""
    name, addr = parseaddr(m.get("From", ""))
    name = (name or "").strip().strip('"')
    return name or addr


# An opt-out is a first word of no / stop / unsubscribe / remove (not "no longer", "no problem"), or one of the
# plain opt-out phrases anywhere in what they wrote.
_OPT_PHRASES = re.compile(r"\b(unsubscribe|not interested|remove me|take me off|stop emailing|stop contacting)\b", re.I)
_OPT_FIRST = re.compile(r"^(no|stop|unsubscribe|remove)\b(?!\s+(longer|problem|worries)\b)", re.I)


def is_opt_out(text: str) -> bool:
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    return bool(_OPT_FIRST.match(first) or _OPT_PHRASES.search(text))
