"""A lead's reply never stays in Gmail Spam (10/20, Joey: "I would like any email reply from a lead to be in my main
[inbox]"). A fake Gmail stands in for the real one: the run's reply scan moves each lead's own mail from Spam to the
inbox once it is read (a first reply, an opt-out, a second reply after the thread stopped, a coworker at the home),
leaves bounce notices, auto-replies and everyone else's mail alone, and a failed move only logs a line. The one-time
`rescue-spam` sweep moves every lead message already sitting in Spam. Run: python -m pytest tests/test_spam_rescue.py -q"""
import imaplib
import json
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from types import SimpleNamespace

import pytest

from surven_outreach import carehome as ch

SPAM = "[Gmail]/Spam"
CFG = SimpleNamespace(imap_host="imap.test", imap_user="inbox@test", imap_password="x")


def mail(frm, subject, body="hi", reply_to=None, when="2026-10-15T12:00:00+00:00", auto=False, mid=None):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = frm, "inbox@test", subject
    m["Message-ID"] = mid or f"<{abs(hash((frm, subject, when)))}@mail.test>"
    if reply_to:
        m["In-Reply-To"] = m["References"] = reply_to
    if auto:
        m["Auto-Submitted"] = "auto-replied"
    m.set_content(body)
    return {"raw": m.as_bytes(), "when": datetime.fromisoformat(when)}


class FakeGmail:
    """Just the IMAP calls carehome makes, with Gmail's MOVE (it refuses in a read-only folder, like the real one)."""

    def __init__(self, boxes):
        self.boxes, self.next_uid, self.moves, self.selects, self.fail_move = {}, 1, [], [], set()
        for box, msgs in boxes.items():
            self.boxes[box] = {}
            for m in msgs:
                self.boxes[box][self.next_uid] = m
                self.next_uid += 1
        self.untagged_responses = {}

    def __call__(self, host, timeout=None):
        return self

    def login(self, user, pw):
        return "OK", [b"ok"]

    def logout(self):
        return "BYE", [b""]

    def select(self, box, readonly=False):
        self.box, self.readonly = box.strip('"'), readonly
        self.selects.append((self.box, readonly))
        self.untagged_responses = {"UIDVALIDITY": [b"7"]}
        return "OK", [str(len(self.boxes[self.box])).encode()]

    def uid(self, cmd, *args):
        msgs = self.boxes[self.box]
        if cmd == "search":
            got = sorted(msgs)
            if args[1].startswith("UID "):
                got = [u for u in got if u >= int(args[1][4:].split(":")[0])]
            return "OK", [" ".join(map(str, got)).encode()]
        if cmd == "fetch":
            out = []
            for u in [int(x) for x in str(args[0]).split(",")]:
                if u not in msgs:
                    continue
                raw = msgs[u]["raw"]
                part = raw if args[1] == "(BODY.PEEK[])" else raw.split(b"\n\n")[0] + b"\r\n\r\n"
                meta = f'{u} (UID {u} INTERNALDATE {imaplib.Time2Internaldate(msgs[u]["when"])} BODY[] {{{len(part)}}}'
                out += [(meta.encode(), part), b")"]
            return "OK", out
        if cmd == "MOVE":
            u = int(args[0])
            if u in self.fail_move:
                raise imaplib.IMAP4.abort("socket error")
            if self.readonly or u not in msgs:
                return "NO", [b"[READ-ONLY] or no such message"]
            self.boxes[args[1]][self.next_uid] = msgs.pop(u)
            self.next_uid += 1
            self.moves.append((self.box, u, args[1]))
            return "OK", [None]
        raise AssertionError(cmd)

    def senders(self, box):
        return sorted(re.search(rb"^From: (\S+)", m["raw"], re.M).group(1).decode() for m in self.boxes[box].values())


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "DB", tmp_path / "carehome.db")
    monkeypatch.setattr(ch, "SCAN_STATE", tmp_path / "scan_state.json")
    monkeypatch.setattr(ch, "ALERTS_PENDING", tmp_path / "pending.jsonl")
    logs = []
    monkeypatch.setattr(ch, "log", logs.append)
    monkeypatch.setattr(ch, "notify_reply", lambda *a, **k: None)
    con = ch.connect()
    homes = [  # id, email, facility, status, our email 1's Message-ID
        ("tx-sunrise", "owner@sunrisehome.com", "Sunrise Home", "active", "<m1@surven.ai>"),
        ("ga-heritage", "myra@heritageoaks.com", "Heritage Oaks", "opted_out", "<m2@surven.ai>"),
        ("ca-maple", "ann.maple@gmail.com", "Maple Court", "replied", "<m3@surven.ai>"),
    ]
    for pid, e, fac, status, mid in homes:
        con.execute("INSERT INTO prospects (id,email,facility,first_name,tz,status,current_step,stopped_on) VALUES (?,?,?,?,?,?,1,?)",
                    (pid, e, fac, "Pat", "America/Chicago", status, "2026-10-12" if status != "active" else None))
        con.execute("INSERT INTO sends (prospect_id,step,email,subject,message_id,sent_at,sender) VALUES (?,?,?,?,?,?,?)",
                    (pid, 1, e, "hey are you in charge of this?", mid, "2026-10-10T09:00:00-04:00", ch.PRIMARY))
    con.execute("INSERT INTO prospects (id,email,facility,tz,status) VALUES ('ny-later','new@laterhome.com','Later Home','America/New_York','queued')")
    con.commit()
    return {"con": con, "logs": logs, "tmp": tmp_path, "mp": monkeypatch}


