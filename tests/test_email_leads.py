"""Cold email reply -> E code lead -> Joey's text (2026-10-06). A fake GoHighLevel stands in for the real one so every
path can be run: the normal reply, a second reply, two replies in one run, GoHighLevel down, night hours, no phone, no
website, a coworker answering, a re-run of the same alert. Run: python -m pytest tests/test_email_leads.py -q"""
import json

import pytest

from surven_outreach import carehome as ch
from surven_outreach import email_leads as el


class FakeGHL:
    def __init__(self):
        self.contacts, self.opps, self.notes, self.down, self.calls = {}, [], [], set(), []

    def __call__(self, method, path, body=None, version=""):
        self.calls.append((method, path))
        for bad in self.down:
            if bad in path:
                return {"message": "server error"}
        if path == "/contacts/upsert":
            c = next((c for c in self.contacts.values() if c["email"] == body["email"]), None)
            if not c:
                c = {"id": f"c{len(self.contacts) + 1}", "email": body["email"], "tags": [], "customFields": []}
                self.contacts[c["id"]] = c
            c.update({k: v for k, v in body.items() if k not in ("tags", "locationId")})
            c["tags"] = sorted(set(c["tags"]) | set(body.get("tags", [])))
            return {"contact": dict(c)}
        if path == "/contacts/search":
            tag = body["filters"][0]["value"]
            return {"contacts": [dict(c) for c in self.contacts.values() if tag in c["tags"]]}
        if path.startswith("/contacts/") and path.endswith("/tags"):
            c = self.contacts[path.split("/")[2]]
            c["tags"] = sorted(set(c["tags"]) | set(body["tags"]))
            return {"tags": c["tags"]}
        if path.startswith("/contacts/") and path.endswith("/notes"):
            self.notes.append((path.split("/")[2], body["body"]))
            return {"note": {"id": "n"}}
        if path.startswith("/contacts/"):
            c = self.contacts[path.split("/")[2]]
            if method == "PUT":
                if "phone" in body:
                    c["phone"] = body["phone"]
                for f in body.get("customFields", []):
                    c["customFields"] = [x for x in c["customFields"] if x["id"] != f["id"]] + [{"id": f["id"], "value": f["field_value"]}]
                return {"contact": dict(c), "succeded": True}
            return {"contact": dict(c)}
        if path.startswith("/opportunities/search"):
            cid = path.split("contact_id=")[1].split("&")[0]
            return {"opportunities": [o for o in self.opps if o["contactId"] == cid]}
        if path == "/opportunities/":
            o = dict(body, id=f"o{len(self.opps) + 1}")
            self.opps.append(o)
            return {"opportunity": o}
        raise AssertionError(path)


