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
    for t in ["No thanks", "Stop", "unsubscribe", "Please remove me from your list", "we are not interested", "STOP emailing me",
              "Please remove us from your email list, OK?", "Take us off your mailing list", "Please stop sending these",
              "Do not email this address again", "Please no"]:
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


def test_a_missed_opt_out_is_fixed_on_the_next_run():
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(carehome.SCHEMA)
    for pid, why in [("gardenias", "reply in inbox: Please remove us from your email list, OK?"),
                     ("lodge", "reply in inbox: I have availability, are you looking for Memory Care?")]:
        con.execute("INSERT INTO prospects (id,email,facility,status,stopped_reason) VALUES (?,?,?,?,?)",
                    (pid, f"{pid}@x.com", pid, "replied", why))
    carehome.fix_missed_opt_outs(con)
    assert {r["id"]: r["status"] for r in con.execute("SELECT * FROM prospects")} == {"gardenias": "opted_out",
                                                                                       "lodge": "replied"}


def test_do_not_email_list_survives_a_lost_database(tmp_path, monkeypatch):
    """11/13: on Nov 9 the lead database was lost and 9 opt-outs got day 1 again. The do-not-email list lives in its
    own file: it learns every opt-out and bounce, and stops a listed home even after its row comes back as new."""
    import sqlite3
    monkeypatch.setattr(carehome, "SUPPRESS", tmp_path / "suppress.json")
    monkeypatch.setattr(carehome, "log", lambda m: None)

    def db(rows):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(carehome.SCHEMA)
        for pid, email_, status in rows:
            con.execute("INSERT INTO prospects (id,email,facility,status) VALUES (?,?,?,?)", (pid, email_, pid, status))
        return con
    carehome.apply_suppress(db([("gone", "a@x.com", "opted_out"), ("bad", "b@x.com", "bounced"), ("ok", "c@x.com", "active")]))
    fresh = db([("gone", "a@x.com", "queued"), ("bad2", "B@x.com", "active"), ("ok", "c@x.com", "active")])
    carehome.apply_suppress(fresh)
    assert {r["id"]: r["status"] for r in fresh.execute("SELECT * FROM prospects")} == {
        "gone": "opted_out", "bad2": "bounced", "ok": "active"}


def test_cloud_state_stops_a_run_with_no_database_and_never_saves_a_smaller_one(tmp_path, monkeypatch):
    import importlib.util, os, sqlite3
    import pytest
    spec = importlib.util.spec_from_file_location("cloud_state", os.path.join(os.path.dirname(__file__), "..", "tools", "cloud_state.py"))
    cs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cs)
    monkeypatch.setattr(cs, "fetch", lambda: "")
    with pytest.raises(SystemExit):
        cs.pull()
    monkeypatch.setattr(cs, "fetch", lambda: "abc")
    monkeypatch.setattr(cs, "remote_files", lambda sha: ["data/carehome/queue.json"])
    with pytest.raises(SystemExit):
        cs.pull()

    made = []

    def db(n):
        made.append(n)
        f = tmp_path / f"{len(made)}.db"
        con = sqlite3.connect(f)
        con.execute("CREATE TABLE sends (id INTEGER)")
        con.executemany("INSERT INTO sends VALUES (?)", [(i,) for i in range(n)])
        con.commit()
        con.close()
        return f.read_bytes()
    assert cs.shrinks(db(5000), db(10)) and not cs.shrinks(db(10), db(12))
    monkeypatch.setenv("ALLOW_DB_SHRINK", "1")
    assert not cs.shrinks(db(5000), db(10))
