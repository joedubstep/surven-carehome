from surven_outreach.notify import push_ntfy


def test_push_ntfy_posts_title_and_body():
    captured = {}

    def fake_opener(req, timeout=None):
        captured["url"] = req.full_url
        captured["title"] = req.headers.get("Title")
        captured["body"] = req.data.decode()
        return object()

    ok = push_ntfy("https://ntfy.sh/topic", "Cold email reply",
                   "Jeremy at The Glasshouse replied.", opener=fake_opener)
    assert ok is True
    assert captured["url"] == "https://ntfy.sh/topic"
    assert captured["title"] == "Cold email reply"
    assert "Glasshouse" in captured["body"]


def test_push_ntfy_noop_without_topic():
    assert push_ntfy("", "t", "m") is False