@pytest.fixture
def world(tmp_path, monkeypatch):
    g = FakeGHL()
    facts = {
        "tx-greens-haven-112104": {"facility": "GREENS HAVEN", "city": "Sanger", "state": "TX", "phone": "+19404587787", "website": ""},
        "ga-inspired-living-at-alpharetta-alc000257": {"facility": "INSPIRED LIVING AT ALPHARETTA", "city": "Alpharetta", "state": "GA", "phone": "+14705095995", "website": ""},
        "ny-nophone": {"facility": "Maple Court", "city": "Albany", "state": "NY", "phone": "", "website": "https://maplecourt.com"},
    }
    (tmp_path / "lead_facts.json").write_text(json.dumps(facts))
    monkeypatch.setattr(el, "FACTS", tmp_path / "lead_facts.json")
    monkeypatch.setattr(el, "_GIVEN", {})
    monkeypatch.setattr(el, "_ghl", g)
    pages = {"https://inspiredlivingatalpharetta.com": "<title>Inspired Living at Alpharetta</title>",
             "https://www.someothersite.com": "<title>Plumbing</title>",
             "https://maplecourt.com": "<title>Maple Court</title>" + " " * 600,
             "https://www.fitelivingcentre.com": "<title>The Fite Living Centre</title>" + " " * 600}
    monkeypatch.setattr(el, "_page_text", lambda url: pages[url] if url in pages else (_ for _ in ()).throw(OSError("dns")))
    sent = []
    sms_ok = {"on": True}
    monkeypatch.setattr(ch, "_ghl_sms", lambda m: (sent.append(m), sms_ok["on"])[1])
    monkeypatch.setattr(ch, "ALERTS_PENDING", tmp_path / "pending.jsonl")
    monkeypatch.setattr(ch, "ALERTS_INFLIGHT", tmp_path / "inflight.jsonl")
    monkeypatch.setattr(ch, "ALERTS_SENT", tmp_path / "sent.jsonl")
    monkeypatch.setattr(ch, "log", lambda m: None)
    monkeypatch.setattr(ch, "QUIET", (99, -1))  # daytime unless a test says night
    monkeypatch.setattr(el, "meta_line", lambda pid, facts, lead=None: [])  # no Ad Library visits in tests
    monkeypatch.setattr(el, "maps_website", lambda pid, facility: "")  # no Google Maps visits either
    return {"g": g, "sent": sent, "sms_ok": sms_ok, "tmp": tmp_path, "mp": monkeypatch}


def reply(pid, email, facility, said, from_name="", from_email=None, again=False, first="Lvn"):
    r = {"id": pid, "email": email, "facility": facility, "first_name": first}
    ch.queue_alert(r, "replied", said, from_name, from_email or email, again=again)


def test_a_reply_becomes_an_e_code_lead_and_texts_joey_everything(world):
    reply("tx-greens-haven-112104", "sjdn39love@gmail.com", "Greens Haven", "Yes please I would like to see your plan", "Shannon Green")
    ch.send_alert_texts()
    g, sent = world["g"], world["sent"]
    assert len(sent) == 1
    assert sent[0] == ('Email reply: E1. Shannon G, Greens Haven\n"Yes please I would like to see your plan"\n'
                       'Where: Sanger, TX\nPhone: (940) 458-7787\n(from state license)\nSite: not found\nReply "E1" to call.')
    c = next(iter(g.contacts.values()))
    assert "email-reply" in c["tags"] and c["phone"] == "+19404587787"
    assert el._code_of(c) == 1
    assert [o["pipelineStageId"] for o in g.opps] == [el.STAGE_REPLIED]
    assert len(g.notes) == 1 and "Yes please" in g.notes[0][1]
    assert world["tmp"].joinpath("inflight.jsonl").read_text() == ""  # done, nothing left (emptied, not removed)
    assert world["tmp"].joinpath("pending.jsonl").read_text() == ""


def test_the_text_says_care_beds_where_and_time_zone(world):
    """10/20, Joey wanted the home itself in the text: care, beds, where, time zone."""
    facts = json.loads(world["tmp"].joinpath("lead_facts.json").read_text())
    facts["ga-ml-conyers-llc-alc000295"] = {"facility": "ML CONYERS, LLC", "city": "Conyers", "state": "GA",
                                            "phone": "+17709221654", "website": "", "address": "1352 WELLBROOK CIR, CONYERS, GA 30012",
                                            "beds": 70, "beds_word": "beds", "care": "assisted living community, memory care",
                                            "tz": "America/New_York", "census": None, "open_beds": None, "census_date": ""}
    world["tmp"].joinpath("lead_facts.json").write_text(json.dumps(facts))
    reply("ga-ml-conyers-llc-alc000295", "dlittle3@mindspring.com", "Ml Conyers", "yes", first="")
    ch.send_alert_texts()
    text = world["sent"][0]
    assert "\nCare: assisted living community, memory care\nBeds: 70\nWhere: 1352 Wellbrook Cir, Conyers, GA 30012\nTime: Eastern\n" in text
    assert text.endswith('Phone: (770) 922-1654\n(from state license)\nSite: not found\nReply "E1" to call.')