def gmail(world, spam, inbox=()):
    g = FakeGmail({"INBOX": list(inbox), SPAM: list(spam)})
    world["mp"].setattr(ch.imaplib, "IMAP4_SSL", g)
    return g


def alerts(world):
    p = world["tmp"] / "pending.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def status(world, pid):
    return world["con"].execute("SELECT status FROM prospects WHERE id=?", (pid,)).fetchone()[0]


def test_leads_mail_leaves_spam_and_everything_else_stays(world):
    g = gmail(world, spam=[
        mail("owner@sunrisehome.com", "Re: hey are you in charge of this?", "Yes, send it.", reply_to="<m1@surven.ai>"),
        mail("myra@heritageoaks.com", "Re: hey are you in charge of this?", "I said NO. Stop.", reply_to="<m2@surven.ai>"),
        mail("ann.maple@gmail.com", "Re: hey", "Call me after 3", reply_to="<m3@surven.ai>"),
        mail("dana@sunrisehome.com", "about your email", "I run the home now, call me"),
        mail("mailer-daemon@googlemail.com", "Delivery Status Notification", "could not deliver to x@nowhere.com"),
        mail("owner@sunrisehome.com", "Automatic reply: hey", "Out until Monday", auto=True),
        mail("deals@spammer.biz", "Make money fast"),
        mail("new@laterhome.com", "hello", "we have not heard from you"),
        mail("owner@sunrisehome.com", "an old note", when="2026-10-01T12:00:00+00:00"),  # before we ever wrote
    ], inbox=[mail("someone@else.com", "inbox mail")])
    ch.scan_replies(world["con"], CFG)

    assert g.senders("INBOX") == ["ann.maple@gmail.com", "dana@sunrisehome.com", "myra@heritageoaks.com",
                                  "owner@sunrisehome.com", "someone@else.com"]
    assert g.senders(SPAM) == ["deals@spammer.biz", "mailer-daemon@googlemail.com", "new@laterhome.com",
                               "owner@sunrisehome.com", "owner@sunrisehome.com"]  # the auto-reply and the old note
    assert all(box == SPAM for box, _, _ in g.moves)  # nothing is ever moved out of the inbox
    assert ("INBOX", True) in g.selects and (SPAM, False) in g.selects  # inbox read-only, Spam writable
    # the alerts work the same as before: a first reply, a coworker writing after it, a second reply after a stop;
    # the opt-out home is already stopped, so its mail is only moved
    assert status(world, "tx-sunrise") == "replied"
    said = {(a["id"], a["again"]): a["said"] for a in alerts(world)}
    assert said == {("tx-sunrise", False): "Yes, send it.", ("tx-sunrise", True): "I run the home now, call me",
                    ("ca-maple", True): "Call me after 3"}
    assert "scan [Gmail]/Spam: 4 lead message(s) moved to the inbox" in world["logs"]


def test_a_reply_joey_already_heard_about_still_leaves_spam(world):
    m = mail("owner@sunrisehome.com", "Re: hey", "Yes", reply_to="<m1@surven.ai>", mid="<once@mail.test>")
    world["tmp"].joinpath("scan_state.json").write_text(json.dumps({"alerted": ["<once@mail.test>"]}))
    g = gmail(world, spam=[m])
    ch.scan_replies(world["con"], CFG)
    assert g.senders("INBOX") == ["owner@sunrisehome.com"] and g.boxes[SPAM] == {}
    assert alerts(world) == []  # no second alert


def test_a_failed_move_logs_a_line_and_the_scan_carries_on(world):
    g = gmail(world, spam=[
        mail("owner@sunrisehome.com", "Re: hey", "Yes, send it.", reply_to="<m1@surven.ai>"),
        mail("ann.maple@gmail.com", "Re: hey", "Call me after 3", reply_to="<m3@surven.ai>"),
    ])
    g.fail_move = {1}
    ch.scan_replies(world["con"], CFG)
    assert any(l.startswith("spam: could not move uid 1 (tx-sunrise) to the inbox: ") for l in world["logs"])
    assert g.senders("INBOX") == ["ann.maple@gmail.com"]  # the next one still moved
    assert status(world, "tx-sunrise") == "replied" and len(alerts(world)) == 2  # the reply still counts and alerts
    st = json.loads(world["tmp"].joinpath("scan_state.json").read_text())
    assert st[SPAM]["last_uid"] == 2  # the scan finished and kept its place


def test_the_sweep_moves_every_leads_message_already_in_spam(world, capsys):
    g = gmail(world, spam=[
        mail("myra@heritageoaks.com", "Re: hey are you in charge of this?", "NO!", reply_to="<m2@surven.ai>"),
        mail("owner@sunrisehome.com", "Automatic reply: hey", "Out until Monday", auto=True),
        mail("no-reply@bounces.improvmx.net", "Mail Delivery failure.", "owner@sunrisehome.com"),
        mail("deals@spammer.biz", "Make money fast"),
    ])
    dry = ch.rescue_spam(world["con"], CFG, dry=True)
    assert [m["facility"] for m in dry] == ["Heritage Oaks", "Sunrise Home"] and g.moves == []
    done = ch.rescue_spam(world["con"], CFG)
    assert [(m["from"], m["moved"]) for m in done] == [("myra@heritageoaks.com", True), ("owner@sunrisehome.com", True)]
    assert g.senders(SPAM) == ["deals@spammer.biz", "no-reply@bounces.improvmx.net"]
    assert "moved: myra@heritageoaks.com | Re: hey are you in charge of this? |" in capsys.readouterr().out
    assert world["logs"] == []  # the sweep writes no run log
