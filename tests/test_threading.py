from surven_outreach.threading_headers import new_message_id, reply_headers


def test_new_message_id_format():
    mid = new_message_id("surven.ai", seed="a@x.com-1")
    assert mid.startswith("<") and mid.endswith("@surven.ai>")


def test_new_message_id_is_deterministic_per_seed():
    assert new_message_id("surven.ai", "a@x.com-1") == new_message_id("surven.ai", "a@x.com-1")
    assert new_message_id("surven.ai", "a@x.com-1") != new_message_id("surven.ai", "a@x.com-2")


def test_reply_headers_chain_to_original():
    h = reply_headers(original_message_id="<m1@surven.ai>", original_subject="quick q Tony")
    assert h["In-Reply-To"] == "<m1@surven.ai>"
    assert h["References"] == "<m1@surven.ai>"
    assert h["Subject"] == "Re: quick q Tony"


def test_reply_headers_no_double_re():
    h = reply_headers("<m1@surven.ai>", "Re: quick q Tony")
    assert h["Subject"] == "Re: quick q Tony"