def test_the_phone_says_where_it_came_from():
    """10/20, Joey: one or two words under the phone saying where it came from."""
    facts = {"phone": "+17709221654"}
    assert el.phone_source("+14045551212", "call me at 404-555-1212", "", facts) == "their reply"
    assert el.phone_source("+17709221654", "yes", "", facts) == "state license"
    assert el.phone_source("+16785550000", "yes", "+16785550000", facts) == "GoHighLevel"
    assert el.phone_source("", "yes", "", facts) == ""


def test_community_lines_units_census_and_gaps():
    f = {"care": "certified assisted living residence", "beds": 50, "beds_word": "units", "open_beds": 4,
         "census_date": "08/20/2026", "address": "157 Herrick Road, Newton, MA", "state": "MA", "tz": "America/Chicago"}
    lines = el.community_lines(f)
    assert lines == ["Care: certified assisted living residence", "Units: 50 (4 open at the 08/20/2026 state visit)",
                     "Where: 157 Herrick Road, Newton, MA", "Time: Central"]
    assert el.community_lines({}) == []  # nothing known, nothing said
    assert el.nice_address("388 12TH STREET, OAKLAND, CA 94607", "CA") == "388 12th Street, Oakland, CA 94607"
    assert el.nice_address("8300 SW BARNES RD, PORTLAND, OR 97225", "OR") == "8300 SW Barnes Rd, Portland, OR 97225"


def test_a_stale_copy_of_a_finished_alert_never_texts_twice(world):
    # The cloud keeps files it is not sent, so the queue must be emptied in place, never removed.
    reply("tx-greens-haven-112104", "sjdn39love@gmail.com", "Greens Haven", "Yes please", "Shannon Green")
    ch.send_alert_texts()
    for name in ("pending.jsonl", "inflight.jsonl"):
        assert world["tmp"].joinpath(name).exists()
    ch.send_alert_texts()
    assert len(world["sent"]) == 1


def test_business_email_site_is_used_only_when_the_page_names_the_home(world):
    reply("ga-inspired-living-at-alpharetta-alc000257", "ED@inspiredlivingatalpharetta.com", "Inspired Living At Alpharetta",
          "I would like for you to send it.", "Jennifer Fredericks")
    ch.send_alert_texts()
    assert "Site: inspiredlivingatalpharetta.com" in world["sent"][0]
    assert el.site_names_facility("https://www.someothersite.com", "Inspired Living At Alpharetta", "Alpharetta", "+14705095995") is False


def test_no_phone_says_so_and_never_invents_one(world):
    reply("ny-nophone", "owner@maplecourt.com", "Maple Court", "Sure", "Pat Lee")
    ch.send_alert_texts()
    t = world["sent"][0]
    assert "Phone: not found" in t and "E1 cannot dial yet" in t and "Site: maplecourt.com" in t


def test_codes_count_up_and_never_repeat_even_in_one_run(world):
    reply("tx-greens-haven-112104", "a@gmail.com", "Greens Haven", "yes", "Ann A")
    reply("ga-inspired-living-at-alpharetta-alc000257", "b@inspiredlivingatalpharetta.com", "Inspired", "yes", "Bo B")
    ch.send_alert_texts()
    assert [t.split(".")[0] for t in world["sent"]] == ["Email reply: E1", "Email reply: E2"]


def test_a_second_reply_keeps_the_same_code_one_contact_one_deal(world):
    reply("tx-greens-haven-112104", "sjdn39love@gmail.com", "Greens Haven", "Yes please", "Shannon Green")
    ch.send_alert_texts()
    reply("tx-greens-haven-112104", "sjdn39love@gmail.com", "Greens Haven", "Call me after 3", "Shannon Green", again=True)
    ch.send_alert_texts()
    g, sent = world["g"], world["sent"]
    assert len(g.contacts) == 1 and len(g.opps) == 1
    assert sent[1].startswith("Email reply again: E1. Shannon G, Greens Haven\n\"Call me after 3\"")


