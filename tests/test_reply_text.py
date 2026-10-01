"""The whole reply reaches Joey, not its first line (2026-10-01, Danielle at CiminoCare)."""
import json
from email.message import EmailMessage

from surven_outreach import carehome
from surven_outreach.reply_text import is_opt_out, new_text, sender_name

OUR_EMAIL = "Hey Luceli,\nAre you in charge of filling beds at Grand River Villa?\nReply stop and I won't email again.\nJoey"


def _msg(plain=None, html=None, frm='"Lombardi, Danielle" <danielle.lombardi@ciminocare.com>'):
    m = EmailMessage()
    m["From"] = frm
    m["Subject"] = "RE: hey are you in charge of this?"
    if plain is not None:
        m.set_content(plain)
    if html is not None:
        if plain is None:
            m.set_content(html, subtype="html")
        else:
            m.add_alternative(html, subtype="html")
    return m


def test_outlook_reply_keeps_every_line_and_drops_the_quote():
    body = ("Hello\n\nLuceli is no longer with CiminoCare\n\nThank you,\nDanielle\nCell phone (916)600-0734 is a 24/7 line\n\n"
            "-----Original Message-----\nFrom: Surven Occupancy <joey@surven.ai>\nSent: Wednesday, September 30, 2026\n\n" + OUR_EMAIL)
    said = new_text(_msg(body))
    assert said == "Hello\nLuceli is no longer with CiminoCare\nThank you,\nDanielle\nCell phone (916)600-0734 is a 24/7 line"
    assert not is_opt_out(said)  # "reply stop" in OUR quoted email must not count


def test_gmail_reply_with_a_wrapped_wrote_line():
    body = ("Yes I'm the owner. Call me after 3.\n\nOn Wed, Sep 30, 2026 at 9:00 AM Surven Occupancy <\njoey@surven.ai> wrote:\n\n> "
            + OUR_EMAIL.replace("\n", "\n> "))
    assert new_text(_msg(body)) == "Yes I'm the owner. Call me after 3."


def test_html_only_reply_is_read():
    html = ("<div>Hello Joey,</div><div><br></div><div>We have 2 open rooms, call me at 555-0100.</div>"
            "<blockquote>Hey Luceli, are you in charge...</blockquote>")
    said = new_text(_msg(html=html))
    assert said == "Hello Joey,\nWe have 2 open rooms, call me at 555-0100."


def test_sender_name_is_who_wrote():
    assert sender_name(_msg("hi")) == "Lombardi, Danielle"
    assert sender_name(_msg("hi", frm="danielle.lombardi@ciminocare.com")) == "danielle.lombardi@ciminocare.com"


def test_opt_outs():
    for t in ["No thanks", "Stop", "unsubscribe", "Please remove me from your list", "we are not interested", "STOP emailing me"]:
        assert is_opt_out(t), t
    for t in ["No longer with the company", "No problem, call me", "Hello\nLuceli is no longer here", "Can we talk at 3?",
              "I stopped by your site"]:
        assert not is_opt_out(t), t


def test_alert_carries_the_whole_reply_and_the_real_writer(tmp_path, monkeypatch):
    monkeypatch.setattr(carehome, "ALERTS_PENDING", tmp_path / "alerts.jsonl")
    r = {"id": "ca-576804173", "email": "luceli@ciminocare.com", "first_name": "Luceli", "facility": "Grand River Villa"}
    said = "Hello\nLuceli is no longer with CiminoCare\nThank you,\nDanielle\nCell phone (916)600-0734 is a 24/7 line"
    carehome.queue_alert(r, "replied", said, "Lombardi, Danielle", "danielle.lombardi@ciminocare.com")
    msg = json.loads((tmp_path / "alerts.jsonl").read_text())["msg"]
    assert "Lombardi, Danielle (we emailed Luceli) at Grand River Villa wrote:" in msg
    assert "(916)600-0734" in msg and "no longer with CiminoCare" in msg


def test_long_reply_is_cut_at_a_word_and_fits_one_text(tmp_path, monkeypatch):
    monkeypatch.setattr(carehome, "ALERTS_PENDING", tmp_path / "alerts.jsonl")
    r = {"id": "x", "email": "a@b.com", "first_name": "Ann", "facility": "Home"}
    carehome.queue_alert(r, "replied", "word " * 400, "Ann", "a@b.com")
    msg = json.loads((tmp_path / "alerts.jsonl").read_text())["msg"]
    assert len(msg) < 1500 and msg.count("...") == 1
