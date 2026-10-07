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
             "https://www.someothersite.com": "<title>Plumbing</title>"}
    monkeypatch.setattr(el, "_page_text", lambda url: pages[url] if url in pages else (_ for _ in ()).throw(OSError("dns")))
    sent = []
    sms_ok = {"on": True}
    monkeypatch.setattr(ch, "_ghl_sms", lambda m: (sent.append(m), sms_ok["on"])[1])
    monkeypatch.setattr(ch, "ALERTS_PENDING", tmp_path / "pending.jsonl")
    monkeypatch.setattr(ch, "ALERTS_INFLIGHT", tmp_path / "inflight.jsonl")
    monkeypatch.setattr(ch, "ALERTS_SENT", tmp_path / "sent.jsonl")
    monkeypatch.setattr(ch, "log", lambda m: None)
    monkeypatch.setattr(ch, "QUIET", (99, -1))  # daytime unless a test says night
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
                       'Phone: (940) 458-7787\nSite: not found\nReply "E1" to call.')
    c = next(iter(g.contacts.values()))
    assert "email-reply" in c["tags"] and c["phone"] == "+19404587787"
    assert el._code_of(c) == 1
    assert [o["pipelineStageId"] for o in g.opps] == [el.STAGE_REPLIED]
    assert len(g.notes) == 1 and "Yes please" in g.notes[0][1]
    assert not world["tmp"].joinpath("inflight.jsonl").exists()  # done, nothing left


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