def test_ghl_down_still_texts_joey_now_then_the_code_when_it_works(world):
    world["g"].down = {"/contacts/upsert"}
    reply("tx-greens-haven-112104", "sjdn39love@gmail.com", "Greens Haven", "Yes please", "Shannon Green")
    ch.send_alert_texts()
    assert len(world["sent"]) == 1 and "Call code coming" in world["sent"][0] and "Phone: not found" in world["sent"][0]
    ch.send_alert_texts()  # still down: nothing new is sent, nothing lost
    assert len(world["sent"]) == 1
    world["g"].down = set()
    ch.send_alert_texts()
    assert world["sent"][1] == 'E1. Shannon G, Greens Haven is ready. Reply "E1" to call.'
    ch.send_alert_texts()
    assert len(world["sent"]) == 2  # done for good


def test_a_failed_text_is_retried_and_never_doubled(world):
    world["sms_ok"]["on"] = False
    reply("tx-greens-haven-112104", "sjdn39love@gmail.com", "Greens Haven", "Yes please", "Shannon Green")
    ch.send_alert_texts()
    world["sms_ok"]["on"] = True
    ch.send_alert_texts()
    ch.send_alert_texts()
    assert len(world["sent"]) == 2  # one failed try, one real send, no repeat
    assert len(world["g"].opps) == 1


def test_at_night_the_lead_is_made_but_the_text_waits_for_morning(world):
    world["mp"].setattr(ch, "QUIET", (0, 24))
    reply("tx-greens-haven-112104", "sjdn39love@gmail.com", "Greens Haven", "Yes please", "Shannon Green")
    ch.send_alert_texts()
    assert world["sent"] == [] and len(world["g"].opps) == 1
    world["mp"].setattr(ch, "QUIET", (99, -1))
    ch.send_alert_texts()
    assert len(world["sent"]) == 1 and 'Reply "E1" to call.' in world["sent"][0]
    assert len(world["g"].notes) == 1  # the note was not written twice


def test_a_coworker_reply_makes_the_coworker_the_lead_with_the_home_phone(world):
    reply("tx-greens-haven-112104", "sjdn39love@gmail.com", "Greens Haven", "She left, call me", "Dana Lee", from_email="dana@gmail.com")
    ch.send_alert_texts()
    c = next(iter(world["g"].contacts.values()))
    assert c["email"] == "dana@gmail.com" and c["phone"] == "+19404587787"
    assert world["sent"][0].startswith("Email reply: E1. Dana L, Greens Haven")


def test_opt_outs_still_text_the_old_way_and_make_no_lead(world):
    ch.queue_alert({"id": "x", "email": "x@y.com", "facility": "Heritage Oaks", "first_name": "Myra"}, "opted_out", "NO!", "Myra", "x@y.com")
    ch.send_alert_texts()
    assert world["sent"][0].startswith("Opt-out: Myra at Heritage Oaks") and not world["g"].contacts


def test_old_queue_lines_without_facts_still_send(world):
    world["tmp"].joinpath("pending.jsonl").write_text(json.dumps({"at": "x", "id": "y", "email": "z", "msg": "Cold email reply: old"}) + "\n")
    ch.send_alert_texts()
    assert world["sent"] == ["Cold email reply: old"]


def test_a_number_they_write_in_the_reply_beats_the_license_list(world):
    reply("tx-greens-haven-112104", "danielle@ciminocare.com", "Grand River Villa", "Luceli left. My cell (916)600-0734 is 24/7", "Danielle Lombardi")
    ch.send_alert_texts()
    c = next(iter(world["g"].contacts.values()))
    assert c["phone"] == "+19166000734" and "Phone: (916) 600-0734" in world["sent"][0]


