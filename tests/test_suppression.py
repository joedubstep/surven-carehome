from surven_outreach.clean import apply_suppression


def test_suppression_removes_listed_emails():
    rows = [{"email": "a@x.com"}, {"email": "joey@surven.ai"}, {"email": "b@y.com"}]
    suppressed = {"joey@surven.ai", "jake@surven.ai"}
    kept = apply_suppression(rows, suppressed)
    assert [r["email"] for r in kept] == ["a@x.com", "b@y.com"]
