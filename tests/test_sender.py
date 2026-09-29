from surven_outreach.sender import build_message, FakeSender
from surven_outreach.threading_headers import reply_headers


def test_build_message_sets_threading_and_plain_text():
    msg = build_message(sender_name="Joseph", sender_email="joey@surven.ai",
                        to="a@x.com", subject="quick q Tony", body="hi\n\nthanks,\nJoseph",
                        message_id="<m1@surven.ai>", extra_headers={})
    assert msg["From"] == "Joseph <joey@surven.ai>"
    assert msg["Message-ID"] == "<m1@surven.ai>"
    assert msg.get_content_type() == "text/plain"


def test_build_message_reply_headers_no_dup_subject():
    extra = reply_headers("<m1@surven.ai>", "quick q Tony")
    msg = build_message("Joseph", "joey@surven.ai", "a@x.com", extra["Subject"],
                        "body", "<m2@surven.ai>", extra)
    assert msg["In-Reply-To"] == "<m1@surven.ai>"
    assert msg["References"] == "<m1@surven.ai>"
    assert msg.get_all("Subject") == ["Re: quick q Tony"]  # exactly one Subject header


def test_fake_sender_records():
    fs = FakeSender()
    fs.send(build_message("Joseph", "joey@surven.ai", "a@x.com", "s", "b", "<m1@surven.ai>", {}))
    assert fs.sent[0]["To"] == "a@x.com"