def test_a_phone_joey_already_saved_is_never_overwritten(world):
    g = world["g"]
    g.contacts["c1"] = {"id": "c1", "email": "sjdn39love@gmail.com", "tags": [], "customFields": [], "phone": "+19404587787"}
    reply("tx-greens-haven-112104", "sjdn39love@gmail.com", "Greens Haven", "Yes please", "Shannon Green")
    ch.send_alert_texts()
    assert g.contacts["c1"]["phone"] == "+19404587787" and "Phone: (940) 458-7787" in world["sent"][0]


def test_meta_ads_line_yes_links_the_ad_library_no_says_no(monkeypatch):
    from datetime import date
    from surven_outreach.day7 import build as day7
    today = date.today().isoformat()
    recs = {"a": {"v": 2, "checked": today, "ads": "yes", "start": "2026-08-20", "page_id": "106212728072360",
                  "how": "Page linked on their website, ads link to gablesofojai.com"},
            "b": {"v": 2, "checked": today, "ads": "no"},
            "c": {"v": 2, "checked": today, "ads": "unsure", "search": "Maple Grove"},
            "d": {"v": 2, "checked": today, "ads": "no", "page": "The Fite Living Centre", "page_id": "339574046120088",
                  "how": "Page linked on their website"},
            "e": {"v": 2, "checked": today, "ads": "no", "page": "Nazareth House", "page_id": "495271293666620", "how": ""}}
    monkeypatch.setattr(day7, "load", lambda: recs)
    yes = el.meta_line("a", {})
    assert yes[0].startswith("Meta ads: yes, since mid August")
    assert yes[1] == "(how we know: Page linked on their website, ads link to gablesofojai.com)"
    assert yes[2].endswith("view_all_page_id=106212728072360")
    assert el.meta_line("b", {}) == ["Meta ads: none found (no Facebook Page found for them)"]
    unsure = el.meta_line("c", {})
    assert unsure[0].startswith("Meta ads: not sure") and "q=%22Maple%20Grove%22" in unsure[1]
    assert el.meta_line("d", {}) == ["Meta ads: no", "(their Page, The Fite Living Centre, has 0 ads. How we know it's "
                                                     "theirs: Page linked on their website)"]
    assert el.meta_line("e", {})[0] == "Meta ads: none found"  # a same-name Page nothing ties to them is not theirs


def test_a_reply_gets_a_fresh_meta_check_with_their_website_and_says_still_checking_when_meta_fails(monkeypatch, tmp_path):
    """10/26: the reply text re-checks Meta with the home's website (the day 7 check only had the name). 10/28: if
    Meta does not answer, the line says it is still checking (the next run tries again)."""
    from datetime import date
    from surven_outreach.day7 import adlib, build as day7
    old = {"x": {"checked": date.today().isoformat(), "ads": "no", "page": "", "page_id": ""}}
    saved, seen = {}, {}
    monkeypatch.setattr(day7, "load", lambda: dict(old))
    monkeypatch.setattr(day7, "save", lambda s: saved.update(s))
    monkeypatch.setattr(el, "FACTS", tmp_path / "lead_facts.json")
    (tmp_path / "queue.json").write_text(json.dumps([{"id": "x", "facility": "Fite Living Center", "home": "Fite Living Center"}]))
    monkeypatch.setattr(day7.S, "start", lambda: type("S", (), {"adlib": lambda self: None, "pw": None})())

    def check(al, name, city, state, website="", fb_links=()):
        seen.update(website=website, fb=list(fb_links))
        return {"running_ads": "no", "active_ads": 0, "page_name": "The Fite Living Centre", "page_id": "339574046120088",
                "match": "their own Page", "how": "Page linked on their website"}
    monkeypatch.setattr(adlib, "check_home", check)
    lines = el.meta_line("x", {}, {"website": "https://www.fitelivingcentre.com", "facebook": ""})
    assert seen == {"website": "https://www.fitelivingcentre.com", "fb": []}
    assert lines[0] == "Meta ads: no" and saved["x"]["v"] == 2 and saved["x"]["page_id"] == "339574046120088"

    def broken(*a, **k):
        raise adlib.RateLimited("Meta rate limited")
    monkeypatch.setattr(adlib, "check_home", broken)
    assert el.meta_line("x", {}, {"website": "https://www.fitelivingcentre.com"}) == [el.META_LATER]


