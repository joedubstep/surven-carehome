import email
import imaplib
from datetime import date, timedelta

from . import db as dbm

OPT_OUT = ("unsubscribe", "not interested", "stop", "remove me", "take me off")

# A reply can land AFTER the final email, once the prospect is already 'completed'
# (the E5 breakup is literally designed to provoke one). Keep scanning completed
# prospects for this many days past their last touch so those replies aren't missed.
COMPLETED_REPLY_WINDOW_DAYS = 21


def _email_body_text(m) -> str:
    if m.is_multipart():
        for part in m.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(errors="ignore")
                except Exception:
                    return ""
        return ""
    try:
        return m.get_payload(decode=True).decode(errors="ignore")
    except Exception:
        return ""


def classify_and_mark(con, gmail) -> dict:
    """For every active prospect: bounce -> suppress (no alert); auto-reply -> skip (stay active, no alert);
    opt-out phrase -> suppress (no alert); GENUINE human reply -> stop + flag for notification."""
    cutoff = (date.today() - timedelta(days=COMPLETED_REPLY_WINDOW_DAYS)).isoformat()
    active = con.execute(
        "SELECT email FROM prospects WHERE status='active' "
        "OR (status='completed' AND COALESCE(last_touch_date,'') >= ?)",
        (cutoff,),
    ).fetchall()
    results = {"replied": [], "bounced": [], "opted_out": [], "auto_skipped": []}
    for row in active:
        e = row["email"]
        if gmail.has_bounce_for(e):
            dbm.mark(con, e, "bounced")
            results["bounced"].append(e)
            continue
        if gmail.has_inbound_from(e):
            if gmail.is_auto_reply_from(e):
                results["auto_skipped"].append(e)   # out-of-office / autoresponder: not a real reply
                continue
            text = (gmail.body_text_from(e) or "").lower()
            status = "opted_out" if any(p in text for p in OPT_OUT) else "replied"
            dbm.mark(con, e, status)
            results[status].append(e)
    return results


class ImapClient:
    """Reads survengeo@gmail.com over IMAP using an app password. Same interface as the test FakeGmail."""

    def __init__(self, host, user, password, since_days=30):
        self.host, self.user, self.password, self.since_days = host, user, password, since_days

    def _conn(self):
        # socket timeout so a slow/stalled Gmail response fails fast instead of hanging the scan
        c = imaplib.IMAP4_SSL(self.host, timeout=45)
        c.login(self.user, self.password)
        c.select("INBOX")
        return c

    def _since(self) -> str:
        return (date.today() - timedelta(days=self.since_days)).strftime("%d-%b-%Y")

    def has_inbound_from(self, email_addr) -> bool:
        c = self._conn()
        try:
            _, data = c.search(None, "FROM", f'"{email_addr}"', "SINCE", self._since())
            return bool(data and data[0].split())
        finally:
            c.logout()

    def has_bounce_for(self, email_addr) -> bool:
        c = self._conn()
        try:
            for sender in ("mailer-daemon", "postmaster"):
                _, data = c.search(None, "SINCE", self._since(), "FROM", sender, "BODY", f'"{email_addr}"')
                if data and data[0].split():
                    return True
            return False
        finally:
            c.logout()

    def is_auto_reply_from(self, email_addr) -> bool:
        c = self._conn()
        try:
            _, data = c.search(None, "FROM", f'"{email_addr}"', "SINCE", self._since())
            ids = data[0].split() if data else []
            if not ids:
                return False
            _, md = c.fetch(ids[-1], "(RFC822)")
            m = email.message_from_bytes(md[0][1])
            auto = (m.get("Auto-Submitted", "") or "").lower()
            if auto and auto != "no":
                return True
            if (m.get("Precedence", "") or "").lower() in ("auto_reply", "bulk", "junk"):
                return True
            if m.get("X-Autoreply") or m.get("X-Autorespond"):
                return True
            subj = (m.get("Subject", "") or "").lower()
            markers = ("out of office", "automatic reply", "auto-reply", "autoreply",
                       "auto reply", "away from", "vacation", "out of the office")
            return any(k in subj for k in markers)
        finally:
            c.logout()

    def body_text_from(self, email_addr) -> str:
        c = self._conn()
        try:
            _, data = c.search(None, "FROM", f'"{email_addr}"', "SINCE", self._since())
            ids = data[0].split() if data else []
            if not ids:
                return ""
            _, msg_data = c.fetch(ids[-1], "(RFC822)")
            m = email.message_from_bytes(msg_data[0][1])
            if m.is_multipart():
                for part in m.walk():
                    if part.get_content_type() == "text/plain":
                        return part.get_payload(decode=True).decode(errors="ignore")
                return ""
            return m.get_payload(decode=True).decode(errors="ignore")
        finally:
            c.logout()

    def find_calcom_bookings(self, sender="hello@cal.com") -> list:
        """Return NEW/rescheduled Cal.com bookings (excludes reminders + cancellations)."""
        c = self._conn()
        try:
            _, data = c.search(None, "FROM", sender, "SINCE", self._since())
            ids = data[0].split() if data else []
            out = []
            for i in ids:
                _, md = c.fetch(i, "(RFC822)")
                m = email.message_from_bytes(md[0][1])
                subj = m.get("Subject", "") or ""
                if subj.lower().startswith("reminder:") or "cancel" in subj.lower():
                    continue
                body = _email_body_text(m).lower()
                if "has been scheduled" not in body and "has been rescheduled" not in body:
                    continue
                out.append({"message_id": m.get("Message-ID", "") or subj, "subject": subj})
            return out
        finally:
            c.logout()
