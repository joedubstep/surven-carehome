# Voice rules for the [PERSONAL] opening line. Locked by Joey 2026-06-01.
# The line must read like a real person who glanced at their site, NOT like AI or a salesperson.
PROMPT = (
    "You write ONE opening line for a cold email to a local business. "
    "Input: business name, category, city, website, scraped 'about' text. "
    "Check the website (and optionally one search) for a real, specific, recent, TRUE thing: "
    "a space or feature, a current event they're running, an expansion, a notable review count, a rebrand, or seasonal timing.\n\n"
    "VOICE RULES (follow exactly, every time):\n"
    "- Make it a PLAIN, genuine observation that shows you actually looked. Do NOT grade it or sell it back to them.\n"
    "- NO verdict / hype / enthusiasm words. Banned: genius, unreal, amazing, incredible, perfect, elite, sick, cool, smart, clean, love, 'nice touch', 'super ___'. A real person notices the thing; they don't review it.\n"
    "- NO em dashes or hyphens used as dashes (that reads AI-written). Use commas or periods only.\n"
    "- all lowercase, no exclamation marks, 12 words or fewer, conversational like a real text.\n"
    "- vary the opener; do not always start with 'noticed' or 'saw' followed by a verdict.\n"
    "- one low-key genuine aside is allowed (e.g. 'first time i've come across that') but never a compliment or sales verdict.\n"
    "- never invent or guess. if there is no genuine, specific, true thing to note, output exactly: SKIP.\n\n"
    "Output ONLY the line, or SKIP."
)

# em dash, en dash, and a spaced hyphen used as a dash all read as AI-written -> reject.
_DASHES = ("—", "–", " - ")


def validate_personal_line(text):
    """Return the cleaned line, or None to drop it (SKIP / fails the guardrails)."""
    if text is None:
        return None
    t = text.strip()
    if not t or t.upper() == "SKIP":
        return None
    if "!" in t:
        return None
    if any(d in t for d in _DASHES):
        return None
    if len(t.split()) > 14:
        return None
    letters = [c for c in t if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        return None
    return t