def test_the_text_says_which_email_they_answered(world):
    """10/28, Joey: "I need to see what email they are replying to, like what stage they are in." """
    import sqlite3
    from datetime import datetime
    con = sqlite3.connect(":memory:")
    con.executescript(ch.SCHEMA)
    for step, mid, at, body in [(1, "<a1@surven.ai>", "2026-10-26T09:41:33-04:00",
                                 "Hi Melissa, how many rooms are open at The Lodge right now?\n\nJoey"),
                                (2, "<a2@surven.ai>", "2026-10-28T08:47:43-04:00", "Hey, I just emailed you.\n\nJoey")]:
        con.execute("INSERT INTO sends (prospect_id, step, email, subject, body, message_id, sent_at) VALUES (?,?,?,?,?,?,?)",
                    ("tx-greens-haven-112104", step, "m@x.com", "s", body, mid, at))
    when = datetime.fromisoformat("2026-10-28T09:50:00-04:00")
    assert ch.replied_to(con, "tx-greens-haven-112104", ["a2@surven.ai", "a1@surven.ai"], when) == {
        "step": 2, "sent": "2026-10-28", "line": "I just emailed you."}
    # they hit reply on the first email after the second one went out: the first is the one they answered
    assert ch.replied_to(con, "tx-greens-haven-112104", ["a1@surven.ai"], when)["line"] == (
        "How many rooms are open at The Lodge right now?")
    assert ch.replied_to(con, "tx-greens-haven-112104", [], datetime.fromisoformat("2026-10-27T10:00:00-04:00"))["step"] == 1
    world["tmp"].joinpath("queue.json").write_text(json.dumps([{"id": "tx-greens-haven-112104", "seq": "v3"}]))
    r = {"id": "tx-greens-haven-112104", "email": "sjdn39love@gmail.com", "facility": "Greens Haven", "first_name": "Lvn"}
    ch.queue_alert(r, "replied", "Yes we have 2", "Shannon Green", "sjdn39love@gmail.com",
                   to=ch.replied_to(con, r["id"], ["a2@surven.ai"], when))
    ch.send_alert_texts()
    assert world["sent"][0].startswith('Email reply: E1. Shannon G, Greens Haven\n'
                                       'Replied to: Day 3 email (2 of 4), sent Oct 28: "I just emailed you."\n"Yes we have 2"')


