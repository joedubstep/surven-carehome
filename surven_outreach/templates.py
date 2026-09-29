import re

SIGNOFF = "thanks,\nJoseph"

BODIES = {
    1: """hey [NAME],

i was going through the [CATEGORY]s in [CITY] and [BUSINESS]
came up.

[PERSONAL].

honestly though, that's what got me reaching out: you turn up
when i search the normal way, but have you ever checked how
[BUSINESS] shows up when someone asks ChatGPT or Google's AI
for a "[CATEGORY] in [CITY]"?

most places haven't set up for it yet, and it's quickly
becoming how people pick who to go with.

worth a quick call on getting [BUSINESS] showing up there too?

%SIGNOFF%""",
    2: """[NAME], figured this might've slipped past.

with Google flipping AI on as the default search, when people ask
AI "best [CATEGORY] in [CITY]" it only names a few, and most
businesses haven't set themselves up to be one of them.

happy to walk you through how we'd get [BUSINESS] in there.

%SIGNOFF%""",
    3: """[NAME], one more thing worth knowing:

the tricky thing with AI search is when it skips you, there's no
missed call or bounced lead to notice. the customer just quietly
picks someone else, and you never know it happened.

worth a quick call on how we'd get [BUSINESS] picked instead?

%SIGNOFF%""",
    4: """[NAME], quick one and genuinely useful:

ask ChatGPT "what do you know about [BUSINESS]?" and most owners
are surprised by the answer: thin, outdated, or flat-out wrong.
that's the exact picture AI uses when it decides whether to
recommend you, so anything off is quietly costing you customers.

want the full picture, every AI engine plus how we'd fix what's
off? worth a quick call.

%SIGNOFF%""",
    5: """[NAME], haven't heard back, which usually means one of two
things: it's not a priority right now, or the timing's just off.

either one's completely fine. should i close this out on my end,
or would it make more sense to reconnect down the road?

%SIGNOFF%""",
}

# E1 carries a subject; E2-E5 are threaded replies (subject = Re: original)
SUBJECTS = {1: "quick q [NAME]", 2: None, 3: None, 4: None, 5: None}


def render(step: int, p: dict) -> dict:
    body = BODIES[step]
    name = (p.get("first_name") or "").strip()
    city = (p.get("city") or "").strip()
    personal = (p.get("personal_line") or "").strip()

    # city: keep "X in CITY" if we have a city, else drop the " in [CITY]" clause
    if city:
        body = body.replace("[CITY]", city)
    else:
        body = body.replace(" in [CITY]", "")

    body = body.replace("[BUSINESS]", p.get("business") or "")
    body = body.replace("[CATEGORY]", p.get("category") or "")

    # name: real name fills every slot; no name -> soft fallbacks (never "hey ," or a leading "[NAME] —")
    if name:
        body = body.replace("[NAME]", name)
    else:
        body = body.replace("hey [NAME],", "hey there,")
        body = body.replace("[NAME], ", "")
        body = body.replace("[NAME]", "there")

    # personal line (E1 only): fill it, or remove the whole "[PERSONAL]." line cleanly
    if personal:
        body = body.replace("[PERSONAL]", personal)
    else:
        body = body.replace("[PERSONAL].\n\n", "")
        body = body.replace("[PERSONAL].\n", "")
        body = body.replace("[PERSONAL]", "")

    body = body.replace("%SIGNOFF%", SIGNOFF)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    subj = SUBJECTS[step]
    subject = subj.replace("[NAME]", name).strip() if subj else None
    return {"subject": subject, "body": body}
