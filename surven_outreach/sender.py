import smtplib
import ssl
import time
from email.message import EmailMessage

# transient connection failures worth retrying (NOT recipient/sender refusals — those are permanent)
TRANSIENT = (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, ConnectionError, TimeoutError, OSError)


def build_message(sender_name, sender_email, to, subject, body, message_id, extra_headers: dict,
                  html: str | None = None, images: list | None = None) -> EmailMessage:
    """Plain text by default. With html, a text + html email; each image (cid, bytes, subtype) sits inside the html
    part, shown in the email itself (cold email day 7, Joey 10/24: "put the actual ad in the email")."""
    msg = EmailMessage()
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    for k, v in (extra_headers or {}).items():
        if k == "Subject" and "Subject" in msg:
            del msg["Subject"]
        msg[k] = v
    msg.set_content(body)  # plain text
    if html:
        msg.add_alternative(html, subtype="html")
        part = msg.get_payload()[1]
        for cid, data, subtype in images or []:
            part.add_related(data, maintype="image", subtype=subtype, cid=f"<{cid}>",
                             filename=f"{cid}.{'jpg' if subtype == 'jpeg' else subtype}", disposition="inline")
    return msg


class SmtpSender:
    def __init__(self, host, port, user, password):
        self.host, self.port, self.user, self.password = host, port, user, password

    def send(self, msg: EmailMessage, attempts: int = 3):
        # retry transient drops (ImprovMX occasionally closes the connection mid-handshake);
        # recipient/sender refusals are NOT caught here -> they propagate for bounce-suppression
        last = None
        for i in range(attempts):
            try:
                with smtplib.SMTP(self.host, self.port, timeout=30) as s:
                    s.starttls(context=ssl.create_default_context())
                    s.login(self.user, self.password)
                    s.send_message(msg)
                return
            except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused):
                raise
            except TRANSIENT as e:
                last = e
                if i < attempts - 1:
                    time.sleep(2 * (i + 1))
        raise last


class FakeSender:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