def test_when_meta_could_not_be_checked_joeys_pc_texts_the_answer(world):
    """10/28, Joey: "We must always check." The cloud's text says Meta is still being checked; Joey's PC
    (meta_followup.py) checks it where the Ad Library answers and texts the answer once."""
    from datetime import datetime
    from surven_outreach import meta_followup as mf
    world["mp"].setattr(el, "meta_line", lambda pid, facts, lead=None: [el.META_LATER])
    reply("tx-greens-haven-112104", "sjdn39love@gmail.com", "Greens Haven", "Yes please", "Shannon Green")
    ch.send_alert_texts()
    assert el.META_LATER in world["sent"][0] and len(world["sent"]) == 1
    sent = [json.loads(l) for l in world["tmp"].joinpath("sent.jsonl").read_text().splitlines()]
    assert sent[0]["meta_later"] is True  # finished in the cloud; the PC picks it up from here

    answers = [[el.META_LATER], ["Meta ads: no", "(their Page, Greens Haven, has 0 ads. How we know it's theirs: "
                                                  "Page linked on their website)"]]
    world["mp"].setattr(el, "meta_line", lambda pid, facts, lead=None: answers.pop(0))
    world["mp"].setattr(mf, "cloud_alerts", lambda: sent)
    world["mp"].setattr(mf, "DONE", world["tmp"] / "meta_followups.json")
    world["mp"].setattr(mf, "LOG", world["tmp"] / "meta_followup.log")
    world["mp"].setattr(mf, "datetime", type("dt", (datetime,), {"now": classmethod(
        lambda cls, tz=None: datetime.fromisoformat(sent[0]["at"]).replace(hour=12, tzinfo=tz))}))
    mf.run()  # Meta did not answer on the PC either: nothing texted, tried again next time
    assert len(world["sent"]) == 1
    mf.run()
    assert world["sent"][1] == ("E1. Shannon G, Greens Haven\nMeta ads: no\n(their Page, Greens Haven, has 0 ads. How "
                                "we know it's theirs: Page linked on their website)")
    mf.run()  # texted once
    assert len(world["sent"]) == 2


def test_a_website_in_their_signature_is_found_even_from_gmail(world):
    """10/26: Fite Living Center wrote from Gmail; its site was only in the signature, and the text said "not found"."""
    said = ("Please feel free to reach out to Jesse Rumsey for further details his number 404-932-9512.\nThank you,\n"
            "Charissa Rumsey, *Executive Director*\n*www.fitelivingcentre.com <http://www.fitelivingcentre.com>*\n"
            "*770-382-2002 office*")
    assert el.sites_in_text(said) == ["https://www.fitelivingcentre.com"]
    assert el.sites_in_text("email me at jo@fitelivingcentre.com or see facebook.com/x and surven.ai") == []
    reply("tx-greens-haven-112104", "fitelivingcentre@gmail.com", "Fite Living Center", said, "Fite Living Centre")
    ch.send_alert_texts()
    assert "\nSite: fitelivingcentre.com\n(from their reply)\n" in world["sent"][0]


def test_a_name_alone_does_not_tie_a_page_whose_ads_are_in_another_state_or_link_elsewhere():
    """10/26: "City View La" (Los Angeles) matched the "City View" apartments Page in Lansing, MI by name alone."""
    from surven_outreach.day7 import adlib
    lansing = {"count": 1, "words": "Luxury apartment home at City View | https://www.cityviewlansing.com/ | "
                                    "Luxury apartment property located at 110 W Hillsdale St Lansing, MI 48933"}
    assert adlib._ties(lansing, "", "City View La", "Los Angeles", "CA", "") == []
    assert adlib._ties(lansing, "", "City View La", "Los Angeles", "CA", "https://cityview.care") == []
    ojai = {"count": 3, "words": "Gables of Ojai assisted living | https://gablesofojai.com/tour | GABLESOFOJAI.COM"}
    assert adlib._ties(ojai, "city", "Gables Of Ojai", "Ojai", "CA", "https://gablesofojai.com", "their website") == [
        "Page linked on their website", "Page says Ojai", "ads name Ojai", "ads link to gablesofojai.com"]


def test_facebook_links_on_a_page():
    from surven_outreach.day7 import adlib
    html = ('<a href="https://www.facebook.com/TheFiteLivingCentre/">f</a>'
            '<a href="https://www.facebook.com/sharer/sharer.php?u=x">s</a>'
            '<a href="https://facebook.com/TheFiteLivingCentre">f2</a>'
            '<script>"https:\\/\\/www.facebook.com\\/tr?id=1"</script>'
            '<a href="https://www.facebook.com/profile.php?id=100064&amp;mibextid=x">p</a>')
    assert adlib.facebook_links(html) == ["https://www.facebook.com/TheFiteLivingCentre",
                                          "https://www.facebook.com/profile.php?id=100064"]
